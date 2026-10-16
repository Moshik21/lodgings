#!/usr/bin/env python3
"""Three-OS smoke: start, one real check, --json, and every stable exit code. About two seconds.

  python3 tests/smoke.py

Runs the public commands only (bin/lodgings-passport, scripts/self-audit) in a
temporary directory it deletes afterwards. Portable: macOS, Windows (py -3),
Linux and WSL; needs git 2.38+ on PATH.
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin", "lodgings-passport")
AUDIT = os.path.join(ROOT, "scripts", "self-audit")
ENV = dict(os.environ, GIT_AUTHOR_NAME="smoke", GIT_AUTHOR_EMAIL="smoke@example.invalid", GIT_COMMITTER_NAME="smoke",
           GIT_COMMITTER_EMAIL="smoke@example.invalid", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
ENV.pop("LODGINGS_PASSPORT", None)
FAILS = []


def run(argv, env=None):
    p = subprocess.run(argv, env=env or ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return p.returncode, p.stdout + p.stderr


def expect(name, argv, code, contains=None, env=None):
    rc, out = run(argv, env)
    ok = rc == code and (contains is None or contains in out)
    print("[%s] %s (exit %d%s)" % ("PASS" if ok else "FAIL", name, rc, "" if ok else ", wanted %d: %s" % (code, out.strip()[:300])))
    if not ok:
        FAILS.append(name)
    return out


def git_out(repo, args, input_bytes=None):
    return subprocess.run(["git", "-C", repo] + args, env=ENV, input=input_bytes, stdout=subprocess.PIPE,
                          check=True).stdout.strip()


def hostile_text_case(py, tmp, repo, state):
    """Repository-derived text never reaches the terminal as a control character or an escape sequence.

    A lane-only commit subject and a file name carry an ESC[31m sequence, a BEL, an OSC title and a C1 CSI; so does a
    branch name given on the command line. The refusals and the adoption listing show them as \\xNN, never raw.
    """
    nasty = "\x1b[31mred\x1b[0m \x07bell \x1b]0;pwned\x07 \u009bcsi \u202eevil"
    base = git_out(repo, ["rev-parse", "main"]).decode()
    blob = git_out(repo, ["hash-object", "-w", "--stdin"], b"lane only\n").decode()
    listing = subprocess.run(["git", "-C", repo, "ls-tree", "-z", base], env=ENV, stdout=subprocess.PIPE,
                             check=True).stdout
    entry = ("100644 blob %s\t%s\0" % (blob, nasty)).encode("utf-8")  # a name git accepts in a tree, a worktree may not
    lane_tree = git_out(repo, ["mktree", "-z"], listing + entry).decode()
    lane = git_out(repo, ["commit-tree", lane_tree, "-p", base, "-m", "lane work " + nasty]).decode()
    gate_tree = git_out(repo, ["mktree", "-z"], listing + ("100644 blob %s\tgate.txt\0" % blob).encode()).decode()
    gate = git_out(repo, ["commit-tree", gate_tree, "-p", base, "-m", "gate work"]).decode()
    branch = "lane/evil"
    git_out(repo, ["update-ref", "refs/heads/" + branch, lane])
    git_out(repo, ["update-ref", "refs/heads/gate/evil", gate])
    argv = [py, BIN, "export", "--task", "evil", "--repo", repo, "--branch", branch, "--out", os.path.join(tmp, "evil-out"),
            "--gate-git-dir", os.path.join(repo, ".git"), "--gate-ref", "refs/heads/gate/evil"] + state
    runs = {
        "export refusal": (argv, 3),
        "adopt listing": (argv + ["--adopt-gate-head"], 3),
        "unknown branch": ([py, BIN, "export", "--task", "evil", "--repo", repo, "--branch", "x\x1b[31m\x07\ry",
                            "--out", os.path.join(tmp, "evil-out")] + state, 2),
    }
    seen = {}
    for name, (cmd, code) in runs.items():
        p = subprocess.run(cmd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        seen[name] = (p.stdout + p.stderr).replace(b"\r\n", b"\n")
        if p.returncode != code:
            FAILS.append("hostile text: %s exited %d, wanted %d" % (name, p.returncode, code))
    raw = [n for n, out in seen.items() if re.search(b"[\x1b\x07\x9b]|\r|\xe2\x80\xae", out)]
    shown = [n for n, token in (("adopt listing", b"\\x1b[31mred"), ("adopt listing", b"\\x07bell"),
                                ("adopt listing", b"\\x1b]0;pwned"), ("adopt listing", b"\\x9bcsi"),
                                ("adopt listing", b"\\u202eevil"),
                                ("unknown branch", b"x\\x1b[31m\\x07\\ry")) if token not in seen[n]]
    js = subprocess.run(argv + ["--adopt-gate-head", "--json"], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        json.loads(js.stdout.decode("utf-8"))
        json_ok = b"\x1b" not in js.stdout
    except ValueError:
        json_ok = False
    ok = not raw and not shown and not FAILS and json_ok
    print("[%s] repository-derived text is escaped, never raw (%s)" % (
        "PASS" if ok else "FAIL", "ok" if ok else "raw in: %s; not shown: %s; json ok: %s" % (raw, shown, json_ok)))
    if not ok:
        FAILS.append("hostile text reached the terminal")


def main():
    print("lodgings-passport smoke · %s %s · python %s" % (platform.system(), platform.release(), sys.version.split()[0]))
    tmp = tempfile.mkdtemp(prefix="passport-smoke-")
    py = sys.executable
    state = ["--state", os.path.join(tmp, "state")]
    try:
        expect("--version", [py, BIN, "--version"], 0, "lodgings-passport 0.")
        expect("no verb is a usage error", [py, BIN], 2)
        expect("bad task id is a usage error", [py, BIN, "export", "--task", "../x", "--repo", tmp, "--branch", "b",
                                                "--out", tmp] + state, 2)
        out = expect("missing passport is invalid, as JSON", [py, BIN, "verify", os.path.join(tmp, "nope"), "--json"] + state, 4)
        try:
            json.loads(out)
        except ValueError:
            FAILS.append("verify --json is not JSON")
        expect("kill switch exits 0 and does nothing", [py, BIN, "export", "--task", "t", "--repo", tmp, "--branch", "b",
                                                        "--out", tmp] + state, 0, "off", env=dict(ENV, LODGINGS_PASSPORT="off"))
        repo = os.path.join(tmp, "repo")
        subprocess.check_call(["git", "init", "-q", "-b", "main", repo], env=ENV)
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        subprocess.check_call(["git", "-C", repo, "add", "a.txt"], env=ENV)
        subprocess.check_call(["git", "-C", repo, "commit", "-q", "-m", "a"], env=ENV)
        subprocess.check_call(["git", "-C", repo, "branch", "lane/smoke"], env=ENV)
        out_dir = os.path.join(tmp, "out")
        expect("export one lane", [py, BIN, "export", "--task", "smoke", "--repo", repo, "--branch", "lane/smoke",
                                   "--out", out_dir] + state, 0, "exported")
        expect("verify it against its own repo", [py, BIN, "verify", os.path.join(out_dir, "smoke-1"), "--repo", repo] + state,
               0, "verified")
        with open(os.path.join(out_dir, "smoke-1", "manifest.json"), "a") as fh:
            fh.write(" ")
        expect("a touched passport fails verify", [py, BIN, "verify", os.path.join(out_dir, "smoke-1")] + state, 4, "seal")
        expect("a second export of the same seq is refused", [py, BIN, "export", "--task", "smoke", "--repo", repo,
                                                              "--branch", "lane/smoke", "--seq", "1", "--out", out_dir] + state, 3)
        hostile_text_case(py, tmp, repo, state)
        # The self-audit, as a test: a planted tree fails, a clean tree passes, no denylist is not a pass.
        deny = os.path.join(tmp, "deny.txt")
        with open(deny, "w") as fh:
            fh.write("# private\nplanted-private-name\n")
        planted = os.path.join(tmp, "planted")
        os.makedirs(planted)
        with open(os.path.join(planted, "notes.md"), "w") as fh:
            fh.write("planted-private-name\nwrite to some.one@" + "corp.example.org.uk\n" + "/" + "home/alice/x\n" +
                     "100." + "101.2.3\n" + "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n")
        out = expect("self-audit fails a planted tree", [py, AUDIT, "--denylist", deny, planted], 1)
        for rule in ("denylist term #1", "email address", "home path", "CGNAT address", "private key"):
            if rule not in out:
                FAILS.append("self-audit missed: %s" % rule)
                print("[FAIL] self-audit missed: %s" % rule)
        clean = os.path.join(tmp, "clean")
        os.makedirs(clean)
        with open(os.path.join(clean, "ok.md"), "w") as fh:
            fh.write("nothing private here; mail to dev@example.invalid\n")
        expect("self-audit passes a clean tree", [py, AUDIT, "--denylist", deny, clean], 0, "clean")
        expect("self-audit without a denylist is not a pass", [py, AUDIT, "--denylist", os.path.join(tmp, "none"), clean], 2,
               env=dict(ENV, LODGINGS_AUDIT_DENYLIST="", HOME=tmp, USERPROFILE=tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("smoke: %s" % ("ok" if not FAILS else "FAILED: " + ", ".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
