"""The sealed passport directory: records, secret scan, SHA256SUMS and the passport id.

Layout (docs/format.md):

  <task>-<seq>/
    manifest.json  custody.json  pipeline.json  decisions.json
    refs.bundle
    records/<name>        brief, status log, backlog row, anything the lane spec names
    SHA256SUMS            one line per sealed file; the passport id is sha256(SHA256SUMS)
    after/                written after sealing (import and confirm receipts); never sealed
"""

import os
import re
import shutil

from .util import Invalid, Refusal, UsageError, sha256_bytes, sha256_file

SUMS = "SHA256SUMS"
AFTER = "after"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def check_record_name(name):
    if not NAME_RE.match(name) or name in (SUMS,):
        raise UsageError("record name %r must be a plain file name (letters, digits, . _ -)" % name)


def scan_secrets(text, patterns):
    """Return [(line_number, pattern_index)] without ever returning the matched text."""
    compiled = [re.compile(p) for p in patterns]
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for index, rx in enumerate(compiled):
            if rx.search(line):
                hits.append((number, index))
    return hits


def copy_record(src, dest_dir, name, cfg):
    check_record_name(name)
    if not os.path.isfile(src):
        raise UsageError("record %s: file not found: %s" % (name, src))
    size = os.path.getsize(src)
    if size > cfg["max_record_bytes"]:
        raise Refusal("record %s is %d bytes, over max_record_bytes %d" % (name, size, cfg["max_record_bytes"]),
                      hint="trim the record or raise max_record_bytes")
    with open(src, "rb") as fh:
        data = fh.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("record %s is not UTF-8 text; passports carry text records only" % name)
    hits = scan_secrets(text, cfg["secret_patterns"])
    if hits:
        where = ", ".join("line %d (pattern %d)" % h for h in hits[:5])
        raise Refusal("record %s looks like it holds a secret at %s" % (name, where),
                      hint="secrets never travel in a passport; remove it from the record and export again")
    dest = os.path.join(dest_dir, name)
    shutil.copyfile(src, dest)
    return {"name": name, "bytes": size, "sha256": sha256_bytes(data)}


def sealed_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == AFTER or rel_dir.startswith(AFTER + os.sep):
            dirnames[:] = []
            continue
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, name)).replace(os.sep, "/")
            if rel == SUMS:
                continue
            files.append(rel)
    return sorted(files)


def seal(root):
    lines = []
    for rel in sealed_files(root):
        lines.append("%s  %s\n" % (sha256_file(os.path.join(root, rel)), rel))
    body = "".join(lines)
    with open(os.path.join(root, SUMS), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return "sha256:" + sha256_bytes(body.encode("utf-8"))


def passport_id(root):
    path = os.path.join(root, SUMS)
    if not os.path.isfile(path):
        raise Invalid("not a sealed passport (no %s): %s" % (SUMS, root))
    with open(path, "rb") as fh:
        return "sha256:" + sha256_bytes(fh.read())


def check_seal(root):
    """Return a list of problems; empty means every sealed file matches and nothing was added."""
    problems = []
    path = os.path.join(root, SUMS)
    if not os.path.isfile(path):
        return ["no %s: not a sealed passport" % SUMS]
    listed = {}
    with open(path, "r", encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2 or not re.match(r"^[0-9a-f]{64}$", parts[0]):
                problems.append("%s line %d is malformed" % (SUMS, number))
                continue
            listed[parts[1]] = parts[0]
    present = set(sealed_files(root))
    for rel, digest in sorted(listed.items()):
        if rel not in present:
            problems.append("missing sealed file: %s" % rel)
        elif sha256_file(os.path.join(root, rel)) != digest:
            problems.append("checksum mismatch: %s" % rel)
    for rel in sorted(present - set(listed)):
        problems.append("file not in the seal: %s" % rel)
    return problems


def after_dir(root):
    path = os.path.join(root, AFTER)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path
