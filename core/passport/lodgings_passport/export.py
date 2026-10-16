"""export: write a sealed passport for one parked lane. Reads the origin; writes only the passport.

Nothing in the origin repository, its work tree or its review gate is written:
objects are read through a scratch repository that borrows them (alternates).
"""

import json
import os
import re
import shutil

from . import PASSPORT_SCHEMA, VERSION
from . import custody, gitio, seal
from .util import (EnvError, Refusal, UsageError, read_json, short, utc_now, write_json)

PIPELINE_SCHEMA = "lodgings.pipeline/1"


def _bundle_name(namespace, kind, refname=None):
    if kind == "archive":
        tail = refname[len("refs/"):] if refname.startswith("refs/") else refname
        return "%s/archive/%s" % (namespace, tail)
    return "%s/%s/head" % (namespace, kind)


def _next_seq(out_dir, task):
    best = 0
    if os.path.isdir(out_dir):
        rx = re.compile(r"^%s-(\d+)$" % re.escape(task))
        for name in os.listdir(out_dir):
            m = rx.match(name)
            if m:
                best = max(best, int(m.group(1)))
    return best + 1


def _load_pipeline(spec, cfg):
    path = spec.get("pipeline")
    if not path:
        return {"schema": PIPELINE_SCHEMA, "source": "none", "note": "no pipeline receipt was given"}
    receipt = read_json(os.path.expanduser(path), "pipeline receipt")
    if not isinstance(receipt, dict):
        raise UsageError("pipeline receipt must be a JSON object: %s" % path)
    receipt.setdefault("schema", PIPELINE_SCHEMA)
    run = receipt.get("run") or {}
    busy = [s for s in receipt.get("steps") or [] if s.get("status") in cfg["busy_step_states"]]
    if busy and not run.get("parked"):
        step = busy[0]
        raise Refusal(
            "pipeline step %r is %s right now: a lane moves only at a checkpoint" % (step.get("name"), step.get("status")),
            hint="let the step reach its gate (or a terminal state) and export again; the passport never aborts a run",
            evidence={"run": run.get("id"), "step": step.get("name"), "status": step.get("status")})
    return receipt


def _check_worktree(spec, git_lane, lane_head):
    path = spec.get("worktree")
    if not path:
        return {"checked": False, "why": "no worktree named; the lane branch is read from the repo"}
    named = path
    if not os.path.isdir(path):
        # A lane branch need not have a worktree (it may have been removed after the lane parked). If the branch is
        # checked out somewhere else, check that one; if nowhere, the branch is read from the repo as it is.
        path = git_lane.worktree_of("refs/heads/" + spec["branch"])
        if not path:
            return {"checked": False, "named": named,
                    "why": "worktree %s does not exist and the branch is checked out nowhere; the lane branch is "
                           "read from the repo" % named}
    wt = gitio.Git(path, timeout=git_lane.timeout)
    dirty = wt.status_porcelain()
    if dirty.strip():
        count = len(dirty.strip().splitlines())
        raise Refusal("worktree %s has %d uncommitted path(s): park the lane first" % (path, count),
                      hint="commit work in progress as a normal commit (never stash), then export again",
                      evidence={"paths": dirty.strip().splitlines()[:20]})
    head = wt.head()
    if head != lane_head:
        raise Refusal("worktree HEAD %s is not the lane branch head %s" % (short(head), short(lane_head)),
                      hint="check out the lane branch in its worktree, or name the right branch")
    found = {"checked": True, "path": path, "clean": True, "head": head, "symbolic": wt.symbolic_head()}
    if path != named:
        found["named"] = named
    return found


def _adopt(sg, spec, relation, lane_head, gate_head):
    """Decide `--adopt-gate-head`. Returns the adoption facts to record, or None when nothing is adopted.

    Equal trees need no confirmation. Any difference is listed in a refusal, and is adopted only when the caller
    passes the token that refusal printed (it is bound to these exact heads and this exact difference).
    """
    if not spec.get("adopt_gate_head"):
        return None
    if not gate_head:
        raise Refusal("--adopt-gate-head: the review gate holds no head for this lane, so there is nothing to adopt",
                      hint="export without --adopt-gate-head; nothing was changed")
    if lane_head == gate_head:
        return None
    facts = custody.adoption(sg, lane_head, gate_head)
    confirmed = facts["tree_equal"]
    if not confirmed:
        if spec.get("confirm_differences") != facts["token"]:
            diff, lane_only = facts["tree_differences"]["count"], facts["lane_only_commits"]["count"]
            stale = spec.get("confirm_differences")
            raise Refusal(
                "adopt-gate-head: the gate head's tree differs from the lane head's in %d path(s) and the lane holds "
                "%d commit(s) the gate lacks%s" % (diff, lane_only,
                                                  "; the --confirm-differences token is for another head or "
                                                  "difference" if stale else ""),
                hint="\n".join(["nothing was changed."] + custody.adoption_lines(facts, relation) +
                               ["to adopt the gate head with exactly these differences, run the same command again "
                                "with: --adopt-gate-head --confirm-differences %s" % facts["token"]]),
                evidence=dict(facts, relation=relation["relation"], ahead=relation["ahead"],
                              gate_holds_lane_changes=relation["gate_holds_lane_changes"]))
    return dict(facts, confirmed=True)


def export(spec, out_dir, cfg, seq=None, log=None):
    gitio.require_git()
    timeout = cfg["git_timeout_s"]
    lane_git = gitio.Git(spec["repo"], timeout=timeout)
    lane_ref = "refs/heads/" + spec["branch"]
    lane_head = lane_git.rev_parse(lane_ref)
    if not lane_head:
        raise UsageError("lane branch %s not found in %s" % (spec["branch"], spec["repo"]))

    worktree = _check_worktree(spec, lane_git, lane_head)
    pipeline = _load_pipeline(spec, cfg)

    # Refs to carry. Archive patterns may name {task} and {branch}.
    patterns = []
    for p in list(spec.get("archive_refs") or []) + list(cfg["archive_ref_patterns"]):
        p = p.replace("{task}", spec["task"]).replace("{branch}", spec["branch"])
        if p not in patterns:
            patterns.append(p)
    archive = [(name, sha) for name, sha in lane_git.refs(patterns) if name != lane_ref]

    gate = spec.get("gate") or {}
    gate_head = None
    object_dirs = [lane_git.objects_dir()]
    if gate:
        if not os.path.isdir(gate["git_dir"]):
            raise EnvError("gate repository not found: %s" % gate["git_dir"])
        gate_git = gitio.Git(gate["git_dir"], bare=True, timeout=timeout)
        gate_head = gate_git.rev_parse(gate["ref"])
        object_dirs.append(gate_git.objects_dir())

    pr = spec.get("pr") or {}
    namespace = cfg["bundle_namespace"]

    with gitio.Scratch(object_dirs, timeout=timeout) as scratch:
        sg = scratch.git
        base_point = None
        if spec.get("base"):
            base_sha = lane_git.rev_parse(spec["base"])
            if not base_sha:
                raise UsageError("base %r not found in %s" % (spec["base"], spec["repo"]))
            base_point = sg.merge_base(base_sha, lane_head)

        heads = [("lane", lane_ref, lane_head)]
        if gate_head:
            heads.append(("gate", gate["ref"], gate_head))
        if pr.get("head"):
            if sg.has_commit(pr["head"]):
                heads.append(("pr", "pr", pr["head"]))
            else:
                pr = dict(pr, bundled=False, note="PR head not present on this host; recorded, not bundled")
        for name, sha in archive:
            heads.append(("archive", name, sha))

        relation = custody.relate(sg, lane_head, gate_head)
        adopt = _adopt(sg, spec, relation, lane_head, gate_head)
        if adopt:
            relation = dict(relation, needs_bridge=False)  # the lane resumes at the gate head: no bridge merge
        elif not relation["safe"]:
            raise Refusal("custody %s: %s" % (relation["relation"], relation["rule"]),
                          hint=custody.hint_for(relation), evidence=relation)

        # A pipeline may hold a newer head of its own (a fix round committed in its run
        # worktree, not yet pushed anywhere). The lane must already contain it.
        pipeline_head = gate.get("pipeline_head") or None
        pipeline_relation = None
        if pipeline_head:
            if not sg.has_commit(pipeline_head):
                raise Refusal("pipeline head %s is not readable from the lane or gate repository" % short(pipeline_head),
                              hint="custody unknown, so the lane does not move; let the run push or finish first")
            pipeline_relation = custody.relate(sg, gate_head if adopt else lane_head, pipeline_head)
            if not pipeline_relation["safe"]:
                raise Refusal("the pipeline holds commits the %s lacks (pipeline head %s is %s)"
                              % ("adopted gate head" if adopt else "lane", short(pipeline_head),
                                 pipeline_relation["relation"]),
                              hint="let the run push its fix or return custody to the lane, then export again",
                              evidence=pipeline_relation)
            if pipeline_head not in (lane_head, gate_head):
                heads.append(("pipeline", "pipeline", pipeline_head))

        refs = []
        for kind, source, sha in heads:
            bundled_as = _bundle_name(namespace, kind, source)
            in_base = bool(base_point) and sg.is_ancestor(sha, base_point)
            refs.append({"kind": kind, "source": source, "sha": sha, "bundle_ref": bundled_as,
                         "in_base": in_base})
            if not in_base:
                sg.create_ref(bundled_as, sha, "lodgings passport export")

        task = spec["task"]
        if seq is None:
            seq = _next_seq(out_dir, task)
        name = "%s-%d" % (task, seq)
        root = os.path.join(out_dir, name)
        if os.path.exists(root):
            raise Refusal("passport %s already exists" % root, hint="pick another --seq or --out")
        ext = custody.external_check((spec.get("custody_check") or {}).get("argv") or [],
                                     (spec.get("custody_check") or {}).get("cwd")
                                     or (worktree["path"] if worktree.get("checked") else spec["repo"]),
                                     cfg["check_timeout_s"])

        os.makedirs(os.path.join(root, "records"))
        try:
            pid = _write(root, sg, spec, cfg, refs, base_point, relation, gate, gate_head, pr, worktree,
                         pipeline, ext, seq, lane_head, pipeline_relation, adopt)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)  # never leave a half-written passport behind
            raise
    return _summary(root, cfg, spec["task"], seq, pid, lane_head, gate_head, relation, refs, base_point, pipeline, adopt,
                    bool(spec.get("adopt_gate_head")))


def _write(root, sg, spec, cfg, refs, base_point, relation, gate, gate_head, pr, worktree, pipeline, ext, seq,
           lane_head, pipeline_relation, adopt=None):
    """Fill the passport directory and seal it. Returns the passport id."""
    task = spec["task"]
    bundle_path = os.path.join(root, "refs.bundle")
    to_bundle = [r["bundle_ref"] for r in refs if not r["in_base"]]
    if to_bundle:
        args = ["bundle", "create", "--quiet", bundle_path] + to_bundle
        if base_point:
            args += ["--not", base_point]
        sg.ok(args, "bundle create")
        sg.ok(["bundle", "verify", "--quiet", bundle_path], "bundle verify")
    else:
        # Every ref is already in the base: the bundle is empty by construction.
        open(bundle_path, "wb").close()

    records = []
    for rec_name, path in sorted((spec.get("records") or {}).items()):
        records.append(seal.copy_record(os.path.expanduser(path), os.path.join(root, "records"), rec_name, cfg))

    decisions = {"schema": "lodgings.decisions/1", "items": []}
    if spec.get("decisions"):
        loaded = read_json(os.path.expanduser(spec["decisions"]), "decisions")
        decisions = loaded if isinstance(loaded, dict) else {"schema": "lodgings.decisions/1", "items": loaded}
    text_blob = json.dumps([decisions, pipeline, ext], indent=1, sort_keys=True)
    hits = seal.scan_secrets(text_blob, cfg["secret_patterns"])
    if hits:
        raise Refusal("pipeline receipt, decisions or custody output look like they hold a secret",
                      hint="secrets never travel in a passport; clean the receipt and export again")

    custody_doc = {
        "schema": "lodgings.custody/1",
        "branch": spec["branch"],
        "relation": relation,
        "gate": {"ref": gate.get("ref"), "head": gate_head, "present": bool(gate)},
        "pipeline_head": {"head": gate.get("pipeline_head") or None, "relation": pipeline_relation},
        "pr": pr,
        "worktree": worktree,
        "external_check": ext,
    }
    if adopt:
        custody_doc["adopt"] = adopt
    manifest = {
        "schema": PASSPORT_SCHEMA,
        "tool": "lodgings-passport",
        "tool_version": VERSION,
        "task": task,
        "seq": seq,
        "project": spec.get("project"),
        "branch": spec["branch"],
        "created_at": utc_now(),
        "origin": dict(spec.get("origin") or {}, host=(spec.get("origin") or {}).get("host") or cfg["host_label"]),
        "base": {"ref": spec.get("base") or None, "prerequisite": base_point},
        "refs": refs,
        "lane_head": lane_head,
        "lane_tree": relation["lane_tree"],
        "gate_head": gate_head,
        "custody_relation": relation["relation"],
        "needs_bridge": relation["needs_bridge"],
        "records": records,
        "pipeline_source": pipeline.get("source"),
    }
    if adopt:
        # The lane resumes at the gate head. The lists live in custody.json; import recomputes them from the
        # objects and refuses unless they match this digest.
        manifest["adopt"] = {
            "mode": adopt["mode"], "lane_head": adopt["lane_head"], "gate_head": adopt["gate_head"],
            "lane_tree": adopt["lane_tree"], "gate_tree": adopt["gate_tree"], "tree_equal": adopt["tree_equal"],
            "lane_only_commits": adopt["lane_only_commits"]["count"],
            "gate_only_commits": adopt["gate_only_commits"]["count"],
            "tree_differences": adopt["tree_differences"]["count"],
            "digest": adopt["digest"], "confirmed": adopt["confirmed"],
        }
    write_json(os.path.join(root, "manifest.json"), manifest)
    write_json(os.path.join(root, "custody.json"), custody_doc)
    write_json(os.path.join(root, "pipeline.json"), pipeline)
    write_json(os.path.join(root, "decisions.json"), decisions)
    return seal.seal(root)


def _summary(root, cfg, task, seq, pid, lane_head, gate_head, relation, refs, base_point, pipeline, adopt=None, adopt_asked=False):
    records = read_json(os.path.join(root, "manifest.json"), "manifest")["records"]
    size = 0
    for dirpath, _, files in os.walk(root):
        for f in files:
            size += os.path.getsize(os.path.join(dirpath, f))
    warnings = []
    if size > cfg["warn_passport_bytes"]:
        warnings.append("passport is %d bytes, over warn_passport_bytes %d" % (size, cfg["warn_passport_bytes"]))
    if not base_point:
        warnings.append("no base given: the bundle carries the full history")
    if relation["needs_bridge"]:
        warnings.append("custody 'contained': the target will add a bridge merge so the gate fast-forwards")
    if adopt:
        diff = adopt["tree_differences"]["count"]
        warnings.append("adopting gate head %s: the lane resumes there, and its %d own commit(s) are carried and "
                        "archived, not resumed; %s"
                        % (short(gate_head), adopt["lane_only_commits"]["count"],
                           "%d path(s) differ and were confirmed" % diff if diff else "the trees are identical"))
    elif adopt_asked:
        warnings.append("--adopt-gate-head was not needed: the lane head equals the gate head")
    return {
        "verdict": "exported",
        "task": task,
        "seq": seq,
        "passport": root,
        "passport_id": pid,
        "bytes": size,
        "lane_head": lane_head,
        "gate_head": gate_head,
        "custody": relation["relation"],
        "adopted": ({"mode": adopt["mode"], "resume_head": gate_head, "tree_equal": adopt["tree_equal"],
                     "lane_only_commits": adopt["lane_only_commits"]["count"],
                     "tree_differences": adopt["tree_differences"]["count"]} if adopt else None),
        "refs": len(refs),
        "records": [r["name"] for r in records],
        "pipeline": pipeline.get("source"),
        "warnings": warnings,
        "origin_written": False,
    }
