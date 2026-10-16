"""The closed list of commands the probe may run, and the tags it can derive.

Every probe command is a fixed argv (never a shell string) that only prints a version. A configuration file can
add tools to this list (`[probe.tool.NAME]`) but cannot make the probe run anything else.
"""
import re

# name -> names to try in order, arguments, version pattern (applied to stdout plus stderr)
TOOLS = {
    "git": {"names": ["git"], "args": ["--version"], "pattern": r"git version (\d+(?:\.\d+)+)"},
    "python3": {"names": ["python3", "python"], "args": ["--version"], "pattern": r"Python (\d+\.\d+(?:\.\d+)?)"},
    "node": {"names": ["node"], "args": ["--version"], "pattern": r"v?(\d+\.\d+\.\d+)"},
    "npm": {"names": ["npm"], "args": ["--version"], "pattern": r"^(\d+\.\d+\.\d+)"},
    "pnpm": {"names": ["pnpm"], "args": ["--version"], "pattern": r"^(\d+\.\d+\.\d+)"},
    "rustc": {"names": ["rustc"], "args": ["--version"], "pattern": r"rustc (\d+\.\d+\.\d+)"},
    "cargo": {"names": ["cargo"], "args": ["--version"], "pattern": r"cargo (\d+\.\d+\.\d+)"},
    "wasm-opt": {"names": ["wasm-opt"], "args": ["--version"], "pattern": r"version (\d+)"},
    "go": {"names": ["go"], "args": ["version"], "pattern": r"go version go(\d+\.\d+(?:\.\d+)?)"},
    "java": {"names": ["java"], "args": ["-version"], "pattern": r'version "([^"]+)"'},
    "dotnet": {"names": ["dotnet"], "args": ["--version"], "pattern": r"^(\d+\.\d+\.\d+)"},
    "docker": {"names": ["docker"], "args": ["--version"], "pattern": r"Docker version (\d+\.\d+\.\d+)"},
    "cmake": {"names": ["cmake"], "args": ["--version"], "pattern": r"cmake version (\d+\.\d+(?:\.\d+)?)"},
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")
VIEWS = ("linux", "wsl", "darwin", "windows")


def tool_specs(probe_cfg):
    """The tools to probe, from the built-ins plus the configuration. -> {name: spec}"""
    wanted = probe_cfg.get("tools")
    specs = {n: dict(s) for n, s in TOOLS.items() if wanted is None or n in wanted}
    for name, extra in (probe_cfg.get("tool") or {}).items():
        argv = extra["argv"]
        specs[name] = {"names": [argv[0]], "args": list(argv[1:]), "pattern": extra.get("pattern") or r"(\d+(?:\.\d+)+)"}
    return specs
