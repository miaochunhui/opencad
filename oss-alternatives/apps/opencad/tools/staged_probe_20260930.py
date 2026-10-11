#!/usr/bin/env python3
"""OpenCAD 交付树探针：导入配方 + 导出路径策略（2026-09-30，纯标准库）。

一句话用途
----------
把上一席对交付树 `build/win/stage/OpenCAD` 的两条结论各做成一条**可复跑判定行**：
① 导入探针 —— 用 `server.py` **自己**的引导配方（`_candidate_package_roots` / `_load_cadcore`，
   按 `<root>/cadcore/docmodel.py` 选根）复跑 `import cadcore, cadcore.ops.features`，
   并对照上一席的配方（`sys.path.insert(0, "app")` + `insert(0, "app/packages")`）；
② 导出路径策略 —— 真起 `server.py --out-root <dir>`，打 `/api/command export_pdf` 落到 out-root，
   贴 `%PDF-` 头与字节数，再做两条阴性对照（`../` 逃逸、绝对路径），最后杀进程。

判定行
------
    STAGED_IMPORT verdict=PASS|FAIL recipe=<server|seat> detail=…
    EXPORT_PATH_POLICY verdict=PASS|FAIL out_root_honored=0|1 detail=…

用法
----
    python3 oss-alternatives/apps/opencad/tools/staged_probe_20260930.py import --app <app 目录> [--recipe server|seat]
    python3 oss-alternatives/apps/opencad/tools/staged_probe_20260930.py export --app <app 目录> --out-root <目录>
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request

STAGE_APP = "/data/dev/infra/build/win/stage/OpenCAD/app"
REPO_APP = "/data/dev/infra/oss-alternatives/apps/opencad"


def load_server(app: str):
    """按路径加载 server.py（不执行 __main__ 分支）；返回模块对象。"""
    path = os.path.join(app, "opencad", "server.py")
    if not os.path.isfile(path):
        raise SystemExit(f"server.py not found under {app!r}")
    spec = importlib.util.spec_from_file_location("opencad_server_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def describe_cadcore_lookup(entries: list[str]) -> None:
    """诊断：逐 sys.path 条目说清 `cadcore` 会被解析成什么（目录 / __init__.py）。"""
    for entry in entries:
        base = entry or "."
        pkg = os.path.join(base, "cadcore")
        init = os.path.join(pkg, "__init__.py")
        print(f"  path_entry={base!r} cadcore_dir={os.path.isdir(pkg)} "
              f"cadcore_init_py={os.path.isfile(init)}")


def probe_import(app: str, recipe: str) -> int:
    server, server_path = load_server(app)
    if recipe == "server":
        root = None
        for candidate in server._candidate_package_roots():
            if os.path.isfile(os.path.join(candidate, "cadcore", "docmodel.py")):
                root = candidate
                break
        print(f"server_recipe root={root!r} server_py={server_path}")
        try:
            core = server._load_cadcore()
        except server.BootstrapError as exc:
            print(f"STAGED_IMPORT verdict=FAIL recipe=server._load_cadcore detail={exc.reason}: {exc.detail}")
            traceback.print_exc()
            return 1
        import cadcore  # noqa: PLC0415
        import cadcore.ops.features as features  # noqa: PLC0415
        print(f"core_modules={sorted(core)}")
        print(f"cadcore_file={cadcore.__file__} cadcore_path={list(cadcore.__path__)}")
        print(f"features_file={features.__file__}")
        print(f"STAGED_IMPORT verdict=PASS recipe=server._load_cadcore root={root} "
              f"cadcore_ops_features={features.__file__}")
        return 0

    # recipe == "seat"：上一席的配方，在交付树根上以相对路径插入 sys.path
    stage_root = os.path.dirname(app)
    os.chdir(stage_root)
    sys.path.insert(0, "app")
    sys.path.insert(0, "app/packages")
    print(f"seat_recipe cwd={os.getcwd()} sys_path_head={sys.path[:3]}")
    try:
        import cadcore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        print("STAGED_IMPORT verdict=FAIL recipe=seat step=import_cadcore")
        traceback.print_exc()
        describe_cadcore_lookup(sys.path[:3])
        return 1
    print(f"cadcore_spec_origin={cadcore.__spec__.origin!r} "
          f"submodule_search={list(cadcore.__spec__.submodule_search_locations or [])} "
          f"path={list(cadcore.__path__)}")
    print(f"cadcore_has_ops_attr={hasattr(cadcore, 'ops')} "
          f"ops_init_py={os.path.isfile(os.path.join(list(cadcore.__path__)[0], 'ops', '__init__.py'))} "
          f"ops_dir={os.path.isdir(os.path.join(list(cadcore.__path__)[0], 'ops'))}")
    try:
        import cadcore.ops.features as features  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        print("STAGED_IMPORT verdict=FAIL recipe=seat detail=ModuleNotFoundError on cadcore.ops.features")
        traceback.print_exc()
        describe_cadcore_lookup(sys.path[:3])
        return 1
    print(f"features_file={features.__file__}")
    print(f"STAGED_IMPORT verdict=PASS recipe=seat cadcore_ops_features={features.__file__}")
    return 0


def _post(port: int, route: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def probe_export(app: str, out_root: str) -> int:
    server_py = os.path.join(app, "opencad", "server.py")
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, server_py, "--port", "0", "--out-root", out_root],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    port = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            print("server| " + line.rstrip())
            if "verdict=PASS" in line and "port=" in line:
                port = int(line.split("port=")[1].split()[0])
                break
        if port is None:
            print(f"EXPORT_PATH_POLICY verdict=FAIL out_root_honored=0 detail=server_start_failed rc={proc.poll()}")
            return 1
        _post(port, "/api/command", {"command": "add_line", "args": {"x1": 0, "y1": 0, "x2": 60, "y2": 30}})
        inside = _post(port, "/api/command", {"command": "export_pdf", "args": {"out": "staged-e2e.pdf"}})
        print("export_inside=" + json.dumps(inside, ensure_ascii=False, sort_keys=True))
        escape = _post(port, "/api/command", {"command": "export_pdf", "args": {"out": "../escaped-e2e.pdf"}})
        print("export_escape=" + json.dumps(escape, ensure_ascii=False, sort_keys=True))
        absolute = _post(port, "/api/command",
                         {"command": "export_pdf", "args": {"out": os.path.join(out_root, "abs-e2e.pdf")}})
        print("export_absolute=" + json.dumps(absolute, ensure_ascii=False, sort_keys=True))
        outside = _post(port, "/api/command",
                        {"command": "export_pdf", "args": {"out": "/etc/opencad-escape-e2e.pdf"}})
        print("export_absolute_outside=" + json.dumps(outside, ensure_ascii=False, sort_keys=True))

        target = os.path.join(out_root, "staged-e2e.pdf")
        ok = bool(inside.get("ok")) and os.path.isfile(target)
        head, size, digest = b"", -1, ""
        if ok:
            with open(target, "rb") as handle:
                blob = handle.read()
            head, size = blob[:5], len(blob)
            digest = hashlib.sha256(blob).hexdigest()[:16]
            print(f"pdf_head={head.decode('latin-1')!r} pdf_bytes={size} sha256_16={digest} "
                  f"reported_bytes={inside.get('bytes')} reported_magic={inside.get('magic')!r} "
                  f"path={inside.get('path')}")
        root_real = os.path.realpath(out_root)  # 与 server 同口径：out-root 可能是软链接（/hf3fs-jd）
        honored = 1 if ok and os.path.realpath(str(inside.get("path", ""))).startswith(root_real + os.sep) else 0
        escape_ok = (not escape.get("ok")) and escape.get("error") == "path_rejected"
        detail = (f"magic={head.decode('latin-1')} bytes={size} sha={digest} "
                  f"escape_still_rejected={int(bool(escape_ok))} "
                  f"absolute_allowed={int(bool(absolute.get('ok')))} "
                  f"absolute_error={absolute.get('error')}")
        verdict = "PASS" if (honored == 1 and escape_ok) else "FAIL"
        print(f"EXPORT_PATH_POLICY verdict={verdict} out_root_honored={honored} detail={detail}")
        return 0 if verdict == "PASS" else 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        print(f"server_killed rc={proc.poll()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_imp = sub.add_parser("import")
    p_imp.add_argument("--app", default=STAGE_APP)
    p_imp.add_argument("--recipe", choices=("server", "seat"), default="server")
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--app", default=STAGE_APP)
    p_exp.add_argument("--out-root", default="/hf3fs-jd/data/tmp/staged-e2e/out")
    args = parser.parse_args()
    app = os.path.abspath(args.app)
    if args.cmd == "import":
        return probe_import(app, args.recipe)
    return probe_export(app, args.out_root)


if __name__ == "__main__":
    sys.exit(main())
