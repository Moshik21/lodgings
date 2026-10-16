"""Run the capability probe on a host (this machine, over ssh, or from a recorded fixture) and store the result."""
import json
import os
import subprocess
import tempfile
import time

from . import catalogue, facts as factsmod, probe_unix

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = probe_unix.MARK
POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]


class ProbeError(Exception):
    pass


def probe_config(reg, host, deep):
    return {"tools": catalogue.tool_specs(reg.probe), "timeout_s": reg.probe["timeout_s"], "deep": bool(deep)}


def _read_source(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _ssh_argv(alias, remote, timeout_s):
    ssh = os.environ.get("LODGINGS_SSH") or "ssh"
    return [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % max(3, int(timeout_s)), "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes", "-o", "LogLevel=ERROR",
            alias] + remote


def script_for(view, cfg):
    """The program streamed to the host's interpreter, and the command that reads it from stdin."""
    if view == "windows":
        head = "$LodgingsCfg = '%s' | ConvertFrom-Json\n" % json.dumps(cfg, separators=(",", ":")).replace("'", "''")
        return head + _read_source("probe_windows.ps1"), POWERSHELL
    tail = "\nrun_cli(json.loads(%r))\n" % json.dumps(cfg, separators=(",", ":"))
    return _read_source("probe_unix.py").replace('if __name__ == "__main__":\n    run_cli()\n', "") + tail, ["python3", "-"]


def parse_output(text):
    for line in text.splitlines():
        if line.startswith(MARK):
            try:
                return json.loads(line[len(MARK):])
            except ValueError as exc:
                raise ProbeError("the host answered with facts that are not valid JSON (%s)" % exc)
    raise ProbeError("the host did not answer with facts (no %s line)" % MARK.strip())


def collect(reg, host, deep):
    """-> (facts, transport). Raises ProbeError with a plain-text reason."""
    cfg = probe_config(reg, host, deep)
    if host["fixture"]:
        try:
            with open(host["fixture"], encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ProbeError("cannot read fixture: %s" % exc)
        if doc.get("schema") != "lodgings.facts":
            raise ProbeError("fixture is not a lodgings.facts document")
        return doc, "fixture"
    total = reg.probe["timeout_s"] * 4 + 30
    if host["local"]:
        if os.name == "nt":
            code, argv = script_for("windows", cfg)
            return _run(argv, code, total, "this machine", reg.pool["state_dir"]), "local"
        return probe_unix.collect(cfg), "local"
    code, remote = script_for(host["view"], cfg)
    return _run(_ssh_argv(host["ssh"], remote, reg.probe["timeout_s"]), code, total, "ssh " + host["ssh"], reg.pool["state_dir"]), "ssh"


def _run(argv, stdin_text, timeout, what, state_dir):
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    with tempfile.TemporaryFile(dir=state_dir) as errfile:  # a pipe for stderr leaves Windows OpenSSH's ssh.exe hanging at start; a file does not
        try:
            p = subprocess.run(argv, input=stdin_text.encode("utf-8"), stdout=subprocess.PIPE, stderr=errfile, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProbeError("%s did not finish within %d s" % (what, timeout))
        except OSError as exc:
            raise ProbeError("cannot start %s: %s" % (argv[0], exc))
        errfile.seek(0)
        stderr = errfile.read()
    out = p.stdout.decode("utf-8", "replace")
    if MARK not in out:
        err = " ".join(stderr.decode("utf-8", "replace").split())[:200]
        raise ProbeError("%s failed (exit %d)%s" % (what, p.returncode, ": " + err if err else ""))
    return parse_output(out)


def probe_host(reg, store, host, deep=None, save_raw=False, now=None):
    """Probe one host and store the outcome. -> (capabilities doc or None, error text or None)."""
    use_deep = reg.probe["deep"] if deep is None else deep
    if host["deep"] is not None and deep is None:
        use_deep = host["deep"]
    t0 = time.time()
    now = now or t0
    previous = store.current(host["name"])
    try:
        raw, transport = collect(reg, host, use_deep)
        doc = factsmod.build(host, raw, reg.expect, transport, now, int((time.time() - t0) * 1000), previous)
    except ProbeError as exc:
        store.save_failure(host["name"], str(exc), now)
        store.log({"at": int(now), "host": host["name"], "ok": False, "error": str(exc)})
        return None, str(exc)
    store.save_success(host["name"], doc, previous, now)
    if save_raw:
        store.save_raw(host["name"], raw)
    store.log({"at": int(now), "host": host["name"], "ok": True, "transport": transport, "ms": doc["probe"]["ms"],
               "fingerprint": doc["fingerprint"]["short"], "drift": doc["probe"]["changed"]})
    return doc, None
