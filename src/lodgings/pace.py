"""Back-off for the foreground loops (`lodgings beat --every`, `lodgings watch`): cost the machine as little as possible.

A loop that finds the machine busy or on a low battery lengthens its own interval and SAYS SO: a paced beat carries
`paced: {reason, factor}` and the interval it will now keep, so the pool reads "slowed on purpose", never silence read as
a lost host (docs/drain.md, "Back-off and paced beats"). Nothing here starts a process: the loop that was already running measures
itself, decides, and sleeps longer. When the load clears, the next measurement returns the interval to normal.

The numbers are documented in docs/drain.md ("Back-off and paced beats") and measured in the README ("Resource budget").
"""
import os
import time
from collections import deque

LOAD_LIMIT = 1.0          # back off above this 5-minute load average per logical core
LOAD_CLEAR = 0.75         # ...and return to normal only below this share of the limit (no flapping at the line)
BATTERY_BELOW = 40        # back off on battery below this percent (or when the charge is unknown)
CPU_BUDGET_S = 10.0       # CPU-seconds per rolling hour for the loop itself, its measuring and its delivery command
RUN_WALL_BUDGET_S = 2.0   # wall time for one beat's measuring (its delivery has its own timeout)
MIN_INTERVAL_S = 5.0      # a paced loop is never faster than this
MAX_INTERVAL_S = 120.0    # ...and never slower than this, so a paced host stays well inside lost_after_s
FACTOR_LOAD = ((4.0, 8), (2.0, 4), (1.0, 2))   # (load over this many limits, interval factor)
FACTOR_BATTERY = 4
FACTOR_CPU = 2
DEFER_STEP_S, DEFER_MAX_S = 300.0, 3600.0       # `lodgings watch` waits this long between looks, at most this long in all


def limits(load=None, battery=None, cpu=None):
    return {"load": LOAD_LIMIT if load is None else load, "battery": BATTERY_BELOW if battery is None else battery,
            "cpu": CPU_BUDGET_S if cpu is None else cpu}


def cpu_seconds():
    """CPU time used by this process and the children it has waited for (the delivery command)."""
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


def load_per_core():
    """5-minute load average per logical core, or None where the platform has none (Windows): unknown, never zero."""
    try:
        return os.getloadavg()[1] / float(os.cpu_count() or 1)
    except (OSError, AttributeError):
        return None


def strain(load_pc, power, cpu_hour_s, was_paced, lim=None):
    """-> (interval factor, [reasons]); factor 1 means the machine is fine.  Pure: every input is passed in.

    load_pc is the load per core or None (unknown), power the {"on_battery", "percent"} of `lodgings beat`, cpu_hour_s the
    CPU-seconds this loop used in the last hour (or None). was_paced is true while a back-off is in force, which lowers
    the line the load must fall under before the interval returns to normal.
    """
    lim = lim or limits()
    reasons, factor = [], 1
    if load_pc is not None:
        line = lim["load"] * (LOAD_CLEAR if was_paced else 1.0)
        for over, f in FACTOR_LOAD:
            if load_pc > line * over:
                factor = max(factor, f)
                reasons.append("load %.1f per core (limit %g)" % (load_pc, lim["load"]))
                break
    if power and power.get("on_battery"):
        pct = power.get("percent")
        if pct is None:
            factor = max(factor, FACTOR_BATTERY)
            reasons.append("on battery, charge unknown")
        elif pct < lim["battery"]:
            factor = max(factor, FACTOR_BATTERY)
            reasons.append("on battery at %d%% (below %d%%)" % (pct, lim["battery"]))
    if cpu_hour_s is not None and cpu_hour_s > lim["cpu"]:
        factor = max(factor, FACTOR_CPU)
        reasons.append("cpu budget: %.1f s in the last hour (budget %g s)" % (cpu_hour_s, lim["cpu"]))
    return factor, reasons


def decide(base_s, load_pc, power, cpu_hour_s, was_paced, lim=None):
    """-> None (normal pace) or {"reason", "factor", "interval_s"} for a loop whose normal interval is base_s."""
    factor, reasons = strain(load_pc, power, cpu_hour_s, was_paced, lim)
    if factor == 1:
        return None
    interval = min(max(base_s * factor, MIN_INTERVAL_S), MAX_INTERVAL_S)
    ratio = round(interval / float(base_s), 2)
    if ratio <= 1:  # a loop already at or beyond the cap has nothing left to lengthen
        return None
    return {"reason": "; ".join(reasons), "factor": ratio, "interval_s": interval}


def gist(paced):
    """-> a key that changes only when the interval or the kind of strain does, not with every new reading."""
    if not paced:
        return None
    return paced["interval_s"], tuple(r.split(" ", 1)[0] for r in paced["reason"].split("; "))


class Pacer(object):
    """Remembers the last hour of the loop's own CPU cost and whether a back-off is in force."""

    def __init__(self, base_s, lim=None, enabled=True):
        self.base_s, self.lim, self.enabled = float(base_s), lim or limits(), enabled
        self.cost = deque()  # (monotonic time, cpu-seconds of one round)
        self.paced = None

    def spent(self, now):
        while self.cost and now - self.cost[0][0] > 3600:
            self.cost.popleft()
        return sum(c for _, c in self.cost)

    def assess(self, power, now=None, load_pc=None):
        """Measure and decide. -> None or the paced dict. Never raises: a failed reading is an unknown, not a reason."""
        if not self.enabled:
            return None
        now = time.monotonic() if now is None else now
        load_pc = load_per_core() if load_pc is None else load_pc
        self.paced = decide(self.base_s, load_pc, power, self.spent(now), self.paced is not None, self.lim)
        return self.paced

    def account(self, cpu_s, now=None):
        self.cost.append((time.monotonic() if now is None else now, max(0.0, cpu_s)))

