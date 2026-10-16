"""Evacuation offers: for a host that is draining (or lost, or asleep), the jobs that would requeue and the lanes that
could move with a lane passport, with the exact commands. An offer is text: Lodgings runs none of it (docs/drain.md).

Jobs are requeued by whoever submitted them (attempt n+1); lanes move only when a person or their supervisor decides,
through `lodgings-passport` (core/passport). Paths come from hosts.toml: `repos.<project>` is the clone on each host and
`confine` is where a passport and an imported worktree may be written there. A part Lodgings cannot know is shown as
<placeholder> and listed under `missing`, never guessed.
"""
import ntpath
import posixpath
import shlex
import subprocess

from . import avail


def _join(view, base, *parts):
    if base.startswith("<"):  # a placeholder stays readable as one
        return "/".join((base,) + parts)
    return (ntpath if view == "windows" else posixpath).join(base, *parts)


def _cmd(view, argv):
    if view == "windows":
        return subprocess.list2cmdline(argv)
    out = []
    for a in argv:
        if a.startswith("<") and a.endswith(">"):
            out.append(a)
        elif a.startswith("~/"):
            out.append("~/" + shlex.quote(a[2:]))  # keep the tilde outside the quotes so the shell still expands it
        else:
            out.append(shlex.quote(a))
    return " ".join(out)


def _passport_bin(view):
    # `py -3` runs a script by path (it is not searched for on PATH), so the Windows command names the file.
    return ["py", "-3", "<checkout>\\core\\passport\\bin\\lodgings-passport"] if view == "windows" else ["lodgings-passport"]


def lane_move(reg, lane, origin, target):
    """-> {target, steps: [{on, run | ship}], missing: [...]} for one lane from origin to target."""
    o, t = reg.hosts[origin], reg.hosts[target]
    missing = []
    project = lane.get("project")
    task = lane.get("task") or lane["lane"]
    name = "%s-%d" % (task, lane.get("seq") or 1)

    def need(value, label):
        if value:
            return value
        missing.append(label)
        return "<%s>" % label

    spec = need(lane.get("spec"), "lane spec of %s on %s" % (lane["lane"], origin))
    o_conf = need(o.get("confine"), "writable dir on %s (set confine)" % origin)
    t_conf = need(t.get("confine"), "writable dir on %s (set confine)" % target)
    t_repo = need(t["repos"].get(project) if project else None, "clone of %s on %s (set repos.%s)" % (project or "the project", target, project or "PROJECT"))
    o_repo = need(lane.get("repo") or (o["repos"].get(project) if project else None), "clone of %s on %s" % (project or "the project", origin))
    for host, h in ((origin, o), (target, t)):
        if h["view"] == "windows":
            missing.append("Lodgings source checkout on %s (the <checkout> in its passport commands)" % host)
    o_dir, t_dir = _join(o["view"], o_conf, "passports"), _join(t["view"], t_conf, "passports")
    o_pass, t_pass = _join(o["view"], o_dir, name), _join(t["view"], t_dir, name)
    t_tree = _join(t["view"], t_conf, "lanes", lane["lane"])
    steps = [
        {"on": origin, "run": _cmd(o["view"], _passport_bin(o["view"]) + ["export", "--lane", spec, "--out", o_dir, "--seq", str(lane.get("seq") or 1)])},
        {"on": origin, "ship": "copy %s to %s:%s (any way you like; the passport carries its own checksums)" % (o_pass, target, t_dir)},
        {"on": target, "run": _cmd(t["view"], _passport_bin(t["view"]) + ["verify", t_pass, "--repo", t_repo])},
        {"on": target, "run": _cmd(t["view"], _passport_bin(t["view"]) + ["import", t_pass, "--repo", t_repo, "--worktree", t_tree])},
        {"on": target, "ship": "copy %s back to %s:%s" % (_join(t["view"], t_pass, "after", "import-receipt.json"), origin, _join(o["view"], o_pass, "after"))},
        {"on": origin, "run": _cmd(o["view"], _passport_bin(o["view"]) + ["confirm", o_pass, "--repo", o_repo])},
    ]
    return {"target": target, "steps": steps, "missing": missing}


def offer(pool, verdicts, name, now):
    v = next(x for x in verdicts if x["host"] == name)
    state = v["state"]
    if state in ("LOST", "ASLEEP"):
        jobs_when = "now: the host is %s, so its jobs cannot finish there" % state.lower()
    elif state in ("DRAINING", "PAUSED", "STALE"):
        jobs_when = "only if you evacuate: a drain lets them finish in place"
    else:
        jobs_when = "only if you evacuate: the host is %s" % state.lower()
    jobs = []
    for j in v["running_jobs"]:
        targets = avail.eligible(pool, verdicts, name, j.get("requires") or (), j.get("class"))
        jobs.append({"job": j["job"], "class": j.get("class"), "attempt": j.get("attempt") or 1, "next_attempt": (j.get("attempt") or 1) + 1,
                     "running_for_s": round(now - j["started_epoch"], 1),
                     "targets": targets, "resubmit": _cmd(pool.reg.hosts[name]["view"], j["resubmit"]) if j.get("resubmit") else None})
    lanes = []
    for l in v["parked_lanes"]:
        targets = avail.eligible(pool, verdicts, name, l.get("requires") or ())
        lanes.append({"lane": l["lane"], "project": l.get("project"), "requires": l.get("requires") or [],
                      "moves": [lane_move(pool.reg, l, name, t) for t in targets]})
    return {"host": name, "state": state, "effect": v["effect"], "why": v["why"], "jobs_requeue": jobs_when, "jobs": jobs, "lanes": lanes,
            "executed": False}

