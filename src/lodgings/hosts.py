"""hosts.toml: load and validate the host registry (schema lodgings.hosts/1, see docs/hosts-toml.md)."""
import os
import re

from . import HOSTS_SCHEMA, catalogue, toml_lite

POOL_DEFAULTS = {"schema": HOSTS_SCHEMA, "state_dir": None, "probe_interval_h": 24, "drift_visible_h": 24, "stale_after_h": 48}
PROBE_DEFAULTS = {"timeout_s": 20, "deep": False, "tools": None, "tool": {}}
# Host states and drain inputs (docs/drain.md). Where these numbers come from is written down there.
DRAIN_DEFAULTS = {"beat_interval_s": 15, "late_after_beats": 2, "stale_after_s": 60, "lost_after_s": 900, "canary_failures": 2,
                  "fallback_share": 0.2, "fallback_window_s": 3600, "fallback_min_placements": 5, "on_battery": True,
                  "reboot_window_s": 3600, "reboot_unknown_due": "note"}
DRAIN_KINDS = {"beat_interval_s": "num", "late_after_beats": "num", "stale_after_s": "num", "lost_after_s": "num", "canary_failures": "int",
               "fallback_share": "num", "fallback_window_s": "num", "fallback_min_placements": "int", "on_battery": "bool",
               "reboot_window_s": "num", "reboot_unknown_due": "str"}
HOST_KEYS = {"label", "machine", "view", "ssh", "local", "fixture", "classes", "declared", "expect", "policy", "confine",
             "host_class", "deep", "note", "drain", "repos", "paused_marker"}
POOL_KEYS = set(POOL_DEFAULTS)
PROBE_KEYS = {"timeout_s", "deep", "tools", "tool"}


class Problem(object):
    def __init__(self, level, where, msg):
        self.level, self.where, self.msg = level, where, msg

    def __str__(self):
        return "%s: %s: %s" % (self.level, self.where, self.msg)


class Registry(object):
    def __init__(self, path, pool, probe, expect, hosts, drain=None):
        self.path, self.pool, self.probe, self.expect, self.hosts = path, pool, probe, expect, hosts
        self.drain = drain or dict(DRAIN_DEFAULTS)

    def thresholds(self, name):
        """The drain thresholds that apply to one host: [drain] with the host's own `drain = {...}` over it."""
        return dict(self.drain, **(self.hosts[name].get("drain") or {}))


def default_state_dir():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "lodgings")
    if os.uname().sysname == "Darwin":
        return os.path.expanduser("~/Library/Application Support/lodgings")
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "lodgings")


def _expand(base, path):
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def _typed(problems, where, val, kind, label):
    ok = {"str": isinstance(val, str), "int": isinstance(val, int) and not isinstance(val, bool),
          "num": isinstance(val, (int, float)) and not isinstance(val, bool), "bool": isinstance(val, bool),
          "table": isinstance(val, dict), "strs": isinstance(val, list) and all(isinstance(x, str) for x in val)}[kind]
    if not ok:
        problems.append(Problem("error", where, "%s must be %s" % (label, {"strs": "a list of strings", "table": "a table"}.get(kind, "a " + kind))))
    return ok


def _unknown(problems, where, table, allowed):
    for k in table:
        if k not in allowed:
            hint = ""
            close = [a for a in sorted(allowed) if a[:3] == k[:3]]
            if close:
                hint = " (did you mean %s?)" % close[0]
            problems.append(Problem("warning", where, "unknown key %r%s" % (k, hint)))


def load(path, force_lite=False):
    """-> (Registry or None, [Problem]). A registry comes back whenever the file could be read, even with errors."""
    problems = []
    try:
        with open(path, encoding="utf-8") as fh:
            doc = toml_lite.loads(fh.read(), force_lite=force_lite)
    except OSError as exc:
        return None, [Problem("error", str(path), "cannot read: %s" % exc)]
    except toml_lite.TomlError as exc:
        return None, [Problem("error", str(path), "not valid TOML: %s" % exc)]
    base = os.path.dirname(os.path.abspath(path))

    _unknown(problems, "top level", doc, {"pool", "probe", "expect", "drain", "host"})

    pool = dict(POOL_DEFAULTS)
    raw = doc.get("pool") or {}
    if _typed(problems, "[pool]", raw, "table", "[pool]"):
        _unknown(problems, "[pool]", raw, POOL_KEYS)
        for k, kind in (("schema", "str"), ("state_dir", "str"), ("probe_interval_h", "num"), ("drift_visible_h", "num"), ("stale_after_h", "num")):
            if k in raw and _typed(problems, "[pool]", raw[k], kind, k):
                pool[k] = raw[k]
        if pool["schema"] != HOSTS_SCHEMA:
            problems.append(Problem("error", "[pool]", "schema %r is not supported (this reader handles %s)" % (pool["schema"], HOSTS_SCHEMA)))
        for k in ("probe_interval_h", "drift_visible_h", "stale_after_h"):
            if pool[k] <= 0:
                problems.append(Problem("error", "[pool]", "%s must be greater than 0" % k))
    pool["state_dir"] = _expand(base, pool["state_dir"]) if pool["state_dir"] else default_state_dir()

    probe = dict(PROBE_DEFAULTS)
    raw = doc.get("probe") or {}
    if _typed(problems, "[probe]", raw, "table", "[probe]"):
        _unknown(problems, "[probe]", raw, PROBE_KEYS)
        for k, kind in (("timeout_s", "num"), ("deep", "bool"), ("tools", "strs")):
            if k in raw and _typed(problems, "[probe]", raw[k], kind, k):
                probe[k] = raw[k]
        if probe["timeout_s"] <= 0 or probe["timeout_s"] > 300:
            problems.append(Problem("error", "[probe]", "timeout_s must be between 0 and 300"))
        for t in probe["tools"] or []:
            if t not in catalogue.TOOLS and t not in (raw.get("tool") or {}):
                problems.append(Problem("error", "[probe]", "tools lists %r, which is neither built in nor defined in [probe.tool.%s]" % (t, t)))
        tools = {}
        for name, spec in (raw.get("tool") or {}).items():
            where = "[probe.tool.%s]" % name
            if not catalogue.NAME_RE.match(name):
                problems.append(Problem("error", where, "tool names are lower-case letters, digits, . _ -"))
            if not _typed(problems, where, spec, "table", "the tool entry"):
                continue
            _unknown(problems, where, spec, {"argv", "pattern"})
            argv = spec.get("argv")
            if not (isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv)):
                problems.append(Problem("error", where, "argv must be a non-empty list of strings (an argument list, never a shell string)"))
                continue
            if any(c in argv[0] for c in "/\\ \t"):
                problems.append(Problem("error", where, "argv[0] must be a bare command name found on PATH, not a path"))
                continue
            pat = spec.get("pattern")
            if pat is not None:
                try:
                    if re.compile(pat).groups < 1:
                        problems.append(Problem("error", where, "pattern needs one capture group around the version"))
                        continue
                except re.error as exc:
                    problems.append(Problem("error", where, "pattern is not a valid regular expression: %s" % exc))
                    continue
            tools[name] = {"argv": argv, "pattern": pat}
        probe["tool"] = tools

    expect = {}
    raw = doc.get("expect") or {}
    if _typed(problems, "[expect]", raw, "table", "[expect]"):
        for k, v in raw.items():
            if isinstance(v, str) and v:
                expect[k] = v
            else:
                problems.append(Problem("error", "[expect]", "%s must be a version prefix such as \"2.10\", or \"*\" for present" % k))

    drain = dict(DRAIN_DEFAULTS)
    raw = doc.get("drain") or {}
    if _typed(problems, "[drain]", raw, "table", "[drain]"):
        drain.update(_drain_table(problems, "[drain]", raw))

    hosts = {}
    rawhosts = doc.get("host") or {}
    if not rawhosts:
        problems.append(Problem("error", "[host.*]", "no hosts are defined"))
    if _typed(problems, "[host.*]", rawhosts, "table", "[host]"):
        for name, h in rawhosts.items():
            where = "[host.%s]" % name
            if not catalogue.NAME_RE.match(name):
                problems.append(Problem("error", where, "host names are lower-case letters, digits, . _ - (up to 48)"))
            if not _typed(problems, where, h, "table", "the host entry"):
                continue
            _unknown(problems, where, h, HOST_KEYS)
            rec = {"name": name, "label": h.get("label") or name, "machine": h.get("machine") or name, "view": h.get("view"),
                   "ssh": None, "local": False, "fixture": None, "classes": {}, "declared": [], "expect": {}, "policy": {},
                   "confine": h.get("confine"), "host_class": h.get("host_class"), "deep": h.get("deep"), "note": h.get("note"),
                   "drain": {}, "repos": {}, "paused_marker": h.get("paused_marker")}
            for k in ("label", "machine", "confine", "host_class", "note", "paused_marker"):
                if k in h:
                    _typed(problems, where, h[k], "str", k)
            if rec["view"] not in catalogue.VIEWS:
                problems.append(Problem("error", where, "view must be one of %s" % ", ".join(catalogue.VIEWS)))
            ways = [k for k in ("ssh", "local", "fixture") if h.get(k)]
            if len(ways) != 1:
                problems.append(Problem("error", where, "give exactly one of ssh = \"alias\", local = true, fixture = \"file\"" + (" (found %s)" % ", ".join(ways) if ways else "")))
            if h.get("ssh"):
                if _typed(problems, where, h["ssh"], "str", "ssh"):
                    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$", h["ssh"]):
                        problems.append(Problem("error", where, "ssh must be an alias or user@host without spaces or options"))
                    else:
                        rec["ssh"] = h["ssh"]
            if "local" in h and _typed(problems, where, h["local"], "bool", "local"):
                rec["local"] = h["local"]
            if h.get("fixture") and _typed(problems, where, h["fixture"], "str", "fixture"):
                rec["fixture"] = _expand(base, h["fixture"])
            if "deep" in h:
                _typed(problems, where, h["deep"], "bool", "deep")
            if "classes" in h and _typed(problems, where, h["classes"], "table", "classes"):
                for c, n in h["classes"].items():
                    if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
                        rec["classes"][c] = n
                    else:
                        problems.append(Problem("error", where, "classes.%s must be a whole number of slots (0 or more)" % c))
            if "declared" in h and _typed(problems, where, h["declared"], "strs", "declared"):
                for t in h["declared"]:
                    if catalogue.TAG_RE.match(t):
                        rec["declared"].append(t)
                    else:
                        problems.append(Problem("error", where, "declared tag %r has characters outside letters, digits and . _ : + -" % t))
            if "expect" in h and _typed(problems, where, h["expect"], "table", "expect"):
                for k, v in h["expect"].items():
                    if isinstance(v, str) and v:
                        rec["expect"][k] = v
                    else:
                        problems.append(Problem("error", where, "expect.%s must be a version prefix or \"*\"" % k))
            if "policy" in h and _typed(problems, where, h["policy"], "table", "policy"):
                rec["policy"] = h["policy"]
            if "drain" in h and _typed(problems, where, h["drain"], "table", "drain"):
                rec["drain"] = _drain_table(problems, where + " drain", h["drain"])
            if "repos" in h and _typed(problems, where, h["repos"], "table", "repos"):
                for proj, clone in h["repos"].items():
                    if isinstance(clone, str) and clone:
                        rec["repos"][proj] = clone  # a path on that host, kept as written (never expanded here)
                    else:
                        problems.append(Problem("error", where, "repos.%s must be the path of a clone on that host" % proj))
            if rec["fixture"] and not os.path.exists(rec["fixture"]):
                problems.append(Problem("error", where, "fixture file %s does not exist" % rec["fixture"]))
            hosts[name] = rec
    for name in [None] + sorted(hosts):
        t = dict(drain, **(hosts[name]["drain"] if name else {}))
        late = t["late_after_beats"] * t["beat_interval_s"]
        if not late < t["stale_after_s"] < t["lost_after_s"]:
            problems.append(Problem("error", "[host.%s] drain" % name if name else "[drain]",
                                    "the ages must grow: late_after_beats x beat_interval_s (%gs) < stale_after_s (%gs) < lost_after_s (%gs)"
                                    % (late, t["stale_after_s"], t["lost_after_s"])))
    return Registry(os.path.abspath(path), pool, probe, expect, hosts, drain), problems


def _drain_table(problems, where, raw):
    """Validate a [drain] table (pool-wide, or one host's `drain = {...}`). -> the valid entries."""
    _unknown(problems, where, raw, set(DRAIN_KINDS))
    out = {}
    for k, kind in DRAIN_KINDS.items():
        if k not in raw or not _typed(problems, where, raw[k], kind, k):
            continue
        v = raw[k]
        if k == "fallback_share" and not 0 < v <= 1:
            problems.append(Problem("error", where, "fallback_share must be a share between 0 and 1, such as 0.2"))
        elif k == "reboot_unknown_due" and v not in ("note", "drain"):
            problems.append(Problem("error", where, "reboot_unknown_due must be \"note\" or \"drain\""))
        elif kind in ("num", "int") and v <= 0:
            problems.append(Problem("error", where, "%s must be greater than 0" % k))
        else:
            out[k] = v
    return out


def errors(problems):
    return [p for p in problems if p.level == "error"]
