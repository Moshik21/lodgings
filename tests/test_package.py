"""The release zip: it carries the files the README points at, and no relative link in it is dangling.

    python3 -m unittest discover -s tests

Builds into a scratch directory with scripts/build-package; standard library only.
"""
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseZip(unittest.TestCase):
    def test_support_is_shipped_and_no_link_dangles(self):
        with tempfile.TemporaryDirectory() as out:
            res = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-package"), "--out", out], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            with zipfile.ZipFile(next(Path(out).glob("lodgings-*.zip"))) as zf:
                names = set(zf.namelist())
                top = sorted(names)[0].split("/")[0]
                self.assertIn(top + "/SUPPORT.md", names)
                have = names | {posixpath.dirname(n) for n in names} | {posixpath.dirname(posixpath.dirname(n)) for n in names}
                dangling = []
                for name in sorted(n for n in names if n.endswith(".md")):
                    text = re.sub(r"```.*?```", "", zf.read(name).decode("utf-8"), flags=re.S)
                    for target in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", text):
                        if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith("#"):
                            continue
                        if posixpath.normpath(posixpath.join(posixpath.dirname(name), target.split("#")[0])) not in have:
                            dangling.append("%s -> %s" % (name, target))
                self.assertEqual(dangling, [])


if __name__ == "__main__":
    unittest.main()
