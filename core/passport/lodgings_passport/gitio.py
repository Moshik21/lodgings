"""Git plumbing used by the passport. Every call is an argv list; nothing goes through a shell.

Writes are limited to: a scratch bare repository the passport owns, create-only
ref updates (old value must be zero), non-forcing fetches, commit-tree, and
`git worktree add` on the target. No call here can force, delete or rewrite a ref.
"""

import os
import re
import shutil
import tempfile

from .util import EnvError, Refusal, run

ZERO = "0" * 40
SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
MIN_GIT = (2, 38)  # git merge-tree --write-tree


class Git(object):
    """A repository addressed either by work tree (`-C`) or by git dir (`--git-dir`)."""

    def __init__(self, path, bare=False, timeout=300):
        self.path = path
        self.bare = bare
        self.timeout = timeout

    def argv(self, args):
        base = ["git", "--git-dir", self.path] if self.bare else ["git", "-C", self.path]
        # Never let a pager, a prompt or a local hook path interfere.
        return base + ["-c", "core.pager=cat", "-c", "credential.helper="] + list(args)

    def call(self, args, env=None, input_text=None):
        # GIT_OPTIONAL_LOCKS=0: `git status` must not refresh (write) the index of a lane it only reads.
        safe_env = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"}
        if env:
            safe_env.update(env)
        return run(self.argv(args), timeout=self.timeout, env=safe_env, input_text=input_text)

    def ok(self, args, what, env=None, input_text=None):
        rc, out, err = self.call(args, env=env, input_text=input_text)
        if rc != 0:
            raise EnvError("git %s failed (%s): %s" % (what, rc, (err or out).strip()[:600]))
        return out

    # ---- reads -------------------------------------------------------------

    def rev_parse(self, ref):
        rc, out, _ = self.call(["rev-parse", "--verify", "--quiet", "%s^{commit}" % ref])
        return out.strip() if rc == 0 and out.strip() else None

    def has_commit(self, sha):
        rc, _, _ = self.call(["cat-file", "-e", "%s^{commit}" % sha])
        return rc == 0

    def tree(self, sha):
        return self.ok(["rev-parse", "%s^{tree}" % sha], "rev-parse tree").strip()

    def is_ancestor(self, a, b):
        rc, _, err = self.call(["merge-base", "--is-ancestor", a, b])
        if rc in (0, 1):
            return rc == 0
        raise EnvError("git merge-base --is-ancestor failed: %s" % err.strip()[:300])

    def merge_base(self, a, b):
        rc, out, _ = self.call(["merge-base", a, b])
        return out.strip() if rc == 0 and out.strip() else None

    def refs(self, patterns):
        if not patterns:
            return []
        out = self.ok(["for-each-ref", "--format=%(objectname) %(objecttype) %(refname)"] + list(patterns),
                      "for-each-ref")
        found = []
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[1] in ("commit", "tag"):
                sha = self.rev_parse(parts[2]) if parts[1] == "tag" else parts[0]
                if sha:
                    found.append((parts[2], sha))
        return found

    def count(self, rev_range):
        """Number of commits in a range such as `gate..lane`."""
        return len(self.rev_list(rev_range))

    def rev_list(self, rev_range):
        """Commit ids of a range, newest first."""
        return self.ok(["rev-list", rev_range], "rev-list").split()

    def subjects(self, rev_range, limit):
        """(sha, subject) of the newest `limit` commits of a range."""
        out = self.ok(["log", "--no-show-signature", "-n", str(limit), "--format=%H%x09%s", rev_range], "log")
        found = []
        for line in out.splitlines():
            sha, _, subject = line.partition("\t")
            if sha:
                found.append((sha, subject))
        return found

    def diff_tree(self, a, b):
        """Raw tree difference a -> b as (status, path) pairs plus the raw text, without rename detection."""
        raw = self.ok(["diff-tree", "-r", "--no-renames", "--raw", "-z", a, b], "diff-tree")
        parts = raw.split("\0")
        entries = []
        for meta, path in zip(parts[0::2], parts[1::2]):
            fields = meta.lstrip(":").split()
            if len(fields) >= 5:
                entries.append((fields[4][:1], path))
        return entries, raw

    def worktree_of(self, branch_ref):
        """Path of the live worktree that has `branch_ref` checked out, or None."""
        listing = self.ok(["worktree", "list", "--porcelain"], "worktree list")
        for block in listing.split("\n\n"):
            lines = block.strip().splitlines()
            fields = dict(line.split(" ", 1) if " " in line else (line, "") for line in lines)
            path = fields.get("worktree")
            if path and fields.get("branch") == branch_ref and "prunable" not in fields and os.path.isdir(path):
                return path
        return None

    def ref_value(self, refname):
        rc, out, _ = self.call(["rev-parse", "--verify", "--quiet", refname])
        return out.strip() if rc == 0 and out.strip() else None

    def common_dir(self):
        out = self.ok(["rev-parse", "--path-format=absolute", "--git-common-dir"], "rev-parse --git-common-dir")
        return out.strip()

    def objects_dir(self):
        if self.bare:
            return os.path.join(os.path.abspath(self.path), "objects")
        return os.path.join(self.common_dir(), "objects")

    def status_porcelain(self):
        return self.ok(["status", "--porcelain=v1", "--untracked-files=normal"], "status")

    def head(self):
        return self.rev_parse("HEAD")

    def symbolic_head(self):
        rc, out, _ = self.call(["symbolic-ref", "--quiet", "HEAD"])
        return out.strip() if rc == 0 else None

    def merge_tree(self, ours, theirs):
        """Return (clean, tree) of a real three-way merge, without touching any ref or index."""
        rc, out, err = self.call(["merge-tree", "--write-tree", "--no-messages", ours, theirs])
        if rc not in (0, 1):
            raise EnvError("git merge-tree failed (%s): %s" % (rc, err.strip()[:300]))
        lines = out.splitlines()
        return rc == 0, (lines[0].strip() if lines else "")

    # ---- create-only writes --------------------------------------------------

    def create_ref(self, refname, sha, reason):
        """Create refname at sha. Refuses if it exists with another value; a no-op if equal."""
        current = self.ref_value(refname)
        if current == sha:
            return False
        if current:
            raise Refusal("ref %s already exists at %s, not %s" % (refname, current[:12], sha[:12]),
                          hint="nothing was changed; pick a new --seq or inspect the ref by hand")
        self.ok(["update-ref", "-m", reason, refname, sha, ZERO], "update-ref (create-only)")
        return True


def git_version():
    rc, out, _ = run(["git", "--version"], timeout=20)
    if rc != 0:
        raise EnvError("git is not available on PATH")
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def require_git():
    version = git_version()
    if version < MIN_GIT:
        raise EnvError("git %d.%d or newer is required (found %d.%d)" % (MIN_GIT + version))
    return version


class Scratch(object):
    """A throwaway bare repository that borrows objects from other repositories.

    It lets the passport read objects from the lane repository and the gate
    repository at once, build a bundle and compute merges, without writing a
    single object or ref into either of them.
    """

    def __init__(self, object_dirs, timeout=300):
        self.dir = tempfile.mkdtemp(prefix="lodgings-passport-")
        self.git = Git(self.dir, bare=True, timeout=timeout)
        self.git.ok(["init", "--bare", "--quiet"], "init scratch")
        alternates = os.path.join(self.dir, "objects", "info", "alternates")
        # git reads this file line by line and takes a CR as part of the path: newline="\n" keeps LF on Windows.
        with open(alternates, "w", encoding="utf-8", newline="\n") as fh:
            for d in object_dirs:
                if d and os.path.isdir(d):
                    fh.write(os.path.abspath(d) + "\n")

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
