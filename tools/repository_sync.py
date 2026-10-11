#!/usr/bin/env python3
"""Explicit, credential-safe OpenCAD GitLab-first source synchronization.

Only named committed refs are published. No force, mirror, deletion, credential
installation, host-key acceptance, service change or automatic retry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import ssl
import stat
import subprocess
import sys
import threading

TARGETS = {
    "gitlab": "ssh://git@192.168.1.181/infra/opencad.git",
    "gitee": "git@gitee.com:sdfwefqaa/opencad.git",
    "github": "https://github.com/miaochunhui/opencad.git",
}
TOKEN_FILE = Path("/home/mch2/.passwd_github_miaochunhui")
SSH = "/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"
GITLAB_SSH = SSH + " -o IdentitiesOnly=yes -i /home/infra/.ssh/id_ed25519_gitlab"
SECRET = re.compile(
    rb"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    rb"https?://[^\s/:]+:[^\s/@]+@|"
    rb"-----BEGIN ((?:RSA |EC |OPENSSH )?)PRIVATE " rb"KEY-----[ \t]*\r?\n"
    rb"[A-Za-z0-9+/=\r\n \t]{64,}-----END \1PRIVATE KEY-----|"
    rb"(?i:authorization\s*:\s*(?:bearer|basic))\s+[A-Za-z0-9+/_.=-]{8,}|"
    rb"(?i:password|api[_-]?key|access[_-]?token)\s*[:=]\s*[\x22\x27][^\x22\x27\r\n]{8,}[\x22\x27]"
)


class Refused(RuntimeError):
    """A safe, non-secret diagnostic code."""


def need(condition, code):
    if not condition:
        raise Refused(code)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def tool_sha():
    return digest(Path(__file__).read_bytes())


def parse_token(raw):
    need(len(raw) <= 16384, "TOKEN_TOO_LARGE")
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        raise Refused("TOKEN_NOT_ASCII") from None
    tokens = re.findall(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])",
        text,
    )
    if tokens:
        need(len(tokens) == 1, "TOKEN_AMBIGUOUS")
        return tokens[0]
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    need(len(lines) == 1 and re.fullmatch(r"[A-Za-z0-9_]{20,255}", lines[0]),
         "TOKEN_INVALID")
    return lines[0]


def load_token():
    fd = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "TOKEN_FILE_TYPE")
        return parse_token(stream.read(16385))


def api(method, path, token, payload=None):
    need((method, path) in {
        ("GET", "/user"), ("GET", "/repos/miaochunhui/opencad"),
        ("POST", "/user/repos")}, "API_SCOPE")
    connection = http.client.HTTPSConnection(
        "api.github.com", timeout=30, context=ssl.create_default_context())
    try:
        connection.request(method, path, body=None if payload is None else encode(payload),
                           headers={"Authorization": "Bearer " + token,
                                    "Accept": "application/vnd.github+json",
                                    "Content-Type": "application/json",
                                    "User-Agent": "opencad-repository-sync"})
        response = connection.getresponse()
        raw = response.read(1048577)
        need(len(raw) <= 1048576, "API_RESPONSE_LIMIT")
        need(not 300 <= response.status < 400, "API_REDIRECT_REFUSED")
        return response.status, json.loads(raw) if response.status in (200, 201) else {}
    finally:
        connection.close()


class Evidence:
    def __init__(self, out, token=None):
        base = Path("/hf3fs-jd/data/tmp")
        need(out.is_absolute() and ".." not in out.parts and out.is_relative_to(base)
             and out != base, "EXTERNAL_OUTPUT_REQUIRED")
        for parent in reversed(out.parents):
            if parent == Path("/hf3fs-jd"):
                need(parent.is_symlink() and os.readlink(parent) == "/3fs/research3fs",
                     "OUTPUT_ALIAS_REFUSED")
            else:
                need(parent.is_dir() and not parent.is_symlink(), "OUTPUT_PARENT_REFUSED")
        out.mkdir(mode=0o700)
        self.out, self.token, self.calls = out, token, []
        self.pin = tool_sha()

    def stable(self):
        need(tool_sha() == self.pin, "TOOL_DRIFT")

    def unsafe(self, raw):
        return bool(SECRET.search(raw) or
                    self.token and self.token.encode() in raw)

    def save(self, name, value):
        raw = encode(value)
        need(not self.unsafe(raw), "SECRET_EVIDENCE_REFUSED")
        path = self.out / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        need(path.read_bytes() == raw, "EVIDENCE_READBACK")
        fd = os.open(self.out, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def run(self, argv, role=None, check=True):
        self.stable()
        need(not self.unsafe(encode(argv)), "SECRET_ARGUMENT_REFUSED")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("GIT_")}
        env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
                   GIT_SSH_COMMAND=GITLAB_SSH if role == "gitlab" else SSH)
        if role == "github":
            env["OPENCAD_SYNC_TOOL_SHA"] = self.pin
            helper = shlex.join([sys.executable, "-I", "-B",
                                 str(Path(__file__).resolve()), "--credential-helper"])
            argv = [argv[0], "-c", "credential.helper=", "-c",
                    "credential.helper=" + helper, "-c", "credential.useHttpPath=true",
                    "-c", "http.sslVerify=true", "-c", "http.followRedirects=false",
                    *argv[1:]]
        child = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True)
        row = {"at": stamp(), "argv": argv, "actual_rc": child.returncode}
        for name, raw in (("stdout", child.stdout), ("stderr", child.stderr)):
            row[name] = {"bytes": len(raw), "redacted": self.unsafe(raw),
                         "text": None if self.unsafe(raw) else raw.decode(errors="replace")}
        self.calls.append(row)
        self.save("commands.json", self.calls)
        need(not self.unsafe(child.stdout + child.stderr), "SECRET_CHILD_OUTPUT_REFUSED")
        self.stable()
        if check:
            need(child.returncode == 0, "COMMAND_RC_" + str(child.returncode))
        return child


def git(evidence, source, *args, role=None, check=True):
    return evidence.run(["/usr/bin/git", "--no-replace-objects", "-c",
                         "push.followTags=false", "-C", str(source), *args], role, check)


def github_repo(evidence, create=False):
    def request(method, path, payload=None):
        status, body = api(method, path, evidence.token, payload)
        row = {"at": stamp(), "method": method, "path": path, "http_status": status}
        if path == "/user":
            login = body.get("login") if isinstance(body, dict) else None
            if isinstance(login, str) and re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
                row["login"] = login
        evidence.calls.append(row)
        evidence.save("commands.json", evidence.calls)
        return status, body
    status, user = request("GET", "/user")
    login = user.get("login") if isinstance(user, dict) else None
    need(status == 200 and isinstance(login, str) and login.isascii()
         and login.casefold() == "miaochunhui", "GITHUB_ACCOUNT_MISMATCH")
    status, repo = request("GET", "/repos/miaochunhui/opencad")
    if status == 404 and not create:
        return "ABSENT"
    if status == 404:
        status, _ = request("POST", "/user/repos",
                            {"name": "opencad", "private": False, "auto_init": False})
        need(status == 201, "GITHUB_CREATE_FAILED")
        status, repo = request("GET", "/repos/miaochunhui/opencad")
    need(status == 200 and isinstance(repo, dict), "GITHUB_REPO_QUERY_FAILED")
    name, clone = repo.get("full_name"), repo.get("clone_url")
    permissions = repo.get("permissions", {})
    need(isinstance(name, str) and name.isascii()
         and name.casefold() == "miaochunhui/opencad"
         and repo.get("private") is False and repo.get("archived") is False
         and isinstance(clone, str) and clone.isascii()
         and clone.casefold() == TARGETS["github"]
         and permissions.get("push") is True, "GITHUB_REPO_IDENTITY_OR_PERMISSION")
    return "PRESENT_PUBLIC_WRITABLE"


def snapshot(evidence, source):
    head = git(evidence, source, "rev-parse", "HEAD").stdout.strip().decode()
    need(not git(evidence, source, "status", "--porcelain").stdout, "SOURCE_DIRTY")
    need(not git(evidence, source, "for-each-ref", "refs/replace").stdout,
         "REPLACE_REFS_REFUSED")
    need(git(evidence, source, "ls-tree", "HEAD", "--", "LICENSE",
             "oss-alternatives/apps/opencad/server.py").stdout.count(b"\n") == 2,
         "OPENCAD_SOURCE_REQUIRED")
    config = git(evidence, source, "rev-parse", "--git-path", "config").stdout.strip().decode()
    path = Path(config) if Path(config).is_absolute() else source / config
    # Compare in memory only; config may contain unrelated private credentials.
    return head, path.read_bytes()


def scan_history(evidence, source, commit):
    objects = git(evidence, source, "rev-list", "--objects", "--no-object-names",
                  commit).stdout.splitlines()
    need(objects, "EMPTY_HISTORY")
    child = subprocess.Popen(["/usr/bin/git", "--no-replace-objects",
                              "-C", str(source), "cat-file", "--batch"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    stderr = []
    drain = threading.Thread(target=lambda: stderr.append(child.stderr.read()), daemon=True)
    drain.start()
    count, first = 0, None
    try:
        for oid in objects:
            child.stdin.write(oid + b"\n")
            child.stdin.flush()
            fields = child.stdout.readline().split()
            need(len(fields) == 3 and fields[0] == oid, "HISTORY_OBJECT_INVALID")
            size = int(fields[2])
            need(0 <= size <= 64000000, "HISTORY_OBJECT_LIMIT")
            raw = child.stdout.read(size)
            need(len(raw) == size and child.stdout.read(1) == b"\n", "HISTORY_TRUNCATED")
            if evidence.unsafe(raw):
                match = SECRET.search(raw)
                evidence.save("history-hit.json", {
                    "object": oid.decode(), "type": fields[1].decode(),
                    "line": raw[:match.start()].count(b"\n") + 1 if match else None,
                    "scope": "COORDINATES_ONLY_NO_MATCH_CONTENT"})
                raise Refused("HISTORY_SECRET_FOUND")
            count += 1
    except Exception as exc:
        first = exc
    finally:
        child.stdin.close()
        child.stdout.close()
        rc = child.wait()
        drain.join()
        child.stderr.close()
        raw = b"".join(stderr)
        evidence.save("history.json", {
            "at": stamp(), "objects": count, "expected": len(objects), "actual_rc": rc,
            "first_cause": str(first) if isinstance(first, Refused) else
            type(first).__name__ if first else None,
            "stderr": None if evidence.unsafe(raw) else raw.decode(errors="replace")})
    if first:
        raise first
    need(rc == 0 and count == len(objects), "HISTORY_SCAN_INCOMPLETE")
    return count


def heads(evidence, source, role, url, allow_create=False):
    child = git(evidence, source, "ls-remote", "--heads", url, role=role, check=False)
    if child.returncode:
        need(role == "gitlab" and allow_create
             and b"project you were looking for could not be found" in child.stderr.lower(),
             "REMOTE_QUERY_RC_" + str(child.returncode))
        auth = evidence.run(["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o",
                             "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
                             "-i", "/home/infra/.ssh/id_ed25519_gitlab",
                             "git@192.168.1.181"])
        need(b"Welcome to GitLab, @infra!" in auth.stdout, "GITLAB_AUTH_REFUSED")
        return {}
    result = {}
    for line in child.stdout.splitlines():
        oid, ref = line.decode().split("\t")
        need(re.fullmatch(r"[0-9a-f]{40}", oid) and ref.startswith("refs/heads/")
             and ref not in result, "REMOTE_REFS_INVALID")
        result[ref] = oid
    return result


def synchronize(evidence, source, commit, targets, *, mutate=False,
                allow_create=False, local=None):
    need("gitlab" in targets, "PRIMARY_REQUIRED")
    before = snapshot(evidence, source)
    need(before[0] == commit and re.fullmatch(r"[0-9a-f]{40}", commit), "HEAD_NOT_PINNED")
    objects = scan_history(evidence, source, commit)
    ref, plans = "refs/heads/main", []
    for role in TARGETS:
        if role not in targets:
            continue
        url = local[role] if local else TARGETS[role]
        previous = heads(evidence, source, role, url, allow_create)
        old = previous.get(ref)
        if old and old != commit:
            git(evidence, source, "fetch", "--no-tags", "--no-write-fetch-head",
                url, ref, role=role)
            need(git(evidence, source, "merge-base", "--is-ancestor", old, commit,
                     check=False).returncode == 0, "REMOTE_DIVERGED")
        plans.append({"role": role, "url": url, "before": previous,
                      "push_attempted": False, "verified": False})
    evidence.save("plan.json", {"commit": commit, "ref": ref, "targets": plans})
    for plan in plans:
        need(snapshot(evidence, source) == before, "SOURCE_DRIFT")
        role, url = plan["role"], plan["url"]
        need(heads(evidence, source, role, url, allow_create) == plan["before"],
             "REMOTE_DRIFT")
        if mutate:
            plan["push_attempted"] = True
            plan["actual_push_rc"] = None
            evidence.save("partial.json", plans)
            first = None
            call_start = len(evidence.calls)
            try:
                pushed = git(evidence, source, "push", "--porcelain", "--no-follow-tags",
                             url, commit + ":" + ref, role=role, check=False)
                plan["actual_push_rc"] = pushed.returncode
                if pushed.returncode:
                    first = "PUSH_RC_" + str(pushed.returncode)
            except Exception as exc:
                first = str(exc) if isinstance(exc, Refused) else type(exc).__name__
                plan["actual_push_rc"] = next(
                    (row["actual_rc"] for row in reversed(evidence.calls[call_start:])
                     if "push" in row.get("argv", []) and url in row["argv"]), None)
            try:
                current = heads(evidence, source, role, url)
                plan["readback"] = current.get(ref)
                plan["verified"] = current.get(ref) == commit
                if not plan["verified"] and first is None:
                    first = "READBACK_MISMATCH"
            except Exception as exc:
                plan["readback_failure"] = (
                    str(exc) if isinstance(exc, Refused) else type(exc).__name__)
                plan["readback"] = None
                plan["readback_state"] = "UNKNOWN_NOT_VERIFIED"
                first = first or "READBACK_FAILED"
            plan["first_cause"] = first
            evidence.save("partial.json", plans)
            need(first is None, first)
    need(snapshot(evidence, source) == before, "SOURCE_DRIFT")
    final = []
    if mutate:
        for plan in plans:
            current = heads(evidence, source, plan["role"], plan["url"])
            final.append({"role": plan["role"], "at": stamp(),
                          "readback": current.get(ref)})
            evidence.save("final-readback.json", final)
            need(current.get(ref) == commit, "FINAL_REMOTE_DRIFT")
    return {"verdict": "PASS", "at": stamp(), "commit": commit, "ref": ref,
            "state": "SYNCED" if mutate else "PLANNED", "objects_scanned": objects,
            "targets": plans, "all_three_synced": mutate and len(plans) == 3,
            "final_readback": final,
            "consistency_scope": "SEQUENTIAL_READBACK_NOT_GLOBAL_ATOMICITY",
            "scope": "LOCAL_TEST" if local else "COMMITTED_CODE_NOT_RELEASE",
            "config_unchanged": True, "source_stable": True}


def helper(action):
    need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
    need(os.environ.get("OPENCAD_SYNC_TOOL_SHA") == tool_sha(), "HELPER_TOOL_DRIFT")
    need(action in ("get", "store", "erase"), "HELPER_ACTION")
    raw = sys.stdin.buffer.read(4097)
    need(len(raw) <= 4096, "HELPER_INPUT_LIMIT")
    pairs = [line.split("=", 1) for line in raw.decode().splitlines() if line]
    need(all(len(pair) == 2 for pair in pairs), "HELPER_FIELDS")
    fields = dict(pairs)
    need(len(fields) == len(pairs) and fields.get("protocol") == "https"
         and fields.get("host") == "github.com"
         and fields.get("path") == "miaochunhui/opencad.git", "HELPER_TARGET")
    if action == "get":
        # Git alone consumes this pipe; never save or print it as evidence.
        sys.stdout.write("username=miaochunhui\npassword" + "=" + load_token() + "\n\n")
        sys.stdout.flush()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--action", required=True,
                        choices=("inspect-github", "create-github", "plan", "sync", "scan"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--target", action="append", choices=tuple(TARGETS), default=[])
    parser.add_argument("--create-gitlab", action="store_true")
    parser.add_argument("--local-bare", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    evidence = None
    try:
        need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
        need(len(args.target) == len(set(args.target)), "DUPLICATE_TARGET")
        local = None
        if args.local_bare:
            pairs = [item.split("=", 1) for item in args.local_bare]
            need(all(len(pair) == 2 for pair in pairs), "LOCAL_TARGET_FORMAT")
            local = dict(pairs)
            need(len(local) == len(pairs) and set(local) == set(args.target)
                 and args.action in ("plan", "sync"), "LOCAL_TARGET_SCOPE")
            for path in local.values():
                need(Path(path).is_absolute()
                     and Path(path).resolve().is_relative_to(args.out.parent.resolve()),
                     "LOCAL_TARGET_ROOT")
        needs_token = args.action in ("inspect-github", "create-github") or (
            "github" in args.target and not local)
        evidence = Evidence(args.out, load_token() if needs_token else None)
        if args.action in ("inspect-github", "create-github"):
            need(not args.target and not args.source and not args.commit and not local,
                 "GITHUB_ACTION_SCOPE")
            result = {"verdict": "PASS", "at": stamp(),
                      "state": github_repo(evidence, args.action == "create-github"),
                      "scope": "CODE_REPOSITORY_NOT_RELEASE"}
        else:
            need(args.source is not None and args.commit
                 and re.fullmatch(r"[0-9a-f]{40}", args.commit), "SOURCE_COMMIT_REQUIRED")
            if args.action == "scan":
                result = {"verdict": "PASS", "at": stamp(), "commit": args.commit,
                          "objects": scan_history(evidence, args.source, args.commit),
                          "scope": "SELECTED_HISTORY_SCAN"}
            else:
                need(args.target, "TARGETS_REQUIRED")
                if needs_token:
                    need(github_repo(evidence) != "ABSENT", "CREATE_GITHUB_FIRST")
                result = synchronize(evidence, args.source, args.commit, args.target,
                                     mutate=args.action == "sync",
                                     allow_create=args.create_gitlab, local=local)
        evidence.stable()
        result["tool_sha256"] = evidence.pin
        evidence.save("result.json", result)
        print(encode(result).decode(), end="", flush=True)
        return 0
    except Exception as exc:
        result = {"verdict": "FAIL", "at": stamp(),
                  "first_cause": str(exc) if isinstance(exc, Refused) else type(exc).__name__}
        if evidence:
            evidence.save("result.failure.json", result)
            evidence.save("result.json", result)
        print(encode(result).decode(), end="", flush=True)
        return 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--credential-helper":
        try:
            raise SystemExit(helper(sys.argv[2]))
        except Exception:
            sys.stderr.write("CREDENTIAL_HELPER_REFUSED\n")
            raise SystemExit(1)
    raise SystemExit(main())
