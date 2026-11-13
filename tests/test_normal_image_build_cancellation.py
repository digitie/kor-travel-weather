"""실제 소유 group의 leader-first 종료·timeout·취소와 foreign 생존 회귀."""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(os.environ.get("WEATHER_CANCELLATION_BUILDER", ROOT / "scripts/build-images.py"))


def load_builder(path):
    spec = importlib.util.spec_from_file_location("weather_kernel_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def process_state(pid):
    try:
        return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return None


def worker(api, leader_exits, ignore_term, directory, builder_path):
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0  # 이 fixture의 고아만 회수한다.
    build = load_builder(builder_path)
    real_popen, real_killpg = subprocess.Popen, os.killpg
    foreign = real_popen([sys.executable, "-I", "-B", "-c", "import time;time.sleep(30)"],
                         start_new_session=True)
    marker, ready = directory / "group.json", directory / "child-ready"
    descendant_code = (
        "import os,signal,time;from pathlib import Path;"
        + ("signal.signal(signal.SIGTERM,signal.SIG_IGN);" if ignore_term else "")
        + "Path(" + repr(str(ready)) + ").touch();time.sleep(30)"
    )
    leader_code = (
        "import os,subprocess,sys,time,json;from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-I','-B','-c'," + repr(descendant_code) + "]);"
        "ready=Path(" + repr(str(ready)) + ");"
        "\nwhile not ready.exists():time.sleep(.005)\n"
        "Path(" + repr(str(marker)) + ").write_text(json.dumps({'child':p.pid,"
        "'leader':os.getpid(),'pgid':os.getpgrp(),'sid':os.getsid(0)}));"
        + ("os._exit(0)" if leader_exits else "time.sleep(30)")
    )
    created, sent = [], []

    class Child:
        def __init__(self, *args, **kwargs):
            self.actual = real_popen(*args, **kwargs)
            created.append(self.actual)

        def communicate(self, input=None, timeout=None):
            return self.actual.communicate(input, timeout=.35)

        def __getattr__(self, name):
            return getattr(self.actual, name)

    def checked_killpg(pgid, signum):
        assert created and pgid == created[0].pid and pgid != foreign.pid
        assert pgid != os.getpgrp()
        sent.append(signum)
        return real_killpg(pgid, signum)

    def cancel(signum, frame):
        raise InterruptedError("own kernel fixture cancel")

    previous = signal.signal(signal.SIGALRM, cancel)
    caught = None
    proof = {}
    try:
        with patch.object(build.subprocess, "Popen", Child), patch.object(
            build.os, "killpg", checked_killpg
        ), patch.object(build, "_GROUP_GRACE_SECONDS", .15, create=True):
            try:
                command = [sys.executable, "-I", "-B", "-c", leader_code]
                if api == "run":
                    build.run(command)
                else:
                    signal.setitimer(signal.ITIMER_REAL, .4)
                    build.execute_build(command, dict(os.environ), directory / "build.log", [])
            except (subprocess.TimeoutExpired, InterruptedError) as error:
                caught = type(error).__name__
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        data = json.loads(marker.read_text())
        assert len(created) == 1 and data["leader"] == created[0].pid == data["pgid"] == data["sid"]
        state = process_state(data["child"])
        live = state not in (None, "Z", "X")
        proof = {
            "api": api, "leaderExitsFirst": leader_exits, "childIgnoresTERM": ignore_term,
            "leaderPID": data["leader"], "leaderExit": created[0].poll(),
            "ownedChildPID": data["child"], "ownedPGID": data["pgid"],
            "childStateAfterFunction": state, "liveChildAfterFunction": live,
            "foreignPID": foreign.pid, "foreignPGID": os.getpgid(foreign.pid),
            "foreignSurvived": foreign.poll() is None,
            "signalsSentOnlyToOwnedPGID": sent, "caught": caught,
        }
        return not live and proof["foreignSurvived"] and caught is not None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if created:
            owned = created[0]
            assert owned.pid != foreign.pid and owned.pid != os.getpgrp()
            with suppress(ProcessLookupError):
                real_killpg(owned.pid, signal.SIGKILL)
            owned.wait(timeout=3)
            try:
                while os.waitpid(-owned.pid, 0)[0]:
                    pass
            except ChildProcessError:
                pass
            for stream in [owned.stdin, owned.stdout, owned.stderr]:
                if stream is not None:
                    stream.close()
        assert foreign.poll() is None  # producer 정리와 fixture 자체 정리를 구별한다.
        foreign.terminate()
        foreign.wait(timeout=3)
        proof["fixtureChildrenRemaining"] = 0
        (directory / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")


class NativeGroupCancellationTests(unittest.TestCase):
    def run_kernel(self, api, leader_exits, ignore_term):
        with tempfile.TemporaryDirectory(prefix="weather-cancel-", dir="/dev/shm") as tmp:
            directory = Path(tmp)
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker", api,
                 str(int(leader_exits)), str(int(ignore_term)), str(directory), str(BUILDER)],
                capture_output=True, text=True, timeout=8,
            )
            proof = json.loads((directory / "proof.json").read_text())
            if out := os.environ.get("WEATHER_CANCELLATION_PROOF_DIR"):
                path = Path(out) / f"{api}-leader{int(leader_exits)}-ignore{int(ignore_term)}.json"
                path.write_text(json.dumps(proof, indent=2) + "\n")
            self.assertEqual(result.returncode, 0, (proof, result.stderr))
            self.assertFalse(proof["liveChildAfterFunction"])
            self.assertTrue(proof["foreignSurvived"])
            self.assertEqual(proof["fixtureChildrenRemaining"], 0)
            if ignore_term:
                self.assertIn(signal.SIGKILL, proof["signalsSentOnlyToOwnedPGID"])

    def test_run_leader_first_exit_real_pipe_timeout(self):
        self.run_kernel("run", True, False)

    def test_execute_build_leader_first_exit_real_cancel(self):
        self.run_kernel("execute", True, False)

    def test_run_live_leader_term_ignoring_child_requires_group_kill(self):
        self.run_kernel("run", False, True)

    def test_execute_live_leader_term_ignoring_child_requires_group_kill(self):
        self.run_kernel("execute", False, True)

    def test_run_exited_leader_term_ignoring_child_requires_group_kill(self):
        self.run_kernel("run", True, True)

    def test_execute_exited_leader_term_ignoring_child_requires_group_kill(self):
        self.run_kernel("execute", True, True)

    def test_parent_group_is_never_signalled(self):
        build = load_builder(BUILDER)
        with patch.object(build.os, "killpg") as kill:
            with self.assertRaises(build.ContractError):
                build._cleanup_owned_group(SimpleNamespace(pid=os.getpgrp()))
            kill.assert_not_called()

    def test_completed_pid_reused_by_live_foreign_leader_is_not_signalled(self):
        build = load_builder(BUILDER)
        foreign = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time;time.sleep(30)"],
                                   start_new_session=True)
        try:
            stale = SimpleNamespace(pid=foreign.pid, returncode=0, poll=lambda: 0)
            with patch.object(build.os, "killpg") as kill:
                with self.assertRaises(build.ContractError):
                    build._cleanup_owned_group(stale)
                kill.assert_not_called()
            self.assertIsNone(foreign.poll())
        finally:
            foreign.terminate()
            foreign.wait(timeout=3)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        ok = worker(sys.argv[2], bool(int(sys.argv[3])), bool(int(sys.argv[4])),
                    Path(sys.argv[5]), Path(sys.argv[6]))
        raise SystemExit(0 if ok else 1)
    unittest.main(verbosity=2)
