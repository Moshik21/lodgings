"""Matching: does a host honestly satisfy a job's requirements? A set test, never a score (placement is a later slice)."""
from . import facts as factsmod


def requirements_met(doc, requires):
    """-> (ok, missing, notes). Declared tags count unless the probe contradicts them."""
    if doc is None:
        return False, list(requires), ["never probed"]
    eff = set(doc["tags"]["effective"])
    missing = [r for r in requires if r not in eff]
    notes = []
    for r in requires:
        if r in doc["tags"]["unverified"]:
            notes.append("%s is declared, not measured" % r)
    return not missing, missing, notes


def toolchain_differences(a, b, classes=("toolchain", "os")):
    """Fingerprint fields that differ between two hosts, limited to the classes that decide whether outputs match."""
    out = []
    fa, fb = a["fingerprint"]["fields"], b["fingerprint"]["fields"]
    for c in factsmod.diff(fa, fb):
        if c["class"] in classes:
            out.append(c)
    return out
