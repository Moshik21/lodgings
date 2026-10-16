"""`lodgings thresholds`: the drain thresholds in force, and what a history of beats and events says about them.

Descriptive, not magic: for each threshold it counts what the recorded history would have done under it (how many
beat gaps would have read LATE, STALE or LOST; how many drains a canary threshold of 1, 2 or 3 would have caused; how
often the fallback share went over the line; how long running jobs take compared with the reboot window). You read the
numbers and choose; Lodgings never changes hosts.toml.
"""
from collections import defaultdict


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def dist(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p90": pct(xs, 90), "p95": pct(xs, 95), "p99": pct(xs, 99), "max": round(max(xs), 1) if xs else None}


def _t(r):
    return r.get("received_epoch") if r.get("received_epoch") is not None else (r.get("at_epoch") or 0)


def beats(records, t, machine_byes=()):
    """Gaps between consecutive beats of one host; a gap that starts with a goodbye is an expected absence and skipped.
    `machine_byes` are machine-wide goodbyes from the other views of the same machine: they speak for this host too."""
    gaps, after_bye, paced_gaps = [], 0, 0
    reads = {"LATE": 0, "STALE": 0, "LOST": 0}
    intervals = [r.get("interval_s") for r in records if r.get("interval_s") and not r.get("paced")]
    interval = pct(intervals, 50) or t["beat_interval_s"]
    rs = sorted(list(records) + list(machine_byes), key=_t)
    for a, b in zip(rs, rs[1:]):
        if a.get("kind") == "goodbye":
            after_bye += 1
            continue
        g = _t(b) - _t(a)
        gaps.append(g)
        late, stale = t["late_after_beats"] * interval, t["stale_after_s"]
        if a.get("paced"):  # slowed on purpose: judged against the interval that beat announced, as `lodgings states` does
            paced_gaps += 1
            late = t["late_after_beats"] * (a.get("interval_s") or interval)
            stale = min(max(stale, 2 * late), t["lost_after_s"])
        if g > t["lost_after_s"]:
            reads["LOST"] += 1
        elif g > stale:
            reads["STALE"] += 1
        elif g > late:
            reads["LATE"] += 1
    return {"beats": len(records), "gap_s": dist(gaps), "interval_s": interval, "goodbye_gaps_skipped": after_bye,
            "paced_gaps": paced_gaps, "would_read": reads}


def canaries(events, t):
    cs = sorted((e for e in events if e.get("kind") == "canary"), key=_t)
    runs, cur = [], 0
    for c in cs:
        if c["ok"]:
            if cur:
                runs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        runs.append(cur)
    spacing = [_t(b) - _t(a) for a, b in zip(cs, cs[1:])]
    ok_d = [c["duration_s"] for c in cs if c["ok"] and c.get("duration_s") is not None]
    return {"canaries": len(cs), "failed": sum(1 for c in cs if not c["ok"]), "failure_runs": runs, "spacing_s": dist(spacing),
            "ok_duration_s": dist(ok_d), "drains_if_threshold": {str(n): sum(1 for r in runs if r >= n) for n in (1, 2, 3)},
            "configured": t["canary_failures"]}


def fallbacks(events, t):
    ps = sorted((e for e in events if e.get("kind") == "placement"), key=_t)
    w = t["fallback_window_s"]
    shares, over = [], []
    lo = 0
    for i, p in enumerate(ps):
        now = _t(p)
        while _t(ps[lo]) <= now - w:
            lo += 1
        win = ps[lo:i + 1]
        if len(win) >= t["fallback_min_placements"]:
            s = sum(1 for e in win if e["outcome"] == "fallback") / float(len(win))
            shares.append(s)
            if s > t["fallback_share"]:
                over.append(now)
    episodes = 0
    for a, b in zip([None] + over, over):
        if a is None or b - a > w:
            episodes += 1
    return {"placements": len(ps), "fallbacks": sum(1 for e in ps if e["outcome"] == "fallback"),
            "share_when_judged": {k: (round(v, 3) if v is not None else None) for k, v in (("p50", pct(shares, 50)), ("p90", pct(shares, 90)), ("p99", pct(shares, 99)), ("max", max(shares) if shares else None))},
            "windows_judged": len(shares), "drain_episodes": episodes}


def jobs(events, t):
    start, ran = {}, defaultdict(list)
    for e in sorted((e for e in events if e.get("kind") == "job"), key=_t):
        if e["phase"] == "start":
            start[e["job"]] = e
        elif e["phase"] == "end" and e["job"] in start:
            s = start.pop(e["job"])
            ran[s.get("class") or "-"].append(_t(e) - _t(s))
    allr = [x for v in ran.values() for x in v]
    return {"by_class": {k: dist(v) for k, v in sorted(ran.items())}, "all": dist(allr),
            "longer_than_reboot_window": sum(1 for x in allr if x > t["reboot_window_s"])}


def evidence(per_host, thresholds_for, siblings=lambda name: ()):
    """per_host: {host: (beats, events)}; siblings(name): the other views of that host's machine.
    -> {host: {...}} for hosts with any history."""
    out = {}
    for name, (bs, es) in sorted(per_host.items()):
        if not bs and not es:
            continue
        t = thresholds_for(name)
        byes = [b for n in siblings(name) if n != name for b in per_host.get(n, ((), ()))[0]
                if b.get("kind") == "goodbye" and b.get("scope") == "machine"]
        out[name] = {"beats": beats(bs, t, byes), "canaries": canaries(es, t), "fallbacks": fallbacks(es, t), "jobs": jobs(es, t)}
    return out
