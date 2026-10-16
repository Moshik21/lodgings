"""import: take a verified passport into a target repository and open a fresh worktree.

Every decision is made first in a scratch repository (it borrows the target's
objects and holds the bundle and any bridge commit). Only when all checks pass
does the target get written, and then only additively:

  * refs under <import_prefix>/<task>/<seq>/ (non-forcing fetch; a differing ref refuses)
  * the lane branch, created only if absent (an equal branch is reused, a different one refuses)
  * one new worktree at a path that must not exist yet
  * receipts under the passport's after/ directory
"""

import calendar
import os
import time

from . import IMPORT_SCHEMA
from . import custody, gitio, seal, verify
from .util import (Invalid, Refusal, UsageError, printable, read_json, sha256_file, short, utc_now, write_json,
                   write_text)


def _epoch(stamp):
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        raise Invalid("manifest created_at %r is not a UTC timestamp" % (stamp,))


def _tail(bundle_ref, namespace):
    prefix = namespace.rstrip("/") + "/"
    if not bundle_ref.startswith(prefix):
        raise Invalid("bundle ref %s is outside the namespace %s" % (bundle_ref, namespace))
    return bundle_ref[len(prefix):]


def _bridge(sg, manifest, lane, gate, cfg, pid):
    stamp = "@%d +0000" % _epoch(manifest["created_at"])
    env = {
        "GIT_AUTHOR_NAME": cfg["bridge_name"], "GIT_AUTHOR_EMAIL": cfg["bridge_email"],
        "GIT_COMMITTER_NAME": cfg["bridge_name"], "GIT_COMMITTER_EMAIL": cfg["bridge_email"],
        "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
    }
    message = (
        "lodgings passport: bridge gate head %s into lane head %s\n\n"
        "The tree is the lane head's tree, unchanged. Merging the gate head into the lane\n"
        "changes nothing (git merge-tree is clean and yields this tree), so this merge only\n"
        "lets the review gate fast-forward. Made on import of passport %s-%s (%s).\n"
        % (short(gate), short(lane), manifest["task"], manifest["seq"], pid))
    out = sg.ok(["commit-tree", manifest["lane_tree"], "-p", lane, "-p", gate, "-F", "-"],
                "commit-tree (bridge)", env=env, input_text=message)
    return out.strip()


def _resume_md(manifest, receipt, pipeline, decisions, custody_doc):
    lines = []
    lines.append("# Resumed from passport %s-%s" % (manifest["task"], manifest["seq"]))
    lines.append("")
    lines.append("- Passport id: `%s`" % receipt["passport_id"])
    lines.append("- Origin host: %s; exported %s" % ((manifest.get("origin") or {}).get("host"), manifest["created_at"]))
    lines.append("- Branch `%s` at `%s` in worktree `%s`" % (receipt["branch"], receipt["resume_head"], receipt["worktree"]))
    adopt = manifest.get("adopt") or {}
    lines.append("- Lane head `%s`, tree `%s` (%s)" % (
        manifest["lane_head"], manifest["lane_tree"],
        "the worktree has the gate head's tree instead" if adopt and not adopt.get("tree_equal")
        else "the worktree tree matches it"))
    rel = manifest["custody_relation"]
    lines.append("- Custody: **%s**: %s" % (rel, custody_doc["relation"]["rule"]))
    adopted = receipt.get("adopted")
    if adopted:
        lines.append("- **Adopted the gate head** `%s`: the lane resumes there, not at its own head `%s`. %s The lane's "
                     "own %d commit(s) are kept under the import refs and archived by confirm; they are not in this "
                     "worktree's history." % (
                         adopted["gate_head"], adopted["lane_head"],
                         "The trees are identical." if adopted["tree_equal"] else
                         "%d path(s) differ between the lane head and the gate head (confirmed at export); this "
                         "worktree has the gate head's content." % adopted["tree_differences"],
                         adopted["lane_only_commits"]))
    if receipt.get("bridge"):
        lines.append("- Bridge merge `%s` (parents: lane head, gate head; tree unchanged) lets the gate fast-forward"
                     % receipt["bridge"])
    lines.append("")
    lines.append("## Pipeline as it stood at export (`%s`)" % pipeline.get("source"))
    if pipeline.get("note"):
        lines.append("")
        lines.append(pipeline["note"])
    run = pipeline.get("run") or {}
    if run:
        lines.append("")
        lines.append("Run `%s`: %s%s; head `%s`" % (run.get("id"), run.get("status"),
                                                   " (parked, waiting for the agent)" if run.get("parked") else "",
                                                   short(run.get("head"))))
    steps = pipeline.get("steps") or []
    if steps:
        lines.append("")
        lines.append("| Step | Status | Rounds | Findings |")
        lines.append("| --- | --- | ---: | ---: |")
        for s in steps:
            lines.append("| %s | %s | %d | %d |" % (s.get("name"), s.get("status"), len(s.get("rounds") or []),
                                                 len(s.get("findings") or [])))
    open_findings = pipeline.get("open_findings") or []
    if open_findings:
        lines.append("")
        lines.append("Open findings the fix agent already knows about:")
        for f in open_findings:
            lines.append("- `%s` (%s): %s" % (f.get("id"), f.get("severity") or "?", (f.get("title") or f.get("summary") or "")[:300]))
    items = decisions.get("items") or []
    if items:
        lines.append("")
        lines.append("## Decisions carried")
        for d in items:
            lines.append("- %s: %s" % (d.get("key") or d.get("id") or "decision", (d.get("answer") or d.get("summary") or "")[:300]))
    lines.append("")
    lines.append("## Next")
    lines.append("")
    lines.append("1. Start a fresh pipeline run from this head; the old run's live state stayed on the origin.")
    lines.append("2. When it reaches its first step, run `lodgings-passport confirm` on the origin with "
                 "`after/import-receipt.json`. Until then the origin keeps the lane untouched.")
    return "\n".join(lines) + "\n"


def do_import(root, repo, worktree, cfg, branch=None, gate_git_dir=None, gate_ref=None, custody_argv=None):
    gitio.require_git()
    repo = os.path.abspath(os.path.expanduser(repo))
    worktree = os.path.abspath(os.path.expanduser(worktree))
    if os.path.exists(worktree):
        raise Refusal("worktree path already exists: %s" % worktree, hint="name a fresh path; nothing was changed")
    if not os.path.isdir(os.path.dirname(worktree)):
        raise UsageError("worktree parent directory does not exist: %s" % os.path.dirname(worktree))

    report = verify.verify(root, cfg, repo=repo)
    root, manifest = verify.load(root)
    pid = report["passport_id"]
    task, seq = manifest["task"], manifest["seq"]
    branch = branch or manifest["branch"]
    namespace = cfg["bundle_namespace"]
    prefix = "%s/%s/%s" % (cfg["import_prefix"].rstrip("/"), task, seq)
    timeout = cfg["git_timeout_s"]

    target = gitio.Git(repo, timeout=timeout)
    object_dirs = [target.objects_dir()]
    target_gate = None
    if gate_git_dir:
        target_gate = gitio.Git(os.path.abspath(os.path.expanduser(gate_git_dir)), bare=True, timeout=timeout)
        object_dirs.append(target_gate.objects_dir())
    custody_doc = read_json(os.path.join(root, "custody.json"), "custody")
    pipeline = read_json(os.path.join(root, "pipeline.json"), "pipeline")
    decisions = read_json(os.path.join(root, "decisions.json"), "decisions")

    with gitio.Scratch(object_dirs, timeout=timeout) as scratch:
        sg = scratch.git
        # 1. Stage everything in the scratch repository.
        mapping = []  # (scratch ref, target ref, sha)
        refspecs = []
        for ref in manifest["refs"]:
            tail = _tail(ref["bundle_ref"], namespace)
            staged = "refs/import/" + tail
            mapping.append((staged, "%s/%s" % (prefix, tail), ref["sha"]))
            if ref["in_base"]:
                sg.create_ref(staged, ref["sha"], "lodgings passport import (in base)")
            else:
                refspecs.append("%s:%s" % (ref["bundle_ref"], staged))
        if refspecs:
            sg.ok(["fetch", "--no-tags", "--quiet", os.path.join(root, "refs.bundle")] + refspecs, "fetch bundle (scratch)")
        for staged, _, sha in mapping:
            if sg.ref_value(staged) != sha:
                raise Invalid("staged ref %s does not match the manifest" % staged)

        # 2. Recompute custody from the objects themselves.
        lane, gate = manifest["lane_head"], manifest.get("gate_head")
        relation = custody.relate(sg, lane, gate)
        if relation["relation"] != manifest["custody_relation"]:
            raise Invalid("custody recomputed as %s, manifest says %s" % (relation["relation"], manifest["custody_relation"]))
        adopt = manifest.get("adopt")
        adopted = None
        if adopt:
            # Adopting the gate head was decided at export. The facts are recomputed here from the objects: a
            # sealed passport can still claim anything, so the digest of the difference must match.
            if not gate or adopt.get("mode") != "gate-head" or adopt.get("lane_head") != lane or adopt.get("gate_head") != gate:
                raise Invalid("manifest adopt record does not match the lane and gate heads")
            facts = custody.adoption(sg, lane, gate)
            if (facts["digest"] != adopt.get("digest") or facts["tree_equal"] != adopt.get("tree_equal")
                    or not (facts["tree_equal"] or adopt.get("confirmed"))):
                raise Invalid("the adopt record does not match the objects: the difference between lane head %s and "
                              "gate head %s is not what was confirmed at export" % (short(lane), short(gate)),
                              hint="export again from the origin")
            adopted = {"mode": "gate-head", "lane_head": lane, "gate_head": gate, "lane_tree": facts["lane_tree"],
                       "gate_tree": facts["gate_tree"], "tree_equal": facts["tree_equal"],
                       "lane_only_commits": facts["lane_only_commits"]["count"],
                       "gate_only_commits": facts["gate_only_commits"]["count"],
                       "tree_differences": facts["tree_differences"]["count"], "digest": facts["digest"]}
        elif not relation["safe"]:
            raise Refusal("custody %s: %s" % (relation["relation"], relation["rule"]), hint=custody.hint_for(relation))
        resume, bridge = (gate if adopted else lane), None
        if relation["needs_bridge"] and not adopted:
            bridge = _bridge(sg, manifest, lane, gate, cfg, pid)
            if sg.tree(bridge) != manifest["lane_tree"] or not sg.is_ancestor(gate, bridge) or not sg.is_ancestor(lane, bridge):
                raise Invalid("bridge commit %s failed its own checks" % short(bridge))
            sg.create_ref("refs/import/bridge", bridge, "lodgings passport bridge")
            mapping.append(("refs/import/bridge", "%s/bridge" % prefix, bridge))
            resume = bridge

        # 3. The target's own gate, if named, must fast-forward to the resume head.
        target_gate_head = None
        if target_gate:
            ref = gate_ref or ("refs/heads/" + branch)
            target_gate_head = target_gate.rev_parse(ref)
            if target_gate_head and not sg.is_ancestor(target_gate_head, resume):
                raise Refusal("the target gate already holds %s for %s, and the resume head %s does not descend from it"
                              % (short(target_gate_head), ref, short(resume)),
                              hint="the target gate has history this lane lacks; decide by hand (nothing was changed)")

        # 4. Pre-check every target write.
        branch_ref = "refs/heads/" + branch
        existing_branch = target.rev_parse(branch_ref)
        if existing_branch and existing_branch != resume:
            raise Refusal("target branch %s already exists at %s, not the resume head %s"
                          % (branch, short(existing_branch), short(resume)),
                          hint="import under another --branch name; never overwrite an existing branch")
        if existing_branch:
            listing = target.ok(["worktree", "list", "--porcelain"], "worktree list")
            if ("branch %s" % branch_ref) in listing.splitlines():
                raise Refusal("target branch %s is already checked out in another worktree" % branch,
                              hint="import under another --branch name; nothing was changed")
        for _, dest, sha in mapping:
            current = target.ref_value(dest)
            if current and current != sha:
                raise Refusal("target ref %s exists at %s, not %s" % (dest, short(current), short(sha)),
                              hint="this passport seq was already imported differently; nothing was changed")

        # 5. Write: refs (non-forcing fetch), the branch (create-only), the worktree.
        target.ok(["fetch", "--no-tags", "--quiet", "--no-write-fetch-head", scratch.dir,
                   "refs/import/*:%s/*" % prefix], "fetch into import refs")
    for _, dest, sha in mapping:
        if target.ref_value(dest) != sha:
            raise Invalid("target ref %s did not land at %s" % (dest, short(sha)))
    created_branch = target.create_ref(branch_ref, resume, "lodgings passport import %s-%s" % (task, seq))
    target.ok(["worktree", "add", "--quiet", worktree, branch], "worktree add")

    # 6. Verify the result from the new worktree itself.
    wt = gitio.Git(worktree, timeout=timeout)
    checks = []
    head = wt.head()
    checks.append(("worktree HEAD is the resume head", head == resume, short(head)))
    resume_tree = adopted["gate_tree"] if adopted else manifest["lane_tree"]
    checks.append(("worktree tree equals the %s tree" % ("gate head's" if adopted else "lane"),
                   wt.tree("HEAD") == resume_tree, short(resume_tree)))
    checks.append(("worktree is clean", not wt.status_porcelain().strip(), ""))
    if gate:
        checks.append(("exported gate head is an ancestor of HEAD", wt.is_ancestor(gate, "HEAD"), short(gate)))
    if target_gate_head:
        checks.append(("target gate head is an ancestor of HEAD", wt.is_ancestor(target_gate_head, "HEAD"),
                       short(target_gate_head)))
    for _, dest, sha in mapping:
        checks.append(("ref %s" % dest, target.ref_value(dest) == sha, short(sha)))
    verified = all(ok for _, ok, _ in checks)

    argv = custody_argv if custody_argv is not None else cfg["import_custody_check"]
    ext = custody.external_check(argv, worktree, cfg["check_timeout_s"])

    receipt = {
        "schema": IMPORT_SCHEMA,
        "passport_id": pid,
        "task": task,
        "seq": seq,
        "imported_at": utc_now(),
        "target_host": cfg["host_label"],
        "target_repo": repo,
        "branch": branch,
        "branch_created": created_branch,
        "worktree": worktree,
        "lane_head": lane,
        "lane_tree": manifest["lane_tree"],
        "gate_head": gate,
        "custody_relation": relation["relation"],
        "adopted": adopted,
        "bridge": bridge,
        "resume_head": resume,
        "target_gate_head": target_gate_head,
        "import_refs": [{"ref": dest, "sha": sha} for _, dest, sha in mapping],
        "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks],
        "verified": verified,
        "external_check": ext,
    }
    after = seal.after_dir(root)
    receipt_path = os.path.join(after, "import-receipt.json")
    write_json(receipt_path, receipt)
    write_text(os.path.join(after, "resume.md"), printable(_resume_md(manifest, receipt, pipeline, decisions, custody_doc)))
    if not verified:
        failed = [c for c, ok, _ in checks if not ok]
        raise Invalid("import landed but failed verification: %s" % "; ".join(failed),
                      hint="nothing on the origin changed; inspect %s before using it" % worktree,
                      evidence={"receipt": receipt_path})
    return {
        "verdict": "imported",
        "task": task,
        "seq": seq,
        "passport_id": pid,
        "branch": branch,
        "worktree": worktree,
        "resume_head": resume,
        "bridge": bridge,
        "custody": relation["relation"],
        "adopted": ({"mode": "gate-head", "tree_equal": adopted["tree_equal"]} if adopted else None),
        "import_refs": len(mapping),
        "receipt": receipt_path,
        "receipt_sha256": sha256_file(receipt_path),
        "resume_brief": os.path.join(after, "resume.md"),
        "origin_written": False,
    }
