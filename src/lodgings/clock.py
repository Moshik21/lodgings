"""Sleep-safe time: notice when this machine (the one judging the hosts) was asleep, so its own absence is never
counted against a host.

A host's age is `now - received_epoch`, both on this machine's clock. That is wrong in one case: when this machine slept
(or a WSL distribution was suspended) it heard nothing, and on waking every host would look silent for as long as it
slept. So each look also reads an *awake clock*, one that stops while the machine sleeps, and compares how far it moved
with how far the epoch moved:

    slept = (epoch now - epoch then) - (awake now - awake then)

More than SLEEP_NOTICE_S, or an awake clock that went backwards (a restart), means this machine was not listening, and
silence is only counted from the moment it resumed. The awake clock is CLOCK_MONOTONIC on Linux and WSL,
CLOCK_UPTIME_RAW on macOS and QueryUnbiasedInterruptTime on Windows; where none is available nothing is assumed.
A forward wall-clock step looks the same as sleep and is treated the same way: as a moment this machine cannot vouch for.
"""
import os
import sys
import time

SLEEP_NOTICE_S = 30


def awake_seconds():
    """Seconds this machine has been awake since boot, from a clock that stops during sleep. None if unavailable."""
    try:
        if sys.platform == "darwin" and hasattr(time, "CLOCK_UPTIME_RAW"):
            return time.clock_gettime(time.CLOCK_UPTIME_RAW)
        if sys.platform.startswith("linux") and hasattr(time, "CLOCK_MONOTONIC"):
            return time.clock_gettime(time.CLOCK_MONOTONIC)
        if os.name == "nt":
            import ctypes
            val = ctypes.c_ulonglong(0)
            if ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(val)):
                return val.value / 1e7
    except (OSError, AttributeError, ValueError):
        pass
    return None


def observe(previous, now=None, awake=None):
    """-> (the new observer record, resumed_epoch or None, note or None). `previous` is the last record (or None)."""
    now = time.time() if now is None else now
    awake = awake_seconds() if awake is None else awake
    resumed = (previous or {}).get("resumed_epoch")
    note = None
    if previous and awake is not None and previous.get("awake_s") is not None and previous.get("epoch") is not None:
        if awake < previous["awake_s"]:
            resumed, note = now, "this machine restarted since the last look"
        else:
            slept = (now - previous["epoch"]) - (awake - previous["awake_s"])
            if slept > SLEEP_NOTICE_S:
                resumed, note = now, "this machine was asleep for about %d s" % slept
    return {"epoch": now, "awake_s": awake, "resumed_epoch": resumed}, resumed, note
