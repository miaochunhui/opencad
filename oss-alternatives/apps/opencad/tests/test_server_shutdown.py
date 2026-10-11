"""Deterministic SIGTERM reentry checks against the real server.main.

Only child processes started here receive signals. They bind loopback port 0
and use newly allocated external state. A line tracer selects the actual
Event.wait lock-held window, or holds a test Event lock at the new main sleep
line. A native faulthandler stack proves deadlock rather than treating an
arbitrary wait deadline as evidence. The lock-free callback is an in-memory
negative control, never a product patch. unittest discovery derives its source
root only from this file; --source-root is an explicit standalone CLI override.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import faulthandler
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
import traceback
import types
import unittest
import uuid


sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[4]
OUT_ROOT = Path("/hf3fs-jd/data/tmp/opencad-features-20261010/seat-bootstrap")
RUN_DIR = None
CASES = []


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def hashes():
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            SOURCE_ROOT / "oss-alternatives/apps/opencad/server.py",
            SOURCE_ROOT / "oss-alternatives/apps/opencad/release_state.py",
            Path(__file__).resolve(),
        )
    }


def child(args):
    app = SOURCE_ROOT / "oss-alternatives/apps/opencad"
    sys.path[:0] = [str(app), str(SOURCE_ROOT / "oss-alternatives/packages/cadcore")]
    server = importlib.import_module("server")
    if Path(server.__file__).resolve() != (app / "server.py").resolve():
        raise AssertionError("foreign server module")
    if args.state_root.resolve() == Path("/var/lib/oss-web/opencad"):
        raise AssertionError("production state root forbidden")
    stack_stream = (args.case_dir / "stack.log").open("x", buffering=1)
    faulthandler.register(signal.SIGUSR1, file=stack_stream)
    real_signal = signal.signal
    installed = {}
    main_lines, main_first = inspect.getsourcelines(server.main)
    sleep_targets = [main_first + offset for offset, text in enumerate(main_lines)
                     if text.strip() == "time.sleep(0.2)"]
    if len(sleep_targets) > 1:
        raise AssertionError("ambiguous actual main sleep line")
    legacy = not sleep_targets

    def install(signum, handler):
        if signum == signal.SIGTERM:
            installed["original"] = handler
            if args.child == "lockfree_control":
                cells = handler.__closure__ or ()
                if (handler.__code__.co_freevars != ("stopping",) or len(cells) != 1):
                    raise AssertionError("negative control requires actual main stopping closure")
                stop_cell = cells[0]
                if legacy and not isinstance(stop_cell.cell_contents, threading.Event):
                    raise AssertionError("legacy stop closure is not an Event")
                if not legacy and type(stop_cell.cell_contents) is not bool:
                    raise AssertionError("new stop closure is not a bool")

                def lockfree_stop(unused_signum, unused_frame):
                    # Change only the stop flag, avoiding the reentered lock.
                    if legacy:
                        stop_cell.cell_contents._flag = True
                    else:
                        stop_cell.cell_contents = True

                handler = lockfree_stop
            installed["effective"] = handler
        return real_signal(signum, handler)

    server.signal.signal = install
    wait_code = threading.Event.wait.__code__
    lines, first = inspect.getsourcelines(threading.Event.wait)
    token = "with self._cond:" if args.child == "uncontended" else "signaled = self._flag"
    target = next(first + offset for offset, text in enumerate(lines)
                  if text.strip() == token)
    triggered = False
    side_event = threading.Event()

    def fire(stop_event, trace_line, side_lock=None):
        nonlocal triggered
        triggered = True
        locked = stop_event._cond._lock.locked()
        if locked != (args.child != "uncontended"):
            raise AssertionError("probe did not select the stated lock window")
        could_acquire = stop_event._cond._lock.acquire(False)
        if could_acquire:
            stop_event._cond._lock.release()
        if could_acquire == locked:
            raise AssertionError("held lock non-reentry self-check failed")
        handler = installed["original"]
        record = {
            "pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid(),
            "case": args.child, "lock_held": locked,
            "nonblocking_lock_acquire": could_acquire,
            "entry_variant": "legacy_event" if legacy else "lockfree_bool",
            "trace_line": trace_line,
            "lock_origin": "actual main Event.wait" if legacy else "test Event side lock",
            "handler_file": handler.__code__.co_filename,
            "handler_line": handler.__code__.co_firstlineno,
            "original_handler_installed": installed["effective"] is handler,
            "server_file": server.__file__, "real_main": True,
            "signal_target": "self only", "port": 0,
        }
        write_json(args.case_dir / "armed.json", record)
        os.write(args.event_fd, b"armed\n")
        try:
            signal.raise_signal(signal.SIGTERM)
            write_json(args.case_dir / "handler-returned.json",
                       {"returned": True, "lock_still_held": stop_event._cond._lock.locked()})
        finally:
            if side_lock is not None:
                side_lock.release()

    def trace(frame, event, unused_arg):
        caller = frame.f_back
        legacy_window = (legacy and frame.f_code is wait_code
                         and frame.f_lineno == target and caller is not None
                         and caller.f_code is server.main.__code__)
        if not triggered and event == "line" and legacy_window:
            fire(frame.f_locals["self"], frame.f_lineno)
        return trace

    def main_sleep(seconds):
        caller = sys._getframe(1)
        if (not triggered and caller.f_code is server.main.__code__
                and caller.f_lineno == sleep_targets[0]):
            side_lock = None
            if args.child != "uncontended":
                side_lock = side_event._cond._lock
                side_lock.acquire()
            fire(side_event, caller.f_lineno, side_lock)
        time.sleep(seconds)

    # Never mutate a bool closure inside a main-frame trace callback: CPython
    # can write the callback's stale fast-locals snapshot back over that cell.
    if legacy:
        sys.settrace(trace)
    else:
        server.time = types.SimpleNamespace(sleep=main_sleep)
    try:
        rc = server.main([
            "--host", "127.0.0.1", "--port", "0", "--out-root", str(args.state_root),
        ])
        if not triggered:
            raise AssertionError("actual main never reached the selected shutdown window")
        write_json(args.case_dir / "completed.json", {"returncode": rc})
        return rc
    finally:
        sys.settrace(None)
        stack_stream.close()


def run_case(name):
    directory = RUN_DIR / name
    directory.mkdir()
    state = directory / "state"
    state.mkdir()
    read_fd, write_fd = os.pipe()
    command = [
        sys.executable, "-B", str(Path(__file__).resolve()),
        "--child", name, "--source-root", str(SOURCE_ROOT),
        "--case-dir", str(directory), "--state-root", str(state),
        "--event-fd", str(write_fd),
    ]
    with (directory / "stdout.log").open("x") as stdout, (
        directory / "stderr.log"
    ).open("x") as stderr:
        process = subprocess.Popen(command, pass_fds=(write_fd,), stdout=stdout, stderr=stderr)
        os.close(write_fd)
        record = {"case": name, "argv": command, "pid": process.pid,
                  "deadlock_proven": False, "production_mutated": False}
        try:
            if not select.select([read_fd], [], [], 15)[0]:
                raise AssertionError("isolated main did not reach instrumentation; not deadlock proof")
            if os.read(read_fd, 64) != b"armed\n":
                raise AssertionError("isolated main exited before instrumentation")
            record["armed"] = json.loads((directory / "armed.json").read_text())
            if name == "reentrant":
                deadline = time.monotonic() + 3
                stack = ""
                while time.monotonic() < deadline:
                    if (directory / "handler-returned.json").exists() or process.poll() is not None:
                        break
                    os.kill(process.pid, signal.SIGUSR1)
                    stack = (directory / "stack.log").read_text()
                    if " in _stop" in stack:
                        break
                    time.sleep(0.01)
                stack_frames = (
                    " in __enter__" in stack and " in set" in stack
                    and " in _stop" in stack and " in fire" in stack
                    and str(SOURCE_ROOT / "oss-alternatives/apps/opencad/server.py") in stack
                )
                record["deadlock_proven"] = (
                    record["armed"]["lock_held"] and stack_frames
                    and process.poll() is None
                    and not (directory / "handler-returned.json").exists()
                )
                if record["deadlock_proven"]:
                    record["cleanup_reason"] = "self SIGTERM reentered its held non-reentrant lock; stack proven"
                    process.kill()
                else:
                    process.wait(timeout=5)
            else:
                process.wait(timeout=5)
            if process.poll() is None:
                process.wait(timeout=5)
            record.update(
                returncode=process.returncode,
                handler_returned=(directory / "handler-returned.json").exists(),
                completed=(directory / "completed.json").exists(),
                stack_file=str(directory / "stack.log"),
            )
            if record["handler_returned"]:
                record["handler_evidence"] = json.loads(
                    (directory / "handler-returned.json").read_text())
        except Exception as exc:
            record["instrumentation_error"] = traceback.format_exc()
            raise AssertionError("probe infrastructure failed: " + str(exc)) from exc
        finally:
            os.close(read_fd)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            write_json(directory / "observation.json", record)
            CASES.append(record)
    return record


class ServerShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUN_DIR, CASES
        CASES = []
        cls.before = hashes()
        if RUN_DIR is None:
            RUN_DIR = OUT_ROOT / (
                "shutdown-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                + "-" + uuid.uuid4().hex
            )
            RUN_DIR.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        after = hashes()
        passed = (len(CASES) == 3 and cls.before == after and all(
            item.get("handler_returned") and item.get("completed")
            and item.get("returncode") == 0 and not item.get("deadlock_proven")
            for item in CASES
        ))
        write_json(RUN_DIR / "discovery-result.json", {
            "verdict": "PASS" if passed else "FAIL",
            "source_root": str(SOURCE_ROOT), "test_file": str(Path(__file__).resolve()),
            "source_before": cls.before, "source_after": after,
            "source_unchanged": cls.before == after, "cases": CASES,
            "production_mutated": False,
        })
        if cls.before != after:
            raise AssertionError("shutdown source changed during discovery")

    def test_uncontended_real_sigterm_exits(self):
        result = run_case("uncontended")
        self.assertTrue(result["handler_returned"], result)
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["returncode"], 0, result)

    def test_reentrant_real_sigterm_exits(self):
        result = run_case("reentrant")
        self.assertTrue(result["armed"]["lock_held"], result)
        self.assertFalse(result["armed"]["nonblocking_lock_acquire"], result)
        self.assertTrue(result["armed"]["original_handler_installed"], result)
        self.assertFalse(result["deadlock_proven"],
                         "actual server.main SIGTERM blocks in Event.set on its own held lock; "
                         + result["stack_file"])
        self.assertTrue(result["handler_returned"], result)
        self.assertTrue(result["handler_evidence"]["lock_still_held"], result)
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["returncode"], 0, result)

    def test_lockfree_callback_negative_control_exits(self):
        result = run_case("lockfree_control")
        self.assertTrue(result["armed"]["lock_held"], result)
        self.assertFalse(result["armed"]["original_handler_installed"], result)
        self.assertTrue(result["handler_returned"], result)
        self.assertTrue(result["handler_evidence"]["lock_still_held"], result)
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["returncode"], 0, result)


def main():
    global SOURCE_ROOT, OUT_ROOT, RUN_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_ROOT)
    parser.add_argument("--child", choices=("uncontended", "reentrant", "lockfree_control"))
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--event-fd", type=int)
    args = parser.parse_args()
    SOURCE_ROOT = args.source_root.resolve()
    OUT_ROOT = args.out.resolve()
    if args.child:
        return child(args)
    RUN_DIR = OUT_ROOT / (
        "shutdown-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-" + uuid.uuid4().hex
    )
    RUN_DIR.mkdir(parents=True)
    before = hashes()
    write_json(RUN_DIR / "START.json", {"verdict": "RUNNING", "source_before": before})
    with (RUN_DIR / "tests.log").open("x") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(ServerShutdownTests)
        )
    after = hashes()
    passed = result.wasSuccessful() and before == after
    report = {
        "verdict": "PASS" if passed else "FAIL",
        "time": datetime.now(timezone.utc).isoformat(),
        "tests": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "source_before": before, "source_after": after,
        "source_unchanged": before == after, "production_mutated": False,
        "cases": CASES,
        "findings": [{"test": case.id(), "detail": detail}
                     for case, detail in result.failures + result.errors],
        "proof_limit": "Deterministic legal signal interleaving; no estimate of natural incidence.",
    }
    write_json(RUN_DIR / "result.json", report)
    print("SERVER_SHUTDOWN verdict=%s tests=%s failures=%s errors=%s evidence=%s" % (
        report["verdict"], report["tests"], report["failures"], report["errors"], RUN_DIR,
    ), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
