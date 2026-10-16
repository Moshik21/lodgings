"""A small TOML reader for hosts.toml on Pythons older than 3.11 (no tomllib).

Supports what hosts.toml uses: comments, [table.paths], [[arrays of tables]], bare and quoted keys, dotted keys,
basic and literal strings (single line), integers, floats, booleans, arrays (several lines allowed) and inline
tables. Dates and multi-line strings are not supported and are reported as errors, never guessed.
`loads()` prefers the standard library's tomllib when it exists; `force_lite=True` uses this reader anyway.
"""
import re

try:
    import tomllib as _tomllib
except ImportError:  # Python < 3.11
    _tomllib = None


class TomlError(ValueError):
    pass


_BARE = re.compile(r"[A-Za-z0-9_-]+")
_NUM = re.compile(r"[+-]?(?:0|[1-9][0-9_]*)(?:\.[0-9_]+)?(?:[eE][+-]?[0-9_]+)?")
_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


def loads(text, force_lite=False):
    if _tomllib is not None and not force_lite:
        try:
            return _tomllib.loads(text)
        except _tomllib.TOMLDecodeError as exc:
            raise TomlError(str(exc))
    return _Parser(text).parse()


class _Parser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.line = 1

    def err(self, msg):
        raise TomlError("line %d: %s" % (self.s.count("\n", 0, self.i) + 1, msg))

    # ---- low level ------------------------------------------------------------------
    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ""

    def skip_ws(self):
        while self.peek() in (" ", "\t"):
            self.i += 1

    def skip_ws_nl(self):
        while True:
            self.skip_ws()
            c = self.peek()
            if c == "#":
                while self.peek() not in ("\n", ""):
                    self.i += 1
            elif c in ("\n", "\r"):
                self.i += 1
            else:
                return

    def end_of_line(self):
        self.skip_ws()
        if self.peek() == "#":
            while self.peek() not in ("\n", ""):
                self.i += 1
        if self.peek() not in ("\n", "\r", ""):
            self.err("unexpected text after value: %r" % self.s[self.i:self.i + 12])

    # ---- keys -----------------------------------------------------------------------
    def key_part(self):
        self.skip_ws()
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        m = _BARE.match(self.s, self.i)
        if not m:
            self.err("expected a key")
        self.i = m.end()
        return m.group(0)

    def key_path(self):
        parts = [self.key_part()]
        while True:
            self.skip_ws()
            if self.peek() == ".":
                self.i += 1
                parts.append(self.key_part())
            else:
                return parts

    # ---- values ---------------------------------------------------------------------
    def basic_string(self):
        if self.s.startswith('"""', self.i):
            self.err("multi-line strings are not supported")
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if c in ("", "\n"):
                self.err("unterminated string")
            self.i += 1
            if c == '"':
                return "".join(out)
            if c == "\\":
                e = self.peek()
                self.i += 1
                if e in _ESC:
                    out.append(_ESC[e])
                elif e in ("u", "U"):
                    n = 4 if e == "u" else 8
                    out.append(chr(int(self.s[self.i:self.i + n], 16)))
                    self.i += n
                else:
                    self.err("bad escape \\%s" % e)
            else:
                out.append(c)

    def literal_string(self):
        if self.s.startswith("'''", self.i):
            self.err("multi-line strings are not supported")
        j = self.s.find("'", self.i + 1)
        k = self.s.find("\n", self.i)
        if j < 0 or (0 <= k < j):
            self.err("unterminated string")
        val = self.s[self.i + 1:j]
        self.i = j + 1
        return val

    def value(self):
        self.skip_ws()
        c = self.peek()
        if c == '"':
            return self.basic_string()
        if c == "'":
            return self.literal_string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        for word, val in (("true", True), ("false", False)):
            if self.s.startswith(word, self.i):
                self.i += len(word)
                return val
        m = _NUM.match(self.s, self.i)
        if m and m.group(0):
            self.i = m.end()
            raw = m.group(0).replace("_", "")
            if re.match(r"\d{4}-\d\d-\d\d", self.s[m.start():m.start() + 10]):
                self.err("dates are not supported")
            return float(raw) if any(x in raw for x in ".eE") else int(raw)
        self.err("cannot read a value here: %r" % self.s[self.i:self.i + 12])

    def array(self):
        self.i += 1
        out = []
        while True:
            self.skip_ws_nl()
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.skip_ws_nl()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                self.err("expected , or ] in array")

    def inline_table(self):
        self.i += 1
        out = {}
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            path = self.key_path()
            self.skip_ws()
            if self.peek() != "=":
                self.err("expected = in inline table")
            self.i += 1
            self.assign(out, path, self.value())
            self.skip_ws()
            c = self.peek()
            self.i += 1
            if c == "}":
                return out
            if c != ",":
                self.err("expected , or } in inline table")
            self.skip_ws()

    # ---- tables ---------------------------------------------------------------------
    def assign(self, table, path, val):
        for p in path[:-1]:
            nxt = table.setdefault(p, {})
            if not isinstance(nxt, dict):
                self.err("key %r is not a table" % p)
            table = nxt
        if path[-1] in table:
            self.err("duplicate key %r" % path[-1])
        table[path[-1]] = val

    def descend(self, root, path, array_last=False):
        table = root
        for n, p in enumerate(path):
            last = n == len(path) - 1
            if last and array_last:
                lst = table.setdefault(p, [])
                if not isinstance(lst, list):
                    self.err("%r is not an array of tables" % p)
                lst.append({})
                return lst[-1]
            nxt = table.setdefault(p, {})
            if isinstance(nxt, list):
                nxt = nxt[-1]
            if not isinstance(nxt, dict):
                self.err("%r is not a table" % p)
            table = nxt
        return table

    def parse(self):
        root, cur = {}, None
        cur = root
        seen = set()
        while True:
            self.skip_ws_nl()
            c = self.peek()
            if c == "":
                return root
            if c == "[":
                arr = self.s.startswith("[[", self.i)
                self.i += 2 if arr else 1
                path = self.key_path()
                self.skip_ws()
                close = "]]" if arr else "]"
                if not self.s.startswith(close, self.i):
                    self.err("expected %s" % close)
                self.i += len(close)
                self.end_of_line()
                if not arr:
                    if tuple(path) in seen:
                        self.err("table [%s] defined twice" % ".".join(path))
                    seen.add(tuple(path))
                cur = self.descend(root, path, array_last=arr)
            else:
                path = self.key_path()
                self.skip_ws()
                if self.peek() != "=":
                    self.err("expected =")
                self.i += 1
                self.assign(cur, path, self.value())
                self.end_of_line()
