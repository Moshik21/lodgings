"""Raw facts -> a capabilities document: tags with their evidence, the toolchain fingerprint, expectations, drift.

Everything here is a pure function of the facts (and the host's declaration), so a recorded facts file replays to
the same answer. See docs/capabilities.md for the contract.
"""
import hashlib
import json
import time

from . import CAPS_SCHEMA, CAPS_VERSION, VERSION
from .catalogue import TOOLS

FIELDS_VERSION = 1
SOFTWARE_GPU = ("swiftshader", "llvmpipe", "software", "softpipe")
VIRTUAL_GPU = ("llvmpipe", "swiftshader", "vmware", "virtio", "qxl", "basic render", "basic display", "parallels", "virtualbox",
               "hyper-v", "virtual display", "remote display", "indirect display")

# field-name prefix -> (class, does a change deserve attention?)
FIELD_CLASSES = (("tool.", "toolchain", True), ("os.", "os", True), ("arch", "os", True), ("hw.", "hardware", True),
                 ("browser.", "runtime", False), ("runtime.", "runtime", False), ("sign.", "runtime", False))


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def real_gpus(f):
    out = []
    for d in ((f.get("gpu") or {}).get("devices")) or []:
        if not any(k in (d.get("name") or "").lower() for k in VIRTUAL_GPU):
            out.append(d)
    return out


def webgpu(f):
    """-> {state: yes|no|unknown, basis}. Only a measurement says yes; a missing browser or a software adapter says no."""
    b = f.get("browsers")
    if b is None:
        return {"state": "unknown", "basis": "browser section unavailable"}
    if not (b.get("chrome") or {}).get("present"):
        return {"state": "no", "basis": "no Chrome installed"}
    p = b.get("chrome_probe")
    if not p:
        return {"state": "unknown", "basis": "not measured (deep probe off)"}
    if not p.get("ran"):
        return {"state": "unknown", "basis": "measurement failed: %s" % p.get("error", "no answer")}
    renderer = (p.get("renderer") or "").lower()
    arch = (p.get("arch") or "").lower()
    if not p.get("gpu"):
        return {"state": "no", "basis": "browser has no WebGPU API"}
    if not p.get("adapter"):
        soft = " (renders in software)" if any(k in renderer for k in SOFTWARE_GPU) else ""
        return {"state": "no", "basis": "no WebGPU adapter%s" % soft}
    if p.get("fallback") or any(k in arch or k in renderer for k in SOFTWARE_GPU):
        return {"state": "no", "basis": "software adapter only"}
    return {"state": "yes", "basis": "hardware adapter acquired in %s ms" % p.get("ms", "?")}


def tag_states(f):
    """{tag: True | False | None}: what the facts say about each tag the probe understands (None = cannot tell)."""
    s = {}
    o = f.get("os") or {}
    view = o.get("view")
    if view:
        s["linux"] = view in ("linux", "wsl")
        s["wsl"] = view == "wsl"
        s["macos"] = view == "darwin"
        s["windows"] = view == "windows"
    for a in ("x86_64", "arm64"):
        if o.get("arch"):
            s[a] = o["arch"] == a
    if f.get("gpu") is not None:
        real = real_gpus(f)
        unnamed = bool(f["gpu"].get("passthrough")) and not f["gpu"].get("devices")
        s["gpu"] = True if real else (None if unnamed else False)
        for v in ("nvidia", "amd", "intel", "apple"):
            s["gpu-" + v] = any(d.get("vendor") == v for d in real) if (real or not unnamed) else None
    b = f.get("browsers")
    if b is not None:
        s["chrome"] = bool((b.get("chrome") or {}).get("present"))
        w = webgpu(f)["state"]
        s["chrome-webgpu"] = {"yes": True, "no": False}.get(w)
        pw = b.get("playwright") or {}
        s["playwright"] = bool(pw)
        safari = bool((b.get("safari") or {}).get("present"))
        s["safari"] = safari
        s["webkit"] = safari or bool(pw.get("webkit"))
    m = f.get("mac")
    if o.get("view") == "darwin" and m is not None:
        n = m.get("signing_identities")
        s["macos-signing"] = None if n is None else n > 0
        s["xcode"] = bool(m.get("xcodebuild"))
        s["xcode-clt"] = bool(m.get("xcode_clt"))
        s["notarytool"] = bool(m.get("notarytool"))
    elif o.get("view"):
        for t in ("macos-signing", "xcode", "xcode-clt", "notarytool"):
            s[t] = False
    w = f.get("windows")
    if o.get("view") == "windows" and w is not None:
        s["signtool"] = bool(w.get("signtool"))
        s["windows-signing"] = (w.get("code_signing_certs") or 0) > 0
        s["desktop-session"] = bool(w.get("desktop_session"))
    elif o.get("view"):
        for t in ("signtool", "windows-signing", "desktop-session"):
            s[t] = False
    if "battery" in f:
        s["laptop"] = f["battery"]
    for name, t in (f.get("tools") or {}).items():
        s[name] = bool(t.get("version"))  # present without a readable version (a stub, a broken install) does not count
    return s


def major(v):
    return str(v).split(".")[0] if v else None


def fingerprint_fields(f):
    """The stable, comparable part of the facts, flattened. Volatile values (free disk, load, patch levels) are excluded."""
    o, fl = f.get("os") or {}, {}
    fl["os.family"] = o.get("family")
    fl["os.name"] = o.get("name")
    fl["os.build"] = o.get("build")
    fl["arch"] = o.get("arch")
    cpu, mem = f.get("cpu") or {}, f.get("mem") or {}
    fl["hw.cpu"] = cpu.get("model")
    fl["hw.cores"] = cpu.get("logical")
    fl["hw.mem_gb"] = int(round(mem["total_gb"])) if mem.get("total_gb") else None
    real = real_gpus(f)
    if f.get("gpu") is not None:
        fl["hw.gpus"] = sorted("%s:%s" % (d.get("vendor"), d.get("name")) for d in real)
        fl["runtime.gpu_driver"] = sorted(str(d.get("driver")) for d in real if d.get("driver"))
    for name, t in sorted((f.get("tools") or {}).items()):
        fl["tool." + name] = t.get("version") or ("unversioned" if t.get("present") else "absent")
    b = f.get("browsers")
    if b is not None:
        fl["browser.chrome"] = major((b.get("chrome") or {}).get("version")) if (b.get("chrome") or {}).get("present") else "absent"
        if b.get("safari") is not None:
            fl["browser.safari"] = major((b["safari"] or {}).get("version")) if b["safari"].get("present") else "absent"
        fl["browser.playwright"] = sorted("%s-%s" % (k, r) for k, revs in (b.get("playwright") or {}).items() for r in revs)
        fl["browser.webgpu"] = webgpu(f)["state"]
    if f.get("mac"):
        fl["tool.xcodebuild"] = f["mac"].get("xcodebuild") or "absent"
        n = f["mac"].get("signing_identities")
        fl["sign.macos"] = "unknown" if n is None else ("yes" if n > 0 else "no")
    if f.get("windows"):
        fl["sign.signtool"] = f["windows"].get("signtool") or "absent"
    return {k: v for k, v in fl.items() if v is not None}


def fingerprint(fields):
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    h = hashlib.sha256(blob.encode()).hexdigest()
    return {"hash": "sha256:" + h, "short": h[:12], "fields_version": FIELDS_VERSION, "fields": fields}


def field_class(name):
    for prefix, cls, attention in FIELD_CLASSES:
        if name.startswith(prefix):
            return cls, attention
    return "runtime", False


def diff(old, new):
    changes = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            cls, attn = field_class(k)
            changes.append({"field": k, "from": old.get(k), "to": new.get(k), "class": cls, "attention": attn})
    return changes


def version_matches(have, want):
    if want == "*":
        return bool(have)
    return bool(have) and (have == want or have.startswith(want + ".") or have.startswith(want + "-"))


def evaluate_expect(f, expect):
    out = []
    for name, want in sorted(expect.items()):
        if name == "chrome":
            b = (f.get("browsers") or {}).get("chrome")
            have, known = (b or {}).get("version"), b is not None
        else:
            t = (f.get("tools") or {}).get(name)
            have, known = (t or {}).get("version"), t is not None
        ok = version_matches(have, want) if known else None
        out.append({"tool": name, "want": want, "have": have, "ok": ok})
    return out


def derive_host_class(host, f):
    if host.get("host_class"):
        return host["host_class"]
    o = f.get("os") or {}
    return "%s-%s" % (o.get("view") or host["view"], o.get("arch") or "unknown")


def build(host, facts, expect, transport, now, ms, previous=None):
    """host: a registry host record; facts: raw facts; expect: the pool-wide expectations. -> capabilities document."""
    states = tag_states(facts)
    probed = sorted(t for t, v in states.items() if v is True)
    declared = list(host["declared"])
    verified = [t for t in declared if states.get(t) is True]
    contradicted = [t for t in declared if states.get(t) is False]
    unverified = [t for t in declared if t in states and states[t] is None]
    plain = [t for t in declared if t not in states]
    effective = sorted(set(probed) | (set(declared) - set(contradicted)))
    fields = fingerprint_fields(facts)
    fp = fingerprint(fields)
    merged_expect = dict(expect)
    merged_expect.update(host["expect"])
    detected = (facts.get("os") or {}).get("view")
    doc = {
        "schema": CAPS_SCHEMA, "schema_version": CAPS_VERSION,
        "host": host["name"], "label": host["label"], "machine": host["machine"], "view": host["view"], "view_detected": detected,
        "host_class": derive_host_class(host, facts),
        "probed_at": iso(now), "probed_epoch": int(now),
        "probe": {"level": "deep" if (facts.get("browsers") or {}).get("chrome_probe") else "quick", "transport": transport, "ms": ms, "lodgings": VERSION,
                  "collector": facts.get("collector"), "errors": list(facts.get("errors") or [])[:6],
                  "baseline": previous is None, "changed": False},
        "tags": {"probed": probed, "declared": declared, "verified": verified, "contradicted": contradicted,
                 "unverified": unverified, "plain": plain, "effective": effective},
        "webgpu": webgpu(facts),
        "expect": evaluate_expect(facts, merged_expect),
        "fingerprint": fp,
        "facts": facts,
        "drift": None, "baseline_epoch": int(now),
    }
    if detected and detected != host["view"]:
        doc["probe"]["errors"].append("hosts.toml says view %s but the host reports %s" % (host["view"], detected))
    if previous:
        doc["baseline_epoch"] = previous.get("baseline_epoch", previous.get("probed_epoch", int(now)))
        pf = previous.get("fingerprint") or {}
        if pf.get("fields_version") != FIELDS_VERSION:
            doc["drift"] = None  # the field set itself changed: re-baseline instead of reporting every field
            doc["probe"]["baseline"] = True
        elif pf.get("hash") != fp["hash"]:
            changes = diff(pf.get("fields") or {}, fields)
            doc["probe"]["changed"] = True
            doc["drift"] = {"changed_epoch": int(now), "changed_at": iso(now), "from": pf.get("short"), "to": fp["short"], "changes": changes}
        else:
            doc["drift"] = previous.get("drift")
    return doc
