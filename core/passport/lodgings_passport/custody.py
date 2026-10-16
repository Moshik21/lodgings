"""Custody: how the lane head relates to the review-gate head, and whether a move is safe.

Relations (lane head L, gate head G):

  no-gate    the gate holds no ref for this lane                      safe, no bridge
  equal      L == G                                                    safe, no bridge
  ahead      G is an ancestor of L (the gate will fast-forward)        safe, no bridge
  contained  G is not an ancestor of L, but merging G into L changes
             nothing (same tree, or a rebase whose changes L already
             holds): a bridge merge with parents (L, G) and L's tree
             makes the gate fast-forward without any content change    safe, bridge
  behind     L is an ancestor of G and G adds content (pipeline
             commits the lane lacks)                                   UNSAFE
  diverged   neither is an ancestor and G holds content L lacks        UNSAFE

Every relation with a gate head also records `lane_only_commits` (commits in L that G lacks), `gate_only_commits`
(commits in G that L lacks) and `ahead` (`lane`, `gate`, `neither` or `equal`), so a refusal can say which side is
ahead and by how much. `gate_holds_lane_changes` is true when merging the two yields G's own tree: every change of
the lane is already in the gate head, which is what a squash or rebase by a review gate looks like.

"Merging G into L changes nothing" is decided by `git merge-tree --write-tree`:
a clean merge whose tree equals L's tree. That is the content rule behind the
hand-made "descendant commit with the same tree" repair, made mechanical.
"""

import hashlib
import re
import time

FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*):\s+(\S.*)$")

from .util import bounded, one_line, run, short

SAFE = ("no-gate", "equal", "ahead", "contained")
ADOPT_SHOWN = 20     # commits and paths listed for a person; the digest covers all of them
ADOPT_KEPT = 200     # entries kept in the passport's custody.json


def relate(git, lane, gate):
    """Compute the custody relation inside `git` (which must hold both commits)."""
    result = {
        "lane_head": lane,
        "lane_tree": git.tree(lane),
        "gate_head": gate,
        "gate_tree": None,
        "merge_base": None,
        "relation": None,
        "safe": False,
        "needs_bridge": False,
        "lane_only_commits": None,
        "gate_only_commits": None,
        "ahead": None,
        "gate_holds_lane_changes": None,
        "rule": "",
    }
    if not gate:
        result.update(relation="no-gate", safe=True,
                      rule="the gate holds no ref for this lane; a fresh run creates it")
        return result
    result["gate_tree"] = git.tree(gate)
    result["merge_base"] = git.merge_base(lane, gate)
    if lane == gate:
        result.update(relation="equal", safe=True, rule="lane head equals gate head",
                      lane_only_commits=0, gate_only_commits=0, ahead="equal")
        return result
    lane_only, gate_only = git.count("%s..%s" % (gate, lane)), git.count("%s..%s" % (lane, gate))
    result.update(lane_only_commits=lane_only, gate_only_commits=gate_only,
                  ahead="lane" if not gate_only else "gate" if not lane_only else "neither")
    if git.is_ancestor(gate, lane):
        result.update(relation="ahead", safe=True,
                      rule="gate head %s is an ancestor of lane head %s (fast-forward)" % (short(gate), short(lane)))
        return result
    clean, merged = git.merge_tree(lane, gate)
    if clean and merged == result["lane_tree"]:
        same_tree = result["gate_tree"] == result["lane_tree"]
        result.update(
            relation="contained", safe=True, needs_bridge=True,
            rule=("gate head %s has the same tree as lane head %s" if same_tree else
                  "every change in gate head %s is already in lane head %s (merge-tree is clean and "
                  "yields the lane tree)") % (short(gate), short(lane)))
        return result
    result["gate_holds_lane_changes"] = bool(clean and merged == result["gate_tree"])
    if git.is_ancestor(lane, gate):
        result.update(relation="behind",
                      rule="the gate is ahead: gate head %s is %d commit(s) ahead of lane head %s, which is its ancestor"
                           % (short(gate), gate_only, short(lane)))
    else:
        if result["gate_holds_lane_changes"]:
            why = ("every change of the lane is already in the gate head, so the gate holds a squash or a rebase "
                   "of this lane (it may also hold newer base commits)")
        else:
            why = "the gate head holds changes the lane head does not (merge %s)" % (
                "conflicts" if not clean else "changes the tree")
        result.update(relation="diverged",
                      rule="neither side is ahead: lane head %s has %d commit(s) the gate lacks, gate head "
                           "%s has %d commit(s) the lane lacks (merge base %s); %s"
                           % (short(lane), lane_only, short(gate), gate_only, short(result["merge_base"]) or "none", why))
    return result


def hint_for(result):
    """What a person can do after an unsafe relation. `result` is the relation record from relate()."""
    relation = result["relation"]
    if relation not in ("behind", "diverged"):
        return ""
    lines = ["nothing was changed. Options:"]  # one numbered line each; the CLI indents them under `how:`
    if relation == "behind":
        lines.append("1. bring the gate's commits into the lane on the origin first (your review gate's own "
                     "custody-return command), then export again")
        lines.append("2. export again with --adopt-gate-head: the lane resumes at the gate head (the lane head is its "
                     "ancestor, so no commit is lost); the paths the gate added are listed and need "
                     "--confirm-differences")
    elif result.get("gate_holds_lane_changes"):
        lines.append("1. the gate head holds every change of the lane (a squash or a rebase by the review gate): "
                     "export again with --adopt-gate-head. The lane resumes at the gate head; the lane's own commits "
                     "are listed, travel in the passport and are archived by confirm, and the paths that differ need "
                     "--confirm-differences")
        lines.append("2. or keep the lane's own history: return the gate's head to the lane on the origin "
                     "(your review gate's custody-return command), then export again")
    else:
        lines.append("1. the gate holds work the lane head does not contain: bring it into the lane on the origin "
                     "first (your review gate's own custody-return command), then export again")
        lines.append("2. or, if the lane's own work is what should be dropped: export again with --adopt-gate-head. "
                     "The commits and paths only the lane holds are listed, and nothing is adopted until you pass "
                     "--confirm-differences")
    lines.append("3. or decide by hand which head is the lane, then export again")
    return "\n".join(lines)


def adoption(git, lane, gate):
    """Facts for resuming at the gate head instead of the lane head (`--adopt-gate-head`).

    Everything is computed from objects in `git`: the commits only the lane holds (the history the gate head would
    replace), the commits only the gate holds, and the tree difference lane -> gate. `token` is bound to those
    exact heads and that exact difference, so a confirmation cannot outlive a moved head.
    """
    lane_tree, gate_tree = git.tree(lane), git.tree(gate)
    lane_only = git.rev_list("%s..%s" % (gate, lane))
    gate_only = git.rev_list("%s..%s" % (lane, gate))
    entries, raw = git.diff_tree(lane, gate)
    side = {"A": "only in the gate", "D": "only in the lane"}
    digest = hashlib.sha256(("lodgings-adopt/1\n%s\n%s\n%s\n%s\n" % (lane, gate, raw, "\n".join(lane_only)))
                            .encode("utf-8", "surrogateescape")).hexdigest()
    return {
        "mode": "gate-head",
        "lane_head": lane,
        "gate_head": gate,
        "lane_tree": lane_tree,
        "gate_tree": gate_tree,
        "tree_equal": lane_tree == gate_tree,
        "lane_only_commits": {"count": len(lane_only),
                              "entries": [{"sha": sha, "subject": subj[:200]}
                                          for sha, subj in git.subjects("%s..%s" % (gate, lane), ADOPT_KEPT)]
                              if lane_only else []},
        "gate_only_commits": {"count": len(gate_only),
                              "entries": [{"sha": sha, "subject": subj[:200]}
                                          for sha, subj in git.subjects("%s..%s" % (lane, gate), ADOPT_KEPT)]
                              if gate_only else []},
        "tree_differences": {"count": len(entries),
                             "entries": [{"status": st, "side": side.get(st, "differs"), "path": path}
                                         for st, path in entries[:ADOPT_KEPT]]},
        "digest": "sha256:" + digest,
        "token": digest[:16],
    }


def adoption_lines(facts, relation=None):
    """Plain text for a person: what the gate head would replace and what differs."""
    lines = []
    lane_only, gate_only, diff = facts["lane_only_commits"], facts["gate_only_commits"], facts["tree_differences"]
    lines.append("the lane would resume at gate head %s; the lane's own %d commit(s) travel in the passport and are "
                 "archived by confirm, never resumed" % (short(facts["gate_head"]), lane_only["count"]))
    if relation and relation.get("gate_holds_lane_changes"):
        lines.append("every change of the lane is already in the gate head (a clean merge yields the gate tree); the "
                     "differences below are content only the gate holds")
    if lane_only["count"]:
        lines.append("commits only the lane holds (%d):" % lane_only["count"])
        for e in lane_only["entries"][:ADOPT_SHOWN]:
            lines.append("  %s %s" % (short(e["sha"]), one_line(e["subject"])))
        if lane_only["count"] > ADOPT_SHOWN:
            lines.append("  ... and %d more (--json lists up to %d)" % (lane_only["count"] - ADOPT_SHOWN, ADOPT_KEPT))
    if gate_only["count"]:
        lines.append("commits only the gate holds (%d):" % gate_only["count"])
        for e in gate_only["entries"][:ADOPT_SHOWN]:
            lines.append("  %s %s" % (short(e["sha"]), one_line(e["subject"])))
        if gate_only["count"] > ADOPT_SHOWN:
            lines.append("  ... and %d more (--json lists up to %d)" % (gate_only["count"] - ADOPT_SHOWN, ADOPT_KEPT))
    if diff["count"]:
        lines.append("paths whose content differs, lane -> gate (%d):" % diff["count"])
        for e in diff["entries"][:ADOPT_SHOWN * 2]:
            lines.append("  %-16s %s" % (e["side"], one_line(e["path"])))
        if diff["count"] > ADOPT_SHOWN * 2:
            lines.append("  ... and %d more (--json lists up to %d)" % (diff["count"] - ADOPT_SHOWN * 2, ADOPT_KEPT))
    else:
        lines.append("the lane head and the gate head have the identical tree")
    return lines


def external_check(argv, cwd, timeout):
    """Run a read-only custody command (for example a review gate's own sync check) as evidence."""
    if not argv:
        return {"ran": False, "why": "no custody check configured"}
    started = time.time()
    rc, out, err = run(argv, cwd=cwd, timeout=timeout)
    text = (out or "") + (("\n" + err) if err else "")
    return {
        "ran": True,
        "fields": _fields(text),
        "argv": list(argv),
        "rc": rc,
        "took_ms": int((time.time() - started) * 1000),
        "output": bounded(text.strip(), 6000),
        "decisive": False,
        "note": "recorded as evidence; the passport decides custody from git objects (custody.json relation)",
    }


def _fields(text, limit=40):
    """First value of each `key: value` line, so a person can scan the check's verdict."""
    fields = {}
    for line in text.splitlines():
        m = FIELD_RE.match(line)
        if m and m.group(1) not in fields and len(fields) < limit:
            fields[m.group(1)] = m.group(2).strip().strip('"')[:200]
    return fields
