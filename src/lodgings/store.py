"""The state directory: what Lodgings remembers between runs. Lodgings writes only here.

    <state>/hosts/<name>/current.json    the latest good capabilities document
    <state>/hosts/<name>/status.json     the last attempt: when, whether it worked, why not
    <state>/hosts/<name>/history.jsonl   one line each time the fingerprint changed (the last 200 are kept)
    <state>/probes.jsonl                 one line per probe attempt (the last 2000 are kept)
    <state>/raw/<name>.json              the latest raw facts, for replay (only with --save-raw)
"""
import json
import os
import tempfile

HISTORY_KEEP = 200
LOG_KEEP = 2000


def _atomic(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _tail_lines(path, keep, extra):
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    lines.append(extra)
    return "\n".join(lines[-keep:]) + "\n"


class Store(object):
    def __init__(self, state_dir):
        self.root = state_dir

    def hdir(self, name):
        return os.path.join(self.root, "hosts", name)

    def current(self, name):
        return _read_json(os.path.join(self.hdir(name), "current.json"))

    def status(self, name):
        return _read_json(os.path.join(self.hdir(name), "status.json"))

    def history(self, name):
        out = []
        try:
            with open(os.path.join(self.hdir(name), "history.jsonl"), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return out

    def save_success(self, name, doc, previous, now):
        d = self.hdir(name)
        if doc["probe"].get("changed"):
            entry = dict(doc["drift"], host=name)
            h = os.path.join(d, "history.jsonl")
            _atomic(h, _tail_lines(h, HISTORY_KEEP, json.dumps(entry, separators=(",", ":"))))
        _atomic(os.path.join(d, "current.json"), json.dumps(doc, indent=1, sort_keys=True) + "\n")
        _atomic(os.path.join(d, "status.json"), json.dumps({"attempt_epoch": int(now), "ok": True, "error": None, "failures": 0}) + "\n")

    def save_failure(self, name, error, now):
        prior = self.status(name) or {}
        failures = (prior.get("failures") or 0) + 1 if prior.get("ok") is False else 1
        _atomic(os.path.join(self.hdir(name), "status.json"),
                json.dumps({"attempt_epoch": int(now), "ok": False, "error": error, "failures": failures}) + "\n")

    def save_raw(self, name, facts):
        _atomic(os.path.join(self.root, "raw", name + ".json"), json.dumps(facts, indent=1, sort_keys=True) + "\n")

    def log(self, entry):
        p = os.path.join(self.root, "probes.jsonl")
        _atomic(p, _tail_lines(p, LOG_KEEP, json.dumps(entry, separators=(",", ":"), sort_keys=True)))
