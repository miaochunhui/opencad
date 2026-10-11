#!/usr/bin/env python3
"""Explicit small-tail API transport when GitHub Git HTTPS is unavailable.

Fixed account, repository and main ref; same existing credential. GitLab and
Gitee are updated first. Exact existing Git objects only, no rewritten commits,
force, ref deletion, new host keys, proxy change, automatic fallback or retry.
Existing regular-file deletions use exact parent-tree entries and null SHA.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import ssl
import sys

import repository_sync as sync

PREFIX = "/repos/miaochunhui/opencad/git"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_COMMITS, MAX_FILES, MAX_BLOB = 8, 32, 1048576


def own_sha():
    return sync.digest(Path(__file__).read_bytes())


def request(evidence, method, suffix, payload=None):
    allowed = {("GET", "/ref/heads/main"), ("PATCH", "/refs/heads/main"),
               ("POST", "/blobs"), ("POST", "/trees"), ("POST", "/commits")}
    sync.need((method, suffix) in allowed or
              method == "GET" and re.fullmatch(r"/commits/[0-9a-f]{40}", suffix),
              "API_OBJECT_SCOPE")
    if method == "PATCH":
        sync.need(isinstance(payload, dict) and set(payload) == {"sha", "force"}
                  and payload["force"] is False
                  and isinstance(payload["sha"], str)
                  and SHA.fullmatch(payload["sha"]), "NON_FORCE_MAIN_REQUIRED")
    evidence.stable()
    connection = http.client.HTTPSConnection(
        "api.github.com", timeout=30, context=ssl.create_default_context())
    try:
        connection.request(method, PREFIX + suffix,
                           body=None if payload is None else sync.encode(payload),
                           headers={"Authorization": "Bearer " + evidence.token,
                                    "Accept": "application/vnd.github+json",
                                    "Content-Type": "application/json",
                                    "Cache-Control": "no-cache",
                                    "User-Agent": "opencad-exact-object-sync"})
        response = connection.getresponse()
        evidence.calls.append({"at": sync.stamp(), "method": method,
                               "path": PREFIX + suffix, "http_status": response.status})
        evidence.save("commands.json", evidence.calls)
        sync.need(response.status == (201 if method == "POST" else 200),
                  "OBJECT_API_HTTP_" + str(response.status))
        raw = response.read(2097153)
        sync.need(len(raw) <= 2097152, "OBJECT_API_RESPONSE_LIMIT")
        value = json.loads(raw)
        sync.need(isinstance(value, dict), "OBJECT_API_RESPONSE_TYPE")
        return value
    finally:
        connection.close()


def remote_main(evidence):
    value = request(evidence, "GET", "/ref/heads/main")
    obj = value.get("object")
    sync.need(value.get("ref") == "refs/heads/main" and isinstance(obj, dict)
              and obj.get("type") == "commit" and isinstance(obj.get("sha"), str)
              and SHA.fullmatch(obj["sha"]), "GITHUB_REF_IDENTITY")
    return obj["sha"]


def exact(value, expected, kind):
    sync.need(value.get("sha") == expected, "OBJECT_SHA_MISMATCH_" + kind)


def person(text):
    match = re.fullmatch(r"(.+) <([^<>]+)> ([0-9]+) ([+-])([0-9]{2})([0-9]{2})", text)
    sync.need(match is not None, "UNSUPPORTED_PERSON_HEADER")
    name, email, seconds, sign, hours, minutes = match.groups()
    sync.need(int(hours) < 24 and int(minutes) < 60, "INVALID_PERSON_OFFSET")
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == "-":
        offset = -offset
    return {"name": name, "email": email,
            "date": datetime.fromtimestamp(int(seconds), timezone(offset)).isoformat()}


def local_commit(evidence, source, sha):
    raw = sync.git(evidence, source, "cat-file", "commit", sha).stdout
    sync.need(hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
              == sha, "LOCAL_COMMIT_SHA")
    header, separator, message = raw.decode("utf-8").partition("\n\n")
    sync.need(separator, "COMMIT_HEADER_REQUIRED")
    fields = {}
    for line in header.splitlines():
        key, separator, value = line.partition(" ")
        sync.need(separator and key in {"tree", "parent", "author", "committer"}
                  and key not in fields, "SIGNED_EXTENDED_OR_MERGE_REFUSED")
        fields[key] = value
    sync.need(set(fields) == {"tree", "parent", "author", "committer"}
              and SHA.fullmatch(fields["tree"]) and SHA.fullmatch(fields["parent"]),
              "LINEAR_NONROOT_COMMIT_REQUIRED")
    return {"tree": fields["tree"], "parents": [fields["parent"]], "message": message,
            "author": person(fields["author"]), "committer": person(fields["committer"])}


def regular_entry(evidence, source, commit, path):
    tree = sync.git(evidence, source, "--literal-pathspecs", "ls-tree", "-z",
                    commit, "--", path).stdout.split(b"\0")
    if tree == [b""]:
        return None
    sync.need(len(tree) == 2 and tree[0], "AMBIGUOUS_TREE_ENTRY_REFUSED")
    metadata, separator, actual = tree[0].decode().partition("\t")
    fields = metadata.split()
    sync.need(separator and actual == path and len(fields) == 3, "TREE_ENTRY_INVALID")
    mode, kind, blob = fields
    sync.need(kind == "blob" and mode in {"100644", "100755"}
              and SHA.fullmatch(blob), "NONREGULAR_ENTRY_REFUSED")
    return {"path": path, "mode": mode, "type": "blob", "sha": blob}


def prepare_tail(evidence, source, before, head):
    sync.need(sync.git(evidence, source, "merge-base", "--is-ancestor", before, head,
                       check=False).returncode == 0, "GITHUB_DIVERGED_OR_MISSING")
    tail = sync.git(evidence, source, "rev-list", "--reverse", before + ".." + head
                    ).stdout.decode().splitlines()
    sync.need(len(tail) <= MAX_COMMITS, "API_COMMIT_CAPACITY")
    prepared = []
    for sha in tail:
        payload = local_commit(evidence, source, sha)
        parent = payload["parents"][0]
        paths = [p for p in sync.git(evidence, source, "diff-tree", "--no-commit-id",
                                    "--name-only", "--no-renames", "-r", "-z",
                                    parent, sha).stdout.decode().split("\0") if p]
        sync.need(len(paths) <= MAX_FILES, "API_FILE_CAPACITY")
        entries = []
        for path in paths:
            current = regular_entry(evidence, source, sha, path)
            previous = regular_entry(evidence, source, parent, path)
            sync.need(current is not None or previous is not None,
                      "DELETION_PARENT_ENTRY_REQUIRED")
            entry = current if current is not None else previous
            raw = sync.git(evidence, source, "cat-file", "blob", entry["sha"]).stdout
            sync.need(len(raw) <= MAX_BLOB, "API_BLOB_CAPACITY")
            sync.need(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                      == entry["sha"], "LOCAL_BLOB_SHA")
            # Null removes an existing base-tree file; it is not a blob upload.
            entries.append({**entry, "raw": raw} if current is not None else
                           {**entry, "sha": None})
        parent_tree = sync.git(evidence, source, "rev-parse", parent + "^{tree}"
                               ).stdout.decode().strip()
        prepared.append((sha, payload, parent_tree, entries))
    return prepared


def run(evidence, source, commit, mutate=False):
    pin = own_sha()
    before_source = sync.snapshot(evidence, source)
    sync.need(before_source[0] == commit, "HEAD_NOT_PINNED")
    sync.need(sync.github_repo(evidence) == "PRESENT_PUBLIC_WRITABLE",
              "AUTHORIZED_PUBLIC_REPOSITORY_REQUIRED")
    objects = sync.scan_history(evidence, source, commit)
    old = remote_main(evidence)
    prepared = prepare_tail(evidence, source, old, commit)
    sync.synchronize(evidence, source, commit, ["gitlab", "gitee"], mutate=False)
    plan = {"commit": commit, "github_before": old, "objects_scanned": objects,
            "tail": [item[0] for item in prepared], "max_commits": MAX_COMMITS,
            "transport": "EXPLICIT_EXACT_OBJECT_API_NOT_AUTOMATIC_FALLBACK"}
    evidence.save("api-plan.json", plan)

    def stable():
        sync.need(own_sha() == pin and sync.snapshot(evidence, source) == before_source,
                  "API_SOURCE_DRIFT")
        sync.need(sync.load_token() == evidence.token, "API_CREDENTIAL_DRIFT")

    stable()
    partial = {"primary_and_gitee_synced": False, "commits_verified": [],
               "reference_update_attempted": False}
    evidence.save("api-partial.json", partial)
    if mutate:
        sync.synchronize(evidence, source, commit, ["gitlab", "gitee"], mutate=True)
        partial["primary_and_gitee_synced"] = True
        evidence.save("api-partial.json", partial)
        sync.need(remote_main(evidence) == old, "GITHUB_REMOTE_DRIFT")
        for sha, payload, parent_tree, entries in prepared:
            stable()
            exact(request(evidence, "GET", "/commits/" + payload["parents"][0]),
                  payload["parents"][0], "parent")
            tree_entries = []
            for entry in entries:
                if entry["sha"] is not None:
                    blob = request(evidence, "POST", "/blobs",
                                   {"encoding": "base64",
                                    "content": base64.b64encode(entry["raw"]).decode()})
                    exact(blob, entry["sha"], "blob")
                tree_entries.append({k: v for k, v in entry.items() if k != "raw"})
            if tree_entries:
                exact(request(evidence, "POST", "/trees",
                              {"base_tree": parent_tree, "tree": tree_entries}),
                      payload["tree"], "tree")
            exact(request(evidence, "POST", "/commits", payload), sha, "commit")
            partial["commits_verified"].append(sha)
            evidence.save("api-partial.json", partial)
        stable()
        sync.need(remote_main(evidence) == old, "GITHUB_REMOTE_DRIFT")
        for role in ("gitlab", "gitee"):
            sync.need(sync.heads(evidence, source, role, sync.TARGETS[role]).get(
                "refs/heads/main") == commit, "PRIMARY_OR_GITEE_DRIFT")
        if old != commit:
            partial["reference_update_attempted"] = True
            evidence.save("api-partial.json", partial)
            update = request(evidence, "PATCH", "/refs/heads/main",
                             {"sha": commit, "force": False})
            sync.need(update.get("ref") == "refs/heads/main"
                      and update.get("object", {}).get("sha") == commit,
                      "API_UPDATE_RESPONSE_MISMATCH")
    final = []
    for role in ("gitlab", "gitee", "github"):
        current = (remote_main(evidence) if role == "github" else
                   sync.heads(evidence, source, role, sync.TARGETS[role]).get(
                       "refs/heads/main"))
        final.append({"role": role, "at": sync.stamp(), "readback": current})
        evidence.save("api-final-readback.json", final)
        if mutate:
            sync.need(current == commit, "FINAL_REF_MISMATCH")
    if mutate:
        exact(request(evidence, "GET", "/commits/" + commit), commit, "readback")
    stable()
    return {"verdict": "PASS", "at": sync.stamp(), **plan,
            "state": "SYNCED" if mutate else "PLANNED",
            "all_three_synced": mutate, "final_readback": final,
            "source_stable": True, "api_tool_sha256": pin,
            "scope": "CODE_SYNC_NOT_RELEASE_OR_GIT_HTTPS_RECOVERY"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--action", choices=("plan", "sync"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence = None
    try:
        sync.need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
        sync.need(SHA.fullmatch(args.commit), "COMMIT_REQUIRED")
        evidence = sync.Evidence(args.out, sync.load_token())
        result = run(evidence, args.source, args.commit, args.action == "sync")
        evidence.save("result.json", result)
        print(sync.encode(result).decode(), end="", flush=True)
        return 0
    except Exception as exc:
        result = {"verdict": "FAIL", "at": sync.stamp(),
                  "first_cause": str(exc) if isinstance(exc, sync.Refused)
                  else type(exc).__name__}
        if evidence:
            evidence.save("result.failure.json", result)
            evidence.save("result.json", result)
        print(sync.encode(result).decode(), end="", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
