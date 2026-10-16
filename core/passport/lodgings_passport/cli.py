"""lodgings-passport: export | verify | import | confirm one lane.

Plain text first (one line per result, `·`-separated), `--json` for machines,
stable exit codes (docs/cli.md). Every action and refusal is one JSON line in
<state_dir>/actions.jsonl. The kill switch (<state_dir>/off or
LODGINGS_PASSPORT=off) makes every verb exit 0 without doing anything.
"""

import argparse
import json
import os
import sys

from . import VERSION
from . import config as config_mod
from . import confirm as confirm_mod
from . import export as export_mod
from . import importer, lane, verify
from .util import EXIT_OK, PassportError, SafeText, epoch_now, short


def _log(cfg, entry):
    try:
        os.makedirs(cfg["state_dir"], exist_ok=True)
        with open(os.path.join(cfg["state_dir"], "actions.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        sys.stderr.write("lodgings-passport: could not write the actions log: %s\n" % exc)


def _killed(cfg):
    if os.environ.get("LODGINGS_PASSPORT", "").lower() == "off":
        return "LODGINGS_PASSPORT=off"
    if os.path.exists(os.path.join(cfg["state_dir"], "off")):
        return "%s exists" % os.path.join(cfg["state_dir"], "off")
    return None


def _custody(result):
    text = "custody %s" % result["custody"]
    adopted = result.get("adopted")
    if adopted:
        text += " (adopted gate head%s)" % (", trees identical" if adopted.get("tree_equal") else ", differences confirmed")
    return text


def _line(result):
    v = result.get("verdict")
    task = "%s-%s" % (result.get("task"), result.get("seq")) if result.get("seq") is not None else str(result.get("task"))
    if v == "exported":
        parts = [v, task, "lane %s" % short(result["lane_head"]), _custody(result),
                 "%d ref(s)" % result["refs"], "%d record(s)" % len(result["records"]),
                 "pipeline %s" % result["pipeline"], "%d bytes" % result["bytes"], result["passport"]]
    elif v == "verified":
        parts = [v, task, "lane %s" % short(result["lane_head"]), _custody(result),
                 "%d check(s) ok" % len(result["checks"]),
                 "target ready" if result["target_checked"] else "no target checked", result["passport_id"][:19]]
    elif v == "imported":
        parts = [v, task, "branch %s at %s" % (result["branch"], short(result["resume_head"])),
                 _custody(result) + (" (bridge %s)" % short(result["bridge"]) if result["bridge"] else ""),
                 "worktree %s" % result["worktree"], "receipt %s" % result["receipt"]]
    elif v in ("confirmed", "already-confirmed"):
        parts = [v, task, "%d archive ref(s)" % len(result.get("archive_refs") or []),
                 "target %s at %s" % (result.get("target_host"), short(result.get("target_resume_head"))),
                 "origin lane, worktree and gate untouched"]
    else:
        parts = [str(v), task]
    return " · ".join(str(p) for p in parts)


def _parser():
    p = argparse.ArgumentParser(prog="lodgings-passport",
                                description="Move one lane between machines at a checkpoint, as a sealed passport.")
    p.add_argument("--version", action="version", version="lodgings-passport %s" % VERSION)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="JSON config file (docs/config.md)")
    common.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override one config value")
    common.add_argument("--state", help="state directory (actions log, kill switch)")
    common.add_argument("--json", action="store_true", help="print one JSON object instead of a line")
    sub = p.add_subparsers(dest="verb")

    e = sub.add_parser("export", parents=[common], help="write a sealed passport for one parked lane (origin read-only)")
    e.add_argument("--lane", help="lane spec JSON (lodgings.lane/1); flags below override it")
    e.add_argument("--out", required=True, help="directory that receives <task>-<seq>/")
    e.add_argument("--seq", type=int, help="sequence number (default: next free)")
    for flag in ("task", "project", "repo", "worktree", "branch", "base", "pipeline", "decisions"):
        e.add_argument("--" + flag)
    e.add_argument("--gate-git-dir", help="the review gate's bare repository")
    e.add_argument("--gate-ref", help="the lane's ref in the gate (default refs/heads/<branch>)")
    e.add_argument("--pipeline-head", help="head the review pipeline holds in its own worktree (must be in the lane)")
    e.add_argument("--archive-ref", action="append", metavar="PATTERN", help="for-each-ref pattern to carry")
    e.add_argument("--record", action="append", metavar="NAME=PATH", help="text record to carry")
    e.add_argument("--adopt-gate-head", action="store_true",
                   help="resume the lane at the review gate's head (a squash or rebase of it); lists what only "
                        "the lane holds and refuses unless the trees are identical or --confirm-differences matches")
    e.add_argument("--confirm-differences", metavar="TOKEN",
                   help="the token a refused --adopt-gate-head printed: adopt with exactly the differences it listed")
    e.add_argument("--pr-url")
    e.add_argument("--pr-head")
    e.add_argument("--custody-check", help="JSON argv of a read-only custody command run in the worktree")

    v = sub.add_parser("verify", parents=[common], help="check a passport's seal, bundle and records")
    v.add_argument("passport")
    v.add_argument("--repo", help="target repository: also check it holds the bundle prerequisites")

    i = sub.add_parser("import", parents=[common], help="take a passport into a target repo and open a fresh worktree")
    i.add_argument("passport")
    i.add_argument("--repo", required=True, help="target repository (clone of the same project)")
    i.add_argument("--worktree", required=True, help="new worktree path (must not exist)")
    i.add_argument("--branch", help="branch to create (default: the lane branch)")
    i.add_argument("--gate-git-dir", help="the target's review gate repository, checked read-only")
    i.add_argument("--gate-ref")
    i.add_argument("--custody-check", help="JSON argv of a read-only custody command run in the new worktree")

    c = sub.add_parser("confirm", parents=[common], help="on the origin: archive the moved heads after a verified import")
    c.add_argument("passport")
    c.add_argument("--repo", required=True, help="origin repository")
    c.add_argument("--receipt", help="the target's import receipt (default <passport>/after/import-receipt.json)")
    c.add_argument("--gate-git-dir", help="the origin's review gate repository, checked read-only")
    c.add_argument("--gate-ref")
    c.add_argument("--no-archive", action="store_true", help="write only the tombstone, no archive refs")
    return p


def _argv_json(raw):
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or not value:
        from .util import UsageError
        raise UsageError("--custody-check expects a JSON argv list, for example '[\"tool\", \"check\"]'")
    return value


def main(argv=None):
    streams = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = SafeText(streams[0]), SafeText(streams[1])  # text from a repository never prints raw
    try:
        return _main(argv)
    finally:
        sys.stdout, sys.stderr = streams


def _main(argv):
    args = _parser().parse_args(argv)
    if not args.verb:
        _parser().print_help()
        return 2
    cfg = None
    entry = {"at": epoch_now(), "verb": args.verb, "argv": list(sys.argv[1:] if argv is None else argv)}
    try:
        cfg = config_mod.load(args.config, args.set, args.state)
        entry["host"] = cfg["host_label"]
        killed = _killed(cfg)
        if killed:
            msg = "off · kill switch (%s): did nothing" % killed
            print(json.dumps({"verdict": "off", "why": killed}) if args.json else msg)
            entry.update(verdict="off", why=killed)
            _log(cfg, entry)
            return EXIT_OK
        if args.verb == "export":
            spec = lane.load(args.lane) if args.lane else {}
            spec = lane.merge(spec, lane.from_flags(args))
            if args.custody_check:
                spec["custody_check"] = {"argv": _argv_json(args.custody_check)}
            spec = lane.validate(spec)
            result = export_mod.export(spec, os.path.abspath(args.out), cfg, seq=args.seq)
        elif args.verb == "verify":
            result = verify.verify(args.passport, cfg, repo=args.repo)
        elif args.verb == "import":
            result = importer.do_import(args.passport, args.repo, args.worktree, cfg, branch=args.branch,
                                        gate_git_dir=args.gate_git_dir, gate_ref=args.gate_ref,
                                        custody_argv=_argv_json(args.custody_check))
        else:
            result = confirm_mod.confirm(args.passport, args.repo, cfg, receipt_path=args.receipt,
                                         gate_git_dir=args.gate_git_dir, gate_ref=args.gate_ref,
                                         archive=not args.no_archive)
        entry.update({k: result.get(k) for k in ("verdict", "task", "seq", "passport_id", "custody") if k in result})
        if cfg:
            _log(cfg, entry)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(_line(result))
            for w in result.get("warnings") or []:
                print("  note: %s" % w)
            if result.get("next"):
                print("  next: %s" % result["next"])
        return EXIT_OK
    except PassportError as exc:
        entry.update(verdict=exc.verdict, reason=exc.reason)
        if cfg:
            _log(cfg, entry)
        if args.json:
            print(json.dumps({"verdict": exc.verdict, "reason": exc.reason, "hint": exc.hint,
                              "evidence": exc.evidence, "exit": exc.code}, indent=2, sort_keys=True))
        else:
            print("%s · %s" % (exc.verdict, exc.reason))
            if exc.hint:
                first, _, rest = exc.hint.partition("\n")
                print("  how: %s" % first)
                for line in rest.splitlines():
                    print("       %s" % line if line.strip() else "")
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
