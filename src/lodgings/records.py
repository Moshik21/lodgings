"""Beats and events: what hosts and runners tell the pool, checked and kept in the state directory (docs/drain.md).

    <state>/hosts/<name>/beats.jsonl        received beats and goodbyes, oldest first (the last 500 are kept)
    <state>/hosts/<name>/events.jsonl       canaries, placements, jobs and lanes reported for the host (the last 5000)
    <state>/hosts/<name>/drain.json         an operator's drain request (`lodgings drain` / `lodgings undrain`)
    <state>/hosts/<name>/transitions.jsonl  host state changes seen by `lodgings states` (the last 200)
    <state>/observer.json                   this machine's clocks at the last look (how sleep is noticed)
    <state>/ledger/lodgings.jsonl             the value ledger, written only by ledger.py (append-only, docs/ledger.md)

Every record carries `received_epoch`: this machine's clock when the record arrived. Ages are measured against that,
never against a time another machine wrote, and never from a wall-clock string.
"""
import json
import math
import os
import time

from . import catalogue
from .store import _atomic, _read_json

BEAT_SCHEMA = "lodgings.beat/1"
EVENT_SCHEMA = "lodgings.event/1"
BEATS_KEEP, EVENTS_KEEP, TRANSITIONS_KEEP = 500, 5000, 200
GOODBYE_REASONS = ("sleep", "lid", "hibernate", "shutdown", "reboot", "wsl-stop", "stop", "other")
HOST_SCOPED = ("wsl-stop", "stop")  # a goodbye for these reasons says nothing about the other views of the machine
GATE_RESULTS = ("passed", "refused", "unchecked")  # what the toolchain gate did for a placement
EVENT_KINDS = {"canary": None, "placement": ("placed", "fallback"), "job": ("start", "renew", "end"), "lane": ("park", "leave")}


class Rejected(ValueError):
    pass


def _num(doc, key, required=False, minimum=None):
    v = doc.get(key)
    if v is None:
        if required:
            raise Rejected("%s is required" % key)
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise Rejected("%s must be a finite number" % key)
    if minimum is not None and v < minimum:
        raise Rejected("%s must be at least %s" % (key, minimum))
    return v


def _str(doc, key, required=False, choices=None, limit=200):
    v = doc.get(key)
    if v is None:
        if required:
            raise Rejected("%s is required" % key)
        return None
    if not isinstance(v, str) or not v or len(v) > limit:
        raise Rejected("%s must be a non-empty string (at most %d characters)" % (key, limit))
    if choices and v not in choices:
        raise Rejected("%s must be one of %s" % (key, ", ".join(choices)))
    return v


def _bool(doc, key):
    v = doc.get(key)
    if v is not None and not isinstance(v, bool):
        raise Rejected("%s must be true, false or null" % key)
    return v


def _tags(doc, key):
    v = doc.get(key)
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(t, str) and catalogue.TAG_RE.match(t) for t in v):
        raise Rejected("%s must be a list of tags" % key)
    return v


def check_beat(doc):
    """-> a clean lodgings.beat/1 record (unknown fields dropped). Raises Rejected."""
    out = {"schema": BEAT_SCHEMA, "host": _str(doc, "host", True, limit=48), "kind": _str(doc, "kind", choices=("beat", "goodbye")) or "beat"}
    for k in ("seq", "sent_epoch", "uptime_s", "interval_s"):
        v = _num(doc, k, minimum=0)
        if v is not None:
            out[k] = v
    if out.get("interval_s") == 0:
        raise Rejected("interval_s must be greater than 0")
    for k in ("boot_id", "note"):
        v = _str(doc, k)
        if v is not None:
            out[k] = v
    if out["kind"] == "goodbye":
        out["reason"] = _str(doc, "reason", choices=GOODBYE_REASONS) or "other"
        out["scope"] = _str(doc, "scope", choices=("machine", "host")) or ("host" if out["reason"] in HOST_SCOPED else "machine")
    for k in ("power", "reboot"):
        v = doc.get(k)
        if v is not None and not isinstance(v, dict):
            raise Rejected("%s must be an object" % k)
    power = doc.get("power") or {}
    out["power"] = {"on_battery": _bool(power, "on_battery"), "percent": _num(power, "percent", minimum=0)}
    reboot = doc.get("reboot") or {}
    out["reboot"] = {"pending": _bool(reboot, "pending"), "due_epoch": _num(reboot, "due_epoch", minimum=0), "source": _str(reboot, "source")}
    out["paused"] = _bool(doc, "paused")
    paced = doc.get("paced")
    if paced is not None and out["kind"] == "beat":
        if not isinstance(paced, dict):
            raise Rejected("paced must be an object")
        factor = _num(paced, "factor", required=True)
        if factor <= 1:
            raise Rejected("paced.factor must be greater than 1 (a beat at its normal pace says nothing)")
        if out.get("interval_s") is None:
            raise Rejected("a paced beat must carry the interval_s it will now keep")
        out["paced"] = {"reason": _str(paced, "reason", True), "factor": factor}
    return out


def check_event(doc):
    """-> a clean lodgings.event/1 record. Raises Rejected."""
    kind = _str(doc, "kind", True, choices=tuple(EVENT_KINDS))
    out = {"schema": EVENT_SCHEMA, "kind": kind, "host": _str(doc, "host", True, limit=48)}
    v = _num(doc, "at_epoch", minimum=0)
    if v is not None:
        out["at_epoch"] = v
    if kind == "canary":
        ok = doc.get("ok")
        if not isinstance(ok, bool):
            raise Rejected("a canary needs ok: true or false")
        out["ok"] = ok
        out["duration_s"] = _num(doc, "duration_s", minimum=0)
        out["reason"] = _str(doc, "reason")
    elif kind == "placement":
        out["outcome"] = _str(doc, "outcome", True, choices=EVENT_KINDS["placement"])
        out["reason"] = _str(doc, "reason")
        out["class"] = _str(doc, "class", limit=48)
        out["gate"] = _str(doc, "gate", choices=GATE_RESULTS)
    elif kind == "job":
        out["phase"] = _str(doc, "phase", True, choices=EVENT_KINDS["job"])
        out["job"] = _str(doc, "job", True)
        out["class"] = _str(doc, "class", limit=48)
        out["attempt"] = int(_num(doc, "attempt", minimum=1) or 1)
        out["lease_s"] = _num(doc, "lease_s", minimum=1)
        out["queued_s"] = _num(doc, "queued_s", minimum=0)
        out["requires"] = _tags(doc, "requires")
        out["rc"] = doc.get("rc") if isinstance(doc.get("rc"), (int, str)) and not isinstance(doc.get("rc"), bool) else None
        argv = doc.get("resubmit")
        if argv is not None and not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise Rejected("resubmit must be an argument list (never a shell string)")
        out["resubmit"] = argv
    else:
        out["phase"] = _str(doc, "phase", True, choices=EVENT_KINDS["lane"])
        out["lane"] = _str(doc, "lane", True, limit=96)
        for k in ("project", "spec", "repo", "task"):
            out[k] = _str(doc, k, limit=400)
        out["seq"] = int(_num(doc, "seq", minimum=1) or 1)
        out["requires"] = _tags(doc, "requires")
    return out


def check(doc):
    """One record of either schema. -> (kind, clean record)."""
    if not isinstance(doc, dict):
        raise Rejected("a record must be a JSON object")
    schema = doc.get("schema")
    if schema == BEAT_SCHEMA:
        return "beat", check_beat(doc)
    if schema == EVENT_SCHEMA:
        return "event", check_event(doc)
    raise Rejected("schema must be %s or %s (got %r)" % (BEAT_SCHEMA, EVENT_SCHEMA, schema))


def parse_stream(text, source):
    """JSON lines, or one JSON document (object or list). -> ([(where, doc)], [problem])"""
    stripped = text.strip()
    if not stripped:
        return [], []
    if stripped[0] == "[" or (stripped[0] == "{" and "\n{" not in stripped):
        try:
            doc = json.loads(stripped)
            return [("%s" % source, d) for d in (doc if isinstance(doc, list) else [doc])], []
        except ValueError:
            pass  # fall through: maybe JSON lines after all
    out, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(("%s:%d" % (source, n), json.loads(line)))
        except ValueError as exc:
            problems.append("%s:%d: not valid JSON (%s)" % (source, n, exc))
    return out, problems


# ---- storage ------------------------------------------------------------------------------------------
class _Lock(object):
    """A lock directory next to the file: mkdir is atomic everywhere. A lock older than 30 s is left over and broken."""

    def __init__(self, path):
        self.path = path + ".lock"

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.stat(self.path).st_mtime > 30:
                        os.rmdir(self.path)
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise OSError("%s is locked by another writer" % self.path)
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            os.rmdir(self.path)
        except OSError:
            pass


def append_lines(path, docs, keep):
    """Append records under the lock; rewrite the file to its last `keep` lines once it holds twice that many."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    text = "".join(json.dumps(d, separators=(",", ":"), sort_keys=True) + "\n" for d in docs)
    with _Lock(path):
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        if os.path.getsize(path) > keep * 2 * 160:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            if len(lines) > keep * 2:
                _atomic(path, "\n".join(lines[-keep:]) + "\n")


def read_lines(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


class Records(object):
    def __init__(self, state_dir):
        self.root = state_dir

    def _p(self, host, name):
        return os.path.join(self.root, "hosts", host, name)

    def add(self, host, beats, events):
        if beats:
            append_lines(self._p(host, "beats.jsonl"), beats, BEATS_KEEP)
        if events:
            append_lines(self._p(host, "events.jsonl"), events, EVENTS_KEEP)

    def beats(self, host):
        return read_lines(self._p(host, "beats.jsonl"))

    def events(self, host):
        return read_lines(self._p(host, "events.jsonl"))

    def drain_request(self, host):
        return _read_json(self._p(host, "drain.json"))

    def set_drain_request(self, host, doc):
        p = self._p(host, "drain.json")
        if doc is None:
            try:
                os.unlink(p)
                return True
            except FileNotFoundError:
                return False
        _atomic(p, json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return True

    def transitions(self, host):
        return read_lines(self._p(host, "transitions.jsonl"))

    def add_transition(self, host, doc):
        append_lines(self._p(host, "transitions.jsonl"), [doc], TRANSITIONS_KEEP)

    def observer(self):
        return _read_json(os.path.join(self.root, "observer.json"))

    def set_observer(self, doc):
        _atomic(os.path.join(self.root, "observer.json"), json.dumps(doc, indent=1, sort_keys=True) + "\n")
