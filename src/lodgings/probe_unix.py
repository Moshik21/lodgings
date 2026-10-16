"""Capability probe for Linux, WSL and macOS. Python 3.8+, standard library only, read-only.

This file is self-contained on purpose: `lodgings probe` streams it over ssh to `python3 -` on a remote host, so
nothing is installed or written there. It can also be run by hand:

    python3 probe_unix.py            # prints one line: LODGINGS-FACTS {json}

It prints facts only (see docs/capabilities.md "Raw facts"); deciding what they mean happens on the collecting
host. It records no user name, host name or file path. The commands it may run are listed in the code below:
version flags of the configured tools, `sw_vers`, `sysctl`, `pmset -g batt`, `system_profiler SPDisplaysDataType`,
`security find-identity`, `xcode-select -p`, `lspci -mm` and `nvidia-smi --query-gpu`. With `deep` it also starts
the installed Chrome once, headless, on a throw-away profile in a temporary directory that is removed afterwards.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

PROBE_VERSION = "0.1.0"
DEFAULT_CFG = {"tools": {}, "timeout_s": 20, "deep": False}
MARK = "LODGINGS-FACTS "

WEBGPU_PAGE = """<!doctype html><body>pending<script>
(async()=>{const r={};try{r.gpu=!!navigator.gpu;if(navigator.gpu){const a=await navigator.gpu.requestAdapter();r.adapter=!!a;
if(a){const i=a.info||{};r.vendor=i.vendor||'';r.arch=i.architecture||'';r.fallback=!!a.isFallbackAdapter;}}}catch(e){r.err=String(e)}
try{const c=document.createElement('canvas');const g=c.getContext('webgl');if(g){const x=g.getExtension('WEBGL_debug_renderer_info');
r.renderer=x?g.getParameter(x.UNMASKED_RENDERER_WEBGL):'(no debug info)';}else r.renderer=null}catch(e){r.glerr=String(e)}
document.body.textContent='LODGINGS-WEBGPU '+JSON.stringify(r);})();
</script>
"""


def run(argv, timeout, merge_err=True):
    """-> (returncode, text) or None when the command is missing or times out. Never raises."""
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT if merge_err else subprocess.DEVNULL, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode, p.stdout.decode("utf-8", "replace")


def read(path, limit=65536):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return None


def gb(n):
    return round(n / 1e9, 1)


def vendor_of(text):
    t = (text or "").lower()
    for key, name in (("nvidia", "nvidia"), ("advanced micro devices", "amd"), ("amd", "amd"), ("radeon", "amd"), ("intel", "intel"),
                      ("apple", "apple")):
        if key in t:
            return name
    return "other"


def is_virtual_gpu(text):
    t = (text or "").lower()
    return any(k in t for k in ("llvmpipe", "swiftshader", "vmware", "virtio", "qxl", "basic render", "basic display", "parallels", "virtualbox", "hyper-v"))


# ---- sections ------------------------------------------------------------------------------
def section_os(f):
    system = platform.system()
    arch = platform.machine().lower()
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(arch, arch)
    if system == "Darwin":
        ver = run(["sw_vers", "-productVersion"], 5)
        build = run(["sw_vers", "-buildVersion"], 5)
        v = ver[1].strip() if ver else platform.mac_ver()[0]
        f["os"] = {"family": "darwin", "name": "macOS " + v, "version": v, "build": build[1].strip() if build else None,
                   "kernel": platform.release(), "arch": arch, "wsl": None, "view": "darwin"}
        return
    rel = {}
    for line in (read("/etc/os-release") or "").splitlines():
        if "=" in line:
            k, _, val = line.partition("=")
            rel[k] = val.strip().strip('"')
    kernel = platform.release()
    proc_version = (read("/proc/version") or "").lower()
    wsl = None
    if "microsoft" in proc_version or "microsoft" in kernel.lower() or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        wsl = 2 if "wsl2" in kernel.lower() or "microsoft-standard" in kernel.lower() else 1
    f["os"] = {"family": "linux", "name": rel.get("PRETTY_NAME") or "Linux", "version": rel.get("VERSION_ID"), "build": None,
               "kernel": kernel, "arch": arch, "wsl": wsl, "view": "wsl" if wsl else "linux"}


def section_hardware(f):
    osd = f["os"]
    model, perf = None, None
    mem_total = None
    if osd["family"] == "darwin":
        r = run(["sysctl", "-n", "machdep.cpu.brand_string"], 5)
        model = r[1].strip() if r and r[0] == 0 else None
        r = run(["sysctl", "-n", "hw.memsize"], 5)
        if r and r[0] == 0 and r[1].strip().isdigit():
            mem_total = gb(int(r[1].strip()))
        r = run(["sysctl", "-n", "hw.perflevel0.logicalcpu"], 5)
        if r and r[0] == 0 and r[1].strip().isdigit():
            perf = int(r[1].strip())
    else:
        cpuinfo = (read("/proc/cpuinfo", 1 << 20) or "").splitlines()
        for prefix in ("model name", "hardware", "cpu model"):  # first match of the most specific key wins
            hit = [l for l in cpuinfo if l.lower().startswith(prefix)]
            if hit:
                model = hit[0].partition(":")[2].strip()
                break
        for line in (read("/proc/meminfo") or "").splitlines():
            if line.startswith("MemTotal:"):
                mem_total = gb(int(line.split()[1]) * 1024)
                break
    f["cpu"] = {"model": model, "logical": os.cpu_count(), "perf_logical": perf}
    f["mem"] = {"total_gb": mem_total, "basis": "vm limit (the machine has more)" if osd.get("wsl") else "machine"}
    disks = []
    for label, path, basis in (("/", "/System/Volumes/Data" if osd["family"] == "darwin" and os.path.isdir("/System/Volumes/Data") else "/", "this volume"),):
        try:
            u = shutil.disk_usage(path)
            disks.append({"label": label, "total_gb": gb(u.total), "free_gb": gb(u.free), "basis": basis})
        except OSError:
            pass
    if osd.get("wsl"):  # df inside WSL reports the virtual disk; the Windows drive that holds it is the real limit
        for d in "cd":
            p = "/mnt/" + d
            try:
                if os.path.ismount(p):
                    u = shutil.disk_usage(p)
                    disks.append({"label": d.upper() + ":", "total_gb": gb(u.total), "free_gb": gb(u.free), "basis": "windows drive"})
            except OSError:
                pass
    f["disks"] = disks
    bat = None
    if osd["family"] == "darwin":
        r = run(["pmset", "-g", "batt"], 5)
        if r and r[0] == 0:
            bat = "InternalBattery" in r[1]
    else:
        try:
            bat = any(n.upper().startswith("BAT") for n in os.listdir("/sys/class/power_supply")) or None
        except OSError:
            bat = None
    f["battery"] = bat


def section_gpu(f, timeout):
    osd = f["os"]
    devices, basis, passthrough = [], "none found", None
    if osd["family"] == "darwin":
        r = run(["system_profiler", "SPDisplaysDataType", "-json"], 20, merge_err=False)
        if r and r[0] == 0:
            try:
                for d in json.loads(r[1]).get("SPDisplaysDataType", []):
                    name = d.get("sppci_model") or d.get("_name") or ""
                    devices.append({"vendor": "apple" if "apple" in name.lower() else vendor_of(d.get("spdisplays_vendor") or name),
                                    "name": name, "driver": None})
                basis = "system_profiler"
            except ValueError:
                basis = "system_profiler output not understood"
        f["gpu"] = {"devices": devices, "basis": basis, "passthrough": None}
        return
    smi = shutil.which("nvidia-smi") or ("/usr/lib/wsl/lib/nvidia-smi" if os.path.exists("/usr/lib/wsl/lib/nvidia-smi") else None)
    if smi:
        r = run([smi, "--query-gpu=name,driver_version", "--format=csv,noheader"], timeout)
        if r and r[0] == 0:
            for line in r[1].strip().splitlines():
                name, _, drv = line.partition(",")
                devices.append({"vendor": "nvidia", "name": name.strip(), "driver": drv.strip() or None})
            basis = "nvidia-smi"
    if not devices and shutil.which("lspci"):
        r = run(["lspci", "-mm"], timeout)
        if r and r[0] == 0:
            for line in r[1].splitlines():
                parts = re.findall(r'"([^"]*)"', line)
                if len(parts) >= 3 and re.search(r"VGA|3D|Display", parts[0]):
                    devices.append({"vendor": vendor_of(parts[1]), "name": (parts[1] + " " + parts[2]).strip(), "driver": None})
            basis = "lspci"
    if osd.get("wsl") and os.path.exists("/dev/dxg"):
        passthrough = "wsl-dxg"
        if not devices:
            basis = "/dev/dxg present, device not named"
    f["gpu"] = {"devices": devices, "basis": basis, "passthrough": passthrough}


def find_chrome(osd):
    if osd["family"] == "darwin":
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return p if os.path.exists(p) else None
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(n)
        if p:
            return p
    return None


def plist_version(path):
    try:
        import plistlib
        with open(path, "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except Exception:
        return None


def section_browsers(f, cfg):
    osd = f["os"]
    b = {"chrome": {"present": False, "version": None}, "safari": None, "safaridriver": None, "playwright": {}, "chrome_probe": None}
    path = find_chrome(osd)
    if path:
        ver = None
        if osd["family"] == "darwin":
            ver = plist_version("/Applications/Google Chrome.app/Contents/Info.plist")
        else:
            r = run([path, "--version"], cfg["timeout_s"])
            m = r and re.search(r"(\d+\.\d+\.\d+\.\d+)", r[1])
            ver = m.group(1) if m else None
        b["chrome"] = {"present": True, "version": ver}
    if osd["family"] == "darwin":
        for p in ("/Applications/Safari.app/Contents/Info.plist", "/System/Applications/Safari.app/Contents/Info.plist"):
            if os.path.exists(p):
                b["safari"] = {"present": True, "version": plist_version(p)}
                break
        else:
            b["safari"] = {"present": False, "version": None}
        b["safaridriver"] = os.path.exists("/usr/bin/safaridriver")
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root or root == "0":
        root = os.path.expanduser("~/Library/Caches/ms-playwright" if osd["family"] == "darwin" else "~/.cache/ms-playwright")
    try:
        for n in sorted(os.listdir(root)):
            m = re.match(r"^(chromium|firefox|webkit)-(\d+)$", n)
            if m:
                b["playwright"].setdefault(m.group(1), []).append(int(m.group(2)))
    except OSError:
        pass
    if cfg["deep"] and path:
        b["chrome_probe"] = deep_chrome(path, cfg["timeout_s"])
    f["browsers"] = b


def deep_chrome(path, timeout):
    """Start the installed Chrome once, headless, and ask it whether WebGPU gives a hardware adapter."""
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="lodgings-probe-")
    try:
        page = os.path.join(tmp, "p.html")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(WEBGPU_PAGE)
        argv = [path, "--headless=new", "--disable-extensions", "--no-first-run", "--no-default-browser-check",
                "--user-data-dir=" + os.path.join(tmp, "profile"), "--virtual-time-budget=8000", "--dump-dom", "file://" + page]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            argv.insert(1, "--no-sandbox")
        r = run(argv, timeout, merge_err=False)
        if not r:
            return {"ran": False, "error": "chrome did not answer within %ss" % timeout}
        m = re.search(r"LODGINGS-WEBGPU (\{.*?\})", r[1])
        if not m:
            return {"ran": False, "error": "no answer page output (exit %s)" % r[0]}
        data = json.loads(m.group(1))
        data.update(ran=True, ms=int((time.time() - t0) * 1000))
        return data
    except (OSError, ValueError) as exc:
        return {"ran": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def section_mac(f, timeout):
    if f["os"]["family"] != "darwin":
        f["mac"] = None
        return
    m = {"xcode_clt": False, "xcodebuild": None, "codesign": os.path.exists("/usr/bin/codesign"), "notarytool": False,
         "signing_identities": None}
    r = run(["xcode-select", "-p"], 5)
    if r and r[0] == 0:  # only then are xcodebuild and xcrun safe to call: without developer tools they open an install prompt
        m["xcode_clt"] = True
        if not r[1].strip().endswith("CommandLineTools"):
            v = run(["xcodebuild", "-version"], timeout)
            mm = v and re.search(r"Xcode (\d+(?:\.\d+)*)", v[1])
            m["xcodebuild"] = mm.group(1) if mm else None
        n = run(["xcrun", "--find", "notarytool"], timeout)
        m["notarytool"] = bool(n and n[0] == 0)
    r = run(["security", "find-identity", "-v", "-p", "codesigning"], timeout, merge_err=False)
    if r and r[0] == 0:
        mm = re.search(r"(\d+) valid identit", r[1])
        m["signing_identities"] = int(mm.group(1)) if mm else 0  # a count only: names and hashes are never recorded
    f["mac"] = m


def section_tools(f, cfg):
    out = {}
    for name, spec in sorted(cfg["tools"].items()):
        entry = {"present": False, "version": None, "note": None}
        for cmd in spec["names"]:
            path = shutil.which(cmd)
            if not path:
                continue
            entry["present"] = True
            r = run([path] + list(spec["args"]), cfg["timeout_s"])
            if r is None:
                entry["note"] = "did not answer in %ss" % cfg["timeout_s"]
                continue
            m = re.search(spec["pattern"], r[1], re.M)
            if m:
                entry["version"], entry["note"] = m.group(1), None
                break
            entry["note"] = "no version in the output"
        out[name] = entry
    f["tools"] = out


def collect(cfg=None):
    cfg = dict(DEFAULT_CFG, **(cfg or {}))
    t0 = time.time()
    f = {"schema": "lodgings.facts", "schema_version": "1.0", "collector": "probe_unix " + PROBE_VERSION,
         "collected_epoch": int(t0), "errors": []}
    steps = (("os", lambda: section_os(f)), ("hardware", lambda: section_hardware(f)), ("gpu", lambda: section_gpu(f, cfg["timeout_s"])),
             ("browsers", lambda: section_browsers(f, cfg)), ("mac", lambda: section_mac(f, cfg["timeout_s"])),
             ("tools", lambda: section_tools(f, cfg)))
    for name, step in steps:
        try:
            step()
        except Exception as exc:  # one failed section must not lose the others
            f["errors"].append("%s: %s: %s" % (name, type(exc).__name__, str(exc)[:120]))
    f["collect_ms"] = int((time.time() - t0) * 1000)
    return f


def run_cli(cfg=None):
    sys.stdout.write(MARK + json.dumps(collect(cfg), separators=(",", ":")) + "\n")


if __name__ == "__main__":
    run_cli()
