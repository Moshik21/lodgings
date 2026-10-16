"""confirm: on the origin, after the target reports a verified import.

It re-checks that the origin did not move since export (a moved lane branch or
gate would strand work), then writes create-only archive refs for every head
the passport carried, and a tombstone receipt. It never deletes, resets or
rewrites anything, and it never touches the origin's worktree: cleanup stays
with whatever normally tears lanes down, and its own landed-work checks.
"""

import os

from . import CONFIRM_SCHEMA, IMPORT_SCHEMA
from . import gitio, seal, verify
from .util import Invalid, Refusal, read_json, sha256_file, short, utc_now, write_json


def confirm(root, repo, cfg, receipt_path=None, gate_git_dir=None, gate_ref=None, archive=True):
    gitio.require_git()
    root, manifest = verify.load(root)
    pid = seal.passport_id(root)
    after = seal.after_dir(root)
    tomb = os.path.join(after, "confirm.json")
    if os.path.exists(tomb):
        done = read_json(tomb, "confirm receipt")
        if done.get("passport_id") == pid:
            return dict(done, verdict="already-confirmed")
        raise Invalid("after/confirm.json belongs to another passport")

    receipt_path = receipt_path or os.path.join(after, "import-receipt.json")
    if not os.path.isfile(receipt_path):
        raise Refusal("no import receipt: the target has not confirmed an import of this passport",
                      hint="copy the target's after/import-receipt.json here, or pass --receipt")
    receipt = read_json(receipt_path, "import receipt")
    if receipt.get("schema") != IMPORT_SCHEMA:
        raise Invalid("import receipt schema %r is not %s" % (receipt.get("schema"), IMPORT_SCHEMA))
    if receipt.get("passport_id") != pid:
        raise Refusal("the import receipt is for passport %s, not %s" % (receipt.get("passport_id"), pid),
                      hint="confirm with the receipt the target wrote for this exact passport")
    if not receipt.get("verified"):
        raise Refusal("the target's import did not verify", hint="inspect the receipt's checks; nothing was changed")
    if receipt.get("lane_tree") != manifest["lane_tree"]:
        raise Refusal("the target resumed a different tree than the passport carried")
    adopt = manifest.get("adopt")
    if adopt and (not receipt.get("adopted") or receipt["adopted"].get("digest") != adopt.get("digest")
                  or receipt.get("resume_head") != adopt.get("gate_head")):
        raise Refusal("the passport adopts gate head %s, and the import receipt does not show that adoption"
                      % short(adopt.get("gate_head")),
                      hint="confirm with the receipt the target wrote for this exact passport; nothing was changed")

    git = gitio.Git(os.path.abspath(os.path.expanduser(repo)), timeout=cfg["git_timeout_s"])
    now = git.rev_parse("refs/heads/" + manifest["branch"])
    if now != manifest["lane_head"]:
        if now and git.is_ancestor(manifest["lane_head"], now):
            why = "the origin lane gained commits after export (%s -> %s); they would be stranded" % (
                short(manifest["lane_head"]), short(now))
        elif now:
            why = "the origin lane branch was rewritten after export (%s -> %s)" % (short(manifest["lane_head"]), short(now))
        else:
            why = "the origin lane branch %s is gone" % manifest["branch"]
        raise Refusal(why, hint="export a new passport from the origin's current state; nothing was changed")
    if gate_git_dir:
        gate = gitio.Git(os.path.abspath(os.path.expanduser(gate_git_dir)), bare=True, timeout=cfg["git_timeout_s"])
        gate_now = gate.rev_parse(gate_ref or ("refs/heads/" + manifest["branch"]))
        if gate_now != manifest.get("gate_head"):
            raise Refusal("the origin gate moved after export (%s -> %s)" % (short(manifest.get("gate_head")), short(gate_now)),
                          hint="a pipeline ran on the origin after export; export again; nothing was changed")

    written = []
    if archive:
        prefix = "%s/%s/%s" % (cfg["archive_prefix"].rstrip("/"), manifest["task"], manifest["seq"])
        ns = cfg["bundle_namespace"].rstrip("/") + "/"
        plan = []
        for ref in manifest["refs"]:
            if not ref["bundle_ref"].startswith(ns):
                raise Invalid("bundle ref %s is outside %s" % (ref["bundle_ref"], ns))
            dest = prefix + "/" + ref["bundle_ref"][len(ns):]
            current = git.ref_value(dest)
            if current and current != ref["sha"]:
                raise Refusal("archive ref %s exists at %s, not %s" % (dest, short(current), short(ref["sha"])),
                              hint="nothing was changed; inspect the archive by hand")
            plan.append((ref, dest, current))
        specs = ["%s:%s" % (r["bundle_ref"], d) for r, d, cur in plan if not r["in_base"] and not cur]
        if specs:
            git.ok(["fetch", "--no-tags", "--quiet", "--no-write-fetch-head", os.path.join(root, "refs.bundle")] + specs,
                   "fetch archive refs from bundle")
        for ref, dest, cur in plan:
            if ref["in_base"] and not cur:
                git.create_ref(dest, ref["sha"], "lodgings passport archive")
            if git.ref_value(dest) != ref["sha"]:
                raise Invalid("archive ref %s did not land at %s" % (dest, short(ref["sha"])))
            written.append({"ref": dest, "sha": ref["sha"], "existed": bool(cur)})

    doc = {
        "schema": CONFIRM_SCHEMA,
        "passport_id": pid,
        "task": manifest["task"],
        "seq": manifest["seq"],
        "confirmed_at": utc_now(),
        "origin_host": cfg["host_label"],
        "target_host": receipt.get("target_host"),
        "target_resume_head": receipt.get("resume_head"),
        "import_receipt_sha256": sha256_file(receipt_path),
        "archive_refs": written,
        "origin_untouched": ["lane branch", "lane worktree", "review gate"],
        "next": "the lane now lives on the target; tear the origin lane down through your normal teardown, "
                "which keeps its own landed-work checks",
    }
    write_json(tomb, doc)
    return dict(doc, verdict="confirmed", origin_written=bool(written))
