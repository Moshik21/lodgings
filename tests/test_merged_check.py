"""scripts/merged-check on a scratch git repository with synthetic fixtures.

    python3 -m unittest discover -s tests

The scratch repository holds copies of the two scripts and a trivial passing test. Standard library only, no network
(the base is the local branch `main`, which merged-check does not fetch).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


class MergedCheck(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = Path(tmp.name) / "tmp"  # merged-check puts its clone here (via TMPDIR), never in the repository
        self.scratch.mkdir()
        self.repo = Path(tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        for name in ("self-audit", "merged-check"):
            (self.repo / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
        (self.repo / "tests" / "test_ok.py").write_text("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        (self.repo / "doc.md").write_text("The pool keeps a list.\n")
        self.git("init", "-q")
        self.git("checkout", "-q", "-b", "main")
        self.commit("base")

    def git(self, *args):
        out = subprocess.run(["git"] + list(args), cwd=str(self.repo), capture_output=True, text=True, env=dict(os.environ, **GIT_ENV))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def branch(self, name, edit):
        self.git("checkout", "-q", "-b", name, "main")
        edit()
        self.commit(name)
        self.git("checkout", "-q", "main")

    def write(self, name, text):
        (self.repo / name).write_text(text)

    def rule(self):
        """A rule change: the self-audit starts to reject the word `widget` (alone it adds no finding: the script scans itself, so the word is spelled in two halves)."""
        path = self.repo / "scripts" / "self-audit"
        path.write_text(path.read_text().replace("PRIVATE_NET = [w for w in (", 'PRIVATE_NET = [w for w in ("wid" + "get", '))

    def run_check(self, *args, env=None):
        extra = env or {}
        env = dict(os.environ, TMPDIR=str(self.scratch), TEMP=str(self.scratch), TMP=str(self.scratch))
        env.pop("LODGINGS_AUDIT_DENYLIST", None)
        env.update(extra)
        before = self.git("branch", "--show-current")
        out = subprocess.run([sys.executable, str(self.repo / "scripts" / "merged-check"), "--base", "main"] + list(args),
                             cwd=str(self.repo), capture_output=True, text=True, env=env)
        self.assertEqual(self.git("branch", "--show-current"), before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(len(self.git("worktree", "list").splitlines()), 1)
        self.assertEqual(list(self.scratch.iterdir()), [], "the scratch clone was left behind")
        return out

    def test_clean_merge_passes(self):
        self.branch("a", lambda: self.write("a.md", "A note.\n"))
        self.branch("b", lambda: self.write("b.md", "Another note.\n"))
        self.git("checkout", "-q", "a")  # the caller's branch is neither the base nor the head, and stays as it is
        out = self.run_check("--head", "b")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "merged-check: ok\n")

    def test_each_branch_clean_alone_but_merged_result_fails_the_audit(self):
        self.branch("rule", self.rule)
        self.branch("wording", lambda: self.write("notes.md", "A widget list.\n"))
        for head in ("rule", "wording"):
            alone = self.run_check("--head", head)
            self.assertEqual(alone.returncode, 0, head + ": " + alone.stdout + alone.stderr)
        self.git("merge", "-q", "--no-edit", "rule")  # main now has the rule; the wording branch was cut before it
        out = self.run_check("--head", "wording")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(len(out.stdout.splitlines()), 1)
        self.assertTrue(out.stdout.startswith("merged-check: FAILED self-audit: notes.md:1: private network name: widget"), out.stdout)

    def test_every_denylist_is_applied(self):
        outside = self.scratch.parent
        (outside / "names.txt").write_text("gadget\n")
        (outside / "tools.txt").write_text("gizmo\n")
        lists = ["--denylist", str(outside / "names.txt"), "--denylist", str(outside / "tools.txt")]
        for word in ("gadget", "gizmo"):
            self.branch(word, lambda: self.write("notes.md", "A %s list.\n" % word))
            out = self.run_check("--head", word, *lists)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertIn(word, out.stdout)
            env = {"LODGINGS_AUDIT_DENYLIST": os.pathsep.join(str(outside / f) for f in ("names.txt", "tools.txt"))}
            out = self.run_check("--head", word, env=env)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertIn(word, out.stdout)
        self.assertEqual(self.run_check("--head", "gizmo").returncode, 0)

    def test_conflicting_merge_fails_with_the_file_list(self):
        self.branch("a", lambda: self.write("doc.md", "The pool keeps a list of hosts.\n"))
        self.branch("b", lambda: self.write("doc.md", "The pool keeps a list of lodgings.\n"))
        self.git("merge", "-q", "--no-edit", "a")
        out = self.run_check("--head", "b")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(out.stdout, "merged-check: FAILED merge: conflict in doc.md\n")


if __name__ == "__main__":
    unittest.main()
