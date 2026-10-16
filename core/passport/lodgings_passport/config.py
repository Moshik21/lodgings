"""Configuration: built-in defaults, an optional JSON file, then --set overrides."""

import copy
import json
import os
import platform
import sys

from .util import UsageError

DEFAULTS = {
    # Where the actions log and the kill switch live. Empty means the platform default.
    "state_dir": "",
    # Name this host gives itself in receipts. Empty means the system host name.
    "host_label": "",
    # Ref namespaces. The bundle stores refs under bundle_namespace; the target fetches
    # them under import_prefix/<task>/<seq>/; the origin archives under archive_prefix.
    "bundle_namespace": "refs/passport",
    "import_prefix": "refs/lodgings/import",
    "archive_prefix": "refs/lodgings/archive",
    # Extra ref globs (git for-each-ref patterns) carried as archive refs for every lane.
    # "{task}" and "{branch}" are substituted.
    "archive_ref_patterns": ["refs/lodgings/archive/{task}/"],
    # Pipeline step states that mean "a step is executing now": a lane in one of them
    # is refused unless the receipt says the run is parked waiting for the agent.
    "busy_step_states": ["running", "fixing"],
    # Records: largest single record, and the passport size that earns a warning.
    "max_record_bytes": 1048576,
    "warn_passport_bytes": 5242880,
    # Timeouts in seconds.
    "git_timeout_s": 300,
    "check_timeout_s": 30,
    # Identity for the bridge commit written on the target (never on the origin).
    "bridge_name": "lodgings passport",
    "bridge_email": "passport@lodgings.invalid",
    # Read-only custody command run in the new worktree after import, as argv.
    # Empty list means none. The result is recorded as evidence, never decisive.
    "import_custody_check": [],
    # Secret patterns (regular expressions). A record that matches is refused.
    "secret_patterns": [
        "-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "\\bgh[pousr]_[A-Za-z0-9]{30,}",
        "\\bgithub_pat_[A-Za-z0-9_]{30,}",
        "\\bsk-[A-Za-z0-9_-]{30,}",
        "\\bAKIA[0-9A-Z]{16}\\b",
        "\\bxox[abposr]-[A-Za-z0-9-]{10,}",
        "(?i)\\b(password|passwd|secret|api_key|apikey|token)\\s*[:=]\\s*['\"]?[A-Za-z0-9/+_.-]{16,}",
    ],
}


def default_state_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "lodgings", "passport")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/lodgings/passport")
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "lodgings", "passport")


def _coerce(name, raw, current):
    if isinstance(current, bool):
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise UsageError("--set %s expects true or false, got %r" % (name, raw))
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError:
            raise UsageError("--set %s expects an integer, got %r" % (name, raw))
    if isinstance(current, list):
        try:
            value = json.loads(raw)
        except ValueError:
            raise UsageError("--set %s expects a JSON list, got %r" % (name, raw))
        if not isinstance(value, list):
            raise UsageError("--set %s expects a JSON list, got %r" % (name, raw))
        return value
    return raw


def load(path=None, overrides=None, state_dir=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise UsageError("config file not found: %s" % path)
        except ValueError as exc:
            raise UsageError("config file is not valid JSON: %s (%s)" % (path, exc))
        if not isinstance(data, dict):
            raise UsageError("config file must hold a JSON object: %s" % path)
        for key, value in data.items():
            if key.startswith("_"):
                continue  # comments
            if key not in DEFAULTS:
                raise UsageError("unknown config key %r in %s" % (key, path))
            cfg[key] = value
    for item in overrides or []:
        if "=" not in item:
            raise UsageError("--set expects name=value, got %r" % item)
        name, raw = item.split("=", 1)
        if name not in DEFAULTS:
            raise UsageError("unknown config key %r (see docs/config.md)" % name)
        cfg[name] = _coerce(name, raw, DEFAULTS[name])
    if state_dir:
        cfg["state_dir"] = state_dir
    if not cfg["state_dir"]:
        cfg["state_dir"] = os.environ.get("LODGINGS_PASSPORT_STATE") or default_state_dir()
    if not cfg["host_label"]:
        cfg["host_label"] = platform.node() or "unknown-host"
    return cfg
