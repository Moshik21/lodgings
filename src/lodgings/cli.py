"""The `lodgings` command."""
import argparse
import json
import os
import re
import subprocess
import sys
import time

from . import NAME, VERSION, avail, beat as beatmod, chips, clock, evacuate as evacmod, evidence as evidmod, hosts, ledger as ledgermod, match, pace as pacemod, \
    probe as probemod, records as recmod, store as storemod

EXIT_OK, EXIT_FINDING, EXIT_USAGE, EXIT_PROBE, EXIT_INTERNAL = 0, 1, 2, 3, 70


def config_path(arg):
    if arg:
        return arg
    if os.environ.get("LODGINGS_HOSTS"):
        return os.environ["LODGINGS_HOSTS"]
    if os.path.exists("hosts.toml"):
        return "hosts.toml"
    base = os.environ.get("APPDATA") if os.name == "nt" else (os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    return os.path.join(base or "", "lodgings", "hosts.toml")


def parse_duration(text):
    text = text.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in mult and text[:-1].replace(".", "", 1).isdigit():
        return float(text[:-1]) * mult[text[-1]]
    raise argparse.ArgumentTypeError("expected a duration such as 90s, 30m, 24h")


def load_registry(args, need_valid=True):
    path = config_path(getattr(args, "hosts", None))
    reg, problems = hosts.load(path, force_lite=getattr(args, "builtin_toml", False))
    errs = hosts.errors(problems)
    for p in problems:
        if p.level == "warning" or need_valid:
            print(p, file=sys.stderr)
    if reg is None or (errs and need_valid):
        print("lodgings: %s is not usable; run `lodgings hosts check` for the full list" % path, file=sys.stderr)
        return None, problems
    if args_state(args):
        reg.pool["state_dir"] = os.path.abspath(os.path.expanduser(args_state(args)))
    return reg, problems


def args_state(args):
    return getattr(args, "state", None)


def pick_hosts(reg, names):
    if not names:
        return list(reg.hosts.values()), []
    missing = [n for n in names if n not in reg.hosts]
    return [reg.hosts[n] for n in names if n in reg.hosts], missing


def show_value(v):
    if v is None:
        return "-"
    return ", ".join(map(str, v)) or "-" if isinstance(v, list) else str(v)


def emit(args, obj, text_fn):
    if getattr(args, "json", False):
        print(json.dumps(obj, indent=1, sort_keys=True))
    else:
        text_fn()


# ---- hosts check ------------------------------------------------------------------------------
def cmd_hosts_check(args):
    path = config_path(args.hosts)
    reg, problems = hosts.load(path, force_lite=args.builtin_toml)
    errs = hosts.errors(problems)
    warns = [p for p in problems if p.level == "warning"]
    summary = {"file": path, "hosts": sorted(reg.hosts) if reg else [], "errors": [str(p) for p in errs], "warnings": [str(p) for p in warns]}

    def text():
        for p in problems:
            print(p)
        if reg:
            print("%s: %d host%s (%s)" % (path, len(reg.hosts), "" if len(reg.hosts) == 1 else "s", ", ".join(sorted(reg.hosts))))
        print("hosts check: %d error%s, %d warning%s" % (len(errs), "" if len(errs) == 1 else "s", len(warns), "" if len(warns) == 1 else "s"))
    emit(args, summary, text)
    return EXIT_FINDING if errs or (args.strict and warns) else EXIT_OK


# ---- probe / watch ------------------------------------------------------------------------------
def probe_line(host, doc, err, secs):
    if err:
        return "%-14s FAILED   %s" % (host["name"], err)
    d = doc["drift"]
    note = "drift: " + chips.drift_text(d["changes"], 2) if doc["probe"]["changed"] else ("baseline" if doc["probe"]["baseline"] else "unchanged")
    return "%-14s ok       %-8s fp %s  %s  (%.1f s)" % (host["name"], doc["probe"]["transport"], doc["fingerprint"]["short"], note, secs)


def run_probes(reg, selected, args):
    st = storemod.Store(reg.pool["state_dir"])
    results, failed = [], 0
    for h in selected:
        t0 = time.time()
        doc, err = probemod.probe_host(reg, st, h, deep=True if args.deep else None, save_raw=args.save_raw)
        failed += 1 if err else 0
        results.append({"host": h["name"], "ok": err is None, "error": err, "fingerprint": doc and doc["fingerprint"]["short"],
                        "drift": bool(doc and doc["probe"]["changed"])})
        if not args.json:
            print(probe_line(h, doc, err, time.time() - t0), flush=True)
    return results, failed


def cmd_probe(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    selected, missing = pick_hosts(reg, args.host)
    if missing:
        print("lodgings: no such host: %s" % ", ".join(missing), file=sys.stderr)
        return EXIT_USAGE
    if args.replay:
        if len(selected) != 1:
            print("lodgings: --replay needs exactly one host", file=sys.stderr)
            return EXIT_USAGE
        selected = [dict(selected[0], fixture=os.path.abspath(args.replay), ssh=None, local=False)]
    results, failed = run_probes(reg, selected, args)
    if args.json:
        print(json.dumps({"results": results}, indent=1))
    return EXIT_PROBE if failed else EXIT_OK


def defer_probe(args, lim):
    """Wait while the machine is busy or on a low battery, at most DEFER_MAX_S in all, then probe anyway (said once)."""
    waited, was = 0.0, False
    while not args.no_pace and waited < pacemod.DEFER_MAX_S:
        factor, reasons = pacemod.strain(pacemod.load_per_core(), beatmod.power_now(), None, was, lim)
        if factor == 1:
            break
        if not was:
            print("lodgings watch: probe deferred, checking again every %s (at most %s): %s"
                  % (chips.age_text(pacemod.DEFER_STEP_S), chips.age_text(pacemod.DEFER_MAX_S), "; ".join(reasons)), flush=True)
        was = True
        time.sleep(pacemod.DEFER_STEP_S)
        waited += pacemod.DEFER_STEP_S
    if was:
        print("lodgings watch: probing now%s" % (" (waited the most it will)" if waited >= pacemod.DEFER_MAX_S else ""), flush=True)


def cmd_watch(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    every = args.every or reg.pool["probe_interval_h"] * 3600
    selected, missing = pick_hosts(reg, args.host)
    if missing:
        print("lodgings: no such host: %s" % ", ".join(missing), file=sys.stderr)
        return EXIT_USAGE
    args.save_raw, args.json = args.save_raw, False
    print("lodgings watch: probing %d host%s now and every %s (Ctrl-C stops)" % (len(selected), "" if len(selected) == 1 else "s", chips.age_text(every)), flush=True)
    lim = pacemod.limits(args.pace_load, args.pace_battery)
    try:
        while True:
            if not args.once:  # a probe run by a scheduler is the scheduler's to time
                defer_probe(args, lim)
            print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), flush=True)
            run_probes(reg, selected, args)
            if args.once:
                return EXIT_OK
            time.sleep(every)
    except KeyboardInterrupt:
        print("lodgings watch: stopped")
        return EXIT_OK


# ---- status / show / history ------------------------------------------------------------------
def host_view(reg, st, h, now):
    doc, status = st.current(h["name"]), st.status(h["name"])
    return doc, status, chips.build(h, doc, status, reg.pool, now)


def cmd_status(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    st, now = storemod.Store(reg.pool["state_dir"]), time.time()
    rows = []
    for h in reg.hosts.values():
        doc, status, c = host_view(reg, st, h, now)
        d = doc and doc.get("drift")
        drift = ""
        if d and now - d["changed_epoch"] <= reg.pool["drift_visible_h"] * 3600:
            drift = "changed %s ago: %s" % (chips.age_text(now - d["changed_epoch"]), chips.drift_text(d["changes"], 2))
        rows.append({"host": h["name"], "view": h["view"], "state": c["state"], "probe_age_s": c["probe_age_s"], "fingerprint": c["fingerprint"],
                     "tags": len(doc["tags"]["effective"]) if doc else 0, "transport": c["transport"], "drift": drift,
                     "alerts": [a["title"] for a in c["alerts"] if a["severity"] != "info"], "chips": c["chips"]})

    def text():
        print("%-14s %-8s %-7s %-7s %-13s %-5s %s" % ("HOST", "VIEW", "STATE", "PROBED", "FINGERPRINT", "TAGS", "NOTES"))
        for r in rows:
            notes = [r["drift"]] if r["drift"] else []
            notes += r["alerts"]
            print("%-14s %-8s %-7s %-7s %-13s %-5s %s" % (r["host"], r["view"], r["state"], chips.age_text(r["probe_age_s"]) if r["probe_age_s"] is not None else "-",
                                                            r["fingerprint"] or "-", r["tags"] or "-", "; ".join(notes)))
            if args.verbose:
                for ch in r["chips"]:
                    print("    %-18s %s%s" % (ch["label"], ch["value"], "   [%s]" % ch["severity"] if ch["severity"] != "ok" else ""))
    emit(args, {"hosts": rows}, text)
    return EXIT_OK


def cmd_show(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    if args.host not in reg.hosts:
        print("lodgings: no such host: %s" % args.host, file=sys.stderr)
        return EXIT_USAGE
    doc = storemod.Store(reg.pool["state_dir"]).current(args.host)
    if not doc:
        print("lodgings: %s has not been probed yet; run `lodgings probe %s`" % (args.host, args.host), file=sys.stderr)
        return EXIT_FINDING

    def text():
        t = doc["tags"]
        print("%s (%s, %s) probed %s via %s, %d ms" % (doc["host"], doc["view"], doc["host_class"], doc["probed_at"], doc["probe"]["transport"], doc["probe"]["ms"]))
        print("fingerprint %s" % doc["fingerprint"]["hash"])
        print("webgpu: %s (%s)" % (doc["webgpu"]["state"], doc["webgpu"]["basis"]))
        print("effective tags: %s" % " ".join(t["effective"]))
        for k, label in (("contradicted", "declared but contradicted by the probe"), ("unverified", "declared, not measurable here"), ("plain", "declared, no probe rule")):
            if t[k]:
                print("%s: %s" % (label, " ".join(t[k])))
        for e in doc["expect"]:
            print("expect %-10s want %-6s have %-10s %s" % (e["tool"], e["want"], e["have"] or "-", {True: "ok", False: "MISMATCH", None: "unknown"}[e["ok"]]))
        print("fingerprint fields:")
        for k, v in sorted(doc["fingerprint"]["fields"].items()):
            print("  %-22s %s" % (k, v if not isinstance(v, list) else ", ".join(map(str, v)) or "-"))
        for e in doc["probe"]["errors"]:
            print("note: " + e)
    emit(args, doc, text)
    return EXIT_OK


def cmd_history(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    entries = storemod.Store(reg.pool["state_dir"]).history(args.host)

    def text():
        if not entries:
            print("%s: no fingerprint changes recorded" % args.host)
        for e in entries:
            print("%s  %s -> %s" % (e["changed_at"], e["from"], e["to"]))
            for c in e["changes"]:
                print("    %-22s %s -> %s  [%s]" % (c["field"], show_value(c["from"]), show_value(c["to"]), c["class"]))
    emit(args, {"host": args.host, "changes": entries}, text)
    return EXIT_OK


# ---- match / compare ------------------------------------------------------------------------------
def cmd_match(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    st = storemod.Store(reg.pool["state_dir"])
    requires = [r for r in (args.requires or "").replace(",", " ").split() if r]
    origin = None
    if args.same_toolchain_as:
        if args.same_toolchain_as not in reg.hosts:
            print("lodgings: no such host: %s" % args.same_toolchain_as, file=sys.stderr)
            return EXIT_USAGE
        origin = st.current(args.same_toolchain_as)
        if not origin:
            print("lodgings: %s has not been probed yet" % args.same_toolchain_as, file=sys.stderr)
            return EXIT_USAGE
    rows = []
    for h in reg.hosts.values():
        doc = st.current(h["name"])
        ok, missing, notes = match.requirements_met(doc, requires)
        diffs = []
        if origin is not None and doc is not None and h["name"] != args.same_toolchain_as:
            diffs = match.toolchain_differences(origin, doc)
            if diffs:
                ok = False
        rows.append({"host": h["name"], "eligible": ok, "missing": missing, "notes": notes,
                     "toolchain_differs": [{"field": d["field"], "origin": d["from"], "here": d["to"]} for d in diffs]})

    def text():
        for r in rows:
            why = []
            if r["missing"]:
                why.append("missing: " + " ".join(r["missing"]))
            for d in r["toolchain_differs"][:4]:
                why.append("%s is %s there, %s here" % (d["field"], show_value(d["origin"]), show_value(d["here"])))
            if len(r["toolchain_differs"]) > 4:
                why.append("+%d more differences (lodgings compare)" % (len(r["toolchain_differs"]) - 4))
            why += r["notes"]
            print("%-14s %-12s %s" % (r["host"], "eligible" if r["eligible"] else "not eligible", "; ".join(why)))
    emit(args, {"requires": requires, "same_toolchain_as": args.same_toolchain_as, "hosts": rows}, text)
    return EXIT_OK if any(r["eligible"] for r in rows) else EXIT_FINDING


def cmd_compare(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    st = storemod.Store(reg.pool["state_dir"])
    docs = []
    for n in (args.a, args.b):
        d = st.current(n) if n in reg.hosts else None
        if not d:
            print("lodgings: %s is unknown or not probed yet" % n, file=sys.stderr)
            return EXIT_USAGE
        docs.append(d)
    changes = match.toolchain_differences(docs[0], docs[1], classes=("toolchain", "os", "hardware", "runtime"))

    def text():
        if not changes:
            print("%s and %s have the same fingerprint fields" % (args.a, args.b))
        for c in changes:
            print("%-22s %-20s %-20s [%s]" % (c["field"], show_value(c["from"]), show_value(c["to"]), c["class"]))
    emit(args, {"a": args.a, "b": args.b, "differences": changes}, text)
    return EXIT_FINDING if changes else EXIT_OK


def cmd_chips(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    st, now = storemod.Store(reg.pool["state_dir"]), time.time()
    selected, missing = pick_hosts(reg, args.host)
    if missing:
        print("lodgings: no such host: %s" % ", ".join(missing), file=sys.stderr)
        return EXIT_USAGE
    out = [host_view(reg, st, h, now)[2] for h in selected]
    print(json.dumps({"schema": "lodgings.chips.list", "schema_version": "1.0", "hosts": out}, indent=1 if args.pretty else None, separators=None if args.pretty else (",", ":")))
    return EXIT_OK


# ---- beats, events, host states, drain, evacuate (docs/drain.md) -----------------------------------
def look(recs, now_arg):
    """This machine's clocks for one look. -> (now, resumed_epoch, note). With --now (a replay) nothing is written."""
    if now_arg is not None:
        return now_arg, (recs.observer() or {}).get("resumed_epoch"), None
    rec, resumed, note = clock.observe(recs.observer())
    recs.set_observer(rec)
    return rec["epoch"], resumed, note


def ingest(reg, recs, items, replay, now):
    """items: [(where, doc)]. -> (accepted per host, problems). Live records are stamped with this machine's clock."""
    per, problems = {}, []
    for where, doc in items:
        try:
            kind, rec = recmod.check(doc)
        except recmod.Rejected as exc:
            problems.append("%s: rejected: %s" % (where, exc))
            continue
        if rec["host"] not in reg.hosts:
            problems.append("%s: rejected: no host %r in %s" % (where, rec["host"], reg.path))
            continue
        if replay:
            at = doc.get("received_epoch") if isinstance(doc.get("received_epoch"), (int, float)) and not isinstance(doc.get("received_epoch"), bool) else None
            at = at if at is not None else (rec.get("sent_epoch") if kind == "beat" else rec.get("at_epoch"))
            if at is None:
                problems.append("%s: rejected: --replay needs received_epoch (or %s) in every record" % (where, "sent_epoch" if kind == "beat" else "at_epoch"))
                continue
            rec["received_epoch"] = at
        else:
            rec["received_epoch"] = round(now, 3)
        if kind == "event" and "at_epoch" not in rec:
            rec["at_epoch"] = rec["received_epoch"]
        beats, events = per.setdefault(rec["host"], ([], []))
        (beats if kind == "beat" else events).append(rec)
    for host, (beats, events) in per.items():
        recs.add(host, beats, events)
    return {h: len(b) + len(e) for h, (b, e) in per.items()}, problems


def read_inputs(paths):
    items, problems = [], []
    for path in paths or ["-"]:
        try:
            if path == "-":
                text, name = sys.stdin.read(), "stdin"
            else:
                with open(path, encoding="utf-8") as fh:
                    text, name = fh.read(), path
        except OSError as exc:
            problems.append("%s: cannot read: %s" % (path, exc))
            continue
        got, bad = recmod.parse_stream(text, name)
        items += got
        problems += bad
    return items, problems


def cmd_record(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    recs = recmod.Records(reg.pool["state_dir"])
    items, problems = read_inputs(args.files)
    now = time.time()
    if not args.replay:
        now = look(recs, None)[0]
    accepted, rejected = ingest(reg, recs, items, args.replay, now)
    problems += rejected
    for p in problems:
        print(p, file=sys.stderr)
    total = sum(accepted.values())

    def text():
        print("recorded %d record%s%s; %d rejected" % (total, "" if total == 1 else "s",
                                                     " (" + ", ".join("%s %d" % kv for kv in sorted(accepted.items())) + ")" if accepted else "", len(problems)))
    emit(args, {"recorded": accepted, "rejected": problems}, text)
    return EXIT_FINDING if problems else EXIT_OK


def beat_host(args, reg):
    if args.host:
        return args.host
    if os.environ.get("LODGINGS_HOST"):
        return os.environ["LODGINGS_HOST"]
    local = [h["name"] for h in reg.hosts.values() if h["local"]] if reg else []
    return local[0] if len(local) == 1 else None


def cmd_beat(args):
    path = config_path(args.hosts)
    reg = None
    if os.path.exists(path):
        reg, problems = hosts.load(path, force_lite=args.builtin_toml)
        if reg is not None and hosts.errors(problems):
            reg = None
        if reg is not None and args.state:
            reg.pool["state_dir"] = os.path.abspath(os.path.expanduser(args.state))
    name = beat_host(args, reg)
    if not name:
        print("lodgings: say which host this is: --host NAME, $LODGINGS_HOST, or one `local = true` host in hosts.toml", file=sys.stderr)
        return EXIT_USAGE
    if args.record and (reg is None or name not in reg.hosts):
        print("lodgings: --record needs a usable hosts.toml that lists %s" % name, file=sys.stderr)
        return EXIT_USAGE
    marker = args.paused_marker or (reg.hosts[name].get("paused_marker") if reg and name in reg.hosts else None)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    every = args.every
    interval = args.interval or every
    pacer = pacemod.Pacer(every or 1, pacemod.limits(args.pace_load, args.pace_battery, args.cpu_budget),
                          enabled=bool(every) and not args.no_pace and not args.goodbye)

    def once(paced=None, power=None):
        now = time.time()
        due = args.reboot_at if args.reboot_at is not None else (now + args.reboot_in if args.reboot_in is not None else None)
        doc = beatmod.measure(name, now=now, goodbye=args.goodbye, scope=args.scope, paused_marker=marker, reboot_due=due,
                              interval_s=interval * paced["factor"] if paced else interval, note=args.note, paced=paced, power=power)
        body = json.dumps(doc, sort_keys=True)
        if args.record:
            recs = recmod.Records(reg.pool["state_dir"])
            accepted, problems = ingest(reg, recs, [("beat", doc)], False, look(recs, None)[0])
            for p in problems:
                print(p, file=sys.stderr)
            return 0 if not problems else EXIT_FINDING
        if command:
            try:
                r = subprocess.run(command, input=body + "\n", encoding="utf-8", timeout=max(10, (every or 0) * 2))
                return r.returncode
            except (OSError, subprocess.SubprocessError) as exc:
                print("lodgings beat: %s: %s" % (command[0], exc), file=sys.stderr)
                return EXIT_FINDING
        if args.out:
            storemod._atomic(os.path.abspath(os.path.expanduser(args.out)), body + "\n")
            return 0
        print(body, flush=True)
        return 0

    if not every:
        return once()
    said, slowed = None, None
    try:
        while True:
            cpu0 = pacemod.cpu_seconds()
            power = beatmod.power_now() if pacer.enabled else None
            paced = pacer.assess(power)
            rc = once(paced, power)
            pacer.account(pacemod.cpu_seconds() - cpu0)
            if pacemod.gist(paced) != pacemod.gist(slowed):  # say a back-off once, and once again when it clears
                print("lodgings beat: %s" % ("slowed on purpose to every %g s: %s" % (paced["interval_s"], paced["reason"]) if paced else "back to every %g s" % every), file=sys.stderr, flush=True)
                slowed = paced
            if rc != said:  # say a failure once, and once again when it clears
                if rc or said is not None:
                    print("lodgings beat: %s" % ("delivered" if rc == 0 else "delivery failed (exit %s)" % rc), file=sys.stderr, flush=True)
                said = rc
            time.sleep(paced["interval_s"] if paced else every)
    except KeyboardInterrupt:
        return EXIT_OK


def pool_view(args):
    reg, _ = load_registry(args)
    if not reg:
        return None, None
    return reg, recmod.Records(reg.pool["state_dir"])


def make_pool(reg, recs, now):
    return avail.Pool(reg, recs, storemod.Store(reg.pool["state_dir"]), now)


def note_transitions(recs, verdicts, now):
    for v in verdicts:
        prev = recs.transitions(v["host"])
        last = prev[-1] if prev else None
        if not last or last.get("state") != v["state"]:
            recs.add_transition(v["host"], {"state": v["state"], "epoch": round(now, 3), "why": v["why"][:3]})
            v["since_epoch"] = round(now, 3)
        else:
            v["since_epoch"] = last.get("epoch")


def cmd_states(args):
    reg, recs = pool_view(args)
    if not reg:
        return EXIT_USAGE
    selected, missing = pick_hosts(reg, args.host)
    if missing:
        print("lodgings: no such host: %s" % ", ".join(missing), file=sys.stderr)
        return EXIT_USAGE
    now, resumed, note = look(recs, args.now)
    pool = make_pool(reg, recs, now)
    verdicts = avail.evaluate(pool, now, resumed)
    if args.now is None:
        note_transitions(recs, verdicts, now)
    else:
        for v in verdicts:
            prev = recs.transitions(v["host"])
            v["since_epoch"] = prev[-1]["epoch"] if prev and prev[-1].get("state") == v["state"] else None
    shown = [v for v in verdicts if v["host"] in {h["name"] for h in selected}]

    def text():
        if note:
            print("note: %s; silence is counted from now" % note)
        print("%-14s %-9s %-5s %-7s %-7s %s" % ("HOST", "STATE", "WORK", "BEAT", "SINCE", "WHY"))
        for v in shown:
            since = chips.age_text(now - v["since_epoch"]) if v.get("since_epoch") is not None else "-"
            beat = chips.age_text(v["beat_age_s"]) if v["beat_age_s"] is not None else "-"
            print("%-14s %-9s %-5s %-7s %-7s %s" % (v["host"], v["state"], "yes" if v["takes_work"] else "no", beat, since, "; ".join(v["why"]) or "-"))
            if args.verbose:
                print("    effect: %s" % v["effect"])
                for n in v["notes"]:
                    print("    note: %s" % n)
                for j in v["running_jobs"]:
                    print("    job %s (%s, attempt %s) running %s" % (j["job"], j.get("class") or "-", j.get("attempt") or 1, chips.age_text(now - j["started_epoch"])))
                for l in v["parked_lanes"]:
                    print("    lane %s (%s) parked %s ago" % (l["lane"], l.get("project") or "-", chips.age_text(now - l["parked_epoch"])))
        if any(v["state"] in ("DRAINING", "DRAINED", "PAUSED", "LOST", "ASLEEP") and (v["running_jobs"] or v["parked_lanes"]) for v in shown):
            print("advice only: nothing was moved; `lodgings evacuate HOST` shows what would move and how")
    emit(args, {"as_of_epoch": round(now, 3), "observer_resumed_epoch": resumed, "observer_note": note, "hosts": shown}, text)
    return EXIT_OK


def cmd_drain(args, undo=False):
    reg, recs = pool_view(args)
    if not reg:
        return EXIT_USAGE
    if args.host not in reg.hosts:
        print("lodgings: no such host: %s" % args.host, file=sys.stderr)
        return EXIT_USAGE
    now = time.time()
    if undo:
        had = recs.set_drain_request(args.host, None)
        msg = "%s: drain request withdrawn" % args.host if had else "%s: no drain request to withdraw" % args.host
    else:
        recs.set_drain_request(args.host, {"requested_epoch": round(now, 3), "reason": args.reason})
        msg = "%s: drain requested%s" % (args.host, " (%s)" % args.reason if args.reason else "")
    now, resumed, _ = look(recs, None)
    v = next(x for x in avail.evaluate(make_pool(reg, recs, now), now, resumed) if x["host"] == args.host)

    def text():
        print(msg)
        print("%s is now %s: %s" % (args.host, v["state"], v["effect"]))
        rest = [i["detail"] for i in v["inputs"] if i["input"] != "operator"]
        if undo and rest:
            print("still draining for: %s" % "; ".join(rest))
        print("advice only: no runner was told; schedulers that read `lodgings states` see it")
    emit(args, {"host": args.host, "requested": not undo, "state": v["state"], "why": v["why"]}, text)
    return EXIT_OK


def cmd_evacuate(args):
    reg, recs = pool_view(args)
    if not reg:
        return EXIT_USAGE
    if args.host not in reg.hosts:
        print("lodgings: no such host: %s" % args.host, file=sys.stderr)
        return EXIT_USAGE
    now, resumed, _ = look(recs, args.now)
    pool = make_pool(reg, recs, now)
    verdicts = avail.evaluate(pool, now, resumed)
    o = evacmod.offer(pool, verdicts, args.host, now)

    def text():
        print("%s: %s (%s)" % (o["host"], o["state"], o["effect"]))
        if o["why"]:
            print("  why: %s" % "; ".join(o["why"]))
        if o["state"] in avail.TAKES_WORK:
            print("  %s takes work; this is what evacuating it would do. `lodgings drain %s` records a drain request." % (o["host"], o["host"]))
        print("jobs (requeue %s):" % o["jobs_requeue"])
        if not o["jobs"]:
            print("  none running")
        for j in o["jobs"]:
            print("  %s  %s  attempt %d -> %d  running %s  %s" % (j["job"], j["class"] or "-", j["attempt"], j["next_attempt"], chips.age_text(j["running_for_s"]),
                                                               "could go to: " + ", ".join(j["targets"]) if j["targets"] else "no eligible host takes work now"))
            if j["resubmit"]:
                print("      resubmit: %s" % j["resubmit"])
        print("lanes (a passport move each, only when you decide):")
        if not o["lanes"]:
            print("  none parked")
        for l in o["lanes"]:
            print("  %s%s%s" % (l["lane"], " (project %s)" % l["project"] if l["project"] else "", ": no eligible host takes work now" if not l["moves"] else ""))
            for m in l["moves"]:
                print("    to %s:" % m["target"])
                for st in m["steps"]:
                    print("      on %-12s %s" % (st["on"], st.get("run") or "# " + st["ship"]))
                if m["missing"]:
                    print("      missing: %s" % "; ".join(m["missing"]))
        if any(l["moves"] for l in o["lanes"]):
            print("lodgings-passport is not installed with Lodgings: it is core/passport/bin/lodgings-passport in the source checkout; "
                  "the commands above assume it is reachable on each host as `lodgings-passport` (a Windows host runs the file by its path: `py -3 <checkout>\\core\\passport\\bin\\lodgings-passport`).")
        print("Lodgings ran nothing: this is an offer. Moving a lane is your decision; jobs are requeued by whoever submitted them.")
    emit(args, o, text)
    return EXIT_OK


def cmd_ledger(args):
    reg, _ = load_registry(args)
    if not reg:
        return EXIT_USAGE
    root, now = reg.pool["state_dir"], args.now if args.now is not None else time.time()
    path = ledgermod.ledger_path(root)
    try:
        fresh, held = ledgermod.run(root, {n: reg.thresholds(n)["beat_interval_s"] for n in reg.hosts}, now, args.days, args.dry_run)
    except ledgermod.Unreadable as exc:
        print("lodgings: the ledger %s is not readable (%s); nothing was written and nothing was changed. Move it aside or repair it." % (path, exc), file=sys.stderr)
        return EXIT_FINDING
    except OSError as exc:
        print("lodgings: cannot write the ledger: %s" % exc, file=sys.stderr)
        return EXIT_INTERNAL
    numbers = sum(1 for r in fresh if r["value"] is not None)
    corrections = sum(1 for r in fresh if r["supersedes"])
    plural = lambda n, word: "%d %s%s" % (n, word, "" if n == 1 else "s")

    def text():
        print("%s %s (%s, %d unknown%s) for %s%s" % ("would write" if args.dry_run else "wrote", plural(len(fresh), "line"), plural(numbers, "number"),
              len(fresh) - numbers, ", %s" % plural(corrections, "correction") if corrections else "", plural(args.days, "day"),
              "; %s already in the ledger" % plural(held, "reading") if held else ""))
        print("ledger: %s" % path)
    emit(args, {"ledger": path, "written": 0 if args.dry_run else len(fresh), "would_write": len(fresh) if args.dry_run else None,
                "corrections": corrections, "already_there": held, "dry_run": args.dry_run, "lines": fresh if args.dry_run else None}, text)
    return EXIT_OK


def cmd_thresholds(args):
    reg, recs = pool_view(args)
    if not reg:
        return EXIT_USAGE
    if args.source:
        items, problems = read_inputs(args.source)
        per = {}
        for where, doc in items:
            try:
                kind, rec = recmod.check(doc)
            except recmod.Rejected as exc:
                problems.append("%s: rejected: %s" % (where, exc))
                continue
            if "received_epoch" in doc:
                rec["received_epoch"] = doc["received_epoch"]
            b, e = per.setdefault(rec["host"], ([], []))
            (b if kind == "beat" else e).append(rec)
        for p in problems[:20]:
            print(p, file=sys.stderr)
    else:
        pool = make_pool(reg, recs, None)
        per = {n: (pool.beats[n], pool.events[n]) for n in reg.hosts}

    def thresholds_for(name):
        return reg.thresholds(name) if name in reg.hosts else dict(reg.drain)
    def siblings(name):
        m = reg.hosts[name]["machine"] if name in reg.hosts else None
        return [n for n, h in reg.hosts.items() if m is not None and h["machine"] == m]
    ev = evidmod.evidence(per, thresholds_for, siblings)
    overrides = {n: h["drain"] for n, h in reg.hosts.items() if h["drain"]}

    def text():
        print("thresholds in force ([drain] in %s; defaults in docs/drain.md):" % reg.path)
        for k in sorted(reg.drain):
            print("  %-24s %s" % (k, json.dumps(reg.drain[k])))
        for n, o in sorted(overrides.items()):
            print("  host %s overrides: %s" % (n, ", ".join("%s = %s" % (k, json.dumps(v)) for k, v in sorted(o.items()))))
        if not ev:
            print("no history: record beats and events (`lodgings record`), or pass --from FILE")
        for n, e in ev.items():
            t = thresholds_for(n)
            b, c, f, j = e["beats"], e["canaries"], e["fallbacks"], e["jobs"]
            print("%s:" % n)
            if b["beats"]:
                print("  beats      %d, interval %gs, gaps p50 %s p99 %s max %s s; would have read LATE %d, STALE %d, LOST %d time(s)%s"
                      % (b["beats"], b["interval_s"], b["gap_s"]["p50"], b["gap_s"]["p99"], b["gap_s"]["max"], b["would_read"]["LATE"],
                         b["would_read"]["STALE"], b["would_read"]["LOST"], ("; %d goodbye gap(s) skipped" % b["goodbye_gaps_skipped"] if b["goodbye_gaps_skipped"] else "")
                         + ("; %d gap(s) after a beat slowed on purpose, judged by its own interval" % b["paced_gaps"] if b["paced_gaps"] else "")))
            if c["canaries"]:
                print("  canaries   %d, %d failed, failure runs %s; drains at threshold 1/2/3: %s/%s/%s (configured %d); ok in p50 %s p99 %s s"
                      % (c["canaries"], c["failed"], c["failure_runs"] or "none", c["drains_if_threshold"]["1"], c["drains_if_threshold"]["2"],
                         c["drains_if_threshold"]["3"], t["canary_failures"], c["ok_duration_s"]["p50"], c["ok_duration_s"]["p99"]))
            if f["placements"]:
                sh = f["share_when_judged"]
                print("  fallbacks  %d of %d placements; share over %gs once %d were made: p50 %s p90 %s max %s; over %g: %d episode(s)"
                      % (f["fallbacks"], f["placements"], t["fallback_window_s"], t["fallback_min_placements"], sh["p50"], sh["p90"], sh["max"],
                         t["fallback_share"], f["drain_episodes"]))
            if j["all"]["n"]:
                print("  jobs       %d finished, ran p50 %s p95 %s p99 %s s; %d longer than the reboot window (%gs)"
                      % (j["all"]["n"], j["all"]["p50"], j["all"]["p95"], j["all"]["p99"], j["longer_than_reboot_window"], t["reboot_window_s"]))
    emit(args, {"thresholds": reg.drain, "host_overrides": overrides, "evidence": ev}, text)
    return EXIT_OK


def epoch_arg(text):
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected epoch seconds, such as 1800000000")
    if v < 0:
        raise argparse.ArgumentTypeError("expected epoch seconds, such as 1800000000")
    return v


# ---- parser ------------------------------------------------------------------------------------
def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hosts", help="hosts.toml (default: $LODGINGS_HOSTS, ./hosts.toml, then the per-user config directory)")
    common.add_argument("--state", help="state directory (default: [pool] state_dir, else the per-user state directory)")
    common.add_argument("--builtin-toml", action="store_true", help=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="lodgings", description="%s %s: know what each host in the pool can honestly run." % (NAME, VERSION))
    ap.add_argument("--version", action="version", version="%s %s" % (NAME.lower(), VERSION))
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, help_, fn, json_flag=True):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.set_defaults(fn=fn)
        if json_flag:
            p.add_argument("--json", action="store_true", help="machine-readable output")
        return p

    p = add("hosts", "check hosts.toml", cmd_hosts_check)
    p.add_argument("action", choices=["check"])
    p.add_argument("--strict", action="store_true", help="warnings (unknown keys) are errors")
    p = add("probe", "probe hosts now and store the result", cmd_probe)
    p.add_argument("host", nargs="*", help="host names (default: all)")
    p.add_argument("--deep", action="store_true", help="also start Chrome once to measure WebGPU")
    p.add_argument("--save-raw", action="store_true", help="keep the raw facts so the run can be replayed")
    p.add_argument("--replay", metavar="FACTS", help="for one host: use recorded facts from this file instead of probing")
    p = add("watch", "probe now, then every interval, in the foreground", cmd_watch, json_flag=False)
    p.add_argument("host", nargs="*")
    p.add_argument("--every", type=parse_duration, help="interval such as 24h (default: [pool] probe_interval_h)")
    p.add_argument("--once", action="store_true", help="probe once and exit")
    p.add_argument("--deep", action="store_true")
    p.add_argument("--save-raw", action="store_true")
    p.add_argument("--no-pace", action="store_true", help="never wait for a quiet machine (default: defer a probe up to an hour while the machine is loaded or on a low battery)")
    p.add_argument("--pace-load", type=float, metavar="PER_CORE", help="defer above this 5-minute load per core (default %g)" % pacemod.LOAD_LIMIT)
    p.add_argument("--pace-battery", type=float, metavar="PERCENT", help="defer on battery below this charge (default %d)" % pacemod.BATTERY_BELOW)
    p = add("status", "one line per host: state, age, fingerprint, drift", cmd_status)
    p.add_argument("-v", "--verbose", action="store_true", help="also list each host's chips")
    p = add("show", "everything known about one host, with evidence", cmd_show)
    p.add_argument("host")
    p = add("history", "fingerprint changes recorded for a host", cmd_history)
    p.add_argument("host")
    p = add("match", "which hosts satisfy a set of requirements", cmd_match)
    p.add_argument("--requires", help="tags, comma or space separated")
    p.add_argument("--same-toolchain-as", metavar="HOST", help="also require the same toolchain and OS fields as this host")
    p = add("compare", "fingerprint differences between two hosts", cmd_compare)
    p.add_argument("a")
    p.add_argument("b")
    p = add("chips", "display-ready chips and alerts per host (schema lodgings.chips)", cmd_chips, json_flag=False)
    p.add_argument("host", nargs="*")
    p.add_argument("--pretty", action="store_true")
    p = add("beat", "say this host's drain inputs (power, pending reboot, paused) as one lodgings.beat/1 record", cmd_beat, json_flag=False)
    p.add_argument("--host", help="this host's name in hosts.toml (default: $LODGINGS_HOST, else the one `local = true` host)")
    p.add_argument("--goodbye", metavar="REASON", choices=recmod.GOODBYE_REASONS, help="a goodbye before an expected absence: %s" % ", ".join(recmod.GOODBYE_REASONS))
    p.add_argument("--scope", choices=["machine", "host"], help="goodbye for the whole machine or this view only (default by reason)")
    p.add_argument("--paused-marker", metavar="FILE", help="paused while this file exists (default: paused_marker in hosts.toml, else %s)" % beatmod.default_paused_marker().replace("%", "%%"))
    p.add_argument("--reboot-in", type=parse_duration, metavar="DURATION", help="a reboot is due in this long (Windows cannot say)")
    p.add_argument("--reboot-at", type=epoch_arg, metavar="EPOCH", help="a reboot is due at this time (epoch seconds)")
    p.add_argument("--interval", type=parse_duration, help="how often this host beats (default: --every)")
    p.add_argument("--note", help="free text carried with the beat")
    p.add_argument("--every", type=parse_duration, help="keep beating at this interval, in the foreground")
    p.add_argument("--no-pace", action="store_true", help="with --every: never lengthen the interval (default: back off under load, on a low battery or over the CPU budget, and say so in the beat)")
    p.add_argument("--pace-load", type=float, metavar="PER_CORE", help="back off above this 5-minute load per core (default %g)" % pacemod.LOAD_LIMIT)
    p.add_argument("--pace-battery", type=float, metavar="PERCENT", help="back off on battery below this charge (default %d)" % pacemod.BATTERY_BELOW)
    p.add_argument("--cpu-budget", type=float, metavar="SECONDS", help="back off when this loop used more CPU-seconds than this in the last hour (default %g)" % pacemod.CPU_BUDGET_S)
    p.add_argument("--record", action="store_true", help="record into this machine's state directly (this machine is the collector)")
    p.add_argument("--out", metavar="FILE", help="write the record to FILE (atomically) instead of printing it")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- CMD ARG...: run CMD with the record on standard input (an argument list, never a shell string)")
    p = add("record", "record beats and events (lodgings.beat/1, lodgings.event/1) from files or standard input", cmd_record)
    p.add_argument("files", nargs="*", metavar="FILE", help="JSON lines or one JSON document; - or nothing reads standard input")
    p.add_argument("--replay", action="store_true", help="keep the times in the records (received_epoch, sent_epoch, at_epoch) instead of now")
    p = add("states", "host states: UP, LATE, STALE, LOST, DRAINING, DRAINED, PAUSED, ASLEEP (advice only)", cmd_states)
    p.add_argument("host", nargs="*")
    p.add_argument("-v", "--verbose", action="store_true", help="also the effect, notes, running jobs and parked lanes")
    p.add_argument("--now", type=epoch_arg, metavar="EPOCH", help="judge as of this time (replays; writes nothing)")
    p = add("drain", "request a drain of a host (advice only: recorded, no runner is told)", lambda a: cmd_drain(a))
    p.add_argument("host")
    p.add_argument("--reason", help="why, shown with the state")
    p = add("undrain", "withdraw a drain request", lambda a: cmd_drain(a, undo=True))
    p.add_argument("host")
    p = add("evacuate", "what evacuating a host would move, with the exact commands (an offer; runs nothing)", cmd_evacuate)
    p.add_argument("host")
    p.add_argument("--now", type=epoch_arg, metavar="EPOCH", help="judge as of this time (replays; writes nothing)")
    p = add("ledger", "append the pool's value ledger lines for the last closed day(s) (counts from its own records)", cmd_ledger)
    p.add_argument("--days", type=int, default=1, choices=range(1, 15), metavar="N", help="read the last N closed UTC days (default 1, at most 14); a day already in the ledger is only superseded by a better reading")
    p.add_argument("--now", type=epoch_arg, metavar="EPOCH", help="read as of this time: only records received by then (replays)")
    p.add_argument("--dry-run", action="store_true", help="print what would be written and write nothing")
    p = add("thresholds", "the drain thresholds in force and what the recorded history says about them", cmd_thresholds)
    p.add_argument("--from", dest="source", action="append", metavar="FILE", help="read this history (lodgings.beat/1 and lodgings.event/1 lines) instead of the state directory")
    return ap


# C0 but not tab or newline, DEL, C1 (0x9b is CSI, 0x9d OSC), zero-width and bidirectional controls, separators, BOM
# Must stay identical to _UNPRINTABLE in core/passport/lodgings_passport/util.py (a separate package).
_UNPRINTABLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")


class SafeText(object):
    """A text stream that shows every control character as a visible \\xNN.

    Lane, job and host names come from records and files that other machines write; an escape sequence in one must
    not reach the terminal. Escaping the ESC, BEL, CR and C1 characters defuses CSI and OSC sequences and their
    terminators. The same rule as core/passport (lodgings_passport.util.printable), which is a separate package.
    """

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return self._stream.write(_UNPRINTABLE.sub(
            lambda m: "\\x%02x" % ord(m.group()) if ord(m.group()) < 0x100 else "\\u%04x" % ord(m.group()), text))

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main(argv=None):
    streams = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = SafeText(streams[0]), SafeText(streams[1])
    try:
        return _main(argv)
    finally:
        sys.stdout, sys.stderr = streams


def _main(argv):
    for stream in (sys.stdout, sys.stderr, sys.stdin):  # the text holds characters such as "→"; a pipe on Windows defaults to the ANSI code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a text stream that can be reconfigured (replaced by a caller)
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
