"""verify: prove a passport is whole and self-consistent, and (with --repo) that a target can take it.

Read-only everywhere: the bundle is checked with `git bundle verify` / `list-heads`,
and records are re-scanned for secrets. Nothing is fetched.
"""

import os
import re

from . import PASSPORT_SCHEMA
from . import gitio, seal
from .lane import BRANCH_RE, TASK_RE
from .util import Invalid, read_json, short

REF_RE = re.compile(r"^refs/[A-Za-z0-9._/-]+$")


def load(root):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise Invalid("passport directory not found: %s" % root)
    problems = seal.check_seal(root)
    if problems:
        raise Invalid("passport seal is broken: %s" % "; ".join(problems[:6]),
                      hint="the passport changed after export; export again from the origin",
                      evidence={"problems": problems})
    manifest = read_json(os.path.join(root, "manifest.json"), "manifest")
    if manifest.get("schema") != PASSPORT_SCHEMA:
        raise Invalid("manifest schema %r is not %s" % (manifest.get("schema"), PASSPORT_SCHEMA))
    _check_names(manifest)
    return root, manifest


def _check_names(manifest):
    """A sealed passport can still be hostile: every name that becomes a ref or a path is re-checked."""
    if not isinstance(manifest.get("task"), str) or not TASK_RE.match(manifest["task"]):
        raise Invalid("manifest task id %r is not a plain id" % (manifest.get("task"),))
    if not isinstance(manifest.get("seq"), int) or isinstance(manifest.get("seq"), bool) or manifest["seq"] < 1:
        raise Invalid("manifest seq %r is not a positive integer" % (manifest.get("seq"),))
    if not isinstance(manifest.get("branch"), str) or not BRANCH_RE.match(manifest["branch"]):
        raise Invalid("manifest branch %r is not a plain branch name" % (manifest.get("branch"),))
    for ref in manifest.get("refs") or []:
        name = ref.get("bundle_ref", "")
        if not REF_RE.match(name) or ".." in name or "//" in name or name.endswith((".lock", "/")):
            raise Invalid("manifest bundle ref %r is not a safe ref name" % (name,))
        if not gitio.SHA_RE.match(ref.get("sha", "")):
            raise Invalid("manifest ref %r has no full commit id" % (name,))
    for rec in manifest.get("records") or []:
        try:
            seal.check_record_name(rec.get("name", ""))
        except Exception:
            raise Invalid("manifest record name %r is not a plain file name" % (rec.get("name"),))


def bundle_heads(git, bundle_path):
    if os.path.getsize(bundle_path) == 0:
        return {}
    out = git.ok(["bundle", "list-heads", bundle_path], "bundle list-heads")
    heads = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            heads[parts[1]] = parts[0]
    return heads


def verify(root, cfg, repo=None):
    gitio.require_git()
    root, manifest = load(root)
    pid = seal.passport_id(root)
    checks = []
    bundle_path = os.path.join(root, "refs.bundle")
    # list-heads needs a repository context only for its own git process; any repo works,
    # so use the target when given, else a scratch repository with no objects.
    if repo:
        git = gitio.Git(repo, timeout=cfg["git_timeout_s"])
        heads = bundle_heads(git, bundle_path)
        scratch = None
    else:
        scratch = gitio.Scratch([], timeout=cfg["git_timeout_s"])
        heads = bundle_heads(scratch.git, bundle_path)
    try:
        for ref in manifest["refs"]:
            if ref["in_base"]:
                checks.append(("ref %s in base" % ref["source"], True, short(ref["sha"])))
                continue
            got = heads.get(ref["bundle_ref"])
            if got != ref["sha"]:
                raise Invalid("bundle ref %s is %s, manifest says %s" % (ref["bundle_ref"], short(got), short(ref["sha"])))
        extra = set(heads) - set(r["bundle_ref"] for r in manifest["refs"])
        if extra:
            raise Invalid("bundle carries refs the manifest does not list: %s" % ", ".join(sorted(extra)))
        checks.append(("bundle refs match manifest", True, "%d ref(s)" % len(heads)))

        lane = [r for r in manifest["refs"] if r["kind"] == "lane"]
        if len(lane) != 1 or lane[0]["sha"] != manifest["lane_head"]:
            raise Invalid("manifest lane head is inconsistent with its refs")
        custody = read_json(os.path.join(root, "custody.json"), "custody")
        if custody["relation"]["relation"] != manifest["custody_relation"]:
            raise Invalid("custody.json relation disagrees with the manifest")
        checks.append(("custody recorded", True, manifest["custody_relation"]))
        adopt = manifest.get("adopt")
        if adopt:
            if (adopt.get("mode") != "gate-head" or adopt.get("lane_head") != manifest["lane_head"]
                    or not manifest.get("gate_head") or adopt.get("gate_head") != manifest["gate_head"]
                    or adopt.get("lane_tree") != manifest["lane_tree"]):
                raise Invalid("manifest adopt record is inconsistent with the lane and gate heads")
            if not adopt.get("tree_equal") and not adopt.get("confirmed"):
                raise Invalid("manifest adopts a gate head whose tree differs, but the differences were not confirmed")
            checks.append(("adopt-gate-head recorded", True, "%s, %d path(s) differ" % (
                "trees identical" if adopt.get("tree_equal") else "differences confirmed", adopt.get("tree_differences", 0))))

        for rec in manifest.get("records") or []:
            path = os.path.join(root, "records", rec["name"])
            with open(path, "r", encoding="utf-8") as fh:
                if seal.scan_secrets(fh.read(), cfg["secret_patterns"]):
                    raise Invalid("record %s matches a secret pattern" % rec["name"])
        checks.append(("records secret scan", True, "%d record(s)" % len(manifest.get("records") or [])))

        prerequisites_ok = None
        if repo and os.path.getsize(bundle_path) > 0:
            rc, out, err = git.call(["bundle", "verify", bundle_path])
            prerequisites_ok = rc == 0
            if not prerequisites_ok:
                raise Invalid("target %s cannot take this bundle: %s" % (repo, " ".join((err or out).split())[:400]),
                              hint="the target lacks the base commit %s; fetch the base branch there first"
                                   % short((manifest.get("base") or {}).get("prerequisite")))
            checks.append(("target has the bundle prerequisites", True, short((manifest.get("base") or {}).get("prerequisite"))))
        if repo:
            for ref in manifest["refs"]:
                if ref["in_base"] and not git.has_commit(ref["sha"]):
                    raise Invalid("target lacks %s (%s), which the passport expects in the base"
                                  % (short(ref["sha"]), ref["source"]))
    finally:
        if scratch:
            scratch.close()
    return {
        "verdict": "verified",
        "task": manifest["task"],
        "seq": manifest["seq"],
        "passport": root,
        "passport_id": pid,
        "lane_head": manifest["lane_head"],
        "custody": manifest["custody_relation"],
        "adopted": ({"mode": "gate-head", "tree_equal": manifest["adopt"].get("tree_equal"),
                     "resume_head": manifest["adopt"].get("gate_head")} if manifest.get("adopt") else None),
        "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks],
        "target_checked": bool(repo),
        "prerequisites_ok": prerequisites_ok,
    }
