"""A short, display-ready summary of a host's capabilities: chips and alerts (schema lodgings.chips/1).

Adapters turn this into whatever their dashboard takes; the core knows no dashboard. A chip is
{label, value, severity} with severity ok | warn | critical. A host's problems come first so they survive any
display that shows only the first few.
"""
import time

from . import CHIPS_SCHEMA, CHIPS_VERSION
from .facts import iso, real_gpus

CORE_TAGS = {"linux", "wsl", "macos", "windows", "x86_64", "arm64"}


def age_text(secs):
    secs = max(0, int(secs))
    if secs < 90:
        return "%ds" % secs
    if secs < 5400:
        return "%dm" % round(secs / 60.0)
    if secs < 172800:
        return "%dh" % round(secs / 3600.0)
    return "%dd" % round(secs / 86400.0)


def short_ver(v):
    """22.23.1 -> 22.23 ; 2.10.2 -> 2.10 ; 119 -> 119 ; 10.0.26100.0 -> 10.0"""
    if not v:
        return "?"
    p = str(v).split(".")
    return ".".join(p[:2]) if len(p) > 2 else str(v)


def os_text(doc):
    f = doc["facts"]
    o = f.get("os") or {}
    name = (o.get("name") or "?").replace("Microsoft ", "")
    if o.get("family") == "windows" and o.get("build"):
        name += " " + str(o["build"])
    bits = [name]
    if o.get("wsl"):
        bits.append("WSL%s" % o["wsl"])
    bits.append(o.get("arch") or "?")
    return " · ".join(bits)


def hw_text(doc):
    f = doc["facts"]
    cpu, mem = f.get("cpu") or {}, f.get("mem") or {}
    bits = []
    if cpu.get("logical"):
        bits.append("%d threads" % cpu["logical"])
    if mem.get("total_gb"):
        bits.append("%d GB RAM%s" % (round(mem["total_gb"]), " (VM limit)" if "vm" in (mem.get("basis") or "") else ""))
    return " · ".join(bits) or "unknown"


def shorten_gpu(name):
    for junk in ("NVIDIA ", "GeForce ", "(R)", "(TM)", "Laptop GPU", "AMD ", "Apple "):
        name = name.replace(junk, "")
    return " ".join(name.split())


def gpu_chip(doc):
    f = doc["facts"]
    if f.get("gpu") is None:
        return {"label": "gpu", "value": "unknown", "severity": "warn"}
    real = real_gpus(f)
    if not real:
        if f["gpu"].get("passthrough"):
            return {"label": "gpu", "value": "passthrough, device not named", "severity": "ok"}
        return {"label": "gpu", "value": "none", "severity": "ok"}
    names = [shorten_gpu(d["name"]) for d in real]
    return {"label": "gpu", "value": " + ".join(names), "severity": "ok"}


def chrome_chip(doc):
    b = (doc["facts"].get("browsers"))
    if b is None:
        return {"label": "chrome", "value": "unknown", "severity": "warn"}
    c = b.get("chrome") or {}
    if not c.get("present"):
        return {"label": "chrome", "value": "none", "severity": "ok"}
    w = doc["webgpu"]
    word = {"yes": "webgpu yes", "no": "webgpu no", "unknown": "webgpu unmeasured"}[w["state"]]
    sev = "ok"
    if "chrome-webgpu" in doc["tags"]["contradicted"]:
        sev = "warn"
    return {"label": "chrome", "value": "%s · %s" % ((c.get("version") or "?").split(".")[0], word), "severity": sev}


def tools_chip(doc):
    tools = doc["facts"].get("tools") or {}
    watched = [e["tool"] for e in doc["expect"]]  # tools somebody expects come first so a short display still shows them
    order = sorted(tools, key=lambda n: (n not in watched, n))
    have = ["%s %s" % (n, short_ver(tools[n]["version"])) for n in order if tools[n].get("version")]
    if not have:
        return None
    shown = have[:8]
    more = len(have) - len(shown)
    return {"label": "tools", "value": " · ".join(shown) + (" · +%d" % more if more > 0 else ""), "severity": "ok"}


def sign_chip(doc):
    f = doc["facts"]
    if f.get("mac"):
        m = f["mac"]
        bits = ["xcode %s" % short_ver(m["xcodebuild"]) if m.get("xcodebuild") else ("clt" if m.get("xcode_clt") else "no xcode")]
        bits.append("notarytool" if m.get("notarytool") else "no notarytool")
        n = m.get("signing_identities")
        bits.append("identities ?" if n is None else "%d signing identit%s" % (n, "y" if n == 1 else "ies"))
        return {"label": "mac build", "value": " · ".join(bits), "severity": "ok"}
    if f.get("windows"):
        w = f["windows"]
        bits = ["signtool %s" % short_ver(w["signtool"]) if w.get("signtool") else "no signtool", "%d signing cert%s" % (w.get("code_signing_certs") or 0, "" if (w.get("code_signing_certs") == 1) else "s")]
        bits.append("desktop session" if w.get("desktop_session") else "no desktop session")
        return {"label": "win build", "value": " · ".join(bits), "severity": "ok"}
    return None


def webkit_chip(doc):
    b = doc["facts"].get("browsers") or {}
    bits = []
    if (b.get("safari") or {}).get("present"):
        bits.append("safari %s" % short_ver(b["safari"].get("version")))
    pw = (b.get("playwright") or {}).get("webkit")
    if pw:
        bits.append("playwright webkit %s" % max(pw))
    return {"label": "webkit", "value": " · ".join(bits), "severity": "ok"} if bits else None


def highlight_tags(doc):
    """The capability tags worth a glance: no OS, arch or plain tool names (those have their own chips)."""
    t = doc["tags"]
    tools = set((doc["facts"].get("tools") or {}).keys())
    out = [x for x in t["effective"] if x not in CORE_TAGS and x not in tools]
    skip = {"chrome", "playwright", "gpu", "xcode-clt"}
    return [x for x in out if x not in skip]


def drift_text(changes, limit=3):
    parts = []
    changes = sorted(changes, key=lambda c: (not c["attention"], c["field"]))  # toolchain and OS before browser builds
    for c in changes[:limit]:
        name = c["field"].split(".", 1)[-1]
        if isinstance(c["from"], list) or isinstance(c["to"], list):
            parts.append("%s changed" % name)
        else:
            parts.append("%s %s→%s" % (name, short_ver(c["from"]) if c["from"] not in (None, "absent") else (c["from"] or "new"),
                                      short_ver(c["to"]) if c["to"] not in (None, "absent") else (c["to"] or "gone")))
    more = len(changes) - limit
    return "; ".join(parts) + ("; +%d more" % more if more > 0 else "")


def build(host, doc, status, pool, now=None):
    """host: registry record; doc: current capabilities (or None); status: last attempt record (or None)."""
    now = now or time.time()
    out = {"schema": CHIPS_SCHEMA, "schema_version": CHIPS_VERSION, "host": host["name"], "label": host["label"], "machine": host["machine"],
           "view": host["view"], "as_of": iso(now), "probed_at": None, "probe_age_s": None, "state": "never", "fingerprint": None,
           "transport": None, "chips": [], "alerts": []}
    chips, alerts = [], []
    failed = bool(status) and status.get("ok") is False
    if not doc:
        out["state"] = "failed" if failed else "never"
        value = "probe failed: %s" % status["error"] if failed else "never probed"
        chips.append({"label": "capabilities", "value": value, "severity": "warn"})
        alerts.append({"id": "pool:%s:unknown" % host["name"], "kind": "machine", "severity": "action",
                       "title": "%s capabilities unknown" % host["label"], "detail": value, "how": "run lodgings probe %s and read the error" % host["name"]})
        out["chips"], out["alerts"] = chips, alerts
        return out
    age = now - doc["probed_epoch"]
    out.update(probed_at=doc["probed_at"], probe_age_s=int(age), fingerprint=doc["fingerprint"]["short"], transport=doc["probe"]["transport"])
    stale = age > pool["stale_after_h"] * 3600
    out["state"] = "stale" if stale else ("failed" if failed else "ok")
    if failed:
        chips.append({"label": "probe", "value": "failing since %s: %s" % (age_text(age), status["error"]), "severity": "warn"})
        alerts.append({"id": "pool:%s:probe" % host["name"], "kind": "machine", "severity": "action",
                       "title": "%s probe is failing" % host["label"], "detail": "last good %s ago; %s" % (age_text(age), status["error"]),
                       "how": "run lodgings probe %s and read the error" % host["name"]})
    elif stale:
        chips.append({"label": "probe", "value": "last good %s ago" % age_text(age), "severity": "warn"})
    elif doc["probe"]["transport"] == "fixture":
        chips.append({"label": "probe", "value": "replayed fixture", "severity": "ok"})
    for e in doc["probe"]["errors"]:
        chips.append({"label": "probe note", "value": e, "severity": "warn"})
    bad = [e for e in doc["expect"] if e["ok"] is False]
    if bad:
        txt = "; ".join("%s %s (want %s)" % (e["tool"], e["have"] or "absent", e["want"] if e["want"] != "*" else "any") for e in bad[:3])
        chips.append({"label": "expected", "value": txt, "severity": "warn"})
        alerts.append({"id": "pool:%s:expect" % host["name"], "kind": "machine", "severity": "action",
                       "title": "%s toolchain differs from what is expected" % host["label"], "detail": txt, "how": "update the host or the expectation in hosts.toml"})
    if doc["tags"]["contradicted"]:
        txt = ", ".join(doc["tags"]["contradicted"][:4])
        chips.append({"label": "declared, not seen", "value": txt, "severity": "warn"})
        alerts.append({"id": "pool:%s:declared" % host["name"], "kind": "machine", "severity": "action",
                       "title": "%s lacks declared capabilities" % host["label"], "detail": txt, "how": "fix the host or remove the tag in hosts.toml"})
    d = doc.get("drift")
    if d and now - d["changed_epoch"] <= pool["drift_visible_h"] * 3600:
        attn = any(c["attention"] for c in d["changes"])
        chips.append({"label": "changed %s ago" % age_text(now - d["changed_epoch"]), "value": drift_text(d["changes"]), "severity": "warn" if attn else "ok"})
        alerts.append({"id": "pool:%s:drift" % host["name"], "kind": "machine", "severity": "info",
                       "title": "%s toolchain changed" % host["label"], "detail": drift_text(d["changes"], 4), "how": "check it was intended"})
    chips.append({"label": "os", "value": os_text(doc), "severity": "ok"})
    chips.append({"label": "hw", "value": hw_text(doc), "severity": "ok"})
    chips.append(gpu_chip(doc))
    chips.append(chrome_chip(doc))
    for extra in (webkit_chip(doc), sign_chip(doc), tools_chip(doc)):
        if extra:
            chips.append(extra)
    tags = highlight_tags(doc)
    if tags:
        chips.append({"label": "can", "value": " ".join(tags), "severity": "ok"})
    chips.append({"label": "fingerprint", "value": doc["fingerprint"]["short"], "severity": "ok"})
    out["chips"], out["alerts"] = chips, alerts
    return out
