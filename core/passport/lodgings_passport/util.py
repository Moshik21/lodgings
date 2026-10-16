"""Small helpers: argv-only subprocess calls, hashing, atomic JSON, errors."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

# Exit codes are part of the public contract (docs/cli.md).
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_INVALID = 4
EXIT_ENV = 5


class PassportError(Exception):
    """Base error. `code` is the process exit code, `hint` is text for a person."""

    code = EXIT_ENV
    verdict = "error"

    def __init__(self, reason, hint="", evidence=None):
        Exception.__init__(self, reason)
        self.reason = reason
        self.hint = hint
        self.evidence = evidence or {}


class UsageError(PassportError):
    code = EXIT_USAGE
    verdict = "usage"


class Refusal(PassportError):
    """The request is unsafe; nothing was changed."""

    code = EXIT_REFUSED
    verdict = "refused"


class Invalid(PassportError):
    """The passport is damaged, tampered with or does not match its receipts."""

    code = EXIT_INVALID
    verdict = "invalid"


class EnvError(PassportError):
    code = EXIT_ENV
    verdict = "error"


def run(argv, cwd=None, timeout=60, env=None, input_text=None):
    """Run argv (never a shell string). Returns (rc, stdout, stderr).

    Text is UTF-8, never the locale's code page: git writes commit subjects and paths as UTF-8 bytes, and decoding
    them as cp1252 on Windows turns U+009B and U+202E into ordinary-looking characters that printable() would not
    see. Bytes that are not UTF-8 become lone surrogates (a path round-trips exactly) and print as U+FFFD.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        raise UsageError("command must be a non-empty argv list, got %r" % (argv,))
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    try:
        proc = subprocess.run(
            list(argv),
            cwd=cwd,
            env=full_env,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", "not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss: %s" % (timeout, argv[0])
    return proc.returncode, proc.stdout, proc.stderr


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, obj):
    """Write JSON atomically (temp file in the same directory, then rename)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path, what="file"):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise UsageError("%s not found: %s" % (what, path))
    except ValueError as exc:
        raise Invalid("%s is not valid JSON: %s (%s)" % (what, path, exc))


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def epoch_now():
    return int(time.time())


def bounded(text, limit=8192):
    """Keep at most `limit` characters, marking the cut."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[... %d more characters cut]" % (len(text) - limit)


def short(sha):
    return (sha or "")[:12]


# C0 but not tab or newline, DEL, C1 (0x9b is CSI, 0x9d OSC), zero-width and bidirectional controls, line/paragraph
# separators, BOM: the last group can reorder or hide what a person reads
# Must stay identical to _UNPRINTABLE in src/lodgings/cli.py (a separate package).
_UNPRINTABLE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")
_UNPRINTABLE_LINE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")  # the same, and a newline too


_UNDECODABLE = re.compile("[\ud800-\udfff]")  # what surrogateescape makes of a byte that is not UTF-8


def _escape(pattern, text):
    text = _UNDECODABLE.sub("\ufffd", text)
    return pattern.sub(lambda m: "\\x%02x" % ord(m.group()) if ord(m.group()) < 0x100 else "\\u%04x" % ord(m.group()), text)


def printable(text):
    """`text` as safe terminal output: every control character becomes a visible \\xNN (\\uNNNN above U+00FF).

    Repository-derived text (a commit subject, a file name) is written by whoever can push to a lane. Escaping the
    ESC, BEL, CR and C1 characters is enough to defuse CSI and OSC sequences and their terminators: what is left
    is plain, visible text.
    """
    return _escape(_UNPRINTABLE, text)


def one_line(text):
    """printable() for one field of a listing: a newline in a file name must not start a line of its own."""
    return _escape(_UNPRINTABLE_LINE, text)


def _fit(text, encoding):
    """`text` as the stream's own encoding can show it: a character it cannot encode, or that it would write as a
    byte in 0x80-0x9f (a C1 control to a Latin-1 terminal, an invalid byte to a UTF-8 one), is written as \\xNN or
    \\uNNNN. Errors and locale never decide what reaches the terminal."""
    if not encoding or text.isascii():
        return text
    out = []
    for ch in text:
        if ch < "\x80":
            out.append(ch)
            continue
        try:
            raw = ch.encode(encoding)
            keep = not (len(raw) == 1 and 0x80 <= raw[0] <= 0x9f)
        except LookupError:
            return text
        except UnicodeError:
            keep = False
        if keep:
            out.append(ch)
        else:
            n = ord(ch)
            out.append("\\x%02x" % n if n < 0x100 else "\\u%04x" % n if n < 0x10000 else "\\U%08x" % n)
    return "".join(out)


class SafeText(object):
    """A text stream that passes everything written to it through printable(): the one print boundary."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        return self._stream.write(_fit(printable(text), getattr(self._stream, "encoding", None)))

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):
        return getattr(self._stream, name)
