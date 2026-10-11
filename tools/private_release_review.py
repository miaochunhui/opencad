#!/usr/bin/env python3
"""Read-only private Web release review; never import or start the application.

Emit immutable evidence on 3FS. PASS means static review completed, NOT that a
production deployment is approved. Python dispatch is conservatively packaged
with all cadcore runtime source; unresolved dynamic calls remain explicit debt.
Selftests create only owned source fixtures in a new evidence directory.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP = "oss-alternatives/apps/opencad"
CORE = "oss-alternatives/packages/cadcore/cadcore"
OWNED = ("tools/private_release_review.py", "docs/design/private-release-review.md")
SEEDS = ("secure_web.py", "project_gateway.py", "access_control.py",
         "project_store.py", "server.py", "solid_commands.py", "viewer3d_page.py")
LOCAL_APP_MODULES = {Path(name).stem for name in SEEDS} | {"request_context"}
ACCEPTANCE = (
    "tools/private_web_acceptance.py", "tools/private_web_browser.py",
    APP + "/tests/test_secure_web.py", APP + "/tests/test_project_gateway.py",
    "oss-alternatives/tools/cad3d/viewer_controls_check.py",
    APP + "/tests/legacy_browser_compat_gate.py",
)
DENIED = {".git", "__pycache__", "build", "dist", "node_modules", "artifacts"}
PANELS = ("parametric", "surfaces", "assembly", "drawing", "render")


class ReviewError(ValueError):
    def __init__(self, code, coordinate):
        self.code, self.coordinate = code, str(coordinate)
        super().__init__(code + ": " + self.coordinate)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def read(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ReviewError("unsafe_source_path", name)
    path = root / name
    for part in (path, *path.parents):
        if part == root.parent:
            break
        if part.is_symlink():
            raise ReviewError("linked_source", name)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ReviewError("missing_source", name) from exc
    if not stat.S_ISREG(mode):
        raise ReviewError("non_regular_source", name)
    data = path.read_bytes()
    if not data:
        raise ReviewError("empty_source", name)
    return data


def write(path, data):
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def git(root, *args):
    process = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             check=False, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if process.returncode:
        # Do not echo arbitrary stderr/contents from an audited tree.
        raise ReviewError("git_query_failed", args[0])
    return process.stdout


def runtime_sources(root):
    """Explicit conservative package boundary, not a claim of reachable code."""
    names = set()
    base = root / CORE
    if not base.is_dir() or base.is_symlink():
        raise ReviewError("missing_cadcore_runtime", CORE)
    for directory, dirs, files in os.walk(base, followlinks=False):
        parent = Path(directory)
        for item in list(dirs):
            if (parent / item).is_symlink():
                raise ReviewError("linked_source_directory", parent / item)
            if item in DENIED or item.startswith("."):
                dirs.remove(item)
        for item in files:
            path = parent / item
            if path.suffix == ".py" or (
                    path.is_relative_to(base / "data") and path.suffix == ".json"):
                names.add(path.relative_to(root).as_posix())
    if not names:
        raise ReviewError("empty_runtime_bundle", CORE)
    return names


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []
        self.scripts, self.links, self.hosts = [], [], []
        self.inline, self.handlers, self.styles, self.details = 0, 0, 0, 0
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.handlers += sum(name.startswith("on") for name in values)
        self.styles += int("style" in values or tag == "style")
        self.details += int(tag == "details")
        self.hosts.extend(name for name in values if name.startswith("data-") and name.endswith("-host"))
        if tag == "script":
            self.in_script = True
            if values.get("src"):
                self.scripts.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.links.append(values.get("href"))
        if tag in {"script", "img", "source", "iframe", "audio", "video"}:
            if values.get("src"):
                self.references.append(values["src"])
        if tag == "link" and values.get("href"):
            self.references.append(values["href"])

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and data.strip():
            self.inline += 1


def static_value(node, values):
    """Interpret only inert string-template AST, never Python code/callables."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [static_value(item, values) for item in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return static_value(node.left, values) + static_value(node.right, values)
    if isinstance(node, ast.JoinedStr):
        return "".join(str(static_value(item.value, values)) if isinstance(item, ast.FormattedValue)
                       and item.conversion == -1 and item.format_spec is None
                       else static_value(item, values) for item in node.values)
    if isinstance(node, ast.GeneratorExp) and len(node.generators) == 1:
        loop = node.generators[0]
        if not isinstance(loop.target, ast.Name) or loop.ifs or loop.is_async:
            raise ReviewError("unsupported_page_template", "generator")
        return [static_value(node.elt, dict(values, **{loop.target.id: item}))
                for item in static_value(loop.iter, values)]
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"join", "replace"}:
            owner = static_value(node.func.value, values)
            if not isinstance(owner, str) or node.keywords:
                raise ReviewError("unsupported_page_template", "string_call")
            arguments = [static_value(item, values) for item in node.args]
            if node.func.attr == "join" and len(arguments) == 1:
                return owner.join(arguments[0])
            if node.func.attr == "replace" and len(arguments) in (2, 3):
                return owner.replace(*arguments)
        if isinstance(node.func, ast.Name) and node.func.id == "render" and not node.args:
            if all(item.arg == "advanced" and static_value(item.value, values) is True
                   for item in node.keywords):
                return values["viewer_page"]
    raise ReviewError("unsupported_page_template", type(node).__name__)


def static_private_page(secure, viewer):
    values = {}
    for node in secure.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"CSP", "PRIVATE_ASSETS", "INDUSTRIAL_ASSETS"}:
                    values[target.id] = static_value(node.value, values)
    functions = [node for node in viewer.body if isinstance(node, ast.FunctionDef) and node.name == "render"]
    if len(functions) != 1:
        raise ReviewError("viewer_template_missing", "render")
    pages = [node for node in functions[0].body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == "page" for target in node.targets)]
    if len(pages) != 1:
        raise ReviewError("viewer_template_ambiguous", "page")
    values["viewer_page"] = static_value(pages[0].value, {"stamp": "STATIC_REVIEW_TIMESTAMP_NOT_EXECUTED"})
    functions = [node for node in secure.body if isinstance(node, ast.FunctionDef)
                 and node.name == "private_3d_page"]
    if len(functions) != 1:
        raise ReviewError("private_3d_template_missing", "private_3d_page")

    def statements(items):
        for node in items:
            if isinstance(node, ast.ImportFrom):
                continue
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                values[node.targets[0].id] = static_value(node.value, values)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
                values[node.target.id] += static_value(node.value, values)
            elif isinstance(node, ast.For) and not node.orelse:
                for item in static_value(node.iter, values):
                    if isinstance(node.target, ast.Tuple):
                        if len(item) != len(node.target.elts) or any(
                                not isinstance(target, ast.Name) for target in node.target.elts):
                            raise ReviewError("unsupported_page_template", "loop_target")
                        values.update({target.id: value for target, value in zip(node.target.elts, item)})
                    elif isinstance(node.target, ast.Name):
                        values[node.target.id] = item
                    else:
                        raise ReviewError("unsupported_page_template", "loop_target")
                    statements(node.body)
            elif isinstance(node, ast.Return):
                return static_value(node.value, values)
            else:
                raise ReviewError("unsupported_page_template", type(node).__name__)
        return None

    page = statements(functions[0].body)
    if not isinstance(page, str) or not page.startswith("<!doctype"):
        raise ReviewError("private_3d_template_empty", "private_3d_page")
    return page, values["CSP"]


def check_page(page, policy):
    parser = Assets()
    parser.feed(page)
    directives = {row.split()[0]: row.split()[1:] for row in policy.split(";") if row.strip()}
    for directive in ("script-src", "style-src", "connect-src", "font-src"):
        if directives.get(directive) != ["'self'"]:
            raise ReviewError("csp_not_self_only", directive)
    if directives.get("img-src") != ["'self'", "data:"]:
        raise ReviewError("csp_image_policy_changed", "img-src")
    if parser.inline or parser.handlers or parser.styles:
        raise ReviewError("csp_inline_resource_blocked", "private_3d_page")
    if parser.hosts != ["data-" + name + "-host" for name in PANELS] or parser.details < len(PANELS):
        raise ReviewError("industrial_hosts_mismatch", "private_3d_page")
    for name in PANELS:
        if parser.scripts.count("/web/js/" + name + "-panel.js") != 1 or (
                parser.links.count("/web/css/" + name + "-panel.css") != 1):
            raise ReviewError("industrial_assets_mismatch", name)
    coordinator = "/web/js/industrial-panels.js"
    if parser.scripts.count(coordinator) != 1:
        raise ReviewError("industrial_coordinator_missing", coordinator)
    if parser.scripts[0] != "/web/js/project-session.js" or any(
            parser.scripts.index("/web/js/" + name + "-panel.js") > parser.scripts.index(coordinator)
            for name in PANELS):
        raise ReviewError("private_script_order", "private_3d_page")
    for reference in parser.references:
        if reference.startswith("data:"):
            continue
        parts = urlsplit(reference)
        if parts.scheme or parts.netloc or not parts.path.startswith("/web/"):
            raise ReviewError("csp_external_page_asset", "private_3d_page")
    return parser


def asset_references(text, suffix):
    refs = []
    if suffix == ".html":
        parser = Assets()
        parser.feed(text)
        refs.extend(parser.references)
    if suffix == ".css":
        refs.extend(re.findall(r"url\(\s*['\"]?([^)'\"\s]+)", text))
        refs.extend(re.findall(r"@import\s+['\"]([^'\"]+)['\"]", text))
    if suffix == ".js":
        refs.extend(re.findall(r"(?m)^[ \t]*(?:import|export)\s+[^;\n]*?\bfrom\s*['\"]([^'\"]+)['\"]", text))
        refs.extend(re.findall(r"(?m)^[ \t]*import[ \t]+['\"]([^'\"]+)['\"]", text))
        refs.extend(re.findall(r"(?<![\w.'\"`=-])import[ \t]*\([ \t]*['\"]([^'\"]+)['\"]", text))
        refs.extend(re.findall(r"\bnew\s+(?:Worker|SharedWorker)\s*\(\s*['\"]([^'\"]+)['\"]", text))
    return refs


class Closure:
    def __init__(self, root):
        self.root = root
        self.modules = {
            "cadcore": "oss-alternatives/packages/cadcore",
            "dxf": "oss-alternatives/packages/dxf",
            "freechat_vault": "oss-alternatives/packages/vault",
        }
        self.local_app_modules = set(LOCAL_APP_MODULES)
        if (root / APP).is_dir():
            self.local_app_modules.update(path.stem for path in (root / APP).glob("*.py"))
        self.names, self.edges, self.external, self.dynamic = set(), set(), set(), set()
        self.hashes, self.trees, self.payload_debt = {}, {}, []

    def module_paths(self, module):
        parts = module.split(".")
        base = self.modules.get(parts[0], APP)
        if parts[0] == "surfaces":
            base = CORE + "/kernel"
        relative = base + "/" + "/".join(parts)
        for name in (relative + ".py", relative + "/__init__.py"):
            if (self.root / name).is_file() or (self.root / name).is_symlink():
                return [name]
        if (self.root / relative).is_dir():
            # Namespace packages are legitimate; bind any real ancestor init.
            return []
        if parts[0] in self.modules or parts[0] in self.local_app_modules or parts[0] == "surfaces":
            raise ReviewError("missing_local_module", module)
        if parts[0] not in sys.stdlib_module_names:
            self.external.add(module)
        return []

    def import_edge(self, source, module):
        if not module:
            return
        try:
            targets = self.module_paths(module)
        except ReviewError as exc:
            if "/ops/features/_quarantine/" not in source:
                raise
            self.payload_debt.append({"source": source, "module": module,
                                      "code": exc.code, "scope": "QUARANTINE_PAYLOAD"})
            return
        for target in targets:
            self.edges.add((source, target, "python_import"))
            self.names.add(target)
        parts = module.split(".")
        for length in range(1, len(parts)):
            for target in self.module_paths(".".join(parts[:length])):
                self.edges.add((source, target, "package_init"))
                self.names.add(target)

    def python(self, name, text):
        try:
            tree = ast.parse(text, filename=name)
        except SyntaxError as exc:
            raise ReviewError("python_syntax", f"{name}:{exc.lineno}") from exc
        self.trees[name] = tree
        location = PurePosixPath(name)
        if name.startswith(APP + "/"):
            package = ""
        else:
            package = ".".join(location.parts[3:-1])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.import_edge(name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    components = package.split(".") if package else []
                    if node.level > len(components):
                        raise ReviewError("invalid_relative_import", f"{name}:{node.lineno}")
                    module = ".".join(components[:len(components) - node.level + 1]
                                      + ([module] if module else []))
                self.import_edge(name, module)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    child = module + "." + alias.name if module else alias.name
                    base = self.modules.get(child.split(".")[0], APP)
                    path = base + "/" + child.replace(".", "/")
                    if (self.root / (path + ".py")).exists() or (
                            self.root / (path + "/__init__.py")).exists():
                        self.import_edge(name, child)
            elif isinstance(node, ast.Call):
                function = node.func
                label = (function.attr if isinstance(function, ast.Attribute)
                         else function.id if isinstance(function, ast.Name) else "")
                if label in {"import_module", "__import__"}:
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        self.import_edge(name, node.args[0].value)
                    else:
                        self.dynamic.add((name, node.lineno, label))
                elif label in {"exec", "eval", "spec_from_file_location"}:
                    self.dynamic.add((name, node.lineno, label))
                elif label == "_cadcore_module" and node.args and isinstance(node.args[0], ast.Constant):
                    self.import_edge(name, "cadcore." + node.args[0].value)
        if name == CORE + "/kernel/features_registry.py":
            assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "MODULES"
                                   for target in node.targets)]
            if len(assignments) != 1:
                raise ReviewError("feature_dispatch_not_static", name)
            for module in ast.literal_eval(assignments[0].value):
                self.import_edge(name, "cadcore.kernel.features_" + module)

    def resource(self, source, reference):
        if reference.startswith(("data:", "#")):
            return
        parts = urlsplit(reference)
        if parts.scheme or parts.netloc:
            raise ReviewError("external_static_resource", source)
        if not parts.path:
            return
        if source == APP + "/web/index.html" or source.endswith(".py"):
            base_url = "/"
        else:
            base_url = "/" + source.removeprefix(APP + "/")
        path = unquote(urlsplit(urljoin(base_url, reference)).path)
        if not path.startswith("/web/") or ".." in PurePosixPath(path).parts:
            raise ReviewError("static_resource_outside_web", source)
        target = APP + path
        read(self.root, target)
        self.edges.add((source, target, "static_resource"))
        self.names.add(target)

    def build(self, seeds):
        if not seeds:
            raise ReviewError("empty_seed_set", "seeds")
        self.names.update(seeds)
        while todo := self.names - self.hashes.keys():
            for name in sorted(todo):
                data = read(self.root, name)
                self.hashes[name] = digest(data)
                suffix = Path(name).suffix
                if suffix not in {".py", ".html", ".css", ".js", ".json", ".md"}:
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeError as exc:
                    raise ReviewError("invalid_source_utf8", name) from exc
                if suffix == ".py":
                    self.python(name, text)
                elif suffix == ".json":
                    self.json_resources(name, text)
                for reference in asset_references(text, suffix):
                    self.resource(name, reference)
        return self

    def json_resources(self, name, text):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ReviewError("duplicate_json_key", name)
                result[key] = value
            return result

        def invalid_constant(_):
            raise ReviewError("nonfinite_source_json", name)

        try:
            data = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
        except (ValueError, RecursionError) as exc:
            raise ReviewError("invalid_source_json", name) from exc
        if name == CORE + "/data/i18n/languages.json":
            entries = data if isinstance(data, list) else data.get("languages")
            if not isinstance(entries, list) or not entries:
                raise ReviewError("empty_catalog_manifest", name)
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                    raise ReviewError("invalid_catalog_manifest", name)
                filename = entry["file"]
                if PurePosixPath(filename).name != filename or not filename.endswith(".json"):
                    raise ReviewError("unsafe_catalog_filename", name)
                target = CORE + "/data/i18n/" + filename
                read(self.root, target)
                self.names.add(target)
                self.edges.add((name, target, "catalog_manifest"))
                standard = entry.get("default_standard")
                if not isinstance(standard, str) or not re.fullmatch(r"[a-z0-9_-]+", standard):
                    raise ReviewError("invalid_default_standard", name)
                target = CORE + "/data/standards/" + standard + ".json"
                read(self.root, target)
                self.names.add(target)
                self.edges.add((name, target, "default_standard"))

    def verify(self):
        after = {name: digest(read(self.root, name)) for name in sorted(self.hashes)}
        changed = [name for name in after if after[name] != self.hashes[name]]
        if changed:
            raise ReviewError("source_drift", changed[0])
        return after


def local_root(source, value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ReviewError("runtime_root_not_absolute", "data_root")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ReviewError("linked_runtime_root", "data_root")
        if (part / ".git").exists():
            raise ReviewError("runtime_inside_git", "data_root")
    if path.is_relative_to(source) or any(path.is_relative_to(base) for base in
            (Path("/3fs"), Path("/hf3fs-jd"), Path("/tmp"), Path("/proc"),
             Path("/sys"), Path("/dev"))):
        raise ReviewError("runtime_not_durable_external", "data_root")
    if path.exists() and not path.is_dir():
        raise ReviewError("runtime_not_directory", "data_root")
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        split = fields.index("-")
        mount = Path(fields[4].replace("\\040", " ").replace("\\134", "\\"))
        if path.is_relative_to(mount):
            mounts.append((len(mount.parts), fields[split + 1]))
    if not mounts:
        raise ReviewError("runtime_mount_not_captured", "data_root")
    filesystem = max(mounts)[1]
    if filesystem.startswith(("fuse", "nfs", "cifs", "smb", "9p")) or filesystem == "tmpfs":
        raise ReviewError("runtime_unsupported_filesystem", filesystem)
    return {"data_root": str(path), "filesystem": filesystem,
            "exists": path.exists(), "storage_operations": "NOT_RUN"}


def config(source, host, port, origin, data_root):
    if host != "127.0.0.1" or type(port) is not int or not 1024 <= port <= 65535:
        raise ReviewError("nonproduction_listener_required", "host/port")
    if origin != f"http://127.0.0.1:{port}":
        raise ReviewError("nonproduction_exact_origin_required", "public_origin")
    result = local_root(source, data_root)
    result.update(host=host, port=port, public_origin=origin, profile="LOOPBACK_ONLY",
                  secure_cookie=False, started=False,
                  argv=[sys.executable, "-B", str(source / APP / "secure_web.py"),
                        "--host", host, "--port", str(port), "--data-root", str(data_root),
                        "--public-origin", origin],
                  environment={"PYTHONDONTWRITEBYTECODE": "1",
                               "TMPDIR": str(Path(data_root) / "runtime")},
                  environment_to_clear=["PYTHONPATH", "OPENCAD_CADCORE_ROOT",
                                        "OPENCAD_I18N_DIRS", "CADCORE_PDF_BACKEND"],
                  precondition="Operator prepares private local root/runtime; not executed here")
    return result


def cli_contract(tree):
    flags = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant)):
            flags[node.args[0].value] = {
                item.arg: item.value.value for item in node.keywords
                if isinstance(item.value, ast.Constant)
            }
    required = {"--host", "--port", "--data-root", "--public-origin"}
    if not required.issubset(flags):
        raise ReviewError("private_cli_flags_missing", APP + "/secure_web.py")
    if not all(flags[key].get("required") is True for key in ("--data-root", "--public-origin")):
        raise ReviewError("private_cli_explicit_roots_required", APP + "/secure_web.py")
    if flags["--host"].get("default") != "127.0.0.1":
        raise ReviewError("private_cli_not_loopback_default", APP + "/secure_web.py")
    return flags


def review(source, options):
    tracked = set(git(source, "ls-files", "-z").decode().split("\0")) - {""}
    untracked = set(git(source, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
    head = git(source, "rev-parse", "HEAD").decode().strip()
    seeds = runtime_sources(source) | {APP + "/" + name for name in SEEDS}
    seeds |= {APP + "/web/index.html", "LICENSE", "NOTICE", "oss-alternatives/license-manifest.json"}
    seeds |= {CORE + "/data/i18n/languages.json", CORE + "/data/i18n/en.json",
              CORE + "/data/standards/gbt.json"}
    tracked_app_modules = {Path(name).stem for name in tracked
                           if Path(name).parent == Path(APP) and name.endswith(".py")}
    closure = Closure(source)
    closure.local_app_modules.update(tracked_app_modules)
    closure.build(seeds)
    flags = cli_contract(closure.trees[APP + "/secure_web.py"])
    cfg = config(source, options.host, options.port, options.public_origin, options.data_root)
    page, policy = static_private_page(closure.trees[APP + "/secure_web.py"],
                                       closure.trees[APP + "/viewer3d_page.py"])
    page_parser = check_page(page, policy)
    for reference in page_parser.references:
        closure.resource(APP + "/secure_web.py", reference)
    closure.build(closure.names)
    mounts = {
        "parametric": ("opencadParametric", None),
        "surfaces": ("OpenCADSurfaces", "data-surfaces-host"),
        "assembly": ("opencadAssembly", "data-assembly-host"),
        "drawing": ("opencadDrawingPanel", "data-drawing-host"),
        "render": ("OpenCADRenderPanel", "data-render-host"),
    }
    coordinator = read(source, APP + "/web/js/industrial-panels.js").decode()
    if "OpenCADProjectSession.ready" not in coordinator:
        raise ReviewError("industrial_session_ready_missing", "industrial-panels.js")
    for panel, (global_name, host) in mounts.items():
        script = read(source, APP + "/web/js/" + panel + "-panel.js").decode()
        if not re.search(r"window\." + global_name + r"\s*=", script):
            raise ReviewError("industrial_mount_export_missing", panel)
        if host and (host not in coordinator or "window." + global_name + ".mount(" not in coordinator):
            raise ReviewError("industrial_mount_binding_missing", panel)
        if not host and "[data-parametric-host]" not in script:
            raise ReviewError("parametric_autoboot_host_missing", panel)
    render_script = read(source, APP + "/web/js/render-panel.js").decode()
    if "readAsDataURL" not in render_script or re.search(r"\bimage\.src\s*=\s*URL\.createObjectURL", render_script):
        raise ReviewError("render_image_csp_incompatible", "render-panel.js")
    route_calls = [node for node in ast.walk(closure.trees[APP + "/secure_web.py"])
                   if isinstance(node, ast.FunctionDef) and node.name == "_static"]
    if len(route_calls) != 1 or not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "private_3d_page" for node in ast.walk(route_calls[0])):
        raise ReviewError("private_3d_route_unbound", "_static")
    runtime_names = set(closure.hashes)
    qa = Closure(source)
    qa.local_app_modules.update(tracked_app_modules)
    qa.build(ACCEPTANCE)
    inventory = {}
    for name in sorted(runtime_names | set(qa.hashes)):
        # Bind working bytes to Git blobs, without reading credentials or DBs.
        expected = closure.hashes.get(name, qa.hashes.get(name))
        status = "UNTRACKED" if name in untracked else "NOT_IN_GIT"
        if name in tracked:
            status = ("TRACKED_MATCH_HEAD" if digest(git(source, "show", "HEAD:" + name)) == expected
                      else "TRACKED_DIFFERS_FROM_HEAD")
        scope = "runtime" if name in runtime_names else "acceptance_only"
        if "/ops/features/_quarantine/" in name:
            scope = "quarantine_payload"
        inventory[name] = {"sha256": expected, "git_state": status, "scope": scope}
    archive_gaps = sorted(name for name in runtime_names
                          if inventory[name]["git_state"] != "TRACKED_MATCH_HEAD")
    source_before = {name: value["sha256"] for name, value in inventory.items()}
    closure.verify()
    qa.verify()
    if seeds != runtime_sources(source) | {APP + "/" + name for name in SEEDS} | {
            APP + "/web/index.html", "LICENSE", "NOTICE", "oss-alternatives/license-manifest.json",
            CORE + "/data/i18n/languages.json", CORE + "/data/i18n/en.json",
            CORE + "/data/standards/gbt.json"}:
        raise ReviewError("runtime_inventory_drift", CORE)
    if git(source, "rev-parse", "HEAD").decode().strip() != head:
        raise ReviewError("source_head_drift", "HEAD")
    tracked_after = set(git(source, "ls-files", "-z").decode().split("\0")) - {""}
    untracked_after = set(git(source, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")) - {""}
    if (tracked, untracked) != (tracked_after, untracked_after):
        raise ReviewError("git_inventory_drift", "tracked/untracked")
    return {
        "verdict": "PASS", "scope": "STATIC_REVIEW_ONLY", "production_ready": False,
        "production_verdict": "BLOCKED", "source_root": str(source), "source_head": head,
        "runtime_files": len(runtime_names), "acceptance_files": len(qa.hashes),
        "source_sha256": source_before, "source_fingerprint": digest(canonical(source_before)),
        "source_drift": [], "inventory": inventory,
        "git_tracked": sorted(tracked), "git_untracked": sorted(untracked),
        "edges": [list(edge) for edge in sorted(closure.edges | qa.edges)],
        "git_archive_gaps": archive_gaps,
        "git_archive_verdict": "FAIL" if archive_gaps else "PASS",
        "external_python": {"runtime_bundle": sorted(closure.external),
                            "acceptance": sorted(qa.external)},
        "dynamic_python": [list(item) for item in sorted(closure.dynamic | qa.dynamic)],
        "quarantine_import_debt": closure.payload_debt + qa.payload_debt,
        "nonproduction_config": cfg, "private_cli_contract": flags,
        "private_3d": {"verdict": "PASS", "analysis": "STATIC_AST_NO_EXECUTION",
                       "page_sha256": digest(page.encode()), "details": page_parser.details,
                       "industrial_panel_count": len(page_parser.hosts),
                       "panel_hosts": page_parser.hosts, "scripts": page_parser.scripts,
                       "stylesheets": page_parser.links, "csp": policy,
                       "inline_scripts": page_parser.inline,
                       "mount_exports": {name: value[0] for name, value in mounts.items()},
                       "render_image": "DATA_URL_CSP_ALLOWED", "browser": "NOT_RUN",
                       "mount_timing_debt": "parametric autoboot does not await project-session.ready; browser acceptance required"},
        "operations": {"http_started": False, "database_opened": False,
                       "application_imported": False, "production_mutated": False},
        "runtime_acceptance": "NOT_RUN",
        "coverage_boundary": [
            "cadcore runtime .py and data JSON conservatively bundled for dynamic dispatch",
            "literal local Python imports, package initializers and referenced static assets checked",
            "acceptance helper files bound separately, not required in the service runtime package",
            "private 3D string-template AST interpreted; hosts, ordered self-only assets and mount export/binding shapes checked",
            "other computed imports, generated worker scripts, JS computed assets and external ABI NOT_ASSESSED",
            "quarantine files retained and hashed; their missing imports recorded, not classified as live dependencies",
            "no runtime CAD, HTTP, browser, TLS, storage sync or backup/restore validation",
        ],
        "production_blockers": [
            {"id": "release_package", "status": "FAIL" if archive_gaps else "REVIEW_REQUIRED",
             "requirement": "Chair integrates reviewed private sources; frozen package matches every runtime hash"},
            {"id": "tls_proxy", "status": "NOT_ASSESSED",
             "requirement": "TLS reverse proxy; loopback upstream inaccessible externally; exact origin/cookie/CSRF tests"},
            {"id": "local_storage_restore", "status": "NOT_ASSESSED",
             "requirement": "Private durable local WAL storage; paired accounts/projects backup; isolated restore and schema acceptance"},
            {"id": "kernel_runtime", "status": "NOT_ASSESSED",
             "requirement": "Pinned FreeCAD/OCCT subprocess ABI, feature workers and real geometry/export acceptance; EGL/GLES/NVIDIA only with explicit GPU opt-in"},
            {"id": "security_operations", "status": "NOT_ASSESSED",
             "requirement": "Account provisioning, revocation, abuse limits, quotas, logs and thread/job exhaustion review"},
            {"id": "switch_recovery", "status": "NOT_ASSESSED",
             "requirement": "Authorized cutover/drain and recovery procedure; /api/release/* is not an enabled maintenance API"},
        ],
    }


def selftest(out):
    fixture = out / "fixture"
    fixture.mkdir()
    sources = {
        "app.py": "raise RuntimeError('must not execute audited source')\nimport cadcore.module\n",
        CORE + "/__init__.py": '"""Fixture package."""\n',
        CORE + "/module.py": "from . import helper\n",
        CORE + "/helper.py": '"""Fixture helper."""\n',
        APP + "/web/index.html": '<script src="/web/js/main.js"></script><link href="/web/css/main.css" rel="stylesheet">',
        APP + "/web/js/main.js": 'import "./extra.js";\n',
        APP + "/web/js/extra.js": "const fixture = true;\n",
        APP + "/web/css/main.css": "body {background:url('../img/mark.svg')}\n",
        APP + "/web/img/mark.svg": "<svg></svg>\n",
        CORE + "/data/i18n/languages.json": '[{"file":"en.json","default_standard":"gbt"}]\n',
        CORE + "/data/i18n/en.json": '{"strings":{"fixture":"Fixture"}}\n',
        CORE + "/data/standards/gbt.json": '{"id":"gbt"}\n',
    }
    for name, text in sources.items():
        path = fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, text.encode())
    cases = []

    def expect(label, code, callback):
        try:
            callback()
        except ReviewError as exc:
            if exc.code != code:
                raise AssertionError(f"{label}: expected {code}, got {exc.code}") from exc
            cases.append({"name": label, "verdict": "PASS", "observed": exc.code})
        else:
            raise AssertionError(label + ": negative control was not rejected")

    closure = Closure(fixture).build(["app.py", APP + "/web/index.html",
                                      CORE + "/data/i18n/languages.json"])
    assert set(closure.hashes) == set(sources), "literal/relative/resource closure incomplete"
    closure.verify()
    assert not (fixture / "__pycache__").exists(), "audited source executed"
    cases.append({"name": "real_ast_relative_js_css_closure_without_execution", "verdict": "PASS"})
    assert asset_references('const page = `<form data-form="import">\n'
                            '<label><span data-label="part"></span></label>`;', ".js") == []
    assert asset_references('import model from "./model.js";\n'
                            'const job = import("./worker.js");', ".js") == ["./model.js", "./worker.js"]
    cases.append({"name": "js_import_statement_not_html_attribute", "verdict": "PASS"})
    expect("missing_import_target", "missing_local_module",
           lambda: closure.import_edge("app.py", "cadcore.absent"))
    closure.import_edge(CORE + "/ops/features/_quarantine/fixture.py", "cadcore.absent")
    assert closure.payload_debt == [{
        "source": CORE + "/ops/features/_quarantine/fixture.py", "module": "cadcore.absent",
        "code": "missing_local_module", "scope": "QUARANTINE_PAYLOAD"}]
    cases.append({"name": "quarantine_debt_visible_not_live", "verdict": "PASS"})
    expect("missing_asset", "missing_source",
           lambda: closure.resource(APP + "/web/index.html", "/web/js/absent.js"))
    expect("external_asset", "external_static_resource",
           lambda: closure.resource(APP + "/web/index.html", "https://invalid.example/a.js"))
    expect("escape_asset", "static_resource_outside_web",
           lambda: closure.resource(APP + "/web/index.html", "/web/%2e%2e/private.env"))
    expect("empty_seed", "empty_seed_set", lambda: Closure(fixture).build([]))
    expect("unsafe_listener", "nonproduction_listener_required",
           lambda: config(fixture, "0.0.0.0", 8767, "http://127.0.0.1:8767", Path("/data/dev/review-fixture")))
    expect("wrong_origin", "nonproduction_exact_origin_required",
           lambda: config(fixture, "127.0.0.1", 8767, "http://127.0.0.1:8768", Path("/data/dev/review-fixture")))
    expect("network_runtime", "runtime_not_durable_external",
           lambda: local_root(fixture, Path("/3fs/research3fs/data/tmp/review-runtime")))
    expect("source_runtime", "runtime_inside_git",
           lambda: local_root(ROOT, ROOT / "runtime"))
    expect("cli_incomplete", "private_cli_flags_missing", lambda: cli_contract(ast.parse("pass")))
    expect("missing_catalog", "missing_source", lambda: closure.json_resources(
        CORE + "/data/i18n/languages.json",
        '[{"file":"absent.json","default_standard":"gbt"}]'))
    expect("missing_standard", "missing_source", lambda: closure.json_resources(
        CORE + "/data/i18n/languages.json",
        '[{"file":"en.json","default_standard":"absent"}]'))
    expect("empty_catalog_manifest", "empty_catalog_manifest", lambda: closure.json_resources(
        CORE + "/data/i18n/languages.json", "[]"))
    expect("duplicate_json", "invalid_source_json", lambda: closure.json_resources(
        CORE + "/data/i18n/en.json", '{"strings":{},"strings":{}}'))
    expect("nonfinite_json", "invalid_source_json", lambda: closure.json_resources(
        CORE + "/data/i18n/en.json", '{"value":NaN}'))
    healthy = config(fixture, "127.0.0.1", 8767, "http://127.0.0.1:8767",
                     Path("/data/dev/opencad-private-review-runtime"))
    assert healthy["started"] is False and healthy["storage_operations"] == "NOT_RUN"
    cases.append({"name": "healthy_local_config_read_only", "verdict": "PASS"})
    page = ('<!doctype html><html><head><script src="/web/js/project-session.js"></script>'
            + "".join('<script src="/web/js/' + name + '-panel.js"></script>'
                      '<link rel="stylesheet" href="/web/css/' + name + '-panel.css">'
                      for name in PANELS)
            + '<script src="/web/js/industrial-panels.js"></script></head><body>'
            + "".join('<details><div data-' + name + '-host></div></details>' for name in PANELS)
            + '</body></html>')
    policy = ("script-src 'self'; style-src 'self'; connect-src 'self'; "
              "font-src 'self'; img-src 'self' data:")
    checked_page = check_page(page, policy)
    assert len(checked_page.hosts) == 5 and len(checked_page.scripts) == 7
    cases.append({"name": "five_industrial_hosts_assets_csp", "verdict": "PASS"})
    expect("missing_industrial_script", "industrial_assets_mismatch", lambda: check_page(
        page.replace('<script src="/web/js/surfaces-panel.js"></script>', ""), policy))
    expect("csp_blocks_data_image", "csp_image_policy_changed", lambda: check_page(
        page, policy.replace(" data:", "")))
    expect("inline_script_blocked", "csp_inline_resource_blocked", lambda: check_page(
        page.replace("</head>", "<script>fixture()</script></head>"), policy))
    expect("wrong_host", "industrial_hosts_mismatch", lambda: check_page(
        page.replace("data-surfaces-host", "data-missing-host"), policy))
    expect("unsafe_template_call", "unsupported_page_template", lambda: static_value(
        ast.parse("execute_audited_code()", mode="eval").body, {}))
    inert = ast.parse('"" .join(f"/web/{name}" for name in ("a", "b"))', mode="eval").body
    assert static_value(inert, {}) == "/web/a/web/b"
    cases.append({"name": "inert_fstring_generator_without_execution", "verdict": "PASS"})
    viewer = ast.parse('def render(*, advanced=False):\n'
                       '    page = "<!doctype html><head></head>" + stamp + "<footer></footer>"\n'
                       '    return page\n')
    secure = ast.parse('CSP = "script-src self"\nPRIVATE_ASSETS = "PRIVATE"\n'
                       'INDUSTRIAL_ASSETS = "".join(f"[{name}]" for name in ("a", "b"))\n'
                       'def private_3d_page():\n    from viewer3d_page import render\n'
                       '    panels = ""\n    for name, caption in (("a","A"),("b","B")):\n'
                       '        panels += f"{name}:{caption}"\n'
                       '    return render(advanced=True).replace("<head>", "<head>" + PRIVATE_ASSETS + INDUSTRIAL_ASSETS, 1).replace("<footer>", panels + "<footer>", 1)\n')
    generated, _ = static_private_page(secure, viewer)
    assert "<head>PRIVATE[a][b]" in generated and "a:Ab:B<footer>" in generated
    cases.append({"name": "private_page_loop_and_order_no_execution", "verdict": "PASS"})
    with (fixture / "app.py").open("ab") as handle:
        handle.write(b"# controlled source drift\n")
    expect("source_drift", "source_drift", closure.verify)
    return {"verdict": "PASS", "scope": "REVIEW_TOOL_SELFTEST", "tests": len(cases),
            "cases": cases, "production_ready": False, "fixture_root": str(fixture)}


def scan_owned():
    import bootstrap_from_infra as bootstrap
    hashes = {}
    for name in OWNED:
        value = read(ROOT, name)
        bootstrap.scan_bytes(name, value)
        hashes[name] = digest(value)
    return {"verdict": "PASS", "files": hashes,
            "scanner": "tools/bootstrap_from_infra.py:scan_bytes",
            "scanner_sha256": digest(read(ROOT, "tools/bootstrap_from_infra.py"))}


def accepted_evidence(path, scope):
    result = json.loads(Path(path).read_bytes())
    if result.get("verdict") != "PASS" or result.get("scope") != scope:
        raise ReviewError("evidence_not_accepted", scope)
    if result.get("reviewer_sha256") != digest(Path(__file__).read_bytes()):
        raise ReviewError("reviewer_evidence_drift", scope)
    line = Path(path).with_name("verdict.log").read_text()
    if "verdict=PASS" not in line or result.get("finished_at") not in line:
        raise ReviewError("evidence_terminal_mismatch", scope)
    return result


def verify_evidence(path):
    result = accepted_evidence(path, "STATIC_REVIEW_ONLY")
    source = Path(result["source_root"]).resolve()
    hashes = result["source_sha256"]
    if not hashes or digest(canonical(hashes)) != result["source_fingerprint"]:
        raise ReviewError("evidence_fingerprint_mismatch", path)
    after = {name: digest(read(source, name)) for name in hashes}
    if hashes != after:
        changed = next(name for name in hashes if hashes[name] != after[name])
        raise ReviewError("source_drift", changed)
    if git(source, "rev-parse", "HEAD").decode().strip() != result["source_head"]:
        raise ReviewError("source_head_drift", "HEAD")
    for key, arguments in (
            ("git_tracked", ("ls-files", "-z")),
            ("git_untracked", ("ls-files", "--others", "--exclude-standard", "-z"))):
        actual = sorted(set(git(source, *arguments).decode().split("\0")) - {""})
        if actual != result[key]:
            raise ReviewError("git_inventory_drift", key)
    return {"verdict": "PASS", "scope": "EVIDENCE_SOURCE_RECHECK",
            "review_evidence": str(Path(path).resolve()), "source_files": len(hashes),
            "source_drift": [], "production_ready": False}


def commit_evidence(review_path, selftest_path):
    binding = verify_evidence(review_path)
    tests = accepted_evidence(selftest_path, "REVIEW_TOOL_SELFTEST")
    if tests.get("tests", 0) < 1 or len(tests["cases"]) != tests["tests"] or any(
            case["verdict"] != "PASS" for case in tests["cases"]):
        raise ReviewError("selftest_counts_not_closed", selftest_path)
    changed = set(git(ROOT, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
                  .decode().splitlines())
    if changed != set(OWNED):
        raise ReviewError("commit_not_exact_owned_paths", "HEAD")
    if git(ROOT, "status", "--porcelain", "--", *OWNED):
        raise ReviewError("owned_commit_dirty", "owned_paths")
    message = git(ROOT, "log", "-1", "--format=%B").decode()
    author = git(ROOT, "log", "-1", "--format=%an <%ae>").decode().strip()
    if "Signed-off-by: " + author not in message.splitlines():
        raise ReviewError("dco_signoff_missing", "HEAD")
    scan = scan_owned()
    for name, sha in scan["files"].items():
        if digest(git(ROOT, "show", "HEAD:" + name)) != sha:
            raise ReviewError("committed_bytes_mismatch", name)
    return {"verdict": "PASS", "scope": "OWNED_DCO_COMMIT",
            "commit": git(ROOT, "rev-parse", "HEAD").decode().strip(),
            "paths": sorted(changed), "author": author, "dco": "PASS",
            "scan": scan, "source_binding": binding, "selftest_tests": tests["tests"],
            "selftest_evidence": str(Path(selftest_path).resolve()), "push": "NOT_RUN",
            "production_ready": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--selftest", action="store_true")
    mode.add_argument("--scan-owned", action="store_true")
    mode.add_argument("--verify-evidence", type=Path)
    mode.add_argument("--commit-evidence", action="store_true")
    parser.add_argument("--review-evidence", type=Path)
    parser.add_argument("--selftest-evidence", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--public-origin", default="http://127.0.0.1:8767")
    parser.add_argument("--data-root", type=Path,
                        default=Path("/data/dev/opencad-private-review-runtime"))
    args = parser.parse_args(argv)
    if args.commit_evidence and (args.review_evidence is None or args.selftest_evidence is None):
        parser.error("commit-evidence requires review-evidence and selftest-evidence")
    source = args.source_root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or Path("/hf3fs-jd/data/tmp/opencad-private-review-s7") / stamp).resolve()
    if not out.is_relative_to(Path("/3fs/research3fs/data/tmp")) or out.is_relative_to(source):
        parser.error("Evidence must be a new external 3FS tmp directory")
    out.mkdir(parents=True, exist_ok=False)
    result = {"verdict": "FAIL", "started_at": now(), "source_root": str(source)}
    try:
        if args.selftest:
            result.update(selftest(out))
        elif args.scan_owned:
            result.update(scope="OWNED_CONTENT_SCAN", scan=scan_owned(), verdict="PASS")
        elif args.verify_evidence:
            result.update(verify_evidence(args.verify_evidence))
        elif args.commit_evidence:
            result.update(commit_evidence(args.review_evidence, args.selftest_evidence))
        else:
            result.update(review(source, args))
    except Exception as exc:
        result["cause_type"] = type(exc).__name__
        result["cause"] = str(exc) if isinstance(exc, (ReviewError, AssertionError)) else "See cause_type; no source contents logged"
    result["reviewer_sha256"] = digest(Path(__file__).read_bytes())
    result["finished_at"] = now()
    line = (f"OPENCAD_PRIVATE_RELEASE_REVIEW verdict={result['verdict']} "
            f"scope={result.get('scope', 'INCOMPLETE')} "
            f"runtime_files={result.get('runtime_files', 'NOT_CAPTURED')} "
            f"tests={result.get('tests', 'NOT_RUN')} "
            f"production_ready=false collected_at={result['finished_at']}")
    write(out / "result.json", canonical(result))
    write(out / "verdict.log", (line + "\n").encode())
    print(line)
    if result["verdict"] == "FAIL":
        print("cause=" + result.get("cause", "NOT_CAPTURED"))
    print("evidence=" + str(out))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
