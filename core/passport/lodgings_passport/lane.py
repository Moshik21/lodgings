"""The lane spec (lodgings.lane/1): what to export, from where. Adapters write it; people can too."""

import os
import re

from . import LANE_SCHEMA
from .util import UsageError, read_json

TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
BRANCH_RE = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<!/)(?<!\.lock)$")


def load(path):
    spec = read_json(path, "lane spec")
    if not isinstance(spec, dict):
        raise UsageError("lane spec must be a JSON object: %s" % path)
    return spec


def from_flags(args):
    spec = {}
    for key in ("task", "project", "repo", "worktree", "branch", "base", "pipeline", "decisions"):
        value = getattr(args, key, None)
        if value:
            spec[key] = value
    if getattr(args, "gate_git_dir", None):
        spec["gate"] = {"git_dir": args.gate_git_dir}
    if getattr(args, "gate_ref", None):
        spec.setdefault("gate", {})["ref"] = args.gate_ref
    if getattr(args, "pipeline_head", None):
        spec.setdefault("gate", {})["pipeline_head"] = args.pipeline_head
    if getattr(args, "archive_ref", None):
        spec["archive_refs"] = list(args.archive_ref)
    if getattr(args, "record", None):
        records = {}
        for item in args.record:
            if "=" not in item:
                raise UsageError("--record expects name=path, got %r" % item)
            name, path = item.split("=", 1)
            records[name] = path
        spec["records"] = records
    if getattr(args, "adopt_gate_head", False):
        spec["adopt_gate_head"] = True
    if getattr(args, "confirm_differences", None):
        spec["confirm_differences"] = args.confirm_differences
    if getattr(args, "pr_url", None) or getattr(args, "pr_head", None):
        spec["pr"] = {"url": args.pr_url or "", "head": args.pr_head or ""}
    return spec


def merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def validate(spec):
    spec = dict(spec)
    spec.setdefault("schema", LANE_SCHEMA)
    if spec["schema"] != LANE_SCHEMA:
        raise UsageError("lane spec schema %r is not %s" % (spec["schema"], LANE_SCHEMA))
    for key in ("task", "repo", "branch"):
        if not spec.get(key):
            raise UsageError("lane spec needs %r (or --%s)" % (key, key))
    if not TASK_RE.match(spec["task"]):
        raise UsageError("task id %r must be letters, digits, . _ - (max 100)" % spec["task"])
    if not BRANCH_RE.match(spec["branch"]):
        raise UsageError("branch %r is not a plain branch name" % spec["branch"])
    if spec.get("confirm_differences") and not spec.get("adopt_gate_head"):
        raise UsageError("--confirm-differences only goes with --adopt-gate-head")
    spec["repo"] = os.path.abspath(os.path.expanduser(spec["repo"]))
    if not os.path.exists(spec["repo"]):
        raise UsageError("repo not found: %s" % spec["repo"])
    if spec.get("worktree"):
        spec["worktree"] = os.path.abspath(os.path.expanduser(spec["worktree"]))
    spec.setdefault("project", os.path.basename(spec["repo"].rstrip("/\\")) or "project")
    spec.setdefault("base", "")
    spec.setdefault("archive_refs", [])
    spec.setdefault("records", {})
    spec.setdefault("origin", {})
    gate = spec.get("gate") or {}
    if gate:
        if not gate.get("git_dir"):
            raise UsageError("lane spec gate needs git_dir")
        gate = dict(gate)
        gate["git_dir"] = os.path.abspath(os.path.expanduser(gate["git_dir"]))
        gate.setdefault("ref", "")
        if not gate["ref"]:
            gate["ref"] = "refs/heads/" + spec["branch"]
        spec["gate"] = gate
    return spec
