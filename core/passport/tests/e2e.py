#!/usr/bin/env python3
"""End-to-end behavioural run of lodgings-passport through its public CLI only.

It builds throwaway repositories in --work (an upstream, an origin host with a
lane worktree and a review gate, a target host with its own gate) and replays
the real cases a lane move has to survive, each with its unsafe twin:

  A  the gate holds an older head the lane was rebased away from
  B  a lane mid-pipeline, parked at a gate with findings and a fix round
  C  a lane with archive refs (one of them already inside the base)
  D  refusals: dirty tree, diverged gate, secret in a record, tampered
     passport, existing target branch, target missing the base, confirm
     without a receipt or after the origin moved, and the kill switch
  E  the review gate holds a squash, then a rebase, of the lane on a newer
     main (custody diverged): refused by default, adopted only when named
     and confirmed, and what only the lane holds is listed first
  F  adopt guards: identical trees need no confirmation, a genuinely
     diverged gate still lists the lane's own work, a hostile passport
  G  a lane branch that has no worktree (named path gone, or none named)

After every step it checks the origin invariant: no ref, gate ref or worktree
on the origin changes until `confirm`, and `confirm` only adds refs under the
archive prefix. Nothing outside --work is written.

  python3 tests/e2e.py --work /tmp/passport-e2e [--log run.log] [--keep]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "lodgings-passport")
SHIM = os.path.join(HERE, "shims", "gate-check")
FIXTURES = os.path.join(HERE, "fixtures")

ENV = dict(os.environ)
ENV.update({
    "GIT_AUTHOR_NAME": "e2e", "GIT_AUTHOR_EMAIL": "e2e@example.invalid",
    "GIT_COMMITTER_NAME": "e2e", "GIT_COMMITTER_EMAIL": "e2e@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
})
ENV.pop("LODGINGS_PASSPORT", None)

RESULTS = []
LOG = []


def say(text):
    LOG.append(text)
    print(text)
    sys.stdout.flush()


def sh(argv, cwd=None, ok=True, input_text=None):
    p = subprocess.run(argv, cwd=cwd, env=ENV, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if ok and p.returncode != 0:
        raise RuntimeError("command failed (%d): %s\n%s%s" % (p.returncode, " ".join(argv), p.stdout, p.stderr))
    return p.stdout.strip()


def git(repo, *args, **kw):
    return sh(["git", "-C", repo] + list(args), **kw)


def commit(repo, path, text, message):
    full = os.path.join(repo, path)
    d = os.path.dirname(full)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(full, "w") as fh:
        fh.write(text)
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def cli(state, *args):
    argv = [sys.executable, BIN] + list(args) + ["--state", state]
    started = time.time()
    p = subprocess.run(argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    ms = int((time.time() - started) * 1000)
    shown = " ".join(a if " " not in a else repr(a) for a in args)
    say("    $ lodgings-passport %s" % shown.replace(WORK, "$WORK"))
    for line in (p.stdout + p.stderr).strip().splitlines():
        say("      %s" % line.replace(WORK, "$WORK"))
    say("      (exit %d, %d ms)" % (p.returncode, ms))
    return p.returncode, p.stdout


def cli_json(state, *args):
    rc, out = cli(state, *(list(args) + ["--json"]))
    try:
        return rc, json.loads(out)
    except ValueError:
        return rc, {"raw": out}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    say("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, (" - " + detail) if detail else ""))
    return cond


def refs(repo, bare=False):
    base = ["git", "--git-dir", repo] if bare else ["git", "-C", repo]
    out = sh(base + ["for-each-ref", "--format=%(refname) %(objectname)"])
    found = {}
    for line in out.splitlines():
        name, sha = line.split(" ", 1)
        found[name] = sha
    return found


class Origin(object):
    """Snapshot of everything the origin owns, to prove the passport left it alone."""

    def __init__(self, repo, gate, worktrees):
        self.repo, self.gate, self.worktrees = repo, gate, worktrees
        self.snap = self.take()

    def take(self):
        wt = {}
        for w in self.worktrees:
            if os.path.isdir(w):
                wt[w] = (git(w, "rev-parse", "HEAD"), git(w, "status", "--porcelain"))
        return {"refs": refs(self.repo), "gate": refs(self.gate, bare=True), "wt": wt}

    def unchanged(self, label, allow_prefix=None):
        now = self.take()
        before, after = dict(self.snap["refs"]), dict(now["refs"])
        added = {k: v for k, v in after.items() if k not in before}
        if allow_prefix:
            for k in list(added):
                if k.startswith(allow_prefix):
                    before[k] = after[k]
        same = before == after and self.snap["gate"] == now["gate"] and self.snap["wt"] == now["wt"]
        check("origin untouched: %s" % label, same,
              "" if same else "refs diff %s" % sorted(set(after.items()) ^ set(self.snap["refs"].items())))
        return now


def setup(work):
    up = os.path.join(work, "upstream.git")
    sh(["git", "init", "-q", "--bare", "-b", "main", up])
    seed = os.path.join(work, "seed")
    sh(["git", "init", "-q", "-b", "main", seed])
    m1 = commit(seed, "README.md", "demo project\n", "M1 initial")
    commit(seed, "src/app.txt", "app v1\n", "M2 app")
    git(seed, "push", "-q", up, "main")
    origin = os.path.join(work, "origin", "repo")
    target = os.path.join(work, "target", "repo")
    stale = os.path.join(work, "stale-target", "repo")
    for d in (origin, target, stale):
        os.makedirs(os.path.dirname(d))
        sh(["git", "clone", "-q", up, d])
    # The stale target is frozen at M1: it lacks the base the lanes are built on.
    git(stale, "reset", "-q", "--hard", m1)
    git(stale, "update-ref", "refs/remotes/origin/main", m1)
    gates = {}
    for side in ("origin", "target"):
        g = os.path.join(work, side, "gate.git")
        sh(["git", "init", "-q", "--bare", "-b", "main", g])
        sh(["git", "--git-dir", g, "config", "receive.denyNonFastForwards", "true"])
        gates[side] = g
    return {"up": up, "seed": seed, "origin": origin, "target": target, "stale": stale,
            "ogate": gates["origin"], "tgate": gates["target"]}


def advance_main(env, text, message):
    commit(env["seed"], "src/app.txt", text, message)
    git(env["seed"], "push", "-q", env["up"], "main")
    for side in ("origin", "target"):
        git(env[side], "fetch", "-q", "origin")
        git(env[side], "update-ref", "refs/heads/main", "refs/remotes/origin/main")


def lane_worktree(env, name, branch, start="main"):
    path = os.path.join(WORK, "origin", "wt-" + name)
    git(env["origin"], "worktree", "add", "-q", "-b", branch, path, start)
    return path


def push_gate(repo, gate, sha, branch, ok=True):
    out = subprocess.run(["git", "-C", repo, "push", "--porcelain", gate, "%s:refs/heads/%s" % (sha, branch)],
                         env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def records(name, extra=""):
    d = os.path.join(WORK, "records", name)
    os.makedirs(d)
    brief = os.path.join(d, "brief.md")
    status = os.path.join(d, "status.log")
    with open(brief, "w") as fh:
        fh.write("# Task %s\n\nFix the thing. Keep the change small.\n" % name)
    with open(status, "w") as fh:
        fh.write("working [at=1791489000]: started\n"
                 "done [at=1791489400]: committed on lane/%s, ready for review\n%s" % (name, extra))
    return brief, status


def reseal(root):
    """What an attacker can always do: recompute SHA256SUMS after editing."""
    import hashlib
    lines = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames.sort()
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            if rel != "SHA256SUMS":
                with open(os.path.join(root, rel), "rb") as fh:
                    lines.append("%s  %s\n" % (hashlib.sha256(fh.read()).hexdigest(), rel))
    with open(os.path.join(root, "SHA256SUMS"), "w") as fh:
        fh.write("".join(sorted(lines, key=lambda l: l.split("  ", 1)[1])))


def write_json(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2)
    return path


def scenario_a(env, st_o, st_t, out):
    say("\n== A. gate holds an older head; the lane was rebased away from it (the 2026-10-08 jam case)")
    wt = lane_worktree(env, "jam", "lane/jam")
    j1 = commit(wt, "src/fix.txt", "remove the debug print\n", "J1 remove debug print")
    ok_push, _ = push_gate(wt, env["ogate"], j1, "lane/jam")
    check("origin gate accepted the first head J1", ok_push)
    advance_main(env, "app v2\n", "M3 main moves on")
    git(wt, "rebase", "-q", "main")
    j1r = git(wt, "rev-parse", "HEAD")
    accepted, why = push_gate(wt, env["ogate"], j1r, "lane/jam")
    check("reproduced: the gate rejects the rebased head as non-fast-forward", not accepted,
          why.splitlines()[-1] if why else "")
    brief, status = records("jam", "blocked [at=1791510355]: push-to-gate rejected: gate holds the old head\n")
    pipeline = write_json(os.path.join(WORK, "records", "jam", "pipeline.json"), {
        "schema": "lodgings.pipeline/1", "source": "fixture",
        "run": {"id": "RUN-JAM-1", "status": "cancelled", "head": j1, "parked": False},
        "steps": [{"name": "review", "status": "completed"}, {"name": "test", "status": "failed"}]})
    origin = Origin(env["origin"], env["ogate"], [wt])
    rc, res = cli_json(st_o, "export", "--task", "jam", "--repo", env["origin"], "--worktree", wt,
                       "--branch", "lane/jam", "--base", "main", "--gate-git-dir", env["ogate"],
                       "--record", "brief.md=" + brief, "--record", "status.log=" + status,
                       "--pipeline", pipeline, "--out", out,
                       "--custody-check", json.dumps([sys.executable, SHIM, os.path.join(FIXTURES, "gate-check-ok.txt")]))
    check("export succeeds", rc == 0)
    check("custody is 'contained' (the gate's change is already in the rebased head)", res.get("custody") == "contained")
    origin.unchanged("after export")
    passport = res["passport"]
    rc, v = cli_json(st_t, "verify", passport, "--repo", env["target"])
    check("verify on the target passes", rc == 0 and v.get("prerequisites_ok") is True)
    # The target gate already holds the old head, exactly as on the origin.
    sh(["git", "--git-dir", env["tgate"], "fetch", "-q", env["ogate"], "refs/heads/lane/jam:refs/heads/lane/jam"])
    new_wt = os.path.join(WORK, "target", "wt-jam")
    rc, imp = cli_json(st_t, "import", passport, "--repo", env["target"], "--worktree", new_wt,
                       "--gate-git-dir", env["tgate"],
                       "--custody-check", json.dumps([sys.executable, SHIM, os.path.join(FIXTURES, "gate-check-ok.txt")]))
    check("import succeeds", rc == 0)
    bridge = imp.get("bridge")
    check("import made a bridge merge", bool(bridge))
    if bridge:
        parents = git(env["target"], "rev-list", "--parents", "-n", "1", bridge).split()[1:]
        check("bridge parents are (rebased lane head, old gate head)", parents == [j1r, j1], " ".join(p[:12] for p in parents))
        check("bridge tree equals the rebased lane tree (no content change)",
              git(env["target"], "rev-parse", bridge + "^{tree}") == git(env["origin"], "rev-parse", j1r + "^{tree}"))
        check("new worktree is at the bridge and clean",
              git(new_wt, "rev-parse", "HEAD") == bridge and git(new_wt, "status", "--porcelain") == "")
        accepted, why = push_gate(new_wt, env["tgate"], bridge, "lane/jam")
        check("the target gate fast-forwards to the resumed head (no hand-made descendant commit)", accepted, why.splitlines()[-1] if why else "")
    origin.unchanged("after import")
    rc, _ = cli(st_o, "confirm", passport, "--repo", env["origin"], "--gate-git-dir", env["ogate"])
    check("confirm on the origin succeeds", rc == 0)
    archived = refs(env["origin"])
    check("confirm archived the lane and gate heads",
          archived.get("refs/lodgings/archive/jam/1/lane/head") == j1r and archived.get("refs/lodgings/archive/jam/1/gate/head") == j1)
    origin.unchanged("after confirm (only archive refs added)", allow_prefix="refs/lodgings/archive/")
    rc, again = cli_json(st_o, "confirm", passport, "--repo", env["origin"])
    check("a second confirm is a no-op", rc == 0 and again.get("verdict") == "already-confirmed")
    return passport


def scenario_b(env, st_o, st_t, out):
    say("\n== B. a lane mid-pipeline: parked at the test gate after a review fix round")
    wt = lane_worktree(env, "mid", "lane/mid")
    commit(wt, "src/mid.txt", "feature part 1\n", "P1 feature part 1")
    p2 = commit(wt, "src/mid.txt", "feature part 1\nfeature part 2\n", "P2 feature part 2")
    push_gate(wt, env["ogate"], p2, "lane/mid")
    brief, status = records("mid", "paused [at=1791490531]: pipeline parked at the test gate\n")
    receipt = {
        "schema": "lodgings.pipeline/1", "source": "fixture",
        "run": {"id": "RUN-MID-1", "status": "running", "head": p2, "parked": True, "awaiting_since": 1791490500},
        "steps": [
            {"name": "review", "status": "completed",
             "rounds": [{"round": 1, "trigger": "initial"}, {"round": 2, "trigger": "auto_fix", "fix_summary": "renamed the flag"}],
             "findings": []},
            {"name": "test", "status": "fixing", "findings": [{"id": "test-1", "severity": "error",
                                                               "title": "floor failed: lobby page raised errors"}]},
            {"name": "push", "status": "pending"}],
        "open_findings": [{"id": "test-1", "severity": "error", "title": "floor failed: lobby page raised errors"}]}
    parked = write_json(os.path.join(WORK, "records", "mid", "pipeline-parked.json"), receipt)
    busy = dict(receipt, run=dict(receipt["run"], parked=False))
    busy_path = write_json(os.path.join(WORK, "records", "mid", "pipeline-busy.json"), busy)
    decisions = write_json(os.path.join(WORK, "records", "mid", "decisions.json"), {
        "schema": "lodgings.decisions/1",
        "items": [{"key": "run-mid-test", "asked": "test gate failed for env reasons", "answer": "fix with a clean install, no waiver"}]})
    origin = Origin(env["origin"], env["ogate"], [wt])
    common = ["--task", "mid", "--repo", env["origin"], "--worktree", wt, "--branch", "lane/mid", "--base", "main",
              "--gate-git-dir", env["ogate"], "--record", "brief.md=" + brief, "--record", "status.log=" + status,
              "--decisions", decisions, "--out", out]
    rc, res = cli_json(st_o, "export", *(common + ["--pipeline", busy_path]))
    check("refused while a step is executing (fixing, not parked)", rc == 3 and res.get("verdict") == "refused")
    check("refusal left no passport behind", not os.path.exists(os.path.join(out, "mid-1")))
    # The gate gets a pipeline fix commit the lane does not have yet.
    fixwt = os.path.join(WORK, "origin", "gate-fix")
    sh(["git", "clone", "-q", "--branch", "lane/mid", env["ogate"], fixwt])
    f = commit(fixwt, "src/mid.txt", "feature part 1\nfeature part 2 (fixed)\n", "F pipeline fix round")
    sh(["git", "-C", fixwt, "push", "-q", "origin", "lane/mid"])
    origin.snap = origin.take()
    rc, res = cli_json(st_o, "export", *(common + ["--pipeline", parked]))
    check("refused when the gate holds pipeline commits the lane lacks (custody behind)",
          rc == 3 and "behind" in res.get("reason", ""), res.get("hint", "")[:90])
    origin.unchanged("after refusals")
    # The custody return a review gate offers: fast-forward the lane to the gate head.
    git(wt, "fetch", "-q", env["ogate"], "lane/mid")
    git(wt, "merge", "-q", "--ff-only", "FETCH_HEAD")
    origin.snap = origin.take()
    rc, res = cli_json(st_o, "export", *(common + ["--pipeline", parked]))
    check("export succeeds once parked and in custody (equal)", rc == 0 and res.get("custody") == "equal")
    passport = res["passport"]
    origin.unchanged("after export")
    new_wt = os.path.join(WORK, "target", "wt-mid")
    rc, imp = cli_json(st_t, "import", passport, "--repo", env["target"], "--worktree", new_wt,
                       "--gate-git-dir", env["tgate"])
    check("import succeeds without a bridge", rc == 0 and not imp.get("bridge") and imp.get("resume_head") == f)
    resume = open(imp.get("resume_brief", os.devnull)).read() if rc == 0 else ""
    check("resume brief carries the open finding and the decision",
          "test-1" in resume and "no waiver" in resume and "RUN-MID-1" in resume)
    with open(os.path.join(passport, "pipeline.json")) as fh:
        check("pipeline receipt travelled intact (2 review rounds)", len(json.load(fh)["steps"][0]["rounds"]) == 2)
    origin.unchanged("after import")
    return passport


def scenario_c(env, st_o, st_t, out):
    say("\n== C. a lane with archive refs from earlier moves (one inside the base)")
    wt = lane_worktree(env, "arch", "lane/arch")
    old = commit(wt, "src/arch.txt", "first attempt\n", "R1 first attempt")
    git(env["origin"], "update-ref", "refs/archive/arch/r1", old)
    git(env["origin"], "update-ref", "refs/archive/arch/in-main", git(env["origin"], "rev-parse", "main~1"))
    git(wt, "reset", "-q", "--hard", "main")
    head = commit(wt, "src/arch.txt", "second attempt\n", "R2 second attempt")
    git(env["origin"], "update-ref", "refs/review-tool/sync/RUN-ARCH-7", head)
    origin = Origin(env["origin"], env["ogate"], [wt])
    rc, res = cli_json(st_o, "export", "--task", "arch", "--repo", env["origin"], "--worktree", wt, "--branch", "lane/arch",
                       "--base", "main", "--archive-ref", "refs/archive/arch/", "--archive-ref", "refs/review-tool/sync/RUN-ARCH-7",
                       "--out", out)
    check("export succeeds with no gate (custody no-gate)", rc == 0 and res.get("custody") == "no-gate")
    check("four refs carried (lane + three archive refs)", res.get("refs") == 4)
    passport = res["passport"]
    with open(os.path.join(passport, "manifest.json")) as fh:
        manifest = json.load(fh)
    check("the archive ref already in main is marked in_base, not bundled",
          [r["source"] for r in manifest["refs"] if r["in_base"]] == ["refs/archive/arch/in-main"])
    new_wt = os.path.join(WORK, "target", "wt-arch")
    rc, imp = cli_json(st_t, "import", passport, "--repo", env["target"], "--worktree", new_wt)
    check("import succeeds", rc == 0)
    t = refs(env["target"])
    pre = "refs/lodgings/import/arch/1/archive/"
    check("every archive ref landed with its exact value",
          t.get(pre + "archive/arch/r1") == old and t.get(pre + "archive/arch/in-main") == manifest["refs"][1]["sha"]
          and t.get(pre + "review-tool/sync/RUN-ARCH-7") == head,
          ", ".join(sorted(k[len(pre):] for k in t if k.startswith(pre))))
    origin.unchanged("after import")
    return passport


def scenario_d(env, st_o, st_t, out, passports):
    say("\n== D. refusals: every unsafe twin changes nothing")
    # D1 dirty worktree
    wt = lane_worktree(env, "dirty", "lane/dirty")
    commit(wt, "src/d.txt", "one\n", "D1")
    with open(os.path.join(wt, "src/d.txt"), "a") as fh:
        fh.write("uncommitted\n")
    origin = Origin(env["origin"], env["ogate"], [wt])
    rc, res = cli_json(st_o, "export", "--task", "dirty", "--repo", env["origin"], "--worktree", wt,
                       "--branch", "lane/dirty", "--base", "main", "--out", out)
    check("D1 dirty worktree refused", rc == 3 and "uncommitted" in res.get("reason", ""))
    origin.unchanged("after D1")
    # D2 diverged gate
    wt2 = lane_worktree(env, "div", "lane/div")
    base = commit(wt2, "src/div.txt", "shared\n", "V1 shared")
    gfix = os.path.join(WORK, "origin", "gate-div")
    push_gate(wt2, env["ogate"], base, "lane/div")
    sh(["git", "clone", "-q", "--branch", "lane/div", env["ogate"], gfix])
    commit(gfix, "src/gate-only.txt", "work only the gate has\n", "V2 gate-only work")
    sh(["git", "-C", gfix, "push", "-q", "origin", "lane/div"])
    commit(wt2, "src/div.txt", "shared\nlane only\n", "V3 lane-only work")
    origin = Origin(env["origin"], env["ogate"], [wt2])
    rc, res = cli_json(st_o, "export", "--task", "div", "--repo", env["origin"], "--worktree", wt2,
                       "--branch", "lane/div", "--base", "main", "--gate-git-dir", env["ogate"], "--out", out)
    check("D2 diverged gate refused (it holds work the lane lacks)", rc == 3 and "diverged" in res.get("reason", ""))
    origin.unchanged("after D2")
    # D3 secret in a record
    brief, status = records("secret", "note: api_key = %s\n" % ("Q" * 24))
    rc, res = cli_json(st_o, "export", "--task", "secret", "--repo", env["origin"], "--branch", "lane/div",
                       "--base", "main", "--record", "status.log=" + status, "--out", out)
    check("D3 a record holding a secret is refused, and the secret is not echoed",
          rc == 3 and "secret" in res.get("reason", "") and "QQQQ" not in json.dumps(res))
    check("D3 left no passport behind", not os.path.exists(os.path.join(out, "secret-1")))
    # D4 tampered passport
    tampered = os.path.join(WORK, "tampered")
    shutil.copytree(passports["mid"], tampered)
    with open(os.path.join(tampered, "records", "brief.md"), "a") as fh:
        fh.write("an extra instruction slipped in\n")
    before = refs(env["target"])
    rc, res = cli_json(st_t, "verify", tampered)
    check("D4 tampered passport fails verify (exit 4)", rc == 4 and "checksum mismatch" in res.get("reason", ""))
    rc, res = cli_json(st_t, "import", tampered, "--repo", env["target"], "--worktree", os.path.join(WORK, "target", "wt-tampered"))
    check("D4 tampered passport import refused, target untouched", rc == 4 and refs(env["target"]) == before
          and not os.path.exists(os.path.join(WORK, "target", "wt-tampered")))
    # D5 target branch exists with different content
    git(env["target"], "branch", "lane/arch-taken", "main")
    before = refs(env["target"])
    rc, res = cli_json(st_t, "import", passports["arch"], "--repo", env["target"], "--branch", "lane/arch-taken",
                       "--worktree", os.path.join(WORK, "target", "wt-taken"), "--set", "import_prefix=refs/lodgings/import2")
    check("D5 an existing target branch with other content is refused, target untouched",
          rc == 3 and refs(env["target"]) == before)
    # D6 the target lacks the base
    rc, res = cli_json(st_t, "verify", passports["mid"], "--repo", env["stale"])
    check("D6 a target that lacks the base commit fails verify with a fetch hint",
          rc == 4 and "base" in res.get("hint", ""))
    # D7 confirm without a receipt, and after the origin moved
    rc, res = cli_json(st_o, "confirm", passports["arch"], "--repo", env["origin"], "--receipt", os.path.join(WORK, "no-such-receipt.json"))
    check("D7 confirm without the target's receipt is refused", rc == 3)
    wt_arch = os.path.join(WORK, "origin", "wt-arch")
    commit(wt_arch, "src/arch.txt", "third attempt after export\n", "R3 work after export")
    before = refs(env["origin"])
    rc, res = cli_json(st_o, "confirm", passports["arch"], "--repo", env["origin"])
    check("D7 confirm after the origin lane gained commits is refused (they would be stranded)",
          rc == 3 and "stranded" in res.get("reason", "") and refs(env["origin"]) == before)
    # D9 a hostile passport that re-seals itself after naming a ref outside the import prefix
    hostile = os.path.join(WORK, "hostile")
    shutil.copytree(passports["arch"], hostile, ignore=shutil.ignore_patterns("after"))
    with open(os.path.join(hostile, "manifest.json")) as fh:
        manifest = json.load(fh)
    manifest["refs"][0]["bundle_ref"] = "refs/passport/../../heads/main"
    with open(os.path.join(hostile, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)
    reseal(hostile)
    before = refs(env["target"])
    rc, res = cli_json(st_t, "import", hostile, "--repo", env["target"], "--worktree", os.path.join(WORK, "target", "wt-hostile"))
    check("D9 a re-sealed passport naming an unsafe ref is invalid, target untouched",
          rc == 4 and "safe ref name" in res.get("reason", "") and refs(env["target"]) == before)
    # D8 kill switch
    off_state = os.path.join(WORK, "state-off")
    os.makedirs(off_state)
    open(os.path.join(off_state, "off"), "w").close()
    rc, res = cli_json(off_state, "export", "--task", "off", "--repo", env["origin"], "--branch", "lane/div", "--out", out)
    check("D8 kill switch: exit 0, did nothing", rc == 0 and res.get("verdict") == "off" and not os.path.exists(os.path.join(out, "off-1")))


def refs_tree(gate, sha):
    return sh(["git", "--git-dir", gate, "rev-parse", sha + "^{tree}"])


def gate_copy(env, lane_branch, name, how):
    """What a review pipeline leaves on the gate: the lane's changes on the current main, squashed or rebased."""
    d = os.path.join(WORK, "pipeline-" + name)
    sh(["git", "clone", "-q", env["up"], d])
    git(d, "fetch", "-q", env["origin"], "%s:refs/heads/%s" % (lane_branch, lane_branch))
    if how == "squash":
        git(d, "merge", "-q", "--squash", lane_branch)
        git(d, "commit", "-q", "-m", "squash %s" % name)
    else:
        git(d, "checkout", "-q", lane_branch)
        git(d, "rebase", "-q", "origin/main")
    head = git(d, "rev-parse", "HEAD")
    sh(["git", "-C", d, "push", "-q", env["ogate"], "HEAD:refs/heads/" + lane_branch])
    return head


def scenario_e(env, st_o, st_t, out):
    say("\n== E. the review gate holds a squash / a rebase of the lane on a newer main (custody diverged)")
    passports = {}
    for name, how in (("sq", "squash"), ("rb", "rebase")):
        branch = "lane/" + name
        wt = lane_worktree(env, name, branch)
        commit(wt, "src/%s.txt" % name, "first part\n", "S1 %s first part" % name)
        lane = commit(wt, "src/%s.txt" % name, "first part\nsecond part\n", "S2 %s second part" % name)
        advance_main(env, "app %s\n" % name, "M main moves under %s" % name)
        gate = gate_copy(env, branch, name, how)
        origin = Origin(env["origin"], env["ogate"], [wt])
        argv = ["export", "--task", name, "--repo", env["origin"], "--worktree", wt, "--branch", branch,
                "--base", "main", "--gate-git-dir", env["ogate"], "--out", out]
        rc, res = cli_json(st_o, *argv)
        reason, hint = res.get("reason", ""), res.get("hint", "")
        check("E %s: refused by default (custody diverged), as the field test saw" % how,
              rc == 3 and "custody diverged" in reason)
        check("E %s: the refusal says which side is ahead and gives both commit counts" % how,
              "neither side is ahead" in reason and "has 2 commit(s) the gate lacks" in reason
              and "commit(s) the lane lacks" in reason,
              reason[:140])
        ev = res.get("evidence") or {}
        check("E %s: the refusal's evidence carries both counts and says the gate holds the lane's changes" % how,
              ev.get("lane_only_commits") == 2 and ev.get("gate_only_commits", 0) >= 2
              and ev.get("gate_holds_lane_changes") is True and ev.get("ahead") == "neither")
        check("E %s: the refusal names the way out (--adopt-gate-head) and the alternatives" % how,
              "--adopt-gate-head" in hint and "custody-return" in hint and "by hand" in hint)
        origin.unchanged("after the refused export (%s)" % how)

        rc, res = cli_json(st_o, *(argv + ["--adopt-gate-head"]))
        ev = res.get("evidence") or {}
        token = ev.get("token", "")
        check("E %s: --adopt-gate-head alone refuses and lists what only the lane holds" % how,
              rc == 3 and "adopt-gate-head" in res.get("reason", "") and token
              and "S2 %s second part" % name in json.dumps(ev.get("lane_only_commits")),
              res.get("reason", "")[:100])
        check("E %s: it lists the paths that differ, and prints the token to confirm them" % how,
              ev.get("tree_differences", {}).get("count", 0) >= 1 and token in res.get("hint", ""))
        origin.unchanged("after --adopt-gate-head without confirmation (%s)" % how)
        check("E %s: nothing was left behind" % how, not os.path.exists(os.path.join(out, "%s-1" % name)))

        rc, res = cli_json(st_o, *(argv + ["--adopt-gate-head", "--confirm-differences", "0123456789abcdef"]))
        check("E %s: a token that is not the printed one is refused" % how, rc == 3 and "another head" in res.get("reason", ""))
        rc, res = cli_json(st_o, "export", "--task", name, "--repo", env["origin"], "--branch", branch,
                           "--gate-git-dir", env["ogate"], "--confirm-differences", token, "--out", out)
        check("E %s: a token without --adopt-gate-head is a usage error" % how, rc == 2)

        rc, res = cli_json(st_o, *(argv + ["--adopt-gate-head", "--confirm-differences", token]))
        check("E %s: export succeeds with the named flag and the confirmed token" % how,
              rc == 0 and res.get("custody") == "diverged" and (res.get("adopted") or {}).get("resume_head") == gate)
        origin.unchanged("after the adopting export (%s)" % how)
        passport = res["passport"]
        with open(os.path.join(passport, "manifest.json")) as fh:
            manifest = json.load(fh)
        adopt = manifest.get("adopt") or {}
        check("E %s: the passport records both heads, the digest and that the differences were confirmed" % how,
              adopt.get("lane_head") == lane and adopt.get("gate_head") == gate and adopt.get("confirmed") is True
              and adopt.get("tree_equal") is False and str(adopt.get("digest", "")).startswith("sha256:")
              and manifest.get("lane_head") == lane and manifest.get("gate_head") == gate
              and manifest.get("needs_bridge") is False)
        rc, v = cli_json(st_t, "verify", passport, "--repo", env["target"])
        check("E %s: verify passes and reports the adoption" % how, rc == 0 and (v.get("adopted") or {}).get("resume_head") == gate)
        new_wt = os.path.join(WORK, "target", "wt-" + name)
        rc, imp = cli_json(st_t, "import", passport, "--repo", env["target"], "--worktree", new_wt,
                           "--gate-git-dir", env["tgate"])
        check("E %s: import resumes at the gate head, with no bridge merge" % how,
              rc == 0 and imp.get("resume_head") == gate and not imp.get("bridge")
              and git(new_wt, "rev-parse", "HEAD") == gate)
        check("E %s: the worktree has the gate head's tree and is clean" % how,
              git(new_wt, "rev-parse", "HEAD^{tree}") == refs_tree(env["ogate"], gate)
              and git(new_wt, "status", "--porcelain") == "")
        check("E %s: the lane's own commits are kept on the target under the import refs" % how,
              refs(env["target"]).get("refs/lodgings/import/%s/1/lane/head" % name) == lane)
        commit(new_wt, "src/%s-next.txt" % name, "next step\n", "N1 next step on the adopted head")
        accepted, why = push_gate(new_wt, env["ogate"], git(new_wt, "rev-parse", "HEAD"), branch)
        check("E %s: the gate fast-forwards to a commit made on the adopted head" % how, accepted, why.splitlines()[-1] if why else "")
        with open(os.path.join(passport, "after", "resume.md")) as fh:
            resume = fh.read()
        check("E %s: resume.md says the gate head was adopted and where the lane's commits are" % how,
              "Adopted the gate head" in resume and "archived by confirm" in resume)
        rc, conf = cli_json(st_o, "confirm", passport, "--repo", env["origin"])
        check("E %s: confirm archives the lane's own head" % how,
              rc == 0 and refs(env["origin"]).get("refs/lodgings/archive/%s/1/lane/head" % name) == lane)
        passports[name] = passport
    return passports


def scenario_f(env, st_o, st_t, out, passports):
    say("\n== F. adopt guards: identical trees, a genuinely diverged gate, a hostile passport")
    # F1 the gate holds a squash of the lane on the SAME base: identical trees, no confirmation needed.
    wt = lane_worktree(env, "same", "lane/same")
    commit(wt, "src/same.txt", "one\n", "T1 same one")
    lane = commit(wt, "src/same.txt", "one\ntwo\n", "T2 same two")
    gate = gate_copy(env, "lane/same", "same", "squash")
    check("F1 the squash has the lane's tree but another history",
          refs_tree(env["ogate"], gate) == git(env["origin"], "rev-parse", lane + "^{tree}")
          and gate != lane)
    origin = Origin(env["origin"], env["ogate"], [wt])
    argv = ["export", "--task", "same", "--repo", env["origin"], "--worktree", wt, "--branch", "lane/same",
            "--base", "main", "--gate-git-dir", env["ogate"], "--out", out]
    rc, res = cli_json(st_o, *argv)
    check("F1 without the flag it is `contained`, as before (a bridge merge)", rc == 0 and res.get("custody") == "contained")
    shutil.rmtree(res["passport"])
    rc, res = cli_json(st_o, *(argv + ["--adopt-gate-head"]))
    check("F1 with the flag the trees are identical: adopted with no token",
          rc == 0 and (res.get("adopted") or {}).get("tree_equal") is True and (res.get("adopted") or {}).get("tree_differences") == 0)
    origin.unchanged("after F1")
    rc, imp = cli_json(st_t, "import", res["passport"], "--repo", env["target"], "--worktree", os.path.join(WORK, "target", "wt-same"))
    check("F1 import resumes at the gate head itself, not at a bridge merge",
          rc == 0 and imp.get("resume_head") == gate and not imp.get("bridge"))

    # F2 --adopt-gate-head with a gate that holds nothing for the lane
    rc, res = cli_json(st_o, "export", "--task", "nogate", "--repo", env["origin"], "--branch", "lane/same",
                       "--gate-git-dir", env["tgate"], "--adopt-gate-head", "--out", out)
    check("F2 adopting when the gate holds no head is refused", rc == 3 and "nothing to adopt" in res.get("reason", ""))

    # F3 the gate holds work the lane lacks AND the lane holds work the gate lacks: all of it is listed
    wt3 = lane_worktree(env, "both", "lane/both")
    shared = commit(wt3, "src/both.txt", "shared\n", "B1 shared")
    push_gate(wt3, env["ogate"], shared, "lane/both")
    gfix = os.path.join(WORK, "origin", "gate-both")
    sh(["git", "clone", "-q", "--branch", "lane/both", env["ogate"], gfix])
    commit(gfix, "src/gate-only.txt", "work only the gate has\n", "B2 gate-only work")
    sh(["git", "-C", gfix, "push", "-q", "origin", "lane/both"])
    commit(wt3, "src/both.txt", "shared\nlane only\n", "B3 lane-only work")
    origin = Origin(env["origin"], env["ogate"], [wt3])
    argv3 = ["export", "--task", "both", "--repo", env["origin"], "--worktree", wt3, "--branch", "lane/both",
             "--base", "main", "--gate-git-dir", env["ogate"], "--out", out]
    rc, res = cli_json(st_o, *argv3)
    check("F3 diverged, and the refusal does not call it a squash",
          rc == 3 and "custody diverged" in res.get("reason", "") and "squash or a rebase" not in res.get("reason", "")
          and "1 commit(s) the gate lacks" in res.get("reason", ""))
    rc, res = cli_json(st_o, *(argv3 + ["--adopt-gate-head"]))
    ev = res.get("evidence") or {}
    listed = json.dumps(ev.get("lane_only_commits"))
    check("F3 adopting lists the lane-only commit and the lane-only change, and refuses",
          rc == 3 and "B3 lane-only work" in listed and ev.get("gate_holds_lane_changes") is False
          and any(e.get("path") == "src/both.txt" for e in ev.get("tree_differences", {}).get("entries", [])))
    origin.unchanged("after F3")
    rc, res2 = cli_json(st_o, *(argv3 + ["--adopt-gate-head", "--confirm-differences", ev.get("token", "-")]))
    check("F3 the lane's work is dropped from the resume only after that exact confirmation, and is still carried",
          rc == 0 and (res2.get("adopted") or {}).get("lane_only_commits") == 1
          and refs(env["origin"]).get("refs/heads/lane/both") is not None)
    with open(os.path.join(res2["passport"], "manifest.json")) as fh:
        both = json.load(fh)
    check("F3 the passport's bundle carries the lane head (the lane-only commit is not lost)",
          any(r["kind"] == "lane" and r["sha"] == both["lane_head"] for r in both["refs"])
          and any(r["kind"] == "gate" for r in both["refs"]))
    # a head that moved after the token was printed invalidates it
    commit(wt3, "src/both.txt", "shared\nlane only\nmore\n", "B4 more lane work after the token")
    rc, res = cli_json(st_o, *(argv3 + ["--adopt-gate-head", "--confirm-differences", ev.get("token", "-")]))
    check("F3 a token does not survive a lane head that moved after it was printed",
          rc == 3 and "another head" in res.get("reason", ""))

    # F4 a re-sealed hostile passport that claims a confirmation it does not match
    hostile = os.path.join(WORK, "hostile-adopt")
    shutil.copytree(passports["sq"], hostile, ignore=shutil.ignore_patterns("after"))
    with open(os.path.join(hostile, "manifest.json")) as fh:
        manifest = json.load(fh)
    manifest["adopt"]["digest"] = "sha256:" + "0" * 64
    with open(os.path.join(hostile, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)
    reseal(hostile)
    before = refs(env["target"])
    rc, res = cli_json(st_t, "import", hostile, "--repo", env["target"], "--worktree", os.path.join(WORK, "target", "wt-hostile-adopt"),
                       "--set", "import_prefix=refs/lodgings/import-h")
    check("F4 a re-sealed passport whose adopt digest does not match the objects is invalid, target untouched",
          rc == 4 and "adopt record does not match" in res.get("reason", "") and refs(env["target"]) == before)
    unconfirmed = os.path.join(WORK, "unconfirmed-adopt")
    shutil.copytree(passports["rb"], unconfirmed, ignore=shutil.ignore_patterns("after"))
    with open(os.path.join(unconfirmed, "manifest.json")) as fh:
        manifest = json.load(fh)
    manifest["adopt"]["confirmed"] = False
    with open(os.path.join(unconfirmed, "manifest.json"), "w") as fh:
        json.dump(manifest, fh)
    reseal(unconfirmed)
    rc, res = cli_json(st_t, "verify", unconfirmed)
    check("F4 a passport that adopts differing trees without a confirmation is invalid", rc == 4 and "not confirmed" in res.get("reason", ""))


def scenario_g(env, st_o, st_t, out):
    say("\n== G. a lane branch that has no worktree")
    wt = lane_worktree(env, "gone", "lane/gone")
    commit(wt, "src/gone.txt", "work\n", "G1 work")
    git(env["origin"], "worktree", "remove", wt)
    origin = Origin(env["origin"], env["ogate"], [])
    rc, res = cli_json(st_o, "export", "--task", "gone", "--repo", env["origin"], "--branch", "lane/gone",
                       "--base", "main", "--out", out)
    check("G1 export works with no worktree named", rc == 0, res.get("reason", ""))
    rc, res = cli_json(st_o, "export", "--task", "gone", "--repo", env["origin"], "--worktree", wt, "--branch", "lane/gone",
                       "--base", "main", "--out", out)
    check("G2 export works when the named worktree no longer exists, and says it checked nothing",
          rc == 0, res.get("reason", ""))
    with open(os.path.join(res["passport"], "custody.json")) as fh:
        wtdoc = json.load(fh)["worktree"]
    check("G2 the passport records that the worktree was not checked, and why",
          wtdoc.get("checked") is False and "checked out nowhere" in wtdoc.get("why", ""))
    origin.unchanged("after G")
    # The named path is gone, but the branch is checked out somewhere else, and that one is dirty: still refused.
    elsewhere = os.path.join(WORK, "origin", "wt-elsewhere")
    git(env["origin"], "worktree", "add", "-q", elsewhere, "lane/gone")
    with open(os.path.join(elsewhere, "src", "gone.txt"), "a") as fh:
        fh.write("uncommitted\n")
    rc, res = cli_json(st_o, "export", "--task", "gone", "--repo", env["origin"], "--worktree", wt, "--branch", "lane/gone",
                       "--base", "main", "--out", out)
    check("G3 a missing path does not hide a dirty checkout of the branch elsewhere",
          rc == 3 and "uncommitted" in res.get("reason", ""))
    git(env["origin"], "worktree", "remove", "--force", elsewhere)


def main():
    global WORK
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--work", required=True, help="empty or absent directory for the throwaway repositories")
    ap.add_argument("--log", help="also write the run log here")
    ap.add_argument("--keep", action="store_true", help="keep --work afterwards")
    args = ap.parse_args()
    WORK = os.path.abspath(args.work)
    if os.path.exists(WORK) and os.listdir(WORK):
        sys.exit("--work must be empty or absent: %s" % WORK)
    os.makedirs(WORK, exist_ok=True)
    started = time.time()
    rc, out = 0, ""
    say("lodgings-passport e2e · %s · python %s · %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                       sys.version.split()[0], sh(["git", "--version"])))
    try:
        env = setup(WORK)
        st_o, st_t = os.path.join(WORK, "state-origin"), os.path.join(WORK, "state-target")
        out_dir = os.path.join(WORK, "passports")
        passports = {}
        passports["jam"] = scenario_a(env, st_o, st_t, out_dir)
        passports["mid"] = scenario_b(env, st_o, st_t, out_dir)
        passports["arch"] = scenario_c(env, st_o, st_t, out_dir)
        scenario_d(env, st_o, st_t, out_dir, passports)
        passports.update(scenario_e(env, st_o, st_t, out_dir))
        scenario_f(env, st_o, st_t, out_dir, passports)
        scenario_g(env, st_o, st_t, out_dir)
        with open(os.path.join(st_o, "actions.jsonl")) as fh:
            lines = [json.loads(l) for l in fh]
        check("actions log has one line per origin call, refusals included",
              len(lines) >= 10 and any(l.get("verdict") == "refused" for l in lines))
    except Exception as exc:  # report, never hide
        check("run completed without an unexpected error", False, repr(exc))
        rc = 1
    passed = sum(1 for _, ok in RESULTS if ok)
    say("\n%d/%d checks passed in %.1f s" % (passed, len(RESULTS), time.time() - started))
    if passed != len(RESULTS):
        rc = 1
    if args.log:
        with open(args.log, "w") as fh:
            fh.write("\n".join(LOG) + "\n")
    if not args.keep and rc == 0:
        shutil.rmtree(WORK, ignore_errors=True)
    return rc


WORK = ""

if __name__ == "__main__":
    sys.exit(main())
