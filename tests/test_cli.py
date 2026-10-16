"""Behavioural tests through the public `lodgings` command only (no imports from src/).

    python3 -m unittest discover -s tests

Standard library only. Facts are replayed from examples/fixtures, remote hosts are stood in for by a fake `ssh`
(LODGINGS_SSH), and the expected values are the recorded ones. The three fingerprints below are golden: they change only when
the fingerprint field set changes, which must be a deliberate edit (bump fields_version in src/lodgings/facts.py).
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LODGINGS = [sys.executable, str(ROOT / "bin" / "lodgings")]
DEMO = str(ROOT / "examples" / "demo-hosts.toml")
GOLDEN = {"mac": "51515edd0bec", "host-a-win": "c5ec80c0672b", "host-a-wsl": "b2744689e94c"}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state = str(self.tmp / "state")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("LODGINGS_SSH", None)
        self.env.pop("LODGINGS_HOSTS", None)

    def lodgings(self, *args, env=None):
        return subprocess.run(LODGINGS + list(args), capture_output=True, text=True, encoding="utf-8", env=env or self.env, timeout=120)

    def demo(self, *args):
        return self.lodgings(*args, "--hosts", DEMO, "--state", self.state)

    def json(self, *args):
        res = self.demo(*args, "--json")
        self.assertIn(res.returncode, (0, 1), res.stdout + res.stderr)
        return json.loads(res.stdout)

    def write_hosts(self, text, name="hosts.toml"):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def shim(self, body):
        p = self.tmp / "fake-ssh"
        p.write_text("#!%s\n%s\n" % (sys.executable, body), encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)


class Registry(Base):
    def test_shipped_examples_are_valid(self):
        shipped = ["examples/hosts.example.toml", "examples/demo-hosts.toml", "tests/fixtures/hosts-rich.toml"]
        self.assertGreaterEqual(len(shipped), 3)
        for f in shipped:
            res = self.lodgings("hosts", "check", "--hosts", str(ROOT / f), "--strict")
            self.assertEqual(res.returncode, 0, f + res.stdout + res.stderr)

    def test_example_registry_works_when_copied_elsewhere(self):
        # the README tells a stranger to copy it to hosts.toml in their own folder
        copy = self.tmp / "hosts.toml"
        copy.write_text((ROOT / "examples" / "hosts.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
        res = self.lodgings("hosts", "check", "--hosts", str(copy), "--strict")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_problems_are_listed_together(self):
        bad = self.write_hosts('''
[host.a]
view = "plan9"
ssh = "x"
local = true
[host.b]
view = "linux"
local = true
sshh = "typo"
[host.c]
view = "linux"
ssh = "-oProxyCommand=evil"
''')
        res = self.lodgings("hosts", "check", "--hosts", bad)
        self.assertEqual(res.returncode, 1)
        for part in ("view must be one of", "give exactly one of", "unknown key 'sshh'", "ssh must be an alias"):
            self.assertIn(part, res.stdout)
        # only the unknown key: a warning, an error under --strict
        warn = self.write_hosts('[host.b]\nview = "linux"\nlocal = true\nsshh = "typo"\n', "warn.toml")
        self.assertEqual(self.lodgings("hosts", "check", "--hosts", warn).returncode, 0)
        self.assertEqual(self.lodgings("hosts", "check", "--hosts", warn, "--strict").returncode, 1)

    def test_builtin_toml_reader_agrees_with_the_standard_one(self):
        richest = str(ROOT / "tests" / "fixtures" / "hosts-rich.toml")
        a = self.lodgings("hosts", "check", "--hosts", richest, "--json")
        b = self.lodgings("hosts", "check", "--hosts", richest, "--json", "--builtin-toml")
        self.assertEqual(json.loads(a.stdout), json.loads(b.stdout))
        for f in ("examples/hosts.example.toml", "examples/demo-hosts.toml"):
            self.assertEqual(self.lodgings("hosts", "check", "--hosts", str(ROOT / f), "--builtin-toml").returncode, 0)

    def test_shell_strings_cannot_be_added_as_tools(self):
        bad = self.write_hosts('[probe.tool.x]\nargv = ["/bin/sh", "-c", "id"]\n[host.a]\nview = "linux"\nlocal = true\n')
        res = self.lodgings("hosts", "check", "--hosts", bad)
        self.assertEqual(res.returncode, 1)
        self.assertIn("bare command name", res.stdout)


class FixtureReplay(Base):
    def probe_all(self):
        res = self.demo("probe")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_replay_gives_the_recorded_answers(self):
        self.probe_all()
        hosts = {h["host"]: h for h in self.json("status")["hosts"]}
        self.assertEqual({k: v["fingerprint"] for k, v in hosts.items()}, GOLDEN)
        self.assertTrue(all(h["state"] == "ok" and h["transport"] == "fixture" for h in hosts.values()))
        self.assertIn("Mac (fixture) toolchain differs from what is expected", hosts["mac"]["alerts"])
        self.assertIn("Tower Windows (fixture) lacks declared capabilities", hosts["host-a-win"]["alerts"])
        self.assertEqual(hosts["host-a-wsl"]["alerts"], [])

    def test_matching_is_a_set_test_with_reasons(self):
        self.probe_all()
        rows = {r["host"]: r for r in self.json("match", "--requires", "gpu,chrome-webgpu")["hosts"]}
        self.assertEqual({k for k, v in rows.items() if v["eligible"]}, {"mac", "host-a-win"})
        self.assertEqual(rows["host-a-wsl"]["missing"], ["chrome-webgpu"])  # measured: software adapter only
        signing = {r["host"] for r in self.json("match", "--requires", "macos-signing")["hosts"] if r["eligible"]}
        self.assertEqual(signing, {"mac"})
        none = self.demo("match", "--requires", "wasm-opt-from-mars")
        self.assertEqual(none.returncode, 1)
        same = {r["host"]: r for r in self.json("match", "--same-toolchain-as", "host-a-win")["hosts"]}
        self.assertTrue(same["host-a-win"]["eligible"])
        self.assertTrue(any(d["field"] == "tool.node" or d["field"] == "os.family" for d in same["mac"]["toolchain_differs"]))

    def test_declared_tags_are_checked_not_trusted(self):
        self.probe_all()
        doc = json.loads(self.demo("show", "host-a-win", "--json").stdout)
        self.assertEqual(doc["tags"]["contradicted"], ["windows-signing"])  # declared, but no valid certificate was seen
        self.assertNotIn("windows-signing", doc["tags"]["effective"])
        wsl = json.loads(self.demo("show", "host-a-wsl", "--json").stdout)
        self.assertEqual(wsl["webgpu"]["state"], "no")
        self.assertIn("software", wsl["webgpu"]["basis"])

    def test_drift_is_recorded_once_and_shown(self):
        self.probe_all()
        self.assertIn("no fingerprint changes", self.demo("history", "mac").stdout)
        res = self.demo("probe", "mac", "--replay", str(ROOT / "examples" / "fixtures" / "macos-arm64-after-update.json"))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("drift:", res.stdout)
        hist = self.json("history", "mac")["changes"]
        self.assertEqual(len(hist), 1)
        fields = {c["field"]: c["class"] for c in hist[0]["changes"]}
        self.assertEqual(fields, {"tool.node": "toolchain", "tool.npm": "toolchain", "tool.spacetime": "toolchain", "browser.chrome": "runtime"})
        # replaying the same facts again is not another change, and the drift stays visible
        self.demo("probe", "mac", "--replay", str(ROOT / "examples" / "fixtures" / "macos-arm64-after-update.json"))
        self.assertEqual(len(self.json("history", "mac")["changes"]), 1)
        chips = json.loads(self.demo("chips", "mac").stdout)["hosts"][0]["chips"]
        drift = [c for c in chips if c["label"].startswith("changed")]
        self.assertEqual(len(drift), 1)
        self.assertIn("node 22.23→24.18", drift[0]["value"])
        self.assertEqual(drift[0]["severity"], "warn")
        # the spacetime expectation is now met
        self.assertEqual([a for a in self.json("status")["hosts"][0]["alerts"]], [])

    def test_output_is_utf8_even_when_the_console_code_page_is_not(self):
        # a pipe on Windows uses the ANSI code page (cp1252), which has no "→"; the output must not depend on it
        env = dict(self.env, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        self.probe_all()
        res = subprocess.run(LODGINGS + ["probe", "mac", "--replay", str(ROOT / "examples" / "fixtures" / "macos-arm64-after-update.json"),
                                         "--hosts", DEMO, "--state", self.state], capture_output=True, env=env, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        self.assertIn("node 22.23→24.18".encode("utf-8"), res.stdout)

    def test_compare_reports_differences_and_exit_code(self):
        self.probe_all()
        res = self.demo("compare", "host-a-win", "host-a-wsl")
        self.assertEqual(res.returncode, 1)
        self.assertIn("os.family", res.stdout)
        self.assertEqual(self.demo("compare", "mac", "mac").returncode, 0)

    def test_unprobed_hosts_are_never_green(self):
        host = self.json("status")["hosts"][0]
        self.assertEqual(host["state"], "never")
        chips = json.loads(self.demo("chips").stdout)["hosts"][0]
        self.assertEqual(chips["chips"][0]["severity"], "warn")
        self.assertEqual(chips["alerts"][0]["severity"], "action")
        self.assertEqual(self.demo("show", "mac").returncode, 1)


@unittest.skipIf(os.name == "nt", "the fake ssh is a script with a shebang")
class SshTransport(Base):
    HOSTS = '[pool]\nstate_dir = "state"\n[probe]\ntimeout_s = 20\n[host.remote-unix]\nview = "%s"\nssh = "remote-unix"\ndeclared = ["linux"]\n'

    def test_probe_program_is_streamed_over_stdin_and_nothing_else(self):
        log = self.tmp / "ssh.log"
        # a faithful stand-in: skip ssh options, record what was asked, then run the remote command locally
        shim = self.shim('''
import json, subprocess, sys
a = sys.argv[1:]
opts = []
i = 0
while i < len(a) and (a[i].startswith("-")):
    if a[i] == "-o":
        opts.append(a[i + 1]); i += 2
    else:
        i += 1
alias, remote = a[i], a[i + 1:]
open(%r, "w").write(json.dumps({"alias": alias, "opts": opts, "remote": remote}))
sys.exit(subprocess.run(remote, stdin=sys.stdin.buffer).returncode)
''' % str(log))
        view = "wsl" if Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists() else ("darwin" if sys.platform == "darwin" else "linux")
        hosts = self.write_hosts(self.HOSTS % view)
        env = dict(self.env, LODGINGS_SSH=shim)
        res = self.lodgings("probe", "--hosts", hosts, "--state", self.state, env=env)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        asked = json.loads(log.read_text())
        self.assertEqual(asked["alias"], "remote-unix")
        self.assertEqual(asked["remote"], ["python3", "-"])
        for opt in ("BatchMode=yes", "ForwardAgent=no", "ClearAllForwardings=yes"):
            self.assertIn(opt, asked["opts"])
        doc = json.loads(self.lodgings("show", "remote-unix", "--hosts", hosts, "--state", self.state, "--json").stdout)
        self.assertEqual(doc["probe"]["transport"], "ssh")
        self.assertIn("git", doc["tags"]["probed"])

    def test_windows_hosts_get_powershell_on_stdin(self):
        log = self.tmp / "ssh-win.log"
        fixture = (ROOT / "examples" / "fixtures" / "windows-tower.json").read_text()
        shim = self.shim('''
import json, sys
a = sys.argv[1:]
stdin = sys.stdin.read()
open(%r, "w").write(json.dumps({"remote": a[-7:], "stdin_head": stdin[:40], "stdin_has_probe": "LODGINGS-FACTS" in stdin}))
print("some banner from the remote shell")
print("LODGINGS-FACTS " + json.dumps(json.loads(%r)))
''' % (str(log), fixture))
        hosts = self.write_hosts('[pool]\nstate_dir = "state"\n[host.win]\nview = "windows"\nssh = "win"\n')
        res = self.lodgings("probe", "--hosts", hosts, "--state", self.state, env=dict(self.env, LODGINGS_SSH=shim))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        asked = json.loads(log.read_text())
        self.assertEqual(asked["remote"], ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"])
        self.assertTrue(asked["stdin_head"].startswith("$LodgingsCfg = '"))
        self.assertTrue(asked["stdin_has_probe"])

    def test_a_failing_host_keeps_its_last_good_record_and_says_so(self):
        good = self.write_hosts('[pool]\nstate_dir = "state"\n[host.h]\nview = "windows"\nfixture = "%s"\n' % str(ROOT / "examples" / "fixtures" / "windows-tower.json").replace("\\", "/"), "good.toml")
        self.assertEqual(self.lodgings("probe", "--hosts", good, "--state", self.state).returncode, 0)
        shim = self.shim('import sys\nsys.stderr.write("ssh: Could not resolve hostname h: Name or service not known\\n")\nsys.exit(255)')
        bad = self.write_hosts('[pool]\nstate_dir = "state"\n[host.h]\nview = "windows"\nssh = "h"\n', "bad.toml")
        res = self.lodgings("probe", "--hosts", bad, "--state", self.state, env=dict(self.env, LODGINGS_SSH=shim))
        self.assertEqual(res.returncode, 3)
        self.assertIn("FAILED", res.stdout)
        self.assertIn("Could not resolve hostname", res.stdout)
        chips = json.loads(self.lodgings("chips", "--hosts", bad, "--state", self.state).stdout)["hosts"][0]
        self.assertEqual(chips["state"], "failed")
        self.assertEqual(chips["fingerprint"], GOLDEN["host-a-win"])  # the last good record is still there
        self.assertEqual(chips["chips"][0]["label"], "probe")
        self.assertIn("failing since", chips["chips"][0]["value"])
        self.assertEqual(chips["alerts"][0]["severity"], "action")

    def test_garbage_from_a_host_is_refused(self):
        shim = self.shim('print("LODGINGS-FACTS {not json")')
        hosts = self.write_hosts('[pool]\nstate_dir = "state"\n[host.h]\nview = "linux"\nssh = "h"\n')
        res = self.lodgings("probe", "--hosts", hosts, "--state", self.state, env=dict(self.env, LODGINGS_SSH=shim))
        self.assertEqual(res.returncode, 3)
        self.assertIn("not valid JSON", res.stdout)


class LocalSmoke(Base):
    """The smoke run: probe this machine for real, read it back. Runs the same on macOS, Linux and Windows."""

    def test_probe_this_machine(self):
        view = "windows" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux"
        hosts = self.write_hosts('[pool]\nstate_dir = "state"\n[host.here]\nview = "%s"\nlocal = true\n' % view)
        version = self.lodgings("--version")
        self.assertEqual(version.returncode, 0)
        self.assertIn("lodgings", version.stdout)
        res = self.lodgings("probe", "--hosts", hosts, "--state", self.state)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        doc = json.loads(self.lodgings("show", "here", "--hosts", hosts, "--state", self.state, "--json").stdout)
        self.assertEqual(doc["schema"], "lodgings.capabilities")
        self.assertTrue(doc["fingerprint"]["hash"].startswith("sha256:"))
        self.assertTrue(doc["facts"]["cpu"]["logical"])
        self.assertIn(doc["facts"]["os"]["family"], ("linux", "darwin", "windows"))
        again = self.lodgings("probe", "--hosts", hosts, "--state", self.state)
        self.assertIn("unchanged", again.stdout)


T0 = 1800000000  # synthetic epoch for replayed beats and events


def beat(host, at, **kw):
    d = {"schema": "lodgings.beat/1", "host": host, "sent_epoch": at, "received_epoch": at, "interval_s": 15}
    d.update(kw)
    return d


def event(kind, host, at, **kw):
    d = {"schema": "lodgings.event/1", "kind": kind, "host": host, "at_epoch": at}
    d.update(kw)
    return d


class HostStates(Base):
    """Host states and drain inputs, from synthetic beats and events replayed through `lodgings record --replay`."""
    POOL = """[pool]
state_dir = "state"
[drain]
canary_failures = 2
[host.lap-win]
view = "windows"
machine = "lap"
fixture = "%(fx)s/windows-tower.json"
[host.lap-wsl]
view = "wsl"
machine = "lap"
fixture = "%(fx)s/wsl-tower.json"
confine = "~/lodgings"
repos = { app = "~/src/app" }
[host.box]
view = "linux"
fixture = "%(fx)s/wsl-tower.json"
confine = "/srv/lodgings"
repos = { app = "/srv/src/app" }
classes = { build = 2 }
[host.desk]
view = "linux"
fixture = "%(fx)s/wsl-tower.json"
drain = { on_battery = false }
"""

    def setUp(self):
        super().setUp()
        self.hosts = self.write_hosts(self.POOL % {"fx": str(ROOT / "examples" / "fixtures").replace("\\", "/")})

    def run_h(self, *args):
        return self.lodgings(*args, "--hosts", self.hosts, "--state", self.state)

    def record(self, docs):
        f = self.tmp / ("rec-%d.jsonl" % len(list(self.tmp.glob("rec-*"))))
        f.write_text("".join(json.dumps(d) + "\n" for d in docs))
        res = self.run_h("record", "--replay", str(f))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def states(self, now):
        res = self.run_h("states", "--now", str(now), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        return {v["host"]: v for v in json.loads(res.stdout)["hosts"]}

    def test_beat_age_gives_up_late_stale_lost_and_unknown(self):
        self.record([beat("box", T0), beat("desk", T0)])
        expect = {10: "UP", 45: "LATE", 120: "STALE", 1000: "LOST"}
        for dt, state in expect.items():
            v = self.states(T0 + dt)
            self.assertEqual(v["box"]["state"], state, dt)
            self.assertEqual(v["box"]["takes_work"], state in ("UP", "LATE"))
        self.assertEqual(self.states(T0 + 10)["lap-win"]["state"], "UNKNOWN")  # no beat yet: never green
        # records after --now are not seen: the history is judged as it stood then
        self.assertEqual(self.states(T0 - 5)["box"]["state"], "UNKNOWN")

    def test_a_goodbye_reads_asleep_not_lost_and_covers_the_machine(self):
        self.record([beat("lap-win", T0), beat("lap-wsl", T0), beat("box", T0),
                     beat("lap-win", T0 + 5, kind="goodbye", reason="lid")])
        v = self.states(T0 + 6 * 3600)
        self.assertEqual((v["lap-win"]["state"], v["lap-wsl"]["state"]), ("ASLEEP", "ASLEEP"))  # machine-wide, hours later
        self.assertEqual(v["box"]["state"], "LOST")  # no goodbye: a real loss
        # the machine wakes: the view that has not beaten yet is counted from the wake, not from before the sleep
        self.record([beat("lap-win", T0 + 7200)])
        v = self.states(T0 + 7210)
        self.assertEqual((v["lap-win"]["state"], v["lap-wsl"]["state"]), ("UP", "UP"))
        self.assertEqual(self.states(T0 + 7200 + 1000)["lap-wsl"]["state"], "LOST")
        # stopping one WSL distribution says nothing about Windows
        self.record([beat("lap-win", T0 + 9000), beat("lap-wsl", T0 + 9000, kind="goodbye", reason="wsl-stop")])
        v = self.states(T0 + 9010)
        self.assertEqual((v["lap-win"]["state"], v["lap-wsl"]["state"]), ("UP", "ASLEEP"))

    def test_canary_failures_and_fallback_share_drain(self):
        self.record([beat("box", T0 + 100), event("canary", "box", T0, ok=True), event("canary", "box", T0 + 10, ok=False, reason="timed out")])
        v = self.states(T0 + 100)["box"]
        self.assertEqual(v["state"], "UP")
        self.assertIn("the last canary failed (1 of 2 before a drain)", v["notes"])
        self.record([event("canary", "box", T0 + 20, ok=False), beat("box", T0 + 200)])
        v = self.states(T0 + 200)["box"]
        self.assertEqual(v["state"], "DRAINED")  # draining with nothing running
        self.assertIn("2 canaries failed in a row", v["why"][0])
        self.record([event("canary", "box", T0 + 300, ok=True), beat("box", T0 + 300)])
        self.assertEqual(self.states(T0 + 300)["box"]["state"], "UP")  # one pass clears it
        # fallbacks: 2 of 4 is 50% but under the minimum sample of 5; 2 of 5 drains
        self.record([event("placement", "box", T0 + 400 + i, outcome="fallback" if i < 2 else "placed") for i in range(4)] + [beat("box", T0 + 500)])
        self.assertEqual(self.states(T0 + 500)["box"]["state"], "UP")
        self.record([event("placement", "box", T0 + 450, outcome="placed"), beat("box", T0 + 510)])
        v = self.states(T0 + 510)["box"]
        self.assertEqual(v["state"], "DRAINED")
        self.assertIn("2 of 5 placements fell back", v["why"][0])
        self.assertEqual(self.states(T0 + 400 + 3610)["box"]["state"], "LOST")  # the window slid past; the beat is old now

    def test_battery_reboot_and_paused_inputs(self):
        self.record([beat("lap-win", T0, power={"on_battery": True, "percent": 64}), beat("lap-wsl", T0),
                     beat("desk", T0, power={"on_battery": True}), beat("box", T0, paused=True)])
        v = self.states(T0 + 5)
        self.assertEqual(v["lap-wsl"]["state"], "DRAINED")  # the machine is on battery, whichever view said so
        self.assertIn("seen by lap-win", v["lap-wsl"]["why"][0])
        self.assertEqual(v["desk"]["state"], "UP")  # on_battery = false for this host: a note, not a drain
        self.assertTrue(any("battery" in n for n in v["desk"]["notes"]))
        self.assertEqual(v["box"]["state"], "PAUSED")
        self.assertFalse(v["box"]["takes_work"])
        # a pending reboot drains only when due within the hour; the due time is a span on the host's own clock
        skew = 5000  # the host's clock runs 5000 s ahead of ours: it must not matter
        self.record([beat("lap-win", T0 + 100, sent_epoch=T0 + 100 + skew, reboot={"pending": True, "due_epoch": T0 + 100 + skew + 3 * 3600})])
        v = self.states(T0 + 105)
        self.assertEqual(v["lap-win"]["state"], "UP")
        self.assertIn("a reboot is due in 3 h", v["lap-win"]["notes"][0])
        self.record([beat("lap-win", T0 + 200, sent_epoch=T0 + 200 + skew, reboot={"pending": True, "due_epoch": T0 + 200 + skew + 1200})])
        v = self.states(T0 + 205)
        self.assertEqual((v["lap-win"]["state"], v["lap-wsl"]["state"]), ("DRAINED", "DRAINED"))
        self.assertIn("due in 20 min", v["lap-win"]["why"][0])
        self.record([beat("box", T0 + 300, reboot={"pending": True})])  # time unknown: a note by default
        self.assertEqual(self.states(T0 + 305)["box"]["state"], "UP")

    def test_operator_drain_and_undrain(self):
        self.record([beat("box", T0)])
        res = self.run_h("drain", "box", "--reason", "disk swap")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("advice only", res.stdout)
        v = self.states(T0 + 5)["box"]
        self.assertEqual(v["state"], "DRAINED")
        self.assertIn("disk swap", v["why"][0])
        self.run_h("undrain", "box")
        self.assertEqual(self.states(T0 + 5)["box"]["state"], "UP")
        self.assertEqual(self.run_h("drain", "nope").returncode, 2)

    def test_draining_until_the_job_ends_and_lost_jobs_requeue(self):
        self.record([beat("box", T0), event("job", "box", T0 + 1, phase="start", job="j1", lease_s=60, **{"class": "build"})])
        self.run_h("drain", "box")
        self.assertEqual(self.states(T0 + 5)["box"]["state"], "DRAINING")
        self.record([event("job", "box", T0 + 20, phase="end", job="j1", rc=0)])
        self.assertEqual(self.states(T0 + 25)["box"]["state"], "DRAINED")
        self.run_h("undrain", "box")
        # a stale host whose lease ran out with no word is lost at once, and its job requeues
        self.record([event("job", "box", T0 + 30, phase="start", job="j2", lease_s=60)])
        v = self.states(T0 + 30 + 100)["box"]
        self.assertEqual(v["state"], "LOST")
        self.assertEqual(v["requeue_now"], ["j2"])
        self.assertIn("lease of job j2 expired", v["why"][0])

    def test_evacuation_is_an_offer_with_exact_commands(self):
        self.record([beat("lap-win", T0), beat("lap-wsl", T0, reboot={"pending": True, "due_epoch": T0 + 600}), beat("box", T0), beat("desk", T0),
                     event("job", "lap-wsl", T0, phase="start", job="j9", attempt=1, **{"class": "build"}),
                     event("lane", "lap-wsl", T0, phase="park", lane="feat-x", project="app", spec="~/lanes/feat-x.json", seq=2)])
        before = sorted(p.name for p in Path(self.state).rglob("*"))
        res = self.run_h("evacuate", "lap-wsl", "--now", str(T0 + 5), "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout)
        self.assertEqual(sorted(p.name for p in Path(self.state).rglob("*")), before)  # nothing written, nothing run
        self.assertFalse(o["executed"])
        self.assertEqual(o["state"], "DRAINING")  # a WSL distribution's own reboot drains that view only
        self.assertEqual(self.states(T0 + 5)["lap-win"]["state"], "UP")
        self.assertEqual(o["jobs"][0]["next_attempt"], 2)
        self.assertEqual(o["jobs"][0]["targets"], ["box", "desk", "lap-win"])  # all take work; none lacks a build slot
        moves = {m["target"]: m for m in o["lanes"][0]["moves"]}
        box = [s.get("run") for s in moves["box"]["steps"] if s.get("run")]
        self.assertEqual(box, ["lodgings-passport export --lane ~/lanes/feat-x.json --out ~/lodgings/passports --seq 2",
                               "lodgings-passport verify /srv/lodgings/passports/feat-x-2 --repo /srv/src/app",
                               "lodgings-passport import /srv/lodgings/passports/feat-x-2 --repo /srv/src/app --worktree /srv/lodgings/lanes/feat-x",
                               "lodgings-passport confirm ~/lodgings/passports/feat-x-2 --repo ~/src/app"])
        self.assertEqual(moves["box"]["missing"], [])
        self.assertTrue(moves["desk"]["missing"])  # no confine or repos there: placeholders, never guesses
        win = [s.get("run") for s in moves["lap-win"]["steps"] if s.get("run")]
        self.assertTrue(win[1].startswith("py -3 <checkout>\\core\\passport\\bin\\lodgings-passport verify"))
        self.assertTrue(any("<checkout>" in m and "lap-win" in m for m in moves["lap-win"]["missing"]))
        self.assertFalse(any("<checkout>" in m for m in moves["box"]["missing"]))
        text = self.run_h("evacuate", "lap-wsl", "--now", str(T0 + 5)).stdout
        self.assertIn("Lodgings ran nothing", text)
        self.assertIn("core/passport/bin/lodgings-passport", text)  # where the command comes from, not a bare name

    def test_this_machine_sleeping_never_counts_against_a_host(self):
        """The observer's own sleep: silence is counted from when this machine resumed (docs/drain.md)."""
        now = time.time()
        self.record([beat("box", now - 3600)])  # an hour-old beat: LOST on its own
        self.assertEqual(json.loads(self.run_h("states", "--json").stdout)["hosts"][2]["state"], "LOST")
        # pretend the last look happened just before a long sleep: the awake clock moved far less than the epoch
        obs = Path(self.state) / "observer.json"
        rec = json.loads(obs.read_text())
        obs.write_text(json.dumps({"epoch": rec["epoch"] - 7200, "awake_s": rec["awake_s"], "resumed_epoch": None}))
        out = json.loads(self.run_h("states", "--json").stdout)
        box = {v["host"]: v for v in out["hosts"]}["box"]
        if rec["awake_s"] is None:
            self.skipTest("no awake clock on this platform")
        self.assertIn("asleep", out["observer_note"])
        self.assertEqual(box["state"], "LATE")  # unconfirmed since the wake, never LOST for time nobody listened
        self.assertIn("silence is counted from when it resumed", "; ".join(box["why"]))

    def test_record_rejects_bad_records_and_keeps_good_ones(self):
        f = self.tmp / "mixed.jsonl"
        f.write_text("\n".join([json.dumps(beat("box", T0)), json.dumps(beat("ghost", T0)), "{not json",
                                json.dumps(event("job", "box", T0, phase="start", job="x", resubmit="rm -rf /")),
                                json.dumps({"schema": "other/9"})]) + "\n")
        res = self.run_h("record", "--replay", str(f))
        self.assertEqual(res.returncode, 1)
        for part in ("no host 'ghost'", "not valid JSON", "argument list", "schema must be"):
            self.assertIn(part, res.stderr)
        self.assertIn("recorded 1 record", res.stdout)

    def test_a_beat_slowed_on_purpose_is_healthy_but_slow_never_late(self):
        slow = {"interval_s": 60, "paced": {"reason": "load 2.3 per core (limit 1)", "factor": 4}}
        self.record([beat("box", T0, **slow), beat("desk", T0)])  # same age, same silence: only box said it would slow down
        v = self.states(T0 + 100)  # 100 s: past STALE at the normal 15 s pace, inside two announced 60 s beats
        self.assertEqual((v["desk"]["state"], v["desk"]["takes_work"]), ("STALE", False))
        self.assertEqual((v["box"]["state"], v["box"]["takes_work"]), ("UP", True))
        self.assertEqual(v["box"]["paced"], {"reason": "load 2.3 per core (limit 1)", "factor": 4, "interval_s": 60})
        self.assertIn("slowed on purpose", v["box"]["why"][0])
        self.assertIn("load 2.3 per core", v["box"]["why"][0])
        self.assertIsNone(v["desk"]["paced"])
        # it is still judged: late after two announced beats, stale after four, lost at lost_after_s like any host
        for dt, state in {119: "UP", 125: "LATE", 239: "LATE", 245: "STALE", 1000: "LOST"}.items():
            self.assertEqual(self.states(T0 + dt)["box"]["state"], state, dt)
        # the load cleared: the next beat is a normal one and says nothing about pacing
        self.record([beat("box", T0 + 130)])
        v = self.states(T0 + 140)["box"]
        self.assertEqual((v["state"], v["paced"], v["why"]), ("UP", None, []))
        # a paced beat must say how slow it now is, and by how much
        f = self.tmp / "badpace.jsonl"
        f.write_text("\n".join(json.dumps(d) for d in [beat("box", T0 + 200, paced={"reason": "x", "factor": 4}, interval_s=None),
                                                       beat("box", T0 + 201, paced={"reason": "x", "factor": 1}, interval_s=30),
                                                       beat("box", T0 + 202, paced={"factor": 2}, interval_s=30)]) + "\n")
        res = self.run_h("record", "--replay", str(f))
        self.assertEqual(res.returncode, 1)
        for part in ("must carry the interval_s", "factor must be greater than 1", "reason is required"):
            self.assertIn(part, res.stderr)
        self.assertIn("recorded 0 record", res.stdout)

    def test_the_history_judges_a_slowed_beat_by_its_own_interval(self):
        hist = [beat("box", T0 + g) for g in (0, 15)] + [beat("box", T0 + 30, interval_s=60, paced={"reason": "x", "factor": 4}), beat("box", T0 + 120)]
        f = self.tmp / "paced-hist.jsonl"
        f.write_text("".join(json.dumps(d) + "\n" for d in hist))
        b = json.loads(self.run_h("thresholds", "--from", str(f), "--json").stdout)["evidence"]["box"]["beats"]
        self.assertEqual((b["paced_gaps"], b["would_read"]), (1, {"LATE": 0, "STALE": 0, "LOST": 0}))  # a 90 s gap after a 60 s promise

    def test_hosts_check_validates_the_drain_table(self):
        bad = self.write_hosts('[drain]\nfallback_share = 2\nstale_after_s = 5000\ncanary_failure = 3\n[host.a]\nview = "linux"\nlocal = true\ndrain = { on_battery = "yes" }\n', "bad.toml")
        res = self.lodgings("hosts", "check", "--hosts", bad)
        self.assertEqual(res.returncode, 1)
        for part in ("fallback_share must be a share", "the ages must grow", "unknown key 'canary_failure'", "on_battery must be a bool"):
            self.assertIn(part, res.stdout)

    def test_thresholds_report_what_the_history_would_have_done(self):
        hist = [event("canary", "box", T0 + i * 600, ok=ok) for i, ok in enumerate([True, False, True, False, False, False, True])]
        hist += [beat("box", T0 + g) for g in (0, 15, 30, 100, 2000)]
        # the Windows view says goodbye for the machine: the WSL view's silence that follows is no LOST of its own
        hist += [beat("lap-wsl", T0), beat("lap-wsl", T0 + 15), beat("lap-win", T0 + 20, kind="goodbye", reason="sleep", scope="machine"),
                 beat("lap-wsl", T0 + 5000)]
        f = self.tmp / "hist.jsonl"
        f.write_text("".join(json.dumps(d) + "\n" for d in hist))
        out = json.loads(self.run_h("thresholds", "--from", str(f), "--json").stdout)["evidence"]
        ev = out["box"]
        self.assertEqual(ev["canaries"]["failure_runs"], [1, 3])
        self.assertEqual(ev["canaries"]["drains_if_threshold"], {"1": 2, "2": 1, "3": 1})
        self.assertEqual(ev["beats"]["would_read"], {"LATE": 0, "STALE": 1, "LOST": 1})
        self.assertEqual(out["lap-wsl"]["beats"]["would_read"]["LOST"], 0)
        self.assertEqual((out["lap-wsl"]["beats"]["beats"], out["lap-wsl"]["beats"]["goodbye_gaps_skipped"]), (3, 1))


@unittest.skipIf(os.name == "nt", "reads /sys and /proc on Linux; the Windows path is the same code with other sources")
class BeatSmoke(Base):
    def test_this_machine_beats_and_says_goodbye(self):
        hosts = self.write_hosts('[pool]\nstate_dir = "state"\n[host.here]\nview = "linux"\nlocal = true\n')
        marker = self.tmp / "paused"
        res = self.lodgings("beat", "--hosts", hosts, "--paused-marker", str(marker), "--reboot-in", "20m")
        self.assertEqual(res.returncode, 0, res.stderr)
        doc = json.loads(res.stdout)
        self.assertEqual((doc["schema"], doc["host"], doc["kind"], doc["paused"]), ("lodgings.beat/1", "here", "beat", False))
        self.assertAlmostEqual(doc["reboot"]["due_epoch"] - doc["sent_epoch"], 1200, delta=2)
        marker.write_text("")
        bye = self.lodgings("beat", "--hosts", hosts, "--goodbye", "sleep", "--paused-marker", str(marker), "--record", "--state", self.state)
        self.assertEqual(bye.returncode, 0, bye.stderr)
        v = json.loads(self.lodgings("states", "--hosts", hosts, "--state", self.state, "--json").stdout)["hosts"][0]
        self.assertEqual(v["state"], "ASLEEP")
        piped = subprocess.run(LODGINGS + ["beat", "--hosts", hosts, "--paused-marker", str(marker), "--", sys.executable, str(ROOT / "bin" / "lodgings"), "record", "-", "--hosts", hosts, "--state", self.state],
                               capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(piped.returncode, 0, piped.stdout + piped.stderr)
        self.assertEqual(json.loads(self.lodgings("states", "--hosts", hosts, "--state", self.state, "--json").stdout)["hosts"][0]["state"], "PAUSED")


    def loop_beat(self, *args):
        """Start `lodgings beat --every 5s`, take its first record from --out, stop it. -> (record, its stderr)."""
        out = self.tmp / ("loop-%d.json" % len(list(self.tmp.glob("loop-*"))))
        proc = subprocess.Popen(LODGINGS + ["beat", "--host", "here", "--every", "5s", "--out", str(out)] + list(args),
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=self.env)
        try:
            deadline = time.time() + 20
            while not out.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(out.exists(), "no beat within 20 s")
            doc = json.loads(out.read_text())
        finally:
            proc.terminate()
            err = proc.communicate(timeout=20)[1]
        return doc, err

    def test_a_loop_under_load_slows_itself_and_says_so_in_the_beat(self):
        doc, err = self.loop_beat("--pace-load", "0.00001")  # any load at all is over this limit
        self.assertGreater(doc["paced"]["factor"], 1)
        self.assertIn("load", doc["paced"]["reason"])
        self.assertAlmostEqual(doc["interval_s"], 5 * doc["paced"]["factor"], places=1)  # the interval it will now keep
        self.assertLessEqual(doc["interval_s"], 120)
        self.assertIn("slowed on purpose", err)
        calm, err = self.loop_beat("--pace-load", "100000", "--pace-battery", "0")  # nothing is over this limit
        self.assertEqual((calm["interval_s"], "paced" in calm), (5, False))
        self.assertNotIn("slowed", err)
        off, _ = self.loop_beat("--pace-load", "0.00001", "--no-pace")
        self.assertEqual((off["interval_s"], "paced" in off), (5, False))


class Ledger(Base):
    """The pool's value ledger, from synthetic records: numbers carry a recountable digest, unknowns are never zeros."""
    D0 = 1799971200  # the start of the UTC day that holds T0
    NOW = D0 + 86400 + 3600  # an hour into the next day: the day above is closed

    def setUp(self):
        super().setUp()
        self.hosts = self.write_hosts(HostStates.POOL % {"fx": str(ROOT / "examples" / "fixtures").replace("\\", "/")})
        self.path = Path(self.state) / "ledger" / "lodgings.jsonl"

    def run_h(self, *args):
        return self.lodgings(*args, "--hosts", self.hosts, "--state", self.state)

    def record(self, docs):
        f = self.tmp / ("rec-%d.jsonl" % len(list(self.tmp.glob("rec-*"))))
        f.write_text("".join(json.dumps(d) + "\n" for d in docs))
        res = self.run_h("record", "--replay", str(f))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def ledger(self, *args):
        return self.run_h("ledger", "--now", str(self.NOW), *args)

    def lines(self):
        """The newest line of each (host, counter) for the day D0."""
        return {(r["host"], r["counter"]): r for r in map(json.loads, self.path.read_text().splitlines()) if r["window"]["from"] == self.D0}

    def hourly(self, start, end, silent=(), every=15):
        """Beats from box and lap-wsl every `every` s, recorded an hour at a time with the writer run after each hour, as
        a scheduler would. lap-wsl sends nothing in the hours that start at the epochs in `silent`."""
        for h in range(start, end, 3600):
            ts = range(h, min(h + 3600, end), every)
            self.record([beat("box", t, interval_s=every) for t in ts] + [beat("lap-wsl", t, interval_s=every) for t in ts if h not in silent])
            res = self.run_h("ledger", "--now", str(min(h + 3600, end)))
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def beats(self, host, start, end, every=600):
        return [beat(host, t, interval_s=every) for t in range(start, end, every)]

    def feed(self):
        d = self.D0
        job = lambda i, at, **kw: event("job", "box", at, phase="start", job="j%d" % i, **kw)
        self.record(self.beats("box", d - 300, d + 86400 + 3600) + self.beats("lap-wsl", d - 300, d + 86400 + 3600) +
                    [event("placement", "box", d + 100, outcome="placed", gate="passed"), event("placement", "box", d + 200, outcome="placed", gate="unchecked"),
                     event("placement", "box", d + 300, outcome="fallback", gate="refused"),
                     job(1, d + 101, queued_s=12), job(2, d + 201, queued_s=75), job(3, d + 301),
                     event("job", "box", d + 400, phase="end", job="j1", rc=0), event("job", "box", d + 500, phase="end", job="j2", rc=0),
                     event("placement", "lap-wsl", d + 150, outcome="placed"), event("placement", "lap-win", d + 150, outcome="placed")])

    def test_numbers_carry_a_digest_a_reviewer_can_recount_and_unknowns_are_not_zeros(self):
        self.feed()
        res = self.ledger()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        got = self.lines()
        box = lambda c: got[("box", c)]
        self.assertEqual([box(c)["value"] for c in ("jobs_placed", "placements_fell_back", "jobs_finished")], [2, 1, 2])
        self.assertEqual((box("gate_refused")["value"], box("gate_unchecked")["value"], box("gate_checked")["value"]), (1, 1, 2))
        self.assertEqual((box("queued_s_sum")["value"], box("jobs_waited_60s")["value"]), (87, 1))
        self.assertEqual(box("queued_s_sum")["quality"], "lower-bound")  # one start carried no queued_s
        self.assertEqual(box("queued_s_sum")["excluded"], {"no_queued_s": 1})
        self.assertEqual(box("jobs_placed")["quality"], "exact")
        self.assertEqual(box("jobs_placed")["coverage"]["covers_window"], 1.0)
        raw = (Path(self.state) / "hosts" / "box" / "events.jsonl").read_bytes().split(b"\n")
        placed = [r for r in raw if r and json.loads(r).get("kind") == "placement" and json.loads(r).get("outcome") == "placed"]
        self.assertEqual(box("jobs_placed")["src"]["digest"], "sha256:" + hashlib.sha256(b"\n".join(placed)).hexdigest())
        # mutation: change one counted row and the recount no longer matches the recorded digest
        mutated = [r.replace(b'"gate":"passed"', b'"gate":"refused"') for r in placed]
        self.assertNotEqual(box("jobs_placed")["src"]["digest"], "sha256:" + hashlib.sha256(b"\n".join(mutated)).hexdigest())
        # drain changes are written only when `lodgings states` runs: a floor, even over a day the beats cover
        self.assertEqual((box("drain_transitions")["value"], box("drain_transitions")["quality"]), (0, "lower-bound"))
        # a host with no records is unknown, never zero; a host that sends no beats has no known coverage and cannot show a drain
        self.assertEqual((got[("desk", "jobs_placed")]["value"], got[("desk", "jobs_placed")]["unknown"]), (None, "no-state-dir"))
        for c in ("jobs_placed", "drain_transitions"):
            self.assertEqual((got[("lap-win", c)]["value"], got[("lap-win", c)]["unknown"]), (None, "field-absent"))
            self.assertIn("no beats", got[("lap-win", c)]["note"])
        for c, why in (("offers", "field-absent"), ("offers_moved", "field-absent"), ("drains_completed", "field-absent"), ("jobs_placed_elsewhere", "field-absent"),
                       ("placement_minutes_vs_local", "not-measurable")):
            self.assertEqual((box(c)["value"], box(c)["unknown"]), (None, why))
            self.assertTrue(box(c)["note"])
        self.assertEqual(got[("lap-wsl", "gate_refused")]["unknown"], "field-absent")  # the one placement carries no gate result
        self.assertIn("no row carries `gate`", got[("lap-wsl", "gate_refused")]["note"])
        self.assertTrue(all(r["value"] is None or r["src"]["digest"].startswith("sha256:") for r in got.values()))
        text = self.path.read_text().lower()
        for word in ("caught", "saved", "prevented", "avoided"):
            self.assertNotIn(word, text)
        # a second run adds nothing, a dry run says so too, and a window still open is never written
        before = self.path.read_bytes()
        again = self.ledger()
        self.assertIn("wrote 0 lines (0 numbers, 0 unknown) for 1 day; %d readings already in the ledger" % len(got), again.stdout)
        dry = self.ledger("--dry-run", "--json")
        self.assertEqual((json.loads(dry.stdout)["would_write"], json.loads(dry.stdout)["already_there"]), (0, len(got)))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(all(r["window"]["to"] <= self.NOW for r in self.lines().values()))

    def test_a_quiet_host_with_continuous_beats_reads_an_exact_zero(self):
        d = self.D0
        # a goodbye and a beat four hours later: an expected absence, not a gap
        quiet = [b for b in self.beats("box", d - 300, d + 86400 + 3600) if not d + 30000 < b["received_epoch"] < d + 44400]
        self.record(quiet + [beat("box", d + 29999, kind="goodbye", reason="sleep")])
        self.assertEqual(self.ledger().returncode, 0)
        got = self.lines()
        for c in ("jobs_placed", "placements_fell_back", "jobs_finished", "queued_s_sum", "gate_refused"):
            self.assertEqual((got[("box", c)]["value"], got[("box", c)]["quality"], got[("box", c)]["coverage"]["covers_window"]), (0, "exact", 1.0), c)

    def test_a_beat_gap_makes_every_events_counter_unknown_never_a_lower_bound(self):
        d = self.D0
        gappy = [b for b in self.beats("box", d - 300, d + 86400 + 3600) if not d + 30000 < b["received_epoch"] < d + 44400]
        self.record(gappy + [event("placement", "box", d + 50000, outcome="placed")])
        self.assertEqual(self.ledger().returncode, 0)
        got = self.lines()
        for c in ("jobs_placed", "placements_fell_back", "drain_transitions"):
            self.assertEqual((got[("box", c)]["value"], got[("box", c)]["unknown"]), (None, "source-truncated"), c)
            self.assertEqual(got[("box", c)]["note"], "no coverage between %d and %d" % (d + 29700, d + 44700))
        self.assertEqual(got[("box", "jobs_placed")]["coverage"]["covers_window"], 0.8263)

    def test_a_day_not_yet_closed_is_left_and_a_later_better_reading_supersedes_the_first(self):
        d = self.D0
        self.record(self.beats("box", d - 300, d + 43200, every=900) + [event("placement", "box", d + 100, outcome="placed")])
        early = self.run_h("ledger", "--now", str(d + 86400 + 1000))  # margin: twice the 900 s interval the beats announced
        self.assertEqual(early.returncode, 0, early.stderr)
        self.assertNotIn(("box", "jobs_placed"), self.lines())
        self.assertIn(("desk", "jobs_placed"), self.lines())
        self.assertEqual(self.ledger().returncode, 0)
        first = self.lines()[("box", "jobs_placed")]
        self.assertEqual((first["id"], first["value"], first["unknown"]), ("jobs_placed@box@%d" % d, None, "source-truncated"))
        self.record(self.beats("box", d + 43200, d + 86400 + 3600, every=900))  # the beats held back arrive with their own times
        res = self.ledger()
        self.assertIn("wrote 6 lines (6 numbers, 0 unknown, 6 corrections)", res.stdout)
        rows = [json.loads(r) for r in self.path.read_text().splitlines()]
        chain = [r for r in rows if r["counter"] == "jobs_placed" and r["host"] == "box"]
        self.assertEqual([(r["id"], r["value"], r["supersedes"]) for r in chain],
                         [("jobs_placed@box@%d" % d, None, None), ("jobs_placed@box@%d#2" % d, 1, "jobs_placed@box@%d" % d)])
        self.assertFalse([r for r in rows if r["counter"] == "offers" and r["supersedes"]])  # an exact line is never superseded
        before = self.path.read_bytes()
        self.assertIn("wrote 0 lines", self.ledger().stdout)  # the same reading is never appended twice
        self.assertEqual(self.path.read_bytes(), before)

    def test_hourly_runs_carry_a_day_past_the_hours_the_beats_file_keeps(self):
        d = self.D0
        self.hourly(d - 3600, self.NOW, silent=(d + 36000,))
        self.assertLess(len((Path(self.state) / "hosts" / "box" / "beats.jsonl").read_text().splitlines()), 1000)  # a few hours, never the day
        got = self.lines()
        for c in ("jobs_placed", "placements_fell_back", "jobs_finished", "queued_s_sum", "gate_refused"):
            self.assertEqual((got[("box", c)]["value"], got[("box", c)]["quality"], got[("box", c)]["coverage"]["covers_window"]), (0, "exact", 1.0), c)
        self.assertEqual((got[("box", "drain_transitions")]["value"], got[("box", "drain_transitions")]["quality"]), (0, "lower-bound"))
        # an hour with no beats from lap-wsl is a hole in its coverage: unknown, with the hole named
        for c in ("jobs_placed", "drain_transitions"):
            self.assertEqual((got[("lap-wsl", c)]["value"], got[("lap-wsl", c)]["unknown"]), (None, "source-truncated"), c)
            self.assertEqual(got[("lap-wsl", c)]["note"], "no coverage between %d and %d" % (d + 36000 - 15, d + 39600))
        self.assertEqual(got[("lap-wsl", "jobs_placed")]["coverage"]["covers_window"], 0.9581)

    def test_a_day_the_writer_never_looked_at_is_unknown(self):
        d = self.D0
        late = d + 86400 + 20000
        self.record([beat("box", t, interval_s=15) for t in range(d, late, 15)])  # the file keeps only the last few hours of these
        res = self.run_h("ledger", "--now", str(late), "--days", "1")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        got = self.lines()
        for c in ("jobs_placed", "drain_transitions"):
            self.assertEqual((got[("box", c)]["value"], got[("box", c)]["unknown"], got[("box", c)]["note"]), (None, "source-truncated", "writer did not observe this day"), c)

    def test_a_non_finite_number_is_rejected_at_the_door(self):
        f = self.tmp / "nan.jsonl"
        f.write_text('{"schema":"lodgings.event/1","kind":"job","host":"box","phase":"start","job":"j1","queued_s":NaN,"at_epoch":%d}\n' % self.D0)
        res = self.run_h("record", "--replay", str(f))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("queued_s must be a finite number", res.stdout + res.stderr)

    def test_a_ledger_that_cannot_be_read_is_never_overwritten(self):
        self.feed()
        for bad in (b"not json\n", b'{"v":1,"id":"x@y@1"}', b'{"v":1}\n', b"\xff\xfe\n"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(bad)
            res = self.ledger()
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertIn("nothing was written", res.stderr)
            self.assertEqual(self.path.read_bytes(), bad)
            self.assertEqual([p.name for p in self.path.parent.iterdir()], ["lodgings.jsonl"])


class SelfAudit(unittest.TestCase):
    def test_product_tree_is_clean(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "self-audit"), str(ROOT)], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_every_denylist_applies_and_the_script_is_scanned_like_any_file(self):
        # Synthetic names and a copy of the script in a scratch tree: nothing real is named here.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            (tree / "scripts").mkdir(parents=True)
            shutil.copy(ROOT / "scripts" / "self-audit", tree / "scripts" / "self-audit")
            (tree / "one.txt").write_text("a line about zebrafruit\n")
            (tree / "two.txt").write_text("a line about quokkaberry\n")
            for name, word in (("dl-one.txt", "zebrafruit"), ("dl-two.txt", "quokkaberry")):
                (Path(tmp) / name).write_text("# a synthetic denylist\n%s\n" % word)

            def audit(*lists):
                cmd = [sys.executable, str(ROOT / "scripts" / "self-audit"), "--exempt", "NONE"]
                for name in lists:
                    cmd += ["--denylist", str(Path(tmp) / name)]
                return subprocess.run(cmd + [str(tree)], capture_output=True, text=True, timeout=120)

            both = audit("dl-one.txt", "dl-two.txt")
            self.assertEqual(both.returncode, 1, both.stdout)
            self.assertIn("one.txt:1: denylisted name: zebrafruit", both.stdout)
            self.assertIn("two.txt:1: denylisted name: quokkaberry", both.stdout)
            self.assertIn("3 files scanned, 2 finding(s)", both.stdout)
            only = audit("dl-two.txt")
            self.assertIn("quokkaberry", only.stdout)
            self.assertNotIn("zebrafruit", only.stdout)
            self.assertEqual(audit().returncode, 0)  # the built-in rules alone know no private words
            with (tree / "scripts" / "self-audit").open("a") as fh:
                fh.write("# zebrafruit\n")
            own = audit("dl-one.txt")
            self.assertEqual(own.returncode, 1, own.stdout)
            self.assertIn("scripts/self-audit:", own.stdout)


if __name__ == "__main__":
    unittest.main()
