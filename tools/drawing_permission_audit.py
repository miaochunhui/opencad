#!/usr/bin/env python3
"""Read-only reconstruction of the drawing permission incident.

Never runs the permission helper, touch, chmod, ACL setters or write probes.
Only new evidence files under an existing external --out-parent are created.
Metadata candidates are not proven chmod changes. No before-state is invented.
--scan walks directories without following symlinks, with explicit coverage caps.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
TARGET = Path("/hf3fs-jd/data/tmp")
SCRIPT = Path("/data/dev/infra/ops/3fs-storage/fix-3fs-perms.sh")
SCRIPT_SHA256 = "1b4e735b8ba06a031d6352462a9f369202182c02e5aa64319de2e25c6699a19c"
TRANSCRIPT = Path("/home/mch/.codex/sessions/2026/10/10/"
                  "rollout-2026-10-10T20-54-03-01a125e0-ab24-7c93-a6dc-f47ed1d2e449.jsonl")
INCIDENT = TARGET / "opencad-drawing-web-20261011/permission-check-incident.md"
BAD_COMMAND = ("sudo -n -u infra sudo -n bash ops/3fs-storage/fix-3fs-perms.sh "
               "/hf3fs-jd/data/tmp --check")
OWNED = {3928190, 3928744, 3928756, 3928762}
OBSERVATION_END = "2026-10-11T03:00:00+00:00"


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def regular_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Expected regular file: " + str(path))
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def fingerprint(path):
    data = regular_bytes(path)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def helper_semantics(text, arguments):
    if hashlib.sha256(text.encode()).hexdigest() != SCRIPT_SHA256:
        raise ValueError("Historical helper hash changed; manual review required")
    required = ('[ "${1:-}" = "--check" ]', 'for d in "$@"',
                'if [ "$CHECK" = "0" ]', 'find "$d" -type d ! -perm 2777',
                'find "$d" -type f ! -perm 666', 'mktemp')
    if not all(item in text for item in required):
        raise ValueError("Helper semantics changed; manual review required")
    check = bool(arguments and arguments[0] == "--check")
    return {"check": check, "targets": arguments[1:] if check else arguments,
            "directory_mode_change_enabled": not check,
            "file_mode_change_enabled": not check,
            "check_mode_still_creates_and_removes_probe": True,
            "errors_from_find_and_chmod_are_redirected": "2>/dev/null" in text}


def process_rows(output):
    result = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.+)$", line)
        if not match:
            continue
        pid, parent = int(match[1]), int(match[2])
        command = match[4]
        if pid in OWNED or parent in OWNED or command.startswith("chmod 2777 "):
            # Retain no unrelated process commands or raw global ps output.
            result.append({"pid": pid, "ppid": parent, "third_column": match[3],
                           "command": command})
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+((?:sudo|bash|find|chmod)\s+.+)$", line)
        if match and int(match[1]) in OWNED:
            result.append({"pid": int(match[1]), "ppid": None,
                           "command": match[2], "format": "pgrep"})
    return result


def timeline(path):
    calls, records, candidates = {}, [], []
    first, stop, stopped, killed = None, None, None, None
    exposed_paths = []
    malformed = []
    with path.open("r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                malformed.append(number)
                continue
            at = record.get("timestamp")
            if not at or timestamp(at) >= timestamp(OBSERVATION_END):
                continue
            payload = record.get("payload", {})
            if payload.get("type") == "function_call":
                try:
                    args = json.loads(payload.get("arguments", "{}"))
                except json.JSONDecodeError:
                    continue
                tools = args.get("tool_uses", [{"parameters": args}])
                for tool in tools:
                    params = tool.get("parameters", {})
                    command = params.get("cmd", "")
                    if command == BAD_COMMAND:
                        first = timestamp(at)
                    if first is None or timestamp(at) < first:
                        continue
                    relevant = ("fix-3fs-perms.sh" in command or
                                any(str(pid) in command for pid in OWNED) or
                                command.startswith("sudo -n -u infra ps -eo") or
                                command.startswith("sudo -n -u infra pgrep"))
                    if relevant:
                        entry = {"time": at, "line": number, "kind": "command", "command": command,
                                 "call_id": payload.get("call_id")}
                        records.append(entry)
                        calls[payload.get("call_id")] = entry
                        if command == "sudo -n -u infra sudo -n kill -STOP 3928756 3928762":
                            stop = timestamp(at)
            elif payload.get("type") == "function_call_output":
                call = calls.get(payload.get("call_id"))
                if call is None:
                    continue
                output = payload.get("output", "")
                if not isinstance(output, str):
                    output = json.dumps(output)
                rows = process_rows(output)
                entry = {"time": at, "line": number, "kind": "output",
                         "command": call["command"], "process_rows": rows,
                         "output_sha256": hashlib.sha256(output.encode()).hexdigest()}
                match = re.search(r"Process exited with code (-?\d+)", output)
                entry["rc"] = int(match[1]) if match else None
                entry["output_truncated"] = "truncated" in output.lower()
                if call["command"].startswith("sudo -n -u infra ps -eo"):
                    # The original global ps result was truncated. These are
                    # visible path tokens, not a complete or attributed argv.
                    fragments = sorted(set(re.findall(
                        r"/hf3fs-jd/data/tmp/[^\s\"\\]+", output)))
                    exposed_paths.extend({"path": path, "time": at,
                                          "classification": "unattributed_truncated_ps_path"}
                                         for path in fragments)
                if call["command"] == "sudo -n -u infra sudo -n kill -STOP 3928756 3928762":
                    if entry["rc"] == 0:
                        stopped = timestamp(at)
                if call["command"] == "sudo -n -u infra sudo -n kill -KILL 3928756 3928762":
                    if entry["rc"] == 0:
                        killed = timestamp(at)
                if "fix-3fs-perms.sh" in call["command"] and len(output) < 3000:
                    entry["bounded_helper_output"] = output
                records.append(entry)
                for row in rows:
                    if row["command"].startswith("chmod 2777 ") and row["ppid"] in OWNED:
                        candidates.append({"time": at, **row,
                                           "classification": "observed_chmod_argv_not_success_proof"})
    if first is None or stop is None or stopped is None or killed is None:
        raise ValueError("Original command/stop record missing")
    return {"start": stamp(first), "stop_request": stamp(stop),
            "stop_acknowledged": stamp(stopped), "kill_acknowledged": stamp(killed),
            "window_seconds": stopped - first, "records": records,
            "observed_chmod_commands": candidates,
            "unattributed_truncated_ps_paths": exposed_paths,
            "malformed_lines": malformed,
            "subsequent_helper_calls": [
                entry for entry in records if entry["kind"] == "command"
                and "bash ops/3fs-storage/fix-3fs-perms.sh" in entry["command"]
                and timestamp(entry["time"]) > killed],
            "records_sha256": hashlib.sha256(
                json.dumps(records, sort_keys=True).encode()).hexdigest(),
            "log_bound": OBSERVATION_END,
            "limits": ["Transcript process outputs can be truncated.",
                       "Command arguments do not prove successful syscall or previous mode.",
                       "The final observation-to-stop gap cannot exclude an unobserved file pass.",
                       "Metadata scan conservatively includes through acknowledged kill.",
                       "No error log or per-path successful chmod syscall was recorded."]}


def mode_record(path, info):
    return {"path": str(path), "mode": format(stat.S_IMODE(info.st_mode), "04o"),
            "uid": info.st_uid, "gid": info.st_gid, "device": info.st_dev, "inode": info.st_ino,
            "ctime_ns": info.st_ctime_ns, "mtime_ns": info.st_mtime_ns,
            "ctime": stamp(info.st_ctime), "mtime": stamp(info.st_mtime)}


def classify(info, start, stop, include_files=False):
    if start <= info.st_ctime <= stop:
        if stat.S_ISDIR(info.st_mode):
            mode = stat.S_IMODE(info.st_mode)
            if mode == 0o2777:
                return "current_2777_ctime_in_window"
            if mode == 0o6777:
                return "current_6777_ctime_in_window_preserved_setuid_candidate"
            return "other_mode_ctime_in_window"
        if include_files and stat.S_ISREG(info.st_mode):
            return ("current_0666_file_ctime_in_window"
                    if stat.S_IMODE(info.st_mode) == 0o666
                    else "other_file_mode_ctime_in_window")
    return None


def directory_fd(root_fd, parts):
    fd = os.dup(root_fd)
    try:
        for component in parts:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def root_descriptor(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def scan_parts(target, requested):
    if ".." in requested.parts:
        raise ValueError("Traversal forbidden in scan root")
    base = target.resolve(strict=True)
    lexical = requested.absolute()
    if lexical.is_relative_to(target):
        parts = lexical.relative_to(target).parts
    elif lexical.is_relative_to(base):
        parts = lexical.relative_to(base).parts
    else:
        raise ValueError("Scan root must be under the incident target")
    return base, parts


def scan(target, requested, out, start, stop, max_directories, max_seconds, include_files):
    started = time.monotonic()
    base, parts = scan_parts(target, requested)
    canonical = base.joinpath(*parts)
    queue = deque([canonical])
    counts, by_root = Counter(), Counter()
    errors = []
    manifest = out / "metadata-candidates.jsonl"
    with root_descriptor(base) as root_fd, manifest.open("x", encoding="utf-8") as handle:
        while queue and counts["directories"] < max_directories and time.monotonic() - started < max_seconds:
            directory = queue.popleft()
            try:
                fd = directory_fd(root_fd, directory.relative_to(base).parts)
                try:
                    info = os.fstat(fd)
                    counts["directories"] += 1
                    category = classify(info, start, stop)
                    if category:
                        entry = {**mode_record(directory, info), "classification": category}
                        handle.write(json.dumps(entry, sort_keys=True) + "\n")
                        handle.flush()
                        counts[category] += 1
                        relative = directory.relative_to(base).parts
                        by_root[relative[0] if relative else "."] += 1
                    with os.scandir(fd) as children:
                        for child in children:
                            counts["entries"] += 1
                            child_path = directory / child.name
                            if child.is_symlink():
                                counts["symlinks_not_followed"] += 1
                            elif child.is_dir(follow_symlinks=False):
                                queue.append(child_path)
                            elif include_files:
                                child_info = child.stat(follow_symlinks=False)
                                counts["file_entries_statted"] += 1
                                category = classify(child_info, start, stop, include_files=True)
                                if category:
                                    entry = {**mode_record(child_path, child_info),
                                             "classification": category}
                                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                                    counts[category] += 1
                                    relative = child_path.relative_to(base).parts
                                    by_root[relative[0] if relative else "."] += 1
                finally:
                    os.close(fd)
            except OSError as exc:
                errors.append({"path": str(directory), "type": type(exc).__name__, "detail": str(exc)})
            if counts["directories"] % 1000 == 0:
                line = (f"METADATA_PROGRESS directories={counts['directories']} pending={len(queue)}"
                        f" errors={len(errors)} elapsed={time.monotonic()-started:.3f}")
                with (out / "audit.log").open("a", encoding="utf-8") as log:
                    log.write(line + "\n")
    pending = out / "unscanned-directories.jsonl"
    with pending.open("x", encoding="utf-8") as handle:
        for path in queue:
            handle.write(json.dumps({"path": str(path), "descendants_not_enumerated": True}) + "\n")
    return {"coverage": "COMPLETE_CURRENT_DIRECTORY_WALK" if not queue and not errors else "INCOMPLETE",
            "requested_root": str(requested), "canonical_root": str(canonical),
            "counts": dict(counts), "candidates_by_top_level": dict(by_root),
            "errors": errors, "pending_directories": len(queue),
            "elapsed_seconds": time.monotonic() - started,
            "files_not_statted": not include_files, "mounts_not_historically_reconstructed": True,
            "ctime_window": {"start": stamp(start), "end": stamp(stop),
                             "rounded_outward_for_3fs_second_precision": True},
            "historical_scope_coverage": "UNKNOWN",
            "manifest": fingerprint(manifest), "pending_manifest": fingerprint(pending)}


class AuditTests(unittest.TestCase):
    def test_argument_order(self):
        text = regular_bytes(SCRIPT).decode()
        self.assertFalse(helper_semantics(text, [str(TARGET), "--check"])["check"])
        self.assertEqual(helper_semantics(text, [str(TARGET), "--check"])["targets"],
                         [str(TARGET), "--check"])
        self.assertTrue(helper_semantics(text, ["--check", str(TARGET)])["check"])

    def test_semantics_drift_rejected(self):
        with self.assertRaises(ValueError):
            helper_semantics("new helper", [str(TARGET)])

    def test_metadata_not_proof(self):
        info = os.stat_result((stat.S_IFDIR | 0o2777, 1, 1, 1, 1, 1, 0, 0, 10, 20))
        self.assertEqual(classify(info, 19, 21), "current_2777_ctime_in_window")
        self.assertIsNone(classify(info, 21, 22))

    def test_process_scope(self):
        rows = process_rows("123 1 root secret-unrelated\n3928762 3928756 root find target\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 3928762)

    def test_pgrep_scope(self):
        rows = process_rows("3928762 find target\n538182 find unrelated\n")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["ppid"])

    def test_same_tokens_changed_helper_rejected(self):
        with self.assertRaises(ValueError):
            helper_semantics(regular_bytes(SCRIPT).decode() + "\n", [str(TARGET)])

    def test_preserved_setuid_candidate(self):
        info = os.stat_result((stat.S_IFDIR | 0o6777, 1, 1, 1, 1, 1, 0, 0, 10, 20))
        self.assertEqual(classify(info, 19, 21),
                         "current_6777_ctime_in_window_preserved_setuid_candidate")

    def test_files_are_opt_in_candidates(self):
        info = os.stat_result((stat.S_IFREG | 0o666, 1, 1, 1, 1, 1, 0, 0, 10, 20))
        self.assertIsNone(classify(info, 19, 21))
        self.assertEqual(classify(info, 19, 21, True), "current_0666_file_ctime_in_window")

    def test_scan_root_cannot_escape(self):
        with self.assertRaises(ValueError):
            scan_parts(TARGET, Path("/data/dev/opencad"))
        with self.assertRaises(ValueError):
            scan_parts(TARGET, TARGET / ".." / "other")


def read_summary(path):
    report = json.loads(regular_bytes(path))
    scanned = report.get("scan")
    compact = {key: report.get(key) for key in (
        "verdict", "incident_status", "start_time", "finish_time",
        "source", "source_drift", "selftest", "proven_changed_paths")}
    if scanned:
        compact["scan"] = {key: scanned.get(key) for key in (
            "coverage", "requested_root", "canonical_root", "counts", "pending_directories",
            "elapsed_seconds", "files_not_statted", "manifest", "pending_manifest")}
        compact["scan"]["error_count"] = len(scanned["errors"])
    print(json.dumps(compact, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-parent", type=Path, default=TARGET / "opencad-drawing-web-20261011")
    parser.add_argument("--name")
    parser.add_argument("--read-result", type=Path,
                        help="Print existing result summary without creating evidence")
    parser.add_argument("--transcript", type=Path, default=TRANSCRIPT)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--scan-root", type=Path, default=TARGET)
    parser.add_argument("--scan-files", action="store_true")
    parser.add_argument("--max-directories", type=int, default=100000)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.read_result:
        read_summary(args.read_result)
        return 0
    if (args.max_directories <= 0 or not math.isfinite(args.max_seconds)
            or args.max_seconds <= 0):
        parser.error("Scan caps must be positive and finite")
    if not args.name:
        parser.error("--name is required unless --read-result is used")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", args.name):
        parser.error("Unsafe evidence name")
    parent = args.out_parent.resolve(strict=True)
    if not parent.is_relative_to(TARGET.resolve()) or parent.is_relative_to(ROOT):
        parser.error("Evidence parent must already exist outside source, under 3FS tmp")
    out = parent / args.name
    out.mkdir(exist_ok=False)
    report = {"verdict": "FAIL", "incident_status": "OPEN_UNATTRIBUTED",
              "permission_mutations_by_auditor": 0,
              "start_time": datetime.now(timezone.utc).isoformat()}
    try:
        if args.selftest:
            with (out / "selftest.log").open("x") as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromTestCase(AuditTests))
            report["selftest"] = {"tests": result.testsRun, "failures": len(result.failures),
                                  "errors": len(result.errors), "skipped": len(result.skipped)}
            if not result.wasSuccessful() or result.skipped:
                raise ValueError("Auditor selftest failed")
        report["source"] = fingerprint(Path(__file__))
        report["helper"] = fingerprint(SCRIPT)
        report["incident_note"] = fingerprint(INCIDENT)
        report["helper_semantics"] = helper_semantics(
            regular_bytes(SCRIPT).decode(), [str(TARGET), "--check"])
        report["target_alias"] = {"requested": str(TARGET), "canonical": str(TARGET.resolve()),
                                  "current": mode_record(TARGET, TARGET.stat())}
        report["timeline"] = timeline(args.transcript)
        report["scan"] = scan(TARGET, args.scan_root, out,
                              math.floor(timestamp(report["timeline"]["start"])),
                              math.ceil(timestamp(report["timeline"]["kill_acknowledged"])),
                              args.max_directories, args.max_seconds, args.scan_files) if args.scan else None
        report["source_drift"] = [
            name for name, path in (("source", Path(__file__)), ("helper", SCRIPT),
                                   ("incident_note", INCIDENT))
            if fingerprint(path) != report[name]]
        if report["source_drift"]:
            raise ValueError("Audit source drift: " + ", ".join(report["source_drift"]))
        report["proven_changed_paths"] = None
        report["proven_changed_paths_reason"] = (
            "No before-mode manifest or successful per-path chmod syscall log is available.")
        report["verdict"] = "AUDIT_RECORDED_NOT_INCIDENT_CLOSED"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "detail": str(exc)}
    report["finish_time"] = datetime.now(timezone.utc).isoformat()
    with (out / "result.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=True)
        handle.write("\n")
    line = (f"DRAWING_PERMISSION_AUDIT verdict={report['verdict']} "
            f"incident_status={report['incident_status']} evidence={out}")
    with (out / "audit.log").open("a", encoding="utf-8") as log:
        log.write(line + "\n")
    print(line, flush=True)
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
