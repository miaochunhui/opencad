#!/usr/bin/env python3
"""Read-only all/source/commit/sync audit; no product execution or credential reads.

Run as infra with Python -B. Only a fresh external 3FS evidence directory is
written. Optional main readbacks use existing strict SSH and public GitHub GET.
No fetch, push, installation, cleanup, config writes, fallback or retry.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import ssl
import stat
import subprocess
import threading


ROOT = Path(__file__).resolve().parents[1]
BASES = (Path("/hf3fs-jd/data/tmp"), Path("/3fs/research3fs/data/tmp"))
LABELS = ("bootstrap-negative-controls", "http-selftest", "cadcore", "web", "dxf")
ROLES = ("gitlab", "gitee", "github")
OWNED = ("tools/gateway_publication_review.py", "tools/test_gateway_publication_review.py",
         "docs/design/gateway-publication-review.md")
COUNT_KEYS = ("tests", "passed", "skipped", "failures", "errors", "http_checks")
SUITES = {
    "sqlite-runtime-negative-controls": ("tools", "tools", "test_sqlite_runtime_isolation.py"),
    "bootstrap-negative-controls": ("tools", "tools", "test_bootstrap.py"),
    "cadcore": ("oss-alternatives/packages/cadcore/tests",
                "oss-alternatives/packages/cadcore", "test*.py"),
    "web": ("oss-alternatives/apps/opencad/tests",
            "oss-alternatives/apps/opencad", "test*.py"),
    "dxf": ("oss-alternatives/packages/dxf/tests",
            "oss-alternatives/packages/dxf", "test*.py"),
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SSH = "/usr/bin/ssh -o BatchMode=yes -o StrictHostKeyChecking=yes"
URLS = {"gitlab": "ssh://git@192.168.1.181/infra/opencad.git",
        "gitee": "git@gitee.com:sdfwefqaa/opencad.git"}
GITLAB_SSH = SSH + " -o IdentitiesOnly=yes -i /home/infra/.ssh/id_ed25519_gitlab"
API = "/repos/miaochunhui/opencad/git"
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN " + rb"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?i)(?:password|passwd|api[_-]?key|client[_-]?secret)"
               rb"""["']?\s*[:=]\s*["'][^"'\r\n]{6,}["']"""),
    re.compile(rb"(?i)IDENTIFIED\s+BY\s+['\"]"),
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
)


class Refused(RuntimeError):
    """Diagnostic code/coordinates only; never disclose input or credential bytes."""


def need(condition, code):
    if not condition:
        raise Refused(code)


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def binding(raw):
    return {"bytes": len(raw), "sha256": sha(raw)}


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def external(value):
    path = value.absolute()
    need(".." not in path.parts, "UNSAFE_EVIDENCE_PATH")
    base = next((base for base in BASES if path.is_relative_to(base) and path != base), None)
    need(base is not None, "EXTERNAL_3FS_REQUIRED")
    # These two existing mount aliases are allowed, but links below them are not.
    for part in (path, *path.parents):
        if part == base:
            break
        need(not part.is_symlink(), "LINKED_EVIDENCE")
    need(path.resolve().is_relative_to(base.resolve()), "EVIDENCE_ESCAPE")
    return path


def regular(path):
    need(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode),
         "NONREGULAR_FILE:" + str(path))
    return path.read_bytes()


def time_value(value):
    parsed = datetime.fromisoformat(value)
    need(parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc), "INVALID_TIMESTAMP")
    return parsed


def safe_relative(name):
    need(isinstance(name, str), "UNSAFE_MANIFEST_PATH")
    path = PurePosixPath(name)
    need(not path.is_absolute() and bool(path.parts) and ".." not in path.parts
         and str(path) == name, "UNSAFE_MANIFEST_PATH")


def manifest(root, *, modes=False):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {
            ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"})
        for name in dirs:
            need(not (Path(directory) / name).is_symlink(), "LINKED_SOURCE_DIRECTORY")
        for name in sorted(files):
            if name == ".git":
                continue
            path = Path(directory) / name
            row = binding(regular(path))
            if modes:
                row["mode"] = "100755" if path.stat().st_mode & 0o111 else "100644"
            result[path.relative_to(root).as_posix()] = row
    need(result and "oss-alternatives/apps/opencad/server.py" in result,
         "EMPTY_OR_NONPRODUCT_SOURCE")
    return dict(sorted(result.items()))


def difference(left, right):
    return sorted(path for path in left.keys() | right.keys() if left.get(path) != right.get(path))


def git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
    return env


def git(root, args):
    child = subprocess.run(["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args],
                           env=git_env(), stdin=subprocess.DEVNULL, capture_output=True)
    need(child.returncode == 0, "LOCAL_GIT_RC_%d:%s" % (child.returncode, args[0]))
    return child.stdout


def review_owned(root):
    changed = git(root, ["diff", "--name-only", "HEAD", "-z"]).split(b"\0")
    untracked = git(root, ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0")
    need(all(name.decode() in OWNED for name in changed + untracked if name),
         "REVIEWER_WRITE_SCOPE")
    files = {}
    for name in OWNED:
        raw = regular(root / name)
        need(not any(pattern.search(raw) for pattern in SECRET_PATTERNS),
             "REVIEWER_SECRET_SHAPE:" + name)
        files[name] = binding(raw)
    return {"head": git(root, ["rev-parse", "HEAD"]).decode().strip(),
            "files": files, "secret_shape_matches": 0, "write_scope": list(OWNED)}


def committed_manifest(root, commit, *, modes=False):
    entries = []
    for line in git(root, ["ls-tree", "-r", "-z", commit]).split(b"\0"):
        if not line:
            continue
        metadata, name = line.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = name.decode()
        safe_relative(name)
        need(kind == "blob" and mode in {"100644", "100755"}, "NONREGULAR_GIT_ENTRY:" + name)
        entries.append((name, oid, mode))
    need(entries, "EMPTY_COMMIT")
    child = subprocess.Popen(["/usr/bin/git", "--no-replace-objects", "-C", str(root),
                              "cat-file", "--batch"], env=git_env(), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors = []
    drain = threading.Thread(target=lambda: errors.append(child.stderr.read()), daemon=True)
    drain.start()
    result, first = {}, None
    try:
        for name, oid, mode in entries:
            child.stdin.write(oid.encode() + b"\n")
            child.stdin.flush()
            header = child.stdout.readline().split()
            need(len(header) == 3 and header[:2] == [oid.encode(), b"blob"], "GIT_BLOB_HEADER")
            size = int(header[2])
            need(0 <= size <= 64000000, "GIT_BLOB_SIZE")
            raw = child.stdout.read(size)
            need(len(raw) == size and child.stdout.read(1) == b"\n", "GIT_BLOB_TRUNCATED")
            need(hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest() == oid,
                 "GIT_BLOB_OBJECT_ID:" + name)
            result[name] = binding(raw)
            if modes:
                result[name]["mode"] = mode
    except Exception as exc:
        first = exc
    finally:
        child.stdin.close()
        child.stdout.close()
        rc = child.wait()
        drain.join()
        child.stderr.close()
    if first:
        raise first
    need(rc == 0 and not any(errors) and len(result) == len(entries), "GIT_BATCH_INCOMPLETE")
    return dict(sorted(result.items()))


def static_test_inventory(root, label, sources, suite=None):
    """Inspect declarations and inheritance without importing test/product modules."""
    start, top, pattern = suite or SUITES[label]
    classes, imports, modules = {}, {}, {}
    for path in sorted((root / start).rglob(pattern)):
        name = path.relative_to(root).as_posix()
        raw = regular(path)
        need(name in sources and binding(raw) == sources[name], "TEST_SOURCE_BINDING:" + name)
        module = path.relative_to(root / top).with_suffix("").as_posix().replace("/", ".")
        tree = ast.parse(raw, filename=str(path))
        aliases = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    for item in node.names:
                        aliases[item.asname or item.name] = node.module + "." + item.name
            elif isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name] = item.name
            elif isinstance(node, ast.ClassDef):
                key = module + "." + node.name
                need(key not in classes, "AMBIGUOUS_TEST_CLASS:" + key)
                bases = [ast.unparse(base) for base in node.bases]
                methods = {child.name for child in node.body
                           if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and child.name.startswith("test")}
                classes[key] = (module, bases, methods)
        imports[module] = aliases
        modules[module] = tree
        # Imported TestCase subclasses are also exposed to unittest discovery.
    cache, active = {}, set()

    def methods(key):
        if key in cache:
            return cache[key]
        need(key not in active, "CYCLIC_TEST_INHERITANCE:" + key)
        active.add(key)
        module, bases, own = classes[key]
        result, is_case = set(), False
        for base in bases:
            first, _, suffix = base.partition(".")
            resolved = imports[module].get(first, module + "." + first)
            if suffix:
                resolved += "." + suffix
            if resolved in {"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"}:
                is_case = True
            elif resolved in classes:
                parent_case, parent = methods(resolved)
                is_case |= parent_case
                result.update(parent)
        if is_case:
            result.update(own)
        active.remove(key)
        cache[key] = is_case, result
        return cache[key]

    def case_ids(key):
        need(key in classes and methods(key)[0], "UNKNOWN_DISCOVERY_CASE:" + key)
        return {name + " (" + key + ")" for name in methods(key)[1]}

    inventory = set()
    for module, aliases in imports.items():
        exposed = {key for key in classes if classes[key][0] == module and methods(key)[0]}
        for key in aliases.values():
            if key in classes and methods(key)[0]:
                exposed.add(key)
        tree = modules[module]
        hooks = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "load_tests"]
        need(len(hooks) <= 1, "AMBIGUOUS_DISCOVERY_HOOK:" + module)
        if not hooks:
            inventory.update(item for key in exposed for item in case_ids(key))
            continue
        hook = hooks[0]
        need(len(hook.args.args) == 3 and not hook.args.vararg and not hook.args.kwarg,
             "UNSUPPORTED_DISCOVERY_HOOK:" + module)
        defaults = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant):
                defaults[node.targets[0].id] = node.value.value
        suites = {hook.args.args[1].arg: set().union(*(case_ids(key) for key in exposed))}

        def selected(node):
            if isinstance(node, ast.Name) and node.id in suites:
                return suites[node.id].copy()
            if isinstance(node, ast.Call) and not node.keywords:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "loadTestsFromTestCase" \
                        and ast.unparse(node.func.value) == hook.args.args[0].arg and len(node.args) == 1:
                    key = ast.unparse(node.args[0])
                    return case_ids(aliases.get(key, module + "." + key))
                if isinstance(node.func, ast.Name) and len(node.args) == 1:
                    wrappers = [item for item in tree.body if isinstance(item, ast.ClassDef)
                                and item.name == node.func.id]
                    need(len(wrappers) == 1 and not any(
                        isinstance(item, ast.FunctionDef) and item.name in {"__init__", "__iter__"}
                        for item in wrappers[0].body), "UNSUPPORTED_DISCOVERY_WRAPPER:" + module)
                    return selected(node.args[0])
            # The delegated UI builder explicitly requests all classes and methods.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "build_suite" and len(node.args) == 1 \
                    and ast.unparse(node.args[0]) == hook.args.args[0].arg:
                options = {item.arg: ast.literal_eval(item.value) for item in node.keywords}
                need(options == {"browser": True, "suite_name": "all"},
                     "SCOPED_DISCOVERY_BUILDER:" + module)
                builders = [item for item in tree.body if isinstance(item, ast.FunctionDef)
                            and item.name == "build_suite"]
                need(len(builders) == 1, "UNSUPPORTED_DISCOVERY_BUILDER:" + module)
                builder = builders[0]
                selections = [item for item in ast.walk(builder) if isinstance(item, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == "classes"
                                      for target in item.targets)]
                need(len(selections) == 1, "UNSUPPORTED_DISCOVERY_BUILDER:" + module)
                value = selections[0].value
                need(isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
                     and isinstance(value.left, ast.List) and isinstance(value.right, ast.IfExp)
                     and ast.unparse(value.right.test) == "browser"
                     and isinstance(value.right.body, ast.List), "UNSUPPORTED_DISCOVERY_BUILDER:" + module)
                keys = [aliases.get(ast.unparse(item), module + "." + ast.unparse(item))
                        for item in value.left.elts + value.right.body.elts]
                # Do not silently interpret a changed builder as complete discovery.
                predicates = [item for item in ast.walk(builder) if isinstance(item, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == "included"
                                      for target in item.targets)]
                branches = [item for item in ast.walk(builder) if isinstance(item, ast.If)
                            and any(isinstance(child, ast.Call)
                                    and isinstance(child.func, ast.Attribute)
                                    and child.func.attr == "addTest" for child in ast.walk(item))]
                need(len(predicates) == len(branches) == 1
                     and isinstance(predicates[0].value, ast.BoolOp)
                     and isinstance(predicates[0].value.op, ast.Or)
                     and ast.unparse(predicates[0].value.values[0]) == "suite_name == 'all'"
                     and ast.unparse(branches[0].test)
                     == "names and name in names or (not names and included)",
                     "UNSUPPORTED_DISCOVERY_FILTER:" + module)
                need(set(keys) == exposed, "DISCOVERY_BUILDER_CLASS_SET:" + module)
                return set().union(*(case_ids(key) for key in keys))
            raise Refused("UNSUPPORTED_DISCOVERY_SELECTION:" + module)

        def statements(nodes):
            for node in nodes:
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    continue
                if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    suites[node.targets[0].id] = selected(node.value)
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                        and isinstance(node.value.func, ast.Attribute) \
                        and node.value.func.attr == "addTests" and len(node.value.args) == 1 \
                        and not node.value.keywords and isinstance(node.value.func.value, ast.Name):
                    suites[node.value.func.value.id].update(selected(node.value.args[0]))
                elif isinstance(node, ast.If):
                    condition = node.test
                    need(isinstance(condition, ast.Compare) and isinstance(condition.left, ast.Name)
                         and condition.left.id in defaults and len(condition.ops) == 1
                         and isinstance(condition.ops[0], (ast.Is, ast.IsNot))
                         and len(condition.comparators) == 1
                         and isinstance(condition.comparators[0], ast.Constant)
                         and condition.comparators[0].value is None,
                         "UNSUPPORTED_DISCOVERY_CONDITION:" + module)
                    enabled = defaults[condition.left.id] is None
                    if isinstance(condition.ops[0], ast.IsNot):
                        enabled = not enabled
                    result = statements(node.body if enabled else node.orelse)
                    if result is not None:
                        return result
                elif isinstance(node, ast.Return):
                    return selected(node.value)
                else:
                    raise Refused("UNSUPPORTED_DISCOVERY_STATEMENT:" + module)
            return None

        found = statements(hook.body)
        need(found is not None, "DISCOVERY_HOOK_NO_RETURN:" + module)
        inventory.update(found)
    need(inventory, "EMPTY_STATIC_DISCOVERY:" + label)
    return sorted(inventory)


TEST_START = re.compile(r"^(test\w* \([\w.]+\))(?: \.\.\. |$)", re.M)
TEST_END = re.compile(r"(?:^| \.\.\. )(ok|skipped .+|FAIL|ERROR|expected failure|unexpected success)$", re.M)


def test_log_results(text, label):
    """Bind each verbosity-2 test start to one terminal, including interleaved stdout."""
    starts = list(TEST_START.finditer(text))
    rows = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[match.start():end]
        foot = re.search(r"^Ran \d+ tests? in ", block, re.M)
        if foot:
            block = block[:foot.start()]
        terminals = TEST_END.findall(block)
        need(len(terminals) == 1, "TEST_TERMINAL_SET:" + label + ":" + match[1])
        terminal = terminals[0]
        need(terminal == "ok" or terminal.startswith("skipped "), "TEST_NOT_PASS:" + match[1])
        rows.append({"test": match[1], "outcome": "skipped" if terminal.startswith("skipped ") else "passed",
                     "line": text.count("\n", 0, match.start()) + 1})
    need(rows, "EMPTY_TEST_LOG:" + label)
    need(len({row["test"] for row in rows}) == len(rows), "DUPLICATE_TEST_LOG:" + label)
    return rows


def delegated_ui(text, root, sources, read, started, finished):
    """Only follow explicit producer markers; never search or guess evidence paths."""
    markers = re.findall(
        r"ADVANCED_UI_REVIEW verdict=PASS tests=(\d+) failures=0 errors=0 browser=RUN evidence=(\S+)",
        text)
    if not markers:
        return [], None
    need(len(markers) == 1 and read is not None, "DELEGATED_UI_RECEIPT_REQUIRED")
    count, path = markers[0]
    path = external(Path(path))
    need(path.name == "result.json", "DELEGATED_UI_RESULT_PATH")
    raw, log = read(path), read(path.parent / "tests.log")
    result = json.loads(raw)
    need(result.get("verdict") == "PASS" and result.get("browser") == "RUN"
         and result.get("suite") == "all" and result.get("selected_tests") == []
         and result.get("resource_mode") == "MAIN_DIRECT"
         and result.get("tests") == int(count) and result.get("failures") == 0
         and result.get("errors") == 0 and result.get("skips") == 0
         and result.get("source_unchanged") is True
         and result.get("source_before") == result.get("source_after")
         and Path(result["source_root"]).resolve() == root, "DELEGATED_UI_RESULT_BINDING")
    need(time_value(started) <= time_value(result["collected_at"]) <= time_value(finished),
         "DELEGATED_UI_TIME")
    need(result["source_before"], "DELEGATED_UI_EMPTY_SOURCE")
    for name, fingerprint in result["source_before"].items():
        relative = Path(name).relative_to(root).as_posix()
        need(relative in sources and fingerprint == sources[relative]["sha256"],
             "DELEGATED_UI_SOURCE:" + relative)
    body = log.decode("utf-8")
    rows = test_log_results(body, "delegated-ui")
    need(len(rows) == int(count) and all(row["outcome"] == "passed" for row in rows),
         "DELEGATED_UI_TEST_COUNT")
    need(re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", body, re.M) == [count]
         and re.findall(r"^OK$", body, re.M) == ["OK"]
         and body.rstrip().endswith(
             "ADVANCED_UI_REVIEW verdict=PASS tests=%s failures=0 errors=0 browser=RUN evidence=%s"
             % (count, path)), "DELEGATED_UI_LOG_TERMINAL")
    for row in rows:
        row["log"] = str(path.parent / "tests.log")
    return rows, {"result": str(path), **binding(raw), "log": binding(log)}


def doc_successor(root, frozen_commit, commit, frozen, current, allowed):
    """Exact doc-only linear successor; never label its whole source all-accepted."""
    allowed = set(allowed)
    for name in allowed:
        safe_relative(name)
        need(name.startswith("docs/") and name.endswith(".md")
             and not any(char in name for char in "*?[]"), "EXPLICIT_DOC_PATH_REQUIRED")
    delta = difference(frozen, current)
    if commit == frozen_commit:
        need(not allowed and not delta, "SAME_COMMIT_SOURCE_MISMATCH")
        return {"status": "EXACT_FROZEN_SOURCE", "docs": [], "whole_source_all_accepted": True}
    need(allowed and set(delta) == allowed, "DOC_PATH_SET_MISMATCH")
    tail = git(root, ["rev-list", "--reverse", frozen_commit + ".." + commit]).decode().splitlines()
    need(tail and tail[-1] == commit, "DOC_SUCCESSOR_ANCESTRY")
    previous, changed = frozen_commit, set()
    for item in tail:
        need(git(root, ["show", "-s", "--format=%P", item]).decode().split() == [previous],
             "DOC_SUCCESSOR_NOT_LINEAR")
        names = git(root, ["diff-tree", "--no-commit-id", "--name-only", "-r",
                          previous, item, "-z"]).split(b"\0")
        changed.update(os.fsdecode(name) for name in names if name)
        need(changed <= allowed, "NON_DOC_INTERMEDIATE_CHANGE")
        previous = item
    need(changed == allowed, "DOC_DECLARATION_NOT_EXACT")
    rows = []
    for name in sorted(allowed):
        need(name in current and current[name]["mode"] == "100644"
             and (name not in frozen or frozen[name]["mode"] == "100644"),
             "DOC_MODE_OR_DELETION:" + name)
        rows.append({"path": name, "before": frozen.get(name), "after": current[name]})
    return {"status": "EXPLICIT_DOC_ONLY_SUCCESSOR", "tail": tail, "docs": rows,
            "whole_source_all_accepted": False, "all_accepted_commit": frozen_commit,
            "publication_commit": commit}


def check_publication_target(relation, frozen_commit, commit, target):
    need(target in {frozen_commit, commit} or target in relation.get("tail", []),
         "PUBLICATION_OUTSIDE_VERIFIED_DOC_LINEAGE")
    return {"recorded_commit": target, "current_source_head_published": target == commit}


def check_manifest(value):
    need(isinstance(value, dict) and value, "EMPTY_MANIFEST")
    for name, row in value.items():
        safe_relative(name)
        need(isinstance(row, dict) and set(row) == {"bytes", "sha256"}
             and type(row["bytes"]) is int and row["bytes"] >= 0
             and isinstance(row["sha256"], str) and HASH.fullmatch(row["sha256"]),
             "BAD_MANIFEST_ENTRY:" + name)


def check_origins(origins, sources):
    prefixes = {"cadcore": "oss-alternatives/packages/cadcore/cadcore",
                "dxf": "oss-alternatives/packages/dxf/dxf",
                "freechat_vault": "oss-alternatives/packages/vault/freechat_vault"}
    need(isinstance(origins, dict) and origins, "EMPTY_RUNTIME_ORIGINS")
    for module, locations in origins.items():
        prefix = prefixes.get(module.split(".")[0])
        need(prefix is not None, "FOREIGN_RUNTIME_NAMESPACE")
        if isinstance(locations, str):
            need(locations.startswith(prefix + "/") and locations in sources,
                 "UNBOUND_RUNTIME_FILE:" + module)
        else:
            need(isinstance(locations, list) and locations, "EMPTY_NAMESPACE_PATH")
            for directory in locations:
                safe_relative(directory)
                need((directory == prefix or directory.startswith(prefix + "/"))
                     and any(name.startswith(directory + "/") for name in sources),
                     "UNBOUND_RUNTIME_DIRECTORY:" + module)


def check_counts(counts):
    need(isinstance(counts, dict), "COUNT_MARKER_MISSING")
    for key in ("discovered", "tests", "passed", "failures", "errors"):
        need(type(counts.get(key)) is int and counts[key] >= 0, "INVALID_COUNT:" + key)
    for key in ("skipped", "expected_failures", "unexpected_successes"):
        need(isinstance(counts.get(key), list), "INVALID_COUNT_LIST:" + key)
    need(counts["tests"] > 0 and counts["discovered"] == counts["tests"], "EMPTY_OR_UNRUN_TESTS")
    need(counts["tests"] == counts["passed"] + counts["failures"] + counts["errors"]
         + len(counts["skipped"]) + len(counts["expected_failures"])
         + len(counts["unexpected_successes"]), "COUNTS_UNRECONCILED")
    need(counts.get("successful") is True and counts.get("counts_reconciled") is True
         and counts["failures"] == counts["errors"] == 0
         and not counts["expected_failures"] and not counts["unexpected_successes"],
         "UNSUCCESSFUL_COUNTS")
    for skip in counts["skipped"]:
        need(set(skip) == {"test", "reason"} and skip["test"] and skip["reason"], "UNNAMED_SKIP")


def check_skip_log(text, skips, label):
    need(len(re.findall(r" \.\.\. skipped .+$", text, re.M)) == len(skips),
         "SKIP_LOG_COUNT:" + label)
    for skip in skips:
        # unittest descriptions can put the first docstring line on a new line.
        pattern = (r"^" + re.escape(skip["test"]) + r"(?:\n(?!\w+ \()[^\n]+)?"
                   + r" \.\.\. skipped " + re.escape(repr(skip["reason"])) + r"$")
        need(len(re.findall(pattern, text, re.M)) == 1, "SKIP_LOG_BINDING:" + label)


def static_http_checks(root, sources):
    name = "oss-alternatives/apps/opencad/server.py"
    raw = regular(root / name)
    need(binding(raw) == sources[name], "HTTP_TEST_SOURCE_BINDING")
    tree = ast.parse(raw)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_selftest_with_out_root"]
    need(len(functions) == 1, "HTTP_CHECK_FUNCTION_REQUIRED")
    checks = []
    parents = {child: node for node in ast.walk(functions[0]) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(functions[0]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check":
            need(len(node.args) >= 2 and isinstance(node.args[0], ast.Constant)
                 and isinstance(node.args[0].value, str), "HTTP_CHECK_ID_REQUIRED")
            ancestor = parents.get(node)
            while ancestor is not functions[0]:
                need(ancestor is not None and not isinstance(ancestor, (
                    ast.If, ast.For, ast.AsyncFor, ast.While, ast.FunctionDef, ast.Lambda,
                    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)),
                     "CONDITIONAL_HTTP_CHECK_NOT_STATIC")
                ancestor = parents.get(ancestor)
            checks.append((node.lineno, node.args[0].value))
    names = [name for _, name in sorted(checks)]
    need(names and len(set(names)) == len(names), "EMPTY_OR_DUPLICATE_HTTP_CHECKS")
    return names


def all_stage_plan(root, sources, evidence, runtime):
    """Read the managed all-stage command builder, never execute acceptance.py."""
    name = "tools/acceptance.py"
    raw = regular(root / name)
    need(name in sources and binding(raw) == sources[name], "STAGE_PLAN_SOURCE_BINDING")
    tree = ast.parse(raw)
    main = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    need(len(main) == 1, "STAGE_PLAN_MAIN_REQUIRED")
    values = {"ROOT": root, "PACKAGES": root / "oss-alternatives/packages",
              "APP": root / "oss-alternatives/apps/opencad", "runtime": Path(runtime),
              "out": evidence.resolve(), "__file__": root / name}
    plan = []

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [value(item) for item in node.elts]
        if isinstance(node, ast.Attribute) and ast.unparse(node) == "args.stage":
            return "all"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return value(node.left) / value(node.right)
        if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords \
                and isinstance(node.func, ast.Name) and node.func.id in {"str", "Path"}:
            return str(value(node.args[0])) if node.func.id == "str" else Path(value(node.args[0]))
        if isinstance(node, ast.Call) and not node.args and not node.keywords \
                and isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
            return value(node.func.value).resolve()
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if isinstance(node.ops[0], ast.In):
                return left in right
            if isinstance(node.ops[0], ast.Eq):
                return left == right
        raise Refused("UNSUPPORTED_ALL_STAGE_PLAN_EXPRESSION")

    def statements(nodes):
        for node in nodes:
            if isinstance(node, ast.If):
                statements(node.body if value(node.test) else node.orelse)
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                    and ast.unparse(node.value.func) == "commands.append" \
                    and len(node.value.args) == 1 and not node.value.keywords:
                row = value(node.value.args[0])
                need(len(row) == 3 and isinstance(row[0], str) and isinstance(row[1], list),
                     "INVALID_ALL_STAGE_PLAN_ROW")
                plan.append({"label": row[0], "argv": row[1], "cwd": str(row[2])})
            else:
                raise Refused("UNSUPPORTED_ALL_STAGE_PLAN_STATEMENT")

    blocks = [node for node in ast.walk(main[0]) if isinstance(node, ast.Try)]
    candidates = []
    for block in blocks:
        begin = next((index for index, node in enumerate(block.body)
                      if isinstance(node, ast.Assign) and ast.unparse(node) == "commands = []"), None)
        if begin is not None:
            end = next((index for index in range(begin + 1, len(block.body))
                        if isinstance(block.body[index], ast.For)
                        and ast.unparse(block.body[index].iter) == "commands"), None)
            need(end is not None, "ALL_STAGE_EXECUTOR_REQUIRED")
            candidates.append(block.body[begin + 1:end])
    need(len(candidates) == 1, "ALL_STAGE_PLAN_REQUIRED")
    statements(candidates[0])
    guarded = any(isinstance(node, ast.ClassDef) and node.name == "SQLiteRuntimeGuard"
                  for node in tree.body)
    expected = (("sqlite-runtime-negative-controls",) if guarded else ()) + LABELS
    need([row["label"] for row in plan] == list(expected), "UNSUPPORTED_ALL_STAGE_PLAN")
    need(all(row["cwd"] == str(root) for row in plan), "ALL_STAGE_PLAN_CWD")
    return plan, guarded, binding(raw)


def sqlite_runtime_binding(step, counts, text, runtime, guarded):
    lines = [line.removeprefix("INDEPENDENT_RUNTIME_BINDING ") for line in text.splitlines()
             if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
    if not guarded:
        need(not lines and "sqlite_runtime" not in step
             and (counts is None or "sqlite_runtime" not in counts), "UNEXPECTED_SQLITE_MARKER")
        return None
    need(not step.get("runtime_error") and len(lines) == 1, "SQLITE_RUNTIME_MARKER_REQUIRED")
    recorded = step.get("sqlite_runtime")
    need(isinstance(recorded, dict) and json.loads(lines[0]) == recorded
         and (counts is None or counts.get("sqlite_runtime") == recorded),
         "SQLITE_RUNTIME_RESULT_BINDING")
    persistent = {"ext2/ext3", "ext4", "xfs", "btrfs", "zfs"}

    def path_check(value, expected_fs):
        path = Path(value)
        need(path.is_absolute() and ".." not in path.parts and path != Path(runtime)
             and path.is_relative_to(Path(runtime)), "SQLITE_PATH_OUTSIDE_RUNTIME")
        for part in (path, *path.parents):
            need(not part.is_symlink(), "SQLITE_PATH_LINKED")
            if part == Path("/data/tmp"):
                break
        ancestor = next((part for part in (path.parent, *path.parents) if part.is_dir()), None)
        need(ancestor is not None, "SQLITE_PATH_ANCESTOR_MISSING")
        process = subprocess.run(["stat", "-f", "-c", "%T", str(ancestor)], capture_output=True)
        need(process.returncode == 0 and process.stdout.decode().strip() == expected_fs,
             "SQLITE_ACTUAL_FILESYSTEM")
    need(recorded.get("root") == recorded.get("TMPDIR") == recorded.get("tempfile") == runtime
         and recorded.get("filesystem") in persistent and recorded.get("rejected") == [],
         "SQLITE_RUNTIME_DECLARATION_OR_REJECTION")
    need(type(recorded.get("sqlite_opens")) is int and recorded["sqlite_opens"] >= 0
         and isinstance(recorded.get("sqlite_paths"), dict), "SQLITE_OBSERVATION_SCHEMA")
    paths = recorded["sqlite_paths"]
    need((recorded["sqlite_opens"] == 0) == (not paths)
         and recorded["sqlite_opens"] >= len(paths), "SQLITE_OBSERVATIONS_UNRECONCILED")
    path_check(str(Path(runtime) / "runtime-witness"), recorded["filesystem"])
    for name, filesystem in paths.items():
        need(filesystem in persistent, "SQLITE_RECORDED_FILESYSTEM")
        path_check(name, filesystem)
        for suffix in ("-wal", "-shm", "-journal"):
            path_check(name + suffix, filesystem)
    if step["label"] in {"cadcore", "dxf"}:
        need(paths and recorded["sqlite_opens"] > 0, "SQLITE_REQUIRED_OBSERVATIONS_EMPTY")
    return recorded


def sqlite_nested_steps(text, root, sources, evidence, runtime, read):
    """Bind nested child markers to calls declared by the SQLite control tests."""
    name = "tools/test_sqlite_runtime_isolation.py"
    raw = regular(root / name)
    need(binding(raw) == sources[name], "SQLITE_NESTED_SOURCE_BINDING")
    tree = ast.parse(raw)
    calls = []
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef) or not method.name.startswith("test"):
                continue
            for node in ast.walk(method):
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "A.child":
                    need(len(node.args) == 5 and not node.keywords
                         and ast.unparse(node.args[2]) == "self.evidence"
                         and isinstance(node.args[3], ast.Constant)
                         and isinstance(node.args[3].value, str), "UNKNOWN_SQLITE_NESTED_CALL")
                    calls.append((method.name, node.args[3].value))
    markers = re.findall(r"^INDEPENDENT_STEP label=(\S+) rc=(-?\d+) counts=(.+)$", text, re.M)
    need(len(markers) == len(calls), "SQLITE_NESTED_MARKER_SET")
    receipts = []
    for (method, label), (recorded_label, rc, literal) in zip(calls, markers):
        need(label == recorded_label and rc == "0" and read is not None, "SQLITE_NESTED_MARKER")
        safe_relative(method)
        safe_relative(label)
        path = evidence / method / (label + ".log")
        data = read(path)
        body = data.decode()
        counts = [json.loads(line.removeprefix("INDEPENDENT_TEST_COUNTS "))
                  for line in body.splitlines() if line.startswith("INDEPENDENT_TEST_COUNTS ")]
        bindings = [json.loads(line.removeprefix("INDEPENDENT_RUNTIME_BINDING "))
                    for line in body.splitlines() if line.startswith("INDEPENDENT_RUNTIME_BINDING ")]
        environments = [json.loads(line.removeprefix("ENVIRONMENT_BINDING "))
                        for line in body.splitlines() if line.startswith("ENVIRONMENT_BINDING ")]
        need(len(counts) == len(bindings) == len(environments) == 1, "SQLITE_NESTED_LOG_MARKERS")
        summary, binding_row, environment = counts[0], bindings[0], environments[0]
        need(ast.literal_eval(literal) == summary and summary.get("successful") is True
             and type(summary.get("tests")) is int and summary["tests"] > 0
             and summary["tests"] == summary["passed"] and summary["errors"] == summary["failures"] == 0
             and summary["skipped"] == summary["expected_failures"]
             == summary["unexpected_successes"] == [], "SQLITE_NESTED_COUNTS")
        child_root = binding_row.get("root")
        need(isinstance(child_root, str) and Path(child_root) != Path(runtime)
             and Path(child_root).is_relative_to(Path(runtime)), "SQLITE_NESTED_ROOT")
        need(body.splitlines()[0] == "OPENCAD_TEST_RUNTIME_ROOT=" + child_root
             and re.findall(r"^INDEPENDENT_CHILD_RC rc=(-?\d+)$", body, re.M) == ["0"]
             and body.rstrip().endswith("INDEPENDENT_CHILD_RC rc=0"), "SQLITE_NESTED_EXIT")
        need(environment.get("equal") is True and environment.get("runtime")
             == environment.get("TMPDIR") == environment.get("tempfile") == child_root,
             "SQLITE_NESTED_ENVIRONMENT")
        sqlite_runtime_binding({"label": label, "sqlite_runtime": binding_row}, None,
                               body, child_root, True)
        allowed = {"OPENCAD_TEST_RUNTIME_ROOT", "INDEPENDENT_TEST_COUNTS", "ENVIRONMENT_BINDING",
                   "INDEPENDENT_RUNTIME_BINDING", "INDEPENDENT_CHILD_RC"}
        need(all(not line or line.split()[0].split("=")[0] in allowed
                 for line in body.splitlines()), "SQLITE_NESTED_UNKNOWN_MARKER")
        receipts.append({"log": str(path), **binding(data), "original_child_rc": 0,
                         "counts": summary, "sqlite_runtime": binding_row,
                         "counted_in_product_totals": False})
    return receipts


def review_all(report, files, root, evidence, baseline, read=None):
    need(report.get("verdict") == "PASS" and report.get("stage") == "all", "ALL_NOT_PASS")
    need(not report.get("error") and not report.get("source_recheck_error")
         and not report.get("first_cause"), "ALL_RECORDED_ERROR")
    need(time_value(report["finished_at"]) >= time_value(report["at"]), "ALL_TIME_ORDER")
    sources = report["source_before"]
    check_manifest(sources)
    fingerprint = digest(sources)
    need(report.get("source_drift") == []
         and report.get("source_sha256") == report.get("source_after_sha256") == fingerprint,
         "ALL_SOURCE_BINDING")
    need(report["scan"]["files"] == len(sources) and report["scan"]["source_sha256"] == fingerprint
         and report["scan"]["credential_matches"] == 0, "ALL_SCAN_BINDING")
    for key in ("production_deployment", "multi_tenant_readiness", "industrial_parity"):
        need(report.get(key) == "NOT_ASSESSED", "ALL_SCOPE_INFLATION")
    check_origins(report["scan"]["loaded_cad_origins"], sources)
    origins = report["scan"]["runtime_origins"]
    need(origins.get("server") == "oss-alternatives/apps/opencad/server.py", "SCAN_SERVER_BINDING")
    check_origins({key: name for key, name in origins.items() if key != "server"}, sources)
    plan, guarded, plan_source = all_stage_plan(root, sources, evidence, report["test_runtime"]["root"])
    need([step.get("label") for step in report["steps"]] == [row["label"] for row in plan],
         "STAGE_PLAN_SET_MISMATCH")
    head_keys = ("source_head_before", "source_head_after", "source_status_before", "source_status_after")
    if guarded:
        need(report.get("source_after") == sources and type(report.get("actual_rc")) is int
             and report["actual_rc"] == 0
             and report.get("source_head_before") == report.get("source_head_after")
             == git(root, ["rev-parse", "HEAD"]).decode().strip()
             and report.get("source_status_before") == report.get("source_status_after") == "",
             "ALL_SOURCE_HEAD_STATUS_OR_RC")
    else:
        need(not any(key in report for key in head_keys), "UNEXPECTED_SOURCE_IDENTITY_FIELDS")
    totals, rows, skips = dict.fromkeys(COUNT_KEYS, 0), [], []
    for step, planned in zip(report["steps"], plan):
        label, counts = step["label"], step["counts"]
        raw = files[label + ".log"]
        need(sha(raw) == step["log_sha256"], "LOG_HASH:" + label)
        text = raw.decode("utf-8")
        terminals = re.findall(r"^INDEPENDENT_CHILD_RC rc=(-?\d+)$", text, re.M)
        need(len(terminals) == 1 and type(step["rc"]) is int
             and int(terminals[0]) == step["rc"] == 0, "CHILD_EXIT:" + label)
        need(text.rstrip().endswith("INDEPENDENT_CHILD_RC rc=0"), "CHILD_NOT_TERMINAL:" + label)
        need(not step.get("marker_error") and not step.get("runtime_error"), "STEP_MARKER_ERROR:" + label)
        need(Path(step["cwd"]).resolve() == root
             and Path(step["argv"][0]).resolve() == root / "tools/acceptance.py",
             "CHILD_SOURCE_ROOT:" + label)
        runtime = report["test_runtime"]["root"]
        need(step["argv"] == planned["argv"] and step["cwd"] == planned["cwd"],
             "FULL_DISCOVERY_ARGUMENTS:" + label)
        need(step["test_runtime_root"] == runtime
             and text.splitlines()[0] == "OPENCAD_TEST_RUNTIME_ROOT=" + runtime,
             "CHILD_RUNTIME:" + label)
        markers = [line.removeprefix("INDEPENDENT_TEST_COUNTS ") for line in text.splitlines()
                   if line.startswith("INDEPENDENT_TEST_COUNTS ")]
        row = {"label": label, "original_child_rc": step["rc"], **binding(raw)}
        row["sqlite_runtime"] = sqlite_runtime_binding(step, counts, text, runtime, guarded)
        if guarded:
            need(row["sqlite_runtime"]["filesystem"] == report["test_runtime"]["filesystem"],
                 "SQLITE_ROOT_FILESYSTEM_BINDING")
        allowed_markers = {"INDEPENDENT_CHILD_RC", "INDEPENDENT_TEST_COUNTS"}
        if label == "http-selftest":
            allowed_markers = {"INDEPENDENT_CHILD_RC", "INDEPENDENT_HTTP_BINDINGS"}
        if guarded:
            allowed_markers.add("INDEPENDENT_RUNTIME_BINDING")
            if label == "sqlite-runtime-negative-controls":
                row["nested_children"] = sqlite_nested_steps(
                    text, root, sources, evidence, runtime, read)
                allowed_markers.add("INDEPENDENT_STEP")
        need(all(line.split()[0] in allowed_markers for line in text.splitlines()
                 if line.startswith("INDEPENDENT_")), "UNEXPECTED_CHILD_MARKER:" + label)
        if label == "http-selftest":
            need(counts is None and not markers, "HTTP_IS_NOT_UNITTEST")
            http_ids = static_http_checks(root, sources)
            need(re.findall(r"^OPENCAD_SERVER selftest verdict=PASS checks=(\d+) failed=0$",
                            text, re.M) == [str(len(http_ids))]
                 and type(step["http_checks"]) is int and step["http_checks"] == len(http_ids),
                 "HTTP_SOURCE_MARKER_COUNT")
            origins = [line.removeprefix("INDEPENDENT_HTTP_BINDINGS ")
                       for line in text.splitlines() if line.startswith("INDEPENDENT_HTTP_BINDINGS ")]
            need(len(origins) == 1, "HTTP_BINDING_MARKER")
            check_origins(json.loads(origins[0]), sources)
            totals["http_checks"] += len(http_ids)
            row.update(http_checks=len(http_ids), source_check_ids=http_ids,
                       per_check_results="NOT_RECORDED; aggregate marker only")
        else:
            need(len(markers) == 1 and json.loads(markers[0]) == counts, "COUNT_LOG_BINDING:" + label)
            check_counts(counts)
            check_origins(counts["loaded_cad_origins"], sources)
            ran = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", text, re.M)
            need(ran == [str(counts["tests"])], "UNITTEST_FOOTER:" + label)
            expected = "OK" + (" (skipped=%d)" % len(counts["skipped"]) if counts["skipped"] else "")
            need([line for line in text.splitlines() if line.startswith("OK")] == [expected],
                 "UNITTEST_OK:" + label)
            check_skip_log(text, counts["skipped"], label)
            observed = test_log_results(text, label)
            for item in observed:
                item["log"] = str(evidence / (label + ".log"))
            delegated, receipt = delegated_ui(text, root, sources, read, report["at"],
                                              report["finished_at"]) if label == "web" else ([], None)
            observed += delegated
            names = [item["test"] for item in observed]
            need(len(set(names)) == len(names), "DUPLICATE_COMBINED_TEST:" + label)
            discovered = static_test_inventory(root, label, sources)
            missing, extra = sorted(set(discovered) - set(names)), sorted(set(names) - set(discovered))
            need(not missing and not extra, "TEST_IDENTITY_SET_MISMATCH:" + label
                 + ":missing=" + repr(missing) + ":extra=" + repr(extra))
            need(len(observed) == counts["discovered"] == counts["tests"]
                 and sum(item["outcome"] == "passed" for item in observed) == counts["passed"]
                 and {item["test"] for item in observed if item["outcome"] == "skipped"}
                 == {item["test"] for item in counts["skipped"]}, "TEST_OUTCOME_COUNTS:" + label)
            row.update(discovered_test_ids=discovered, observed_tests=observed,
                       test_identity_difference={"missing": missing, "extra": extra},
                       delegated_receipt=receipt)
            for key in ("tests", "passed", "failures", "errors"):
                totals[key] += counts[key]
                row[key] = counts[key]
            totals["skipped"] += len(counts["skipped"])
            row["skipped"] = len(counts["skipped"])
            skips.extend({"label": label, **skip} for skip in counts["skipped"])
        rows.append(row)
    need(isinstance(report.get("totals"), dict) and set(report["totals"]) == set(COUNT_KEYS)
         and all(type(value) is int and value >= 0 for value in report["totals"].values())
         and totals == report["totals"], "TOTALS_MISMATCH")
    need(baseline.get("verdict") == "PASS" and baseline.get("source_drift") == [], "BASELINE_NOT_ACCEPTED")
    original = [{"label": step["label"], **skip} for step in baseline["steps"]
                if step.get("counts") for skip in step["counts"]["skipped"]]
    order = lambda value: (value["label"], value["test"], value["reason"])
    for items in (original, skips):
        need(len({(item["label"], item["test"]) for item in items}) == len(items),
             "DUPLICATE_BASELINE_OR_ACTUAL_SKIP")
        for item in items:
            need(set(item) == {"label", "test", "reason"}
                 and all(isinstance(value, str) and value for value in item.values()),
                 "INVALID_BASELINE_OR_ACTUAL_SKIP")
    need(sorted(original, key=order) == sorted(skips, key=order), "ORIGINAL_SKIPS_CHANGED")
    terminal = files["acceptance.log"].decode().strip()
    prefix = "INDEPENDENT_ACCEPTANCE verdict=PASS stage=all evidence="
    need(terminal.startswith(prefix)
         and Path(terminal[len(prefix):]).resolve() == evidence.resolve(), "ALL_TERMINAL_BINDING")
    runtime = report["test_runtime"]
    need(Path(runtime["root"]).is_absolute()
         and Path(runtime["root"]).is_relative_to(Path("/data/tmp"))
         and runtime["filesystem"] not in {"tmpfs", "ramfs", "nfs", "nfs4", "smb2", "cifs"}
         and not runtime["filesystem"].startswith("fuse"), "NONLOCAL_TEST_RUNTIME")
    return {"totals": totals, "steps": rows, "original_skips": skips, "source_files": len(sources),
            "stage_plan": plan, "stage_plan_source": plan_source, "sqlite_runtime_guarded": guarded,
            "source_sha256": fingerprint, "all_started_at": report["at"],
            "all_finished_at": report["finished_at"],
            "original_all_parent_rc": report.get("actual_rc"),
            "parent_rc_status": "RECORDED" if "actual_rc" in report
            else "NOT_RECORDED_IN_INPUTS; child exits recorded"}


def review_publication(value, refs, commands, commit, finished):
    need(value.get("verdict") == "PASS" and value.get("state") == "SYNCED"
         and value.get("all_three_synced") is True and value.get("source_stable") is True,
         "PUBLICATION_NOT_SYNCED")
    need(value.get("commit") == commit, "PUBLICATION_COMMIT")
    need(value.get("final_readback") == refs and [row.get("role") for row in refs] == list(ROLES),
         "PUBLICATION_THREE_ROLES")
    prior = time_value(finished)
    for row in refs:
        current = time_value(row["at"])
        need(row.get("readback") == commit and current >= prior, "PUBLICATION_REF_OR_TIME")
        prior = current
    need(time_value(value["at"]) >= prior, "PUBLICATION_FINISH_TIME")
    need(isinstance(commands, list) and commands, "PUBLICATION_COMMANDS_EMPTY")
    for role in ROLES[:2]:
        queries = [row for row in commands if "ls-remote" in row.get("argv", [])
                   and URLS[role] in row["argv"]]
        need(queries, "PUBLICATION_QUERY_MISSING:" + role)
        query, stdout = queries[-1], queries[-1].get("stdout", {})
        need(query.get("actual_rc") == 0 and stdout.get("redacted") is False
             and stdout.get("text", "").splitlines().count(commit + "\trefs/heads/main") == 1,
             "PUBLICATION_QUERY_BINDING:" + role)
        need(time_value(query["at"]) <= time_value(refs[ROLES.index(role)]["at"]),
             "PUBLICATION_QUERY_TIME:" + role)
    github = [row for row in commands if row.get("method") == "GET"
              and row.get("path") == API + "/ref/heads/main"]
    need(github and github[-1].get("http_status") == 200
         and time_value(github[-1]["at"]) <= time_value(refs[2]["at"]), "PUBLICATION_GITHUB_GET")
    return {"status": "RECORDED_THREE_REF_EVIDENCE_VERIFIED", "commit": commit, "final_readback": refs}


def review_transport_tail(value, root):
    commit, old, tail = value["commit"], value["github_before"], value["tail"]
    need(isinstance(old, str) and SHA.fullmatch(old) and isinstance(tail, list) and tail
         and all(isinstance(item, str) and SHA.fullmatch(item) for item in tail),
         "TRANSPORT_TAIL_FORMAT")
    actual = git(root, ["rev-list", "--reverse", old + ".." + commit]).decode().splitlines()
    need(tail == actual and tail[-1] == commit and 0 < len(tail) <= value["max_commits"] == 8,
         "TRANSPORT_EXACT_TAIL")
    for item in tail:
        need(git(root, ["show", "-s", "--format=%P", item]).decode().split() == [old],
             "TRANSPORT_NONLINEAR_TAIL")
        old = item
    return tail


def review_transport(value, commands, extra, root, finished):
    commit = value["commit"]
    tail = review_transport_tail(value, root)
    plan, history = extra["api-plan.json"], extra["history.json"]
    need(all(plan.get(key) == value.get(key) for key in
             ("commit", "github_before", "tail", "objects_scanned", "max_commits", "transport")),
         "TRANSPORT_PLAN_BINDING")
    objects = git(root, ["rev-list", "--objects", "--no-object-names", commit]).splitlines()
    need(history.get("actual_rc") == 0 and not history.get("first_cause") and history.get("stderr") == ""
         and history.get("objects") == history.get("expected") == value["objects_scanned"]
         == len(objects), "TRANSPORT_HISTORY_COUNT_OR_EXIT")
    need(value.get("api_tool_sha256") == sha(regular(root / "tools/github_api_sync.py")),
         "TRANSPORT_TOOL_BINDING")
    prior = time_value(finished)
    for command in commands:
        current = time_value(command["at"])
        need(current >= prior, "TRANSPORT_COMMAND_TIME_ORDER")
        prior = current
        if "argv" in command:
            need(command.get("actual_rc") == 0, "TRANSPORT_COMMAND_EXIT")
        else:
            method = command.get("method")
            need(method in {"GET", "POST", "PATCH"}
                 and command.get("http_status") == (201 if method == "POST" else 200),
                 "TRANSPORT_HTTP_EXIT")
    partial = extra["partial.json"]
    need([item.get("role") for item in partial] == list(ROLES[:2]), "TRANSPORT_PUSH_ROLES")
    pushes = [(index, row) for index, row in enumerate(commands) if "push" in row.get("argv", [])]
    need(len(pushes) == 2, "TRANSPORT_PUSH_COUNT")
    for (_, command), plan, role in zip(pushes, partial, ROLES[:2]):
        argv = command["argv"]
        need(argv[argv.index("push"):] == [
            "push", "--porcelain", "--no-follow-tags", URLS[role], commit + ":refs/heads/main"],
            "TRANSPORT_PUSH_TARGET_OR_FORCE")
        need(plan.get("url") == URLS[role] and plan.get("push_attempted") is True
             and plan.get("actual_push_rc") == command["actual_rc"] == 0
             and plan.get("first_cause") is None and plan.get("verified") is True
             and plan.get("readback") == commit
             and plan.get("before", {}).get("refs/heads/main") == value["github_before"],
             "TRANSPORT_PUSH_RECEIPT")
    writes = [(index, row) for index, row in enumerate(commands) if row.get("method") in {"POST", "PATCH"}]
    need(writes and pushes[-1][0] < writes[0][0], "TRANSPORT_PRIMARY_FIRST")
    for role, lower, upper in (
            ("gitlab", pushes[0][0], pushes[1][0]),
            ("gitlab", pushes[1][0], writes[0][0]),
            ("gitee", pushes[1][0], writes[0][0])):
        need(any("ls-remote" in row.get("argv", []) and URLS[role] in row["argv"]
                 and row.get("stdout", {}).get("redacted") is False
                 and commit + "\trefs/heads/main" in row["stdout"].get("text", "").splitlines()
                 for row in commands[lower + 1:upper]), "TRANSPORT_PRIMARY_READBACK:" + role)
    patches = [(index, row) for index, row in writes if row["method"] == "PATCH"]
    need(len(patches) == 1 and patches[0][1]["path"] == API + "/refs/heads/main"
         and writes[-1] == patches[0], "TRANSPORT_SINGLE_MAIN_UPDATE")
    posts = [row for _, row in writes if row["method"] == "POST" and row["path"] == API + "/commits"]
    need(len(posts) == len(tail), "TRANSPORT_COMMIT_POST_COUNT")
    api_partial = extra["api-partial.json"]
    need(api_partial.get("primary_and_gitee_synced") is True
         and api_partial.get("reference_update_attempted") is True
         and api_partial.get("commits_verified") == tail, "TRANSPORT_EXACT_COMMIT_RECEIPTS")
    need(any(row.get("method") == "GET" and row.get("path") == API + "/commits/" + commit
             for row in commands[patches[0][0] + 1:]), "TRANSPORT_FINAL_COMMIT_GET")
    return {"objects": len(objects), "history_original_child_rc": history["actual_rc"], "tail": tail,
            "git_push_original_rcs": [row["actual_rc"] for _, row in pushes],
            "command_records": len(commands), "api_write_records": len(writes),
            "commit_post_records": len(posts), "main_patch_records": len(patches), "primary_first": True,
            "payload_bytes_or_force_false": "NOT_RECORDED; source checked, not HTTP payload",
            "sync_outer_rc": None, "sync_outer_rc_status": "NOT_RECORDED_IN_INPUTS"}


def live_remote(root, commit, out):
    rows = []
    for role in ROLES:
        row = {"role": role, "started_at": now(), "readback": None}
        try:
            if role != "github":
                env = git_env()
                env["GIT_SSH_COMMAND"] = GITLAB_SSH if role == "gitlab" else SSH
                child = subprocess.run(["/usr/bin/git", "--no-replace-objects", "-C", str(root),
                                        "ls-remote", "--heads", URLS[role], "refs/heads/main"],
                                       env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                row.update(actual_rc=child.returncode, stdout=binding(child.stdout),
                           stderr=binding(child.stderr), transport="ESTABLISHED_SSH_READ_ONLY")
                need(child.returncode == 0, "REMOTE_%s_RC_%d" % (role, child.returncode))
                need(child.stdout.decode().splitlines() == [commit + "\trefs/heads/main"],
                     "REMOTE_REF_MISMATCH:" + role)
                receipt, raw = out / (role + "-main.txt"), child.stdout
            else:
                connection = http.client.HTTPSConnection("api.github.com", timeout=30,
                                                         context=ssl.create_default_context())
                try:
                    connection.request("GET", API + "/ref/heads/main", headers={
                        "Accept": "application/vnd.github+json", "Cache-Control": "no-cache",
                        "User-Agent": "opencad-read-only-publication-review"})
                    response = connection.getresponse()
                    raw = response.read(1048577)
                    row.update(http_status=response.status, body=binding(raw),
                               transport="PUBLIC_API_GET_NO_CREDENTIAL")
                    need(response.status == 200 and len(raw) <= 1048576, "GITHUB_PUBLIC_GET")
                    body = json.loads(raw)
                    need(body.get("ref") == "refs/heads/main" and body.get("object", {}).get("type") == "commit"
                         and body["object"].get("sha") == commit, "REMOTE_REF_MISMATCH:github")
                    receipt = out / "github-main.json"
                finally:
                    connection.close()
            with receipt.open("xb") as stream:
                stream.write(raw)
            row.update(receipt=str(receipt), readback=commit, status="PASS", finished_at=now())
        except Exception as exc:
            row.update(first_cause=str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                       status="FAIL", finished_at=now())
            if isinstance(exc, subprocess.TimeoutExpired):
                row.update(actual_rc=None, transport_status="TIMED_OUT_NOT_SUCCESS",
                           stdout=binding(exc.stdout or b""), stderr=binding(exc.stderr or b""))
            rows.append(row)
            save(out / "remote-readback.json", rows)
            raise Refused("LIVE_READBACK:" + row["first_cause"]) from None
        rows.append(row)
    save(out / "remote-readback.json", rows)
    return {"status": "LIVE_THREE_REF_VERIFIED", "rows": rows,
            "scope": "sequential main refs only; not global atomicity, deployment or default branch"}


def selftest(report, files, root, evidence, baseline, publication, out, read=None,
             publication_root=None):
    cases = []
    first_log = report["steps"][0]["label"] + ".log"
    http_index = next(index for index, row in enumerate(report["steps"])
                      if row["label"] == "http-selftest")

    def case(name, function, expected=None):
        found = None
        try:
            function()
        except Refused as exc:
            found = str(exc)
        cases.append({"name": name, "expected": expected, "first_cause": found,
                      "pass": found is None if expected is None else
                      found is not None and found.startswith(expected)})

    review = lambda r, f: review_all(r, f, root, evidence, baseline, read)
    case("real-all-positive", lambda: review(report, files))
    skip = {"test": "test_a (tests.Example)", "reason": "not available"}
    single = skip["test"] + " ... skipped " + repr(skip["reason"])
    multiline = skip["test"] + "\nDescription ... skipped " + repr(skip["reason"])
    for name, text, expected in (
            ("skip-single", single, None), ("skip-description", multiline, None),
            ("skip-wrong-name", multiline.replace("test_a", "test_b"), "SKIP_LOG_BINDING"),
            ("skip-wrong-reason", multiline.replace("not available", "other"), "SKIP_LOG_BINDING"),
            ("skip-cross-test", skip["test"] + "\ntest_b (tests.Example) ... skipped "
             + repr(skip["reason"]), "SKIP_LOG_BINDING"),
            ("skip-duplicate", single + "\n" + single, "SKIP_LOG_COUNT")):
        case(name, lambda t=text: check_skip_log(t, [skip], "fixture"), expected)
    for name, mutate, error in (
            ("empty-manifest", lambda r: r.update(source_before={}), "EMPTY_MANIFEST"),
            ("source-hash", lambda r: r.update(source_sha256="0" * 64), "ALL_SOURCE_BINDING"),
            ("source-drift", lambda r: r.update(source_drift=["changed"]), "ALL_SOURCE_BINDING"),
            ("missing-step", lambda r: r["steps"].pop(), "STAGE_PLAN_SET_MISMATCH"),
            ("step-order", lambda r: r["steps"].reverse(), "STAGE_PLAN_SET_MISMATCH"),
            ("step-rc", lambda r: r["steps"][0].update(rc=9), "CHILD_EXIT"),
            ("count-log", lambda r: r["steps"][0]["counts"].update(passed=0), "COUNT_LOG_BINDING"),
            ("totals", lambda r: r["totals"].update(passed=r["totals"]["passed"] + 1), "TOTALS_MISMATCH"),
            ("http-count", lambda r: r["steps"][http_index].update(
                http_checks=r["steps"][http_index]["http_checks"] - 1),
             "HTTP_SOURCE_MARKER_COUNT"),
            ("deployment", lambda r: r.update(production_deployment="PASS"), "ALL_SCOPE_INFLATION")):
        variant = deepcopy(report)
        mutate(variant)
        case(name, lambda r=variant: review(r, files), error)
    for name, mutate, error in (
            ("log-corruption", lambda raw: raw + b"\nchanged\n", "LOG_HASH"),
            ("terminal-rc", lambda raw: raw.replace(b"INDEPENDENT_CHILD_RC rc=0",
                                                  b"INDEPENDENT_CHILD_RC rc=1"), "CHILD_EXIT"),
            ("missing-count", lambda raw: b"\n".join(
                line for line in raw.splitlines() if not line.startswith(b"INDEPENDENT_TEST_COUNTS ")),
             "COUNT_LOG_BINDING")):
        variant, changed = deepcopy(report), dict(files)
        changed[first_log] = mutate(files[first_log])
        if name != "log-corruption":
            variant["steps"][0]["log_sha256"] = sha(changed[first_log])
        case(name, lambda r=variant, f=changed: review(r, f), error)
    changed = deepcopy(baseline)
    next(step for step in changed["steps"] if step.get("counts") and step["counts"]["skipped"]
         )["counts"]["skipped"].pop()
    case("original-skip-change", lambda: review_all(report, files, root, evidence, changed, read),
         "ORIGINAL_SKIPS_CHANGED")
    case("manifest-difference-visible", lambda: need(
        difference({"a": binding(b"1")}, {"a": binding(b"2"), "b": binding(b"3")}) == ["a", "b"],
        "DIFFERENCE_NOT_VISIBLE"))
    if publication:
        value, refs, commands, extra = publication
        review_sync = lambda v, r, c: review_publication(v, r, c, value["commit"], report["finished_at"])
        transport = lambda v, c, e: review_transport(v, c, e, publication_root or root,
                                                     report["finished_at"])
        case("real-publication-positive", lambda: review_sync(value, refs, commands))
        case("real-transport-positive", lambda: transport(value, commands, extra))
        for name, mutate, error in (
                ("planned-not-published", lambda v: v.update(state="PLANNED"), "PUBLICATION_NOT_SYNCED"),
                ("wrong-commit", lambda v: v.update(commit="b" * 40), "PUBLICATION_COMMIT"),
                ("missing-remote", lambda v: v["final_readback"].pop(), "PUBLICATION_THREE_ROLES"),
                ("wrong-remote", lambda v: v["final_readback"][0].update(readback="b" * 40),
                 "PUBLICATION_REF_OR_TIME")):
            variant = deepcopy(value)
            mutate(variant)
            case(name, lambda v=variant: review_sync(v, v["final_readback"], commands), error)
        for name, mutate, error in (
                ("transport-child-exit", lambda c: c[0].update(actual_rc=7), "TRANSPORT_COMMAND_EXIT"),
                ("transport-http-exit", lambda c: next(row for row in c if row.get("method") == "PATCH"
                                                       ).update(http_status=500), "TRANSPORT_HTTP_EXIT"),
                ("transport-force", lambda c: next(row for row in c if "push" in row.get("argv", [])
                                                   )["argv"].append("--force"), "TRANSPORT_PUSH_TARGET_OR_FORCE"),
                ("transport-missing-patch", lambda c: c.remove(next(row for row in c if
                     row.get("method") == "PATCH")), "TRANSPORT_SINGLE_MAIN_UPDATE")):
            variant = deepcopy(commands)
            mutate(variant)
            case(name, lambda c=variant: transport(value, c, extra), error)
        changed = deepcopy(extra)
        changed["history.json"]["objects"] -= 1
        case("transport-object-count", lambda: transport(value, commands, changed),
             "TRANSPORT_HISTORY_COUNT_OR_EXIT")
        changed_tail = deepcopy(value)
        if len(changed_tail["tail"]) > 1:
            changed_tail["tail"].reverse()
        else:
            item = changed_tail["tail"][0]
            changed_tail["tail"][0] = ("0" if item[0] != "0" else "1") + item[1:]
        need(changed_tail["tail"] != value["tail"], "SELFTEST_EMPTY_TAIL_MUTATION")
        case("transport-tail-order", lambda: transport(changed_tail, commands, extra),
             "TRANSPORT_EXACT_TAIL")
        tail_root = publication_root or root
        single = deepcopy(value)
        single["github_before"] = git(tail_root, [
            "rev-parse", value["commit"] + "^"]).decode().strip()
        single["tail"] = [value["commit"]]
        case("transport-single-tail-positive", lambda: review_transport_tail(single, tail_root))
        changed_tail = deepcopy(single)
        changed_tail["tail"][0] = (
            ("0" if single["tail"][0][0] != "0" else "1") + single["tail"][0][1:])
        need(changed_tail["tail"] != single["tail"], "SELFTEST_EMPTY_TAIL_MUTATION")
        case("transport-tail-wrong-sha", lambda: review_transport_tail(changed_tail, tail_root),
             "TRANSPORT_EXACT_TAIL")
        multiple = deepcopy(value)
        multiple["github_before"] = git(tail_root, [
            "rev-parse", value["commit"] + "^^"]).decode().strip()
        multiple["tail"] = git(tail_root, ["rev-list", "--reverse",
            multiple["github_before"] + ".." + value["commit"]]).decode().splitlines()
        need(len(multiple["tail"]) == 2, "SELFTEST_TWO_COMMIT_LINEAR_FIXTURE")
        case("transport-multiple-tail-positive", lambda: review_transport_tail(multiple, tail_root))
        reordered_tail = deepcopy(multiple)
        reordered_tail["tail"].reverse()
        need(reordered_tail["tail"] != multiple["tail"], "SELFTEST_EMPTY_TAIL_MUTATION")
        case("transport-multiple-tail-order", lambda: review_transport_tail(reordered_tail, tail_root),
             "TRANSPORT_EXACT_TAIL")
        reordered = deepcopy(commands)
        push = next(row for row in reordered if "push" in row.get("argv", [])
                    and URLS["gitee"] in row["argv"])
        reordered.remove(push)
        last_write = max(index for index, row in enumerate(reordered)
                         if row.get("method") in {"POST", "PATCH"})
        reordered.insert(last_write + 1, push)
        for row in reordered:
            row["at"] = value["at"]
        case("transport-primary-first", lambda: transport(value, reordered, extra),
             "TRANSPORT_PRIMARY_FIRST")
        changed_receipts = deepcopy(extra)
        changed_receipts["api-partial.json"]["commits_verified"].pop()
        case("transport-missing-exact-commit", lambda: transport(value, commands, changed_receipts),
             "TRANSPORT_EXACT_COMMIT_RECEIPTS")
    result = {"checks": cases, "count": len(cases), "passed": sum(case["pass"] for case in cases),
              "finished_at": now(), "scope": "real inputs positive; in-memory negative controls"}
    save(out / "selftest.json", result)
    need(result["count"] > 0 and result["passed"] == result["count"], "SELFTEST_FAILED")
    return {key: result[key] for key in ("count", "passed", "scope")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("source-root", "all-evidence", "baseline-evidence", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--all-source-root", type=Path,
                        help="frozen all executor root; defaults to source-root")
    parser.add_argument("--all-commit",
                        help="exact all source commit; defaults to commit")
    parser.add_argument("--allow-doc-path", action="append", default=[],
                        help="exact changed docs/*.md path for a linear doc-only successor")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--publication-evidence", type=Path)
    parser.add_argument("--publication-commit",
                        help="recorded sync target within the verified doc-only tail; defaults to commit")
    parser.add_argument("--remote-readback", action="store_true")
    args = parser.parse_args()
    need(os.geteuid() == pwd.getpwnam("infra").pw_uid, "INFRA_ONLY")
    need(bool(SHA.fullmatch(args.commit)), "FULL_COMMIT_REQUIRED")
    frozen_commit = args.all_commit or args.commit
    need(bool(SHA.fullmatch(frozen_commit)), "FULL_ALL_COMMIT_REQUIRED")
    need(len(args.allow_doc_path) == len(set(args.allow_doc_path)), "DUPLICATE_DOC_DECLARATION")
    publication_commit = args.publication_commit or args.commit
    need(bool(SHA.fullmatch(publication_commit)), "FULL_PUBLICATION_COMMIT_REQUIRED")
    need(args.publication_commit is None or args.publication_evidence is not None,
         "PUBLICATION_COMMIT_WITHOUT_EVIDENCE")
    out, source = external(args.out), args.source_root.resolve()
    all_source = (args.all_source_root or args.source_root).resolve()
    need(not out.resolve().is_relative_to(source), "OUTPUT_IN_SOURCE")
    need(not out.resolve().is_relative_to(all_source), "OUTPUT_IN_ALL_SOURCE")
    for value in (args.all_evidence, args.baseline_evidence, args.publication_evidence):
        if value is not None:
            input_root = external(value).resolve()
            need(not out.resolve().is_relative_to(input_root)
                 and not input_root.is_relative_to(out.resolve()), "OUTPUT_OVERLAPS_INPUT")
    out.mkdir(parents=True, exist_ok=False)
    result = {"verdict": "FAIL", "started_at": now(), "source_root": str(source), "commit": args.commit,
              "checks": [], "tool_sha256": sha(regular(Path(__file__))), "product_tests_reexecuted": 0,
              "production_ready": False, "industrial_parity": "NOT_ASSESSED",
              "publication_status": "NOT_REQUESTED", "all_source_root": str(all_source),
              "all_commit": frozen_commit, "allowed_doc_paths": args.allow_doc_path,
              "publication_commit": publication_commit,
              "argv": list(os.sys.argv), "source_scope": "ALL_AND_PUBLICATION_SEPARATE"}
    inputs, before, all_before = {}, None, None

    def read(path):
        path = external(path)
        raw = regular(path)
        inputs[str(path)] = binding(raw)
        return raw

    try:
        evidence = external(args.all_evidence)
        report = json.loads(read(evidence / "result.json"))
        need(report.get("verdict") == "PASS" and report.get("stage") == "all", "ALL_NOT_PASS")
        plan, _, _ = all_stage_plan(all_source, report["source_before"], evidence,
                                    report["test_runtime"]["root"])
        files = {name: read(evidence / name) for name in (
            "acceptance.log", *(row["label"] + ".log" for row in plan))}
        baseline = json.loads(read(external(args.baseline_evidence) / "result.json"))
        before, all_before = manifest(source, modes=True), manifest(all_source, modes=True)
        result.update(source_before=before, all_source_before=all_before)
        result["all_review"] = review_all(report, files, all_source, evidence, baseline, read)
        result["checks"].append("ALL_SOURCE_STAGE_PLAN_LOGS_COUNTS_EXITS_AND_ORIGINAL_SKIPS")
        all_bytes = {name: {key: row[key] for key in ("bytes", "sha256")}
                     for name, row in all_before.items()}
        need(not difference(report["source_before"], all_bytes), "CURRENT_ALL_SOURCE_NOT_ACCEPTED")
        result["checks"].append("CURRENT_BYTES_EQUAL_ALL_MANIFEST")
        need(git(all_source, ["rev-parse", "HEAD"]).decode().strip() == frozen_commit,
             "ALL_HEAD_NOT_PINNED")
        need(not git(all_source, ["status", "--porcelain=v1", "--untracked-files=all"]), "ALL_SOURCE_DIRTY")
        frozen = committed_manifest(all_source, frozen_commit, modes=True)
        need(not difference(frozen, all_before), "ALL_COMMIT_BYTE_MODE_SET_MISMATCH")
        result.update(all_commit_manifest_sha256=digest(frozen), all_commit_files=len(frozen),
                      all_source_byte_sha256=digest(all_bytes))
        result["checks"].append("EXACT_FROZEN_COMMIT_BYTE_MODE_SET")
        need(git(source, ["rev-parse", "HEAD"]).decode().strip() == args.commit, "HEAD_NOT_PINNED")
        need(not git(source, ["status", "--porcelain=v1", "--untracked-files=all"]), "SOURCE_DIRTY")
        committed = committed_manifest(source, args.commit, modes=True)
        result.update(commit_manifest_sha256=digest(committed), commit_files=len(committed),
                      commit_manifest_difference=difference(committed, before))
        need(not result["commit_manifest_difference"], "COMMIT_NOT_ACCEPTED_SOURCE")
        result["source_relation"] = doc_successor(source, frozen_commit, args.commit, frozen,
                                                  committed, args.allow_doc_path)
        result["checks"].append("EXACT_PUBLICATION_COMMIT_AND_DOC_ONLY_RELATION")
        result["reviewer"] = review_owned(ROOT)
        result["checks"].append("REVIEWER_OWNED_FILE_SCOPE_AND_SECRET_SHAPE")
        publication = None
        if args.publication_evidence:
            target_binding = check_publication_target(result["source_relation"], frozen_commit,
                                                       args.commit, publication_commit)
            published = external(args.publication_evidence)
            value = json.loads(read(published / "result.json"))
            need("api_tool_sha256" in value, "EXACT_API_PUBLICATION_REQUIRED")
            refs = json.loads(read(published / "api-final-readback.json"))
            commands = json.loads(read(published / "commands.json"))
            extra = {name: json.loads(read(published / name)) for name in (
                "api-plan.json", "history.json", "partial.json", "api-partial.json")}
            result["publication"] = review_publication(
                value, refs, commands, publication_commit, report["finished_at"])
            result["publication"]["transport_review"] = review_transport(
                value, commands, extra, source, report["finished_at"])
            result["publication_status"] = result["publication"]["status"]
            result["publication"].update(target_binding)
            result["checks"].append("RECORDED_PUBLICATION_EXITS_EXACT_TAIL_PRIMARY_FIRST_THREE_REFS")
            publication = value, refs, commands, extra
        if args.selftest:
            result["selftest"] = selftest(report, files, all_source, evidence, baseline, publication,
                                          out, read, source)
            result["checks"].append("REAL_EVIDENCE_AND_NEGATIVE_CONTROLS")
        if args.remote_readback:
            result["live_readback"] = live_remote(source, args.commit, out)
            result["publication_status"] = result["live_readback"]["status"]
            result["checks"].append("LIVE_THREE_MAIN_REFS_NO_CREDENTIAL_READ")
        result["verdict"] = "PASS"
    except Exception as exc:
        result["first_cause"] = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        trace = exc.__traceback__
        coordinates = []
        while trace:
            coordinates.append({"file": trace.tb_frame.f_code.co_filename, "line": trace.tb_lineno})
            trace = trace.tb_next
        result["error_coordinates"] = coordinates
    finally:
        result.update(inputs=inputs, input_drift=[])
        for name, original in inputs.items():
            try:
                if binding(regular(Path(name))) != original:
                    result["input_drift"].append(name)
            except Exception:
                result["input_drift"].append(name)
        if before is not None:
            try:
                after = manifest(source, modes=True)
                result.update(source_after=after, source_drift=difference(before, after),
                              source_sha256=digest(before), source_after_sha256=digest(after))
                result["head_drift"] = git(source, ["rev-parse", "HEAD"]).decode().strip() != args.commit
            except Exception as exc:
                result["source_recheck_error"] = type(exc).__name__
        if all_before is not None:
            try:
                all_after = manifest(all_source, modes=True)
                result.update(all_source_after=all_after,
                              all_source_drift=difference(all_before, all_after),
                              all_head_drift=git(all_source, ["rev-parse", "HEAD"]).decode().strip()
                              != frozen_commit)
            except Exception as exc:
                result["all_source_recheck_error"] = type(exc).__name__
        result["tool_drift"] = sha(regular(Path(__file__))) != result["tool_sha256"]
        result["reviewer_drift"] = []
        if "reviewer" in result:
            for name, original in result["reviewer"]["files"].items():
                try:
                    if binding(regular(ROOT / name)) != original:
                        result["reviewer_drift"].append(name)
                except Exception:
                    result["reviewer_drift"].append(name)
            try:
                need(review_owned(ROOT) == result["reviewer"], "REVIEWER_STATE_DRIFT")
            except Exception:
                result["reviewer_drift"].append("REVIEWER_STATE")
        if (result["input_drift"] or result.get("source_drift") or result.get("head_drift")
                or result.get("all_source_drift") or result.get("all_head_drift")
                or result.get("all_source_recheck_error")
                or result.get("source_recheck_error") or result["tool_drift"] or result["reviewer_drift"]):
            result["verdict"] = "FAIL"
            result.setdefault("first_cause", "INPUT_SOURCE_HEAD_OR_TOOL_DRIFT")
        result.update(finished_at=now(), actual_rc=0 if result["verdict"] == "PASS" else 1)
        save(out / "result.json", result)
        line = ("GATEWAY_PUBLICATION verdict=%s rc=%d checks=%d publication=%s at=%s result=%s"
                % (result["verdict"], result["actual_rc"], len(result["checks"]),
                   result["publication_status"], result["finished_at"], out / "result.json"))
        with (out / "review.log").open("x") as stream:
            stream.write(line + "\n")
            if result.get("first_cause"):
                stream.write("first_cause=" + result["first_cause"] + "\n")
        print(line)
        if result.get("first_cause"):
            print("first_cause=" + result["first_cause"])
    return result["actual_rc"]


if __name__ == "__main__":
    raise SystemExit(main())
