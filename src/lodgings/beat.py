"""`lodgings beat`: measure this machine's drain inputs and say them as one lodgings.beat/1 record (docs/drain.md).

Runs on the host it describes. Read-only: it reads power state, whether a reboot is pending, and whether the paused
marker file exists; it never changes any of them. What it reads:

    Linux, WSL   /sys/class/power_supply/*/{type,online,status,capacity}, /run/reboot-required (a distribution says a
                 reboot is needed), /run/systemd/shutdown/scheduled (a reboot scheduled with `shutdown -r`, with its time),
                 /proc/sys/kernel/random/boot_id, /proc/uptime
    macOS        `pmset -g batt`, `sysctl -n kern.boottime`
    Windows      GetSystemPowerStatus, GetTickCount64, and whether either registry key Windows Update and servicing
                 use to ask for a restart exists (...\\WindowsUpdate\\Auto Update\\RebootRequired,
                 ...\\Component Based Servicing\\RebootPending). Windows does not publish when it will restart, so
                 the due time is unknown unless you pass --reboot-in or --reboot-at.

With `--every` it also reads the load average (`os.getloadavg`, /proc/loadavg on Linux) to decide whether to back off
(pace.py); a back-off is said inside the beat it does send (`paced`).

A goodbye (`--goodbye REASON`) is the last thing a host says before an expected absence (sleep, lid close, a WSL
distribution stopping), so its silence reads ASLEEP instead of LOST.
"""
import os
import re
import subprocess
import sys
import time

from .records import BEAT_SCHEMA, GOODBYE_REASONS, HOST_SCOPED


def default_paused_marker():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "lodgings", "paused")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _run(argv, timeout=2):  # RUN_WALL_BUDGET_S (pace.py): a reading that takes longer is an unknown
    try:
        r = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def power_linux(root="/sys/class/power_supply"):
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {"on_battery": None, "percent": None}
    mains, batteries = [], []
    for n in names:
        kind = (_read(os.path.join(root, n, "type")) or "").lower()
        if kind in ("mains", "usb", "usb_c", "usb_pd"):
            mains.append(_read(os.path.join(root, n, "online")) == "1")
        elif kind == "battery":
            cap = _read(os.path.join(root, n, "capacity"))
            batteries.append(((_read(os.path.join(root, n, "status")) or "").lower(), int(cap) if cap and cap.isdigit() else None))
    if not batteries:
        return {"on_battery": False if mains else None, "percent": None}
    if mains:
        on_battery = not any(mains)
    else:
        on_battery = any(s == "discharging" for s, _ in batteries)
    pcts = [p for _, p in batteries if p is not None]
    return {"on_battery": on_battery, "percent": min(pcts) if pcts else None}


def power_darwin():
    out = _run(["pmset", "-g", "batt"])
    if out is None:
        return {"on_battery": None, "percent": None}
    m = re.search(r"(\d+)%", out)
    return {"on_battery": "'Battery Power'" in out, "percent": int(m.group(1)) if m else None}


def power_windows():
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        st = Status()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return {"on_battery": None, "percent": None}
        if st.BatteryFlag & 128:  # no system battery
            return {"on_battery": False, "percent": None}
        line = {0: True, 1: False}.get(st.ACLineStatus)
        return {"on_battery": line, "percent": st.BatteryLifePercent if st.BatteryLifePercent <= 100 else None}
    except (OSError, AttributeError, ValueError):
        return {"on_battery": None, "percent": None}


def power_now():
    """This machine's {"on_battery", "percent"}, read the way `measure` reads it."""
    if sys.platform.startswith("linux"):
        return power_linux()
    if sys.platform == "darwin":
        return power_darwin()
    if os.name == "nt":
        return power_windows()
    return {"on_battery": None, "percent": None}


def reboot_linux(now):
    sched = _read("/run/systemd/shutdown/scheduled")
    if sched:
        kv = dict(line.split("=", 1) for line in sched.splitlines() if "=" in line)
        if kv.get("MODE") == "reboot" and kv.get("USEC", "").isdigit():
            return {"pending": True, "due_epoch": int(kv["USEC"]) / 1e6, "source": "shutdown -r scheduled"}
    if os.path.exists("/run/reboot-required"):
        return {"pending": True, "due_epoch": None, "source": "/run/reboot-required"}
    return {"pending": False, "due_epoch": None, "source": None}


def reboot_windows():
    try:
        import winreg
    except ImportError:
        return {"pending": None, "due_epoch": None, "source": None}
    keys = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
    for k in keys:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, k))
            return {"pending": True, "due_epoch": None, "source": "Windows Update" if "WindowsUpdate" in k else "Windows servicing"}
        except OSError:
            continue
    return {"pending": False, "due_epoch": None, "source": None}


def boot_and_uptime(now):
    """-> (boot id or None, seconds since boot or None)."""
    if sys.platform.startswith("linux"):
        up = _read("/proc/uptime")
        return _read("/proc/sys/kernel/random/boot_id"), float(up.split()[0]) if up else None
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "kern.boottime"]) or ""
        m = re.search(r"sec = (\d+)", out)
        return ("boot-%s" % m.group(1) if m else None), (now - int(m.group(1)) if m else None)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            up = ctypes.windll.kernel32.GetTickCount64() / 1000.0
            return "boot-%d" % int(round((now - up) / 60.0) * 60), up
        except (OSError, AttributeError):
            pass
    return None, None


def measure(host, now=None, goodbye=None, scope=None, paused_marker=None, reboot_due=None, interval_s=None, note=None, paced=None, power=None):
    """-> one lodgings.beat/1 record for this machine. `paced` is {"reason", "factor"} when the interval was lengthened on purpose."""
    now = time.time() if now is None else now
    if sys.platform.startswith("linux"):
        reboot = reboot_linux(now)
    elif os.name == "nt":
        reboot = reboot_windows()
    else:
        reboot = {"pending": None, "due_epoch": None, "source": None}
    power = power if power is not None else power_now()
    if reboot_due is not None:
        reboot = {"pending": True, "due_epoch": reboot_due, "source": reboot.get("source") or "given on the command line"}
    boot_id, uptime = boot_and_uptime(now)
    marker = paused_marker or default_paused_marker()
    doc = {"schema": BEAT_SCHEMA, "host": host, "kind": "goodbye" if goodbye else "beat", "sent_epoch": round(now, 3),
           "boot_id": boot_id, "uptime_s": None if uptime is None else round(uptime, 1), "interval_s": interval_s,
           "power": power, "reboot": reboot, "paused": os.path.exists(os.path.expanduser(marker))}
    if goodbye:
        if goodbye not in GOODBYE_REASONS:
            raise ValueError("goodbye reason must be one of %s" % ", ".join(GOODBYE_REASONS))
        doc["reason"] = goodbye
        doc["scope"] = scope or ("host" if goodbye in HOST_SCOPED else "machine")
    if paced and not goodbye:
        doc["paced"] = {"reason": paced["reason"], "factor": paced["factor"]}
    if note:
        doc["note"] = note
    return {k: v for k, v in doc.items() if v is not None}
