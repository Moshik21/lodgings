"""Host states from beats, goodbyes, canaries and placements: UP, LATE, STALE, LOST, DRAINING, DRAINED, PAUSED, ASLEEP
(and UNKNOWN before the first beat). See docs/drain.md for the table and the reasons behind each threshold.

Advice only: a state says what a scheduler *should* do with a host; nothing here tells a runner anything.
Every age is this machine's clock against `received_epoch` (sleep-safe, see clock.py); a time written by another machine
is only ever compared with another time from that same machine.
"""
from . import match

STATES = ("UP", "LATE", "STALE", "LOST", "DRAINING", "DRAINED", "PAUSED", "ASLEEP", "UNKNOWN")
TAKES_WORK = ("UP", "LATE")
EFFECT = {
    "UP": "takes work",
    "LATE": "takes work; the beat is late",
    "STALE": "no new placements; running work keeps its leases",
    "LOST": "no placements; its running jobs requeue once elsewhere",
    "DRAINING": "no new placements; running work finishes; parked lanes are offered a move",
    "DRAINED": "draining and nothing running: safe to sleep, reboot or update",
    "PAUSED": "no new placements while the host is in use; running work finishes",
    "ASLEEP": "said goodbye: an expected absence, no lost alarm; jobs it held requeue at once",
    "UNKNOWN": "no beat received yet: Lodgings cannot tell",
}


def _recv(r):
    return r.get("received_epoch") or 0


def _age_text(secs):
    secs = max(0, int(secs))
    if secs < 90:
        return "%d s" % secs
    if secs < 5400:
        return "%d min" % round(secs / 60.0)
    if secs < 172800:
        return "%d h" % round(secs / 3600.0)
    return "%d d" % round(secs / 86400.0)


def running_jobs(events):
    """Jobs whose last word was start or renew. -> [job record with started/last_word epochs], oldest first."""
    jobs = {}
    for e in sorted((e for e in events if e.get("kind") == "job"), key=_recv):
        j = jobs.setdefault(e["job"], {"job": e["job"], "started_epoch": _recv(e)})
        if e["phase"] == "start":
            j.update(started_epoch=_recv(e))
        for k in ("class", "attempt", "lease_s", "requires", "resubmit"):
            if e.get(k) not in (None, []) or k not in j:
                j[k] = e.get(k)
        j.update(phase=e["phase"], last_word_epoch=_recv(e))
    return [j for j in sorted(jobs.values(), key=lambda j: j["started_epoch"]) if j["phase"] != "end"]


def parked_lanes(events):
    lanes = {}
    for e in sorted((e for e in events if e.get("kind") == "lane"), key=_recv):
        lanes[e["lane"]] = dict(e, parked_epoch=_recv(e))
    return [l for l in sorted(lanes.values(), key=lambda l: l["parked_epoch"]) if l["phase"] == "park"]


def _canary_tail(events):
    """-> (consecutive failures at the end of the history, the last failure or None)"""
    n, last = 0, None
    for e in sorted((e for e in events if e.get("kind") == "canary"), key=_recv, reverse=True):
        if e["ok"]:
            break
        n += 1
        last = last or e
    return n, last


class Pool(object):
    """Everything the evaluation reads, loaded once: the registry, the records and the stored probes."""

    def __init__(self, reg, recs, probes, now=None):
        """With `now`, records received after it are left out, so a replayed history is judged as it stood then."""
        self.reg, self.recs, self.probes = reg, recs, probes
        keep = (lambda r: _recv(r) <= now) if now is not None else (lambda r: True)
        self.beats = {n: sorted((b for b in recs.beats(n) if keep(b)), key=_recv) for n in reg.hosts}
        self.events = {n: [e for e in recs.events(n) if keep(e)] for n in reg.hosts}

    def siblings(self, name):
        m = self.reg.hosts[name]["machine"]
        return [n for n, h in self.reg.hosts.items() if h["machine"] == m]

    def last(self, name, kind=None):
        for b in reversed(self.beats[name]):
            if kind is None or b.get("kind", "beat") == kind:
                return b
        return None


def liveness(pool, name, now, resumed):
    """-> (state, age_s or None, anchor note or None, goodbye or None) from beats alone."""
    t = pool.reg.thresholds(name)
    regular = pool.last(name, "beat")
    # goodbyes that speak for this host: its own, and machine-wide ones from the other views of its machine
    byes = [b for n in pool.siblings(name) for b in pool.beats[n]
            if b.get("kind") == "goodbye" and (n == name or b.get("scope") == "machine")]
    bye = max(byes, key=_recv) if byes else None
    if regular is None and bye is None:
        return "UNKNOWN", None, None, None
    anchor, note = _recv(regular) if regular else 0, None
    if bye and _recv(bye) > anchor:
        woke = [b for n in pool.siblings(name) for b in pool.beats[n] if b.get("kind", "beat") == "beat" and _recv(b) > _recv(bye)]
        if not woke or bye.get("scope") == "host":
            return "ASLEEP", now - _recv(bye), None, bye
        anchor = min(_recv(b) for b in woke)  # the machine is back; this view gets its silence counted from then
        note = "the machine woke %s ago (%s beat); no beat from this view since" % (_age_text(now - anchor), min(woke, key=_recv)["host"])
    floor_late = False
    if resumed and resumed > anchor:
        anchor, floor_late = resumed, True
        note = "this machine (the observer) was asleep or restarted; silence is counted from when it resumed"
    age = max(0.0, now - anchor)
    interval = (regular or {}).get("interval_s") or t["beat_interval_s"]
    stale_after = t["stale_after_s"]
    if (regular or {}).get("paced"):
        # a beat that said it was slowed on purpose is healthy but slow: it is judged against the interval it announced,
        # and STALE waits for twice the beats LATE does at that interval (never past lost_after_s)
        stale_after = min(max(stale_after, 2 * t["late_after_beats"] * interval), t["lost_after_s"])
    if age > t["lost_after_s"]:
        state = "LOST"
    elif age > stale_after:
        state = "STALE"
    elif age > t["late_after_beats"] * interval or floor_late:
        state = "LATE"
    else:
        state = "UP"
    beat_age = now - _recv(regular) if regular else None
    return state, beat_age if beat_age is not None else age, note, None


def _fresh_machine_beats(pool, name, now):
    """The latest regular beat of each view of this host's machine that is no older than stale_after_s."""
    t = pool.reg.thresholds(name)
    out = []
    for n in pool.siblings(name):
        b = pool.last(n, "beat")
        if b and now - _recv(b) <= t["stale_after_s"]:
            out.append(b)
    return out


def drain_inputs(pool, name, now):
    """-> (inputs that drain, paused input or None, notes). Each input is {input, detail}."""
    t = pool.reg.thresholds(name)
    inputs, notes, paused = [], [], None
    req = pool.recs.drain_request(name)
    if req:
        inputs.append({"input": "operator", "detail": "drain requested %s ago%s" % (_age_text(now - (req.get("requested_epoch") or now)),
                                                                                       ": " + req["reason"] if req.get("reason") else "")})
    events = pool.events[name]
    fails, last_fail = _canary_tail(events)
    if fails >= t["canary_failures"]:
        inputs.append({"input": "canary", "detail": "%d canaries failed in a row%s" % (fails, " (last: %s)" % last_fail["reason"] if last_fail.get("reason") else "")})
    elif fails:
        notes.append("the last canary failed (%d of %d before a drain)" % (fails, t["canary_failures"]))
    window = [e for e in events if e.get("kind") == "placement" and now - t["fallback_window_s"] < _recv(e) <= now]
    if window:
        fb = sum(1 for e in window if e["outcome"] == "fallback")
        share = fb / float(len(window))
        if len(window) >= t["fallback_min_placements"] and share > t["fallback_share"]:
            inputs.append({"input": "fallback", "detail": "%d of %d placements fell back in the last %s (%.0f%%, over %.0f%%)"
                           % (fb, len(window), _age_text(t["fallback_window_s"]), share * 100, t["fallback_share"] * 100)})
        elif fb:
            notes.append("%d of %d placements fell back in the last %s" % (fb, len(window), _age_text(t["fallback_window_s"])))
    seen = set()  # each machine-wide input once, from the freshest view that reports it
    for b in sorted(_fresh_machine_beats(pool, name, now), key=_recv, reverse=True):
        via = "seen by %s" % b["host"] if b["host"] != name else None

        def said(text, *quals):
            quals = [q for q in quals + (via,) if q]
            return text + (" (%s)" % ", ".join(quals) if quals else "")
        p = b.get("power") or {}
        if p.get("on_battery") and "battery" not in seen:
            seen.add("battery")
            pct = " at %d%%" % p["percent"] if p.get("percent") is not None else ""
            if t["on_battery"]:
                inputs.append({"input": "battery", "detail": said("the machine runs on battery" + pct)})
            else:
                notes.append(said("the machine runs on battery" + pct, "on_battery = false for this host"))
        r = b.get("reboot") or {}
        # a reboot pending inside a WSL distribution restarts that distribution, not the machine
        wsl_only = pool.reg.hosts.get(b["host"], {}).get("view") == "wsl" and b["host"] != name
        if r.get("pending") and not wsl_only and "reboot" not in seen:
            seen.add("reboot")
            if r.get("due_epoch") is not None and b.get("sent_epoch") is not None:
                due = _recv(b) + (r["due_epoch"] - b["sent_epoch"])  # a span on the host's clock, placed on ours
                left = due - now
                when = "due in %s" % _age_text(left) if left >= 0 else "was due %s ago" % _age_text(-left)
                detail = said("a reboot is " + when, r.get("source"))
                if left <= t["reboot_window_s"]:
                    inputs.append({"input": "reboot", "detail": detail})
                else:
                    notes.append(detail)
            elif t["reboot_unknown_due"] == "drain":
                inputs.append({"input": "reboot", "detail": said("a reboot is pending, time unknown", r.get("source"))})
            else:
                notes.append(said("a reboot is pending, time unknown", r.get("source")))
        if b.get("paused") and paused is None:
            paused = {"input": "paused", "detail": said("the paused marker is set")}
    return inputs, paused, notes


def evaluate_host(pool, name, now, resumed):
    h = pool.reg.hosts[name]
    t = pool.reg.thresholds(name)
    live, age, anchor_note, bye = liveness(pool, name, now, resumed)
    jobs = running_jobs(pool.events[name])
    lanes = parked_lanes(pool.events[name])
    inputs, paused, notes = drain_inputs(pool, name, now)
    why = []
    last_beat = pool.last(name, "beat") or {}
    paced = dict(last_beat["paced"], interval_s=last_beat.get("interval_s")) if last_beat.get("paced") and live in ("UP", "LATE", "STALE") else None
    if paced:
        why.append("beat slowed on purpose (%s; every %s, x%g)" % (paced["reason"], _age_text(paced["interval_s"]), paced["factor"]))
    if live == "STALE" and jobs:
        expired = [j for j in jobs if j.get("lease_s") and now - j["last_word_epoch"] > j["lease_s"]]
        if expired:
            live = "LOST"
            why.append("the lease of job %s expired with no word (%s ago)" % (expired[0]["job"], _age_text(now - expired[0]["last_word_epoch"] - expired[0]["lease_s"])))
    if live in ("UNKNOWN", "ASLEEP", "LOST"):
        state = live
    elif inputs:
        state = "DRAINING" if jobs else "DRAINED"
    elif paused:
        state = "PAUSED"
    else:
        state = live
    if live == "ASLEEP":
        why.append("%s said goodbye (%s) %s ago%s" % (bye["host"], bye.get("reason", "other"), _age_text(age),
                                                     "" if bye["host"] == name else ", for the whole machine"))
    elif live == "LOST" and not why:
        why.append("no beat for %s (lost after %s)" % (_age_text(age), _age_text(t["lost_after_s"])))
    elif live in ("LATE", "STALE"):
        why.append("beat %s old (%s after %s)" % (_age_text(age), live.lower(),
                                                   _age_text(t["stale_after_s"]) if live == "STALE" else "%g beats" % t["late_after_beats"]))
    elif live == "UNKNOWN":
        why.append("no beat received yet")
    if anchor_note:
        why.append(anchor_note)
    why += [i["detail"] for i in inputs]
    if paused:
        why.append(paused["detail"])
    if jobs:
        why.append("%d job%s running" % (len(jobs), "" if len(jobs) == 1 else "s"))
    if lanes:
        why.append("%d lane%s parked" % (len(lanes), "" if len(lanes) == 1 else "s"))
    requeue = [j["job"] for j in jobs] if state in ("LOST", "ASLEEP") else []
    last = pool.last(name)
    return {"host": name, "label": h["label"], "machine": h["machine"], "view": h["view"], "state": state, "liveness": live,
            "takes_work": state in TAKES_WORK, "effect": EFFECT[state], "beat_age_s": None if age is None else round(age, 1),
            "last_record": None if last is None else {"kind": last.get("kind", "beat"), "received_epoch": _recv(last)},
            "paced": paced, "why": why, "inputs": inputs + ([paused] if paused else []), "notes": notes,
            "running_jobs": jobs, "parked_lanes": lanes, "requeue_now": requeue}


def evaluate(pool, now, resumed=None, names=None):
    return [evaluate_host(pool, n, now, resumed) for n in (names or list(pool.reg.hosts))]


def eligible(pool, verdicts, origin, requires=(), job_class=None):
    """Hosts that could take work moved off `origin`: they take work now and honestly meet the requirements."""
    out = []
    by = {v["host"]: v for v in verdicts}
    for n, h in pool.reg.hosts.items():
        v = by.get(n)
        if n == origin or not v or not v["takes_work"]:
            continue
        if job_class and h["classes"] and not h["classes"].get(job_class):
            continue
        ok, missing, _ = match.requirements_met(pool.probes.current(n), list(requires)) if requires else (True, [], [])
        if ok:
            out.append((0 if v["state"] == "UP" else 1, n))
    return [n for _, n in sorted(out)]
