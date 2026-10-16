"""The pool's own value ledger: what the records it already holds say it did, as numbers a reviewer can recount.

    <state>/ledger/lodgings.jsonl            append-only, one line per counter per host per closed UTC day (docs/ledger.md)
    <state>/ledger/coverage/<host>.jsonl   what each run of the writer saw of a host's beats (the last 15 days)

The line is the tool value ledger's v1 line. Everything is read from the pool's own records
(`hosts/<name>/events.jsonl`, `beats.jsonl`, `transitions.jsonl`, `drain.json`); nothing else is read and nothing is
probed. A number carries its record range and a digest of the exact source rows it was counted from. What the records
cannot show is written as `value: null` with its reason and, in `note`, the one field that would make it measurable.
How much of a day a host's records cover is read from the continuity of its beats, never from when its events happened.
`beats.jsonl` keeps a few hours, so each run first writes down what it sees of them (the coverage record), and a day's
coverage is the union of those rows: the number 0 is written only for a day they cover with no gap. The writer must
therefore run at least every hour. A day is written once it is closed, and a reading
that a later run makes better is appended as a new line that names the one it supersedes. Minutes gained by placement
are never a counter.
"""
import hashlib
import json
import math
import os
import tempfile

from . import hosts as hostsmod, records as recmod

READER = "lodgings-ledger-writer/1"
DAY = 86400
PATH_PARTS = ("ledger", "lodgings.jsonl")
DRAIN_STATES = ("DRAINING", "DRAINED")
NO_BEATS = "no beats received from this host, so no state change is seen and a drain cannot be measured"
NO_BEATS_COVER = "no beats received from this host, so how much of the day its records cover is not known"
UNSEEN = "writer did not observe this day"
CLOSE_MARGIN_S, GAP_MIN_S, GAP_BEATS = 300, 120, 3
COVERAGE_PARTS, COVERAGE_KEEP_S = ("ledger", "coverage"), 15 * DAY

# Counters the records cannot show, with the one field that would make each measurable (docs/ledger.md).
UNMEASURED = (
    ("jobs_placed_elsewhere", "field-absent", "needs a `submitter` on placement events: where a job came from is not recorded"),
    ("offers", "field-absent", "needs an `offer_id` on every evacuation offer: an offer prints and is not recorded"),
    ("offers_moved", "field-absent", "needs the `offer_id` carried into the lane passport actions: without it an offer and a move cannot be linked"),
    ("drains_completed", "field-absent", "needs a `kind: \"drain\"` event with `phase: complete`: the end of a drain is not recorded"),
    ("placement_minutes_vs_local", "not-measurable", "a counterfactual: no record holds how long a local run would have taken, and none is proposed"),
)


class Unreadable(Exception):
    pass


class Row(object):
    def __init__(self, n, raw, doc, key):
        self.n, self.raw, self.doc = n, raw, doc
        at = doc.get(key)
        self.at = at if isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at) else None


class Source(object):
    """One record file of one host, read once, holding only the rows that had arrived by `now` (by `key`).
    status: ok, absent (no such file) or unreadable."""

    def __init__(self, path, now, key="received_epoch"):
        self.status, self.rows = "ok", []
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            self.status = "absent"
            return
        except OSError:
            self.status = "unreadable"
            return
        for n, raw in enumerate(data.split(b"\n"), 1):
            raw = raw[:-1] if raw.endswith(b"\r") else raw  # a state file written on Windows by 0.3.0 has CRLF; the digest is of the line without its newline
            if not raw.strip():
                continue
            try:
                doc = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(doc, dict):
                row = Row(n, raw, doc, key)
                if row.at is None or row.at <= now:
                    self.rows.append(row)
        if not any(r.at is not None for r in self.rows):
            self.status = "absent"  # nothing with a time on it: nothing a day can be counted from


def _digest(rows):
    return "sha256:" + hashlib.sha256(b"\n".join(r.raw for r in rows)).hexdigest()


def _ref(row):
    return "received:%s" % ("%d" % row.at if float(row.at).is_integer() else repr(row.at))


def _line(host, counter, lo, hi, now, value, unknown, quality, note=None, src=None, cov=None, excluded=None, combine=None):
    rec = {"v": 1, "id": "%s@%s@%d" % (counter, host, lo), "tool": "lodgings", "counter": counter, "host": host,
           "window": {"from": lo, "to": hi}, "value": value, "unknown": unknown, "quality": quality,
           "coverage": cov or {"first_at": None, "last_at": None, "rows": 0, "covers_window": 0.0},
           "src": src, "excluded": excluded if excluded is not None else ({} if value is not None else None),
           "read_at": int(now), "reader": READER, "supersedes": None}
    if combine:
        rec["combine"] = combine
    if note:
        rec["note"] = note
    return rec


def _epoch(t):
    return "%d" % t if float(t).is_integer() else repr(t)


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _late(row, interval):
    v = row.doc.get("interval_s")
    return max(GAP_BEATS * (v if _finite(v) and v > 0 else interval), GAP_MIN_S)


class Host(object):
    """One host's record files, read once per run, as they stood at `now`."""

    def __init__(self, root, host, now, interval):
        self.dir = os.path.join(root, "hosts", host)
        self.host, self.now, self.interval = host, now, interval
        self.has_dir = os.path.isdir(self.dir)
        self.events = Source(os.path.join(self.dir, "events.jsonl"), now)
        self.beats = Source(os.path.join(self.dir, "beats.jsonl"), now)
        self.transitions = Source(os.path.join(self.dir, "transitions.jsonl"), now, "epoch")
        self.drain_path = os.path.join(self.dir, "drain.json")

    def look(self):
        """-> this run's coverage row: the span of the beats it can see, and the gaps in it longer than the late limit
        (max(3 x the interval the earlier beat announced, 120 s)). The span after a goodbye is an expected absence, not a
        gap, and runs on to the time of the look. None when it sees no beats."""
        beats = sorted((r for r in self.beats.rows if r.at is not None), key=lambda r: r.at)
        if self.beats.status != "ok" or not beats:
            return None
        gaps = [[a.at, b.at] for a, b in zip(beats, beats[1:]) if a.doc.get("kind") != "goodbye" and b.at - a.at > _late(a, self.interval)]
        every = [r.doc["interval_s"] for r in beats if _finite(r.doc.get("interval_s")) and r.doc["interval_s"] > 0]
        return {"looked_at": self.now, "first_at": beats[0].at, "last_at": beats[-1].at, "gaps": gaps,
                "asleep": beats[-1].doc.get("kind") == "goodbye", "interval_max": max(every) if every else None}


def _end(row):
    return row["looked_at"] if row.get("asleep") else row["last_at"]


def _spans(rows):
    """The covered spans of coverage rows, merged; a hole no longer than GAP_MIN_S between two spans is not a gap."""
    out = []
    for r in rows:
        start, end = r["first_at"], _end(r)
        for a, b in sorted(r["gaps"]):
            out.append((start, a))
            start = b
        out.append((start, end))
    merged = []
    for a, b in sorted(x for x in out if x[1] >= x[0]):
        if merged and a - merged[-1][1] <= GAP_MIN_S:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _holes(spans, lo, hi):
    out, at = [], lo
    for a, b in spans:
        if b <= at or a >= hi:
            continue
        if a > at:
            out.append((at, a))
        at = max(at, b)
    if at < hi:
        out.append((at, hi))
    return [(a, b) for a, b in out if b - a > GAP_MIN_S]


class Reading(object):
    """The counters of one host over one closed day. How much of the day its records cover is read from the coverage
    rows the writer recorded of its beats: the day, and the closing margin after it, must lie in their union."""

    def __init__(self, files, cov_rows, lo, hi):
        self.files, self.host, self.lo, self.hi, self.now = files, files.host, lo, hi, files.now
        self.has_dir, self.events, self.beats, self.transitions = files.has_dir, files.events, files.beats, files.transitions
        self.drain_path = files.drain_path
        rows = [r for r in cov_rows if r["looked_at"] <= self.now]
        spans = _spans(rows)
        into = [r for r in rows if r["first_at"] < hi and _end(r) >= lo]
        every = [r["interval_max"] for r in into if _finite(r.get("interval_max"))]
        self.margin = max(CLOSE_MARGIN_S, 2 * max(every or [0]))
        self.closed = self.now >= hi + self.margin
        self.seen = any(a < hi and b > lo for a, b in spans)
        day = _holes(spans, lo, hi)
        self.gap = (_holes(spans, lo, hi + self.margin) or [None])[0]
        self.cover = max(0.0, math.floor((1.0 - sum(b - a for a, b in day) / float(hi - lo)) * 10000) / 10000.0) if self.seen else 0.0
        self.span = (int(min(a for a, _ in spans)), int(max(b for _, b in spans))) if spans else (None, None)

    def _coverage(self, rows):
        return {"first_at": self.span[0], "last_at": self.span[1], "rows": rows, "covers_window": self.cover}

    def _unknown(self, counter, code, note, cov=None):
        return _line(self.host, counter, self.lo, self.hi, self.now, None, code, "exact", note, None, cov)

    def _blind(self, counter, name, src):
        """The unknown line for a counter this host's records cannot show for the day, or None when they can."""
        if not self.has_dir:
            return self._unknown(counter, "no-state-dir", "this host has no records in the state directory")
        if src.status == "unreadable":
            return self._unknown(counter, "source-unreadable", "%s exists and could not be read" % name)
        if self.beats.status == "unreadable":
            return self._unknown(counter, "source-unreadable", "beats.jsonl exists and could not be read")
        if self.beats.status == "absent":
            return self._unknown(counter, "field-absent", NO_BEATS_COVER)
        if not self.seen:
            return self._unknown(counter, "source-truncated", UNSEEN, self._coverage(0))
        if self.gap:
            return self._unknown(counter, "source-truncated", "no coverage between %s and %s" % tuple(_epoch(t) for t in self.gap), self._coverage(0))
        return None

    def count(self, counter, src, name, scope, hit, filt, field=None, has=None, total=None):
        """A count (or sum) of the rows `hit` picks among the rows `scope` selects, in this window.
        `has` says a row carries `field`; rows that lack it are left out and make the number a lower bound."""
        lo, hi = self.lo, self.hi
        blind = self._blind(counter, name, src)
        if blind:
            return blind
        inwin = [r for r in src.rows if r.at is not None and lo <= r.at < hi and scope(r)]
        carry = [r for r in inwin if has is None or has(r)]
        lacking = len(inwin) - len(carry)
        counted = [r for r in carry if hit(r)]
        if inwin and not carry:
            return self._unknown(counter, "field-absent", "no row carries `%s`: needs it on every row" % field, self._coverage(len(inwin)))
        value = total(counted) if total else len(counted)
        why = "%d row(s) carry no `%s`" % (lacking, field)
        if value == 0 and lacking:
            return self._unknown(counter, "field-absent", "a zero would be a guess: " + why, self._coverage(len(inwin)))
        s = {"kind": "jsonl", "file": name, "filter": filt, "first_ref": _ref(counted[0]) if counted else None,
             "last_ref": _ref(counted[-1]) if counted else None, "digest": _digest(counted)}
        ex = {"no_" + field: lacking} if lacking else {}
        note = "a lower bound: " + why if lacking else None
        return _line(self.host, counter, lo, hi, self.now, value, None, "lower-bound" if lacking else "exact", note, s, self._coverage(len(inwin)), ex)

    def drain_counters(self):
        out = []
        if self.has_dir and self.beats.status == "absent":
            for c in ("drain_transitions", "drain_requests"):
                out.append(self._unknown(c, "field-absent", NO_BEATS))
            return out
        lo, hi = self.lo, self.hi
        # state changes are recorded only when `lodgings states` runs: whatever is seen is a floor
        blind = self._blind("drain_transitions", "transitions.jsonl", self.transitions)
        if blind:
            out.append(blind)
        else:
            hits = [r for r in self.transitions.rows if r.doc.get("state") in DRAIN_STATES and r.at is not None and lo <= r.at < hi]
            s = {"kind": "jsonl", "file": "transitions.jsonl", "filter": "state in DRAINING,DRAINED, epoch in the window",
                 "first_ref": "epoch:%s" % _epoch(hits[0].at) if hits else None,
                 "last_ref": "epoch:%s" % _epoch(hits[-1].at) if hits else None, "digest": _digest(hits)}
            out.append(_line(self.host, "drain_transitions", lo, hi, self.now, len(hits), None, "lower-bound",
                             "a lower bound: a change is written only when `lodgings states` runs", s, self._coverage(len(hits)), {}))
        out.append(self.drain_requests())
        return out

    def drain_requests(self):
        c, lo, hi = "drain_requests", self.lo, self.hi
        need = "needs a `kind: \"drain\"` event with `phase: request` on every request: a repeated request overwrites the one before"
        if not self.has_dir:
            return self._unknown(c, "no-state-dir", "this host has no records in the state directory")
        try:
            with open(self.drain_path, "rb") as fh:
                data = fh.read()
            doc = json.loads(data.decode("utf-8"))
            at = doc.get("requested_epoch") if isinstance(doc, dict) else None
        except FileNotFoundError:
            return self._unknown(c, "field-absent", "no request is standing, and a withdrawn one leaves nothing: " + need)
        except (OSError, ValueError):
            return self._unknown(c, "source-unreadable", "drain.json exists and could not be read")
        if not _finite(at) or not lo <= at < hi:
            return self._unknown(c, "field-absent", "the standing request was made outside this day, and earlier ones are overwritten: " + need)
        s = {"kind": "log", "file": "drain.json", "filter": "requested_epoch in the window", "canon": "the file's bytes as one record",
             "first_ref": "requested:%d" % at, "last_ref": "requested:%d" % at, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
        cov = {"first_at": int(at), "last_at": int(at), "rows": 1, "covers_window": 0.0}
        return _line(self.host, c, lo, hi, self.now, 1, None, "lower-bound", "a lower bound: only the standing request is kept; " + need, s, cov, {})

    def lines(self):
        ev, name = self.events, "events.jsonl"
        kind = lambda k, **kv: (lambda r: r.doc.get("kind") == k and all(r.doc.get(a) == b for a, b in kv.items()))
        num = lambda r: _finite(r.doc.get("queued_s"))
        gate = lambda r: r.doc.get("gate") in ("passed", "refused", "unchecked")
        placed = kind("placement")
        start = kind("job", phase="start")
        out = [
            self.count("jobs_placed", ev, name, placed, lambda r: r.doc.get("outcome") == "placed", "kind=placement outcome=placed"),
            self.count("placements_fell_back", ev, name, placed, lambda r: r.doc.get("outcome") == "fallback", "kind=placement outcome=fallback"),
            self.count("jobs_finished", ev, name, kind("job", phase="end"), lambda r: True, "kind=job phase=end"),
            self.count("queued_s_sum", ev, name, start, lambda r: True, "kind=job phase=start, queued_s present", "queued_s", num,
                       lambda rs: round(sum(r.doc["queued_s"] for r in rs), 3)),
            self.count("jobs_waited_60s", ev, name, start, lambda r: r.doc["queued_s"] >= 60, "kind=job phase=start queued_s>=60", "queued_s", num),
            self.count("gate_refused", ev, name, placed, lambda r: r.doc["gate"] == "refused", "kind=placement gate=refused", "gate", gate),
            self.count("gate_unchecked", ev, name, placed, lambda r: r.doc["gate"] == "unchecked", "kind=placement gate=unchecked", "gate", gate),
            self.count("gate_checked", ev, name, placed, lambda r: r.doc["gate"] in ("passed", "refused"), "kind=placement gate in passed,refused", "gate", gate),
        ]
        out += self.drain_counters()
        for counter, code, note in UNMEASURED:
            out.append(self._unknown(counter, code, note))
        return out


def window_ends(now, days):
    """The ends of the last `days` closed UTC days, oldest first. A day still open, or ended less than
    CLOSE_MARGIN_S ago, is never one of them."""
    anchor = int((now - CLOSE_MARGIN_S) // DAY) * DAY
    return [anchor - k * DAY for k in range(days - 1, -1, -1)]


def hosts_in(root, registered):
    names = set(registered)
    try:
        names.update(n for n in os.listdir(os.path.join(root, "hosts")) if os.path.isdir(os.path.join(root, "hosts", n)))
    except OSError:
        pass
    return sorted(n for n in names if 0 < len(n) <= 64)


def build(files, coverage, now, days):
    """A host whose day is not closed yet (now < day end + max(300 s, twice the largest interval its beats announced
    then)) is left for a later run."""
    out = []
    for hi in window_ends(now, days):
        for host in sorted(files):
            rd = Reading(files[host], coverage.get(host, []), hi - DAY, hi)
            if rd.closed:
                out += rd.lines()
    return out


def ledger_path(root):
    return os.path.join(root, *PATH_PARTS)


def coverage_path(root, host):
    return os.path.join(root, *(COVERAGE_PARTS + ("%s.jsonl" % host,)))


def _row_ok(doc):
    nums = [doc.get(k) for k in ("looked_at", "first_at", "last_at")]
    gaps = doc.get("gaps")
    return all(_finite(v) for v in nums) and isinstance(gaps, list) and all(
        isinstance(g, list) and len(g) == 2 and all(_finite(v) for v in g) for g in gaps)


def read_coverage(path):
    """-> the coverage rows of one host. Raises Unreadable, like the ledger, rather than lose any."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise Unreadable("%s cannot be read (%s)" % (path, exc.strerror))
    if data and not data.endswith(b"\n"):
        raise Unreadable("%s ends in a torn line" % path)
    out = []
    for n, raw in enumerate(data.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise Unreadable("%s line %d is not JSON" % (path, n))
        if not isinstance(doc, dict) or not _row_ok(doc):
            raise Unreadable("%s line %d is not a coverage row" % (path, n))
        out.append(doc)
    return out


def _replace(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _jsonl(docs):
    return "".join(json.dumps(d, separators=(",", ":"), sort_keys=True) + "\n" for d in docs).encode("utf-8")


def _chain(rid):
    base, _, k = rid.partition("#")
    return base, int(k) if k.isdigit() else 1


def read_existing(path):
    """-> (bytes, {base id: the newest line of its chain}). Raises Unreadable when the file is not a ledger this writer
    can safely append to."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return b"", {}
    except OSError as exc:
        raise Unreadable("cannot be read (%s)" % exc.strerror)
    if data and not data.endswith(b"\n"):
        raise Unreadable("ends in a torn line")
    newest = {}
    for n, raw in enumerate(data.split(b"\n"), 1):
        if not raw.strip():
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise Unreadable("line %d is not JSON" % n)
        if not isinstance(doc, dict) or not isinstance(doc.get("id"), str):
            raise Unreadable("line %d is not a ledger line" % n)
        base, k = _chain(doc["id"])
        if base not in newest or k >= _chain(newest[base]["id"])[1]:
            newest[base] = doc
    return data, newest


def _short_of_beats(doc):
    """A line held back by the beats' coverage of its day: a later, better reading may supersede it."""
    if doc.get("unknown") == "source-truncated":
        return True
    cover = (doc.get("coverage") or {}).get("covers_window")
    return doc.get("quality") == "lower-bound" and isinstance(cover, (int, float)) and cover < 1.0


def plan(recs, newest):
    """-> (the lines to append, how many readings the ledger already holds). A held reading is superseded only when it was
    short of beats and the new one is a number that differs from it; the new line's id is the next in its chain."""
    fresh = []
    for r in recs:
        held = newest.get(r["id"])
        if held is None:
            fresh.append(r)
        elif r["value"] is not None and _short_of_beats(held) and (r["value"], r["quality"]) != (held.get("value"), held.get("quality")):
            fresh.append(dict(r, id="%s#%d" % (r["id"], _chain(held["id"])[1] + 1), supersedes=held["id"]))
    return fresh, len(recs) - len(fresh)


def run(root, intervals, now, days, dry_run=False):
    """One run of the writer: record what it sees of each host's beats, then add the lines `plan` picks for the last
    `days` closed days. Every file is replaced whole and atomically, under the ledger's lock, never edited in place; a
    dry run writes nothing. -> (the lines written or that would be, readings already held). Raises Unreadable, and then
    writes nothing."""
    path = ledger_path(root)
    if dry_run:
        return _run(root, intervals, now, days, path, False)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with recmod._Lock(path):
        return _run(root, intervals, now, days, path, True)


def _run(root, intervals, now, days, path, write):
    data, newest = read_existing(path)
    names = hosts_in(root, intervals)
    coverage = {h: read_coverage(coverage_path(root, h)) for h in names}
    files = {h: Host(root, h, now, intervals.get(h) or hostsmod.DRAIN_DEFAULTS["beat_interval_s"]) for h in names}
    looked = {}
    for h in names:
        row = files[h].look()
        if row is not None:
            looked[h] = row
            coverage[h].append(row)
    fresh, held = plan(build(files, coverage, now, days), newest)
    if write:
        for h in looked:
            keep = [r for r in coverage[h] if _end(r) >= now - COVERAGE_KEEP_S]
            _replace(coverage_path(root, h), _jsonl(keep))
        if fresh:
            _replace(path, data + _jsonl(fresh))
    return fresh, held
