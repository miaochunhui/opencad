#!/usr/bin/env python3
"""selfcheck.py —— **随包发到 Windows** 的自检入口（用户在目标机上跑它自证）。

为什么要随包发：判定行 G1–G11 都在开发机上跑，而"装到 Windows Server 2022 上到底能不能用"只有
目标机能回答。这个脚本只用标准库，起真服务、打真 API、导出一张真 DXF，然后打印一条判定行 ——
用户把那一行贴回来即可，不需要装任何东西、不需要读日志。

用法（在安装目录下）：
    runtime\\python.exe app\\opencad\\selfcheck.py
    opencad.exe --check            ← 原生启动器等价入口
判定行：
    OPENCAD_WIN_SELFCHECK verdict=PASS python=3.10.11 platform=win32 host=127.0.0.1 port=… \
        api_state=1 catalogs=3 created=1 entities_in_doc=1 locale_switch=1 \
        dxf_bytes=… dxf_entities=… seconds=…
退出码：0=PASS / 1=FAIL / 2=环境不满足（缺件）

工作目录：自检**自己建一个一次性临时目录**（`%TEMP%\\opencad-selfcheck-*`），服务端以它为 cwd、
以它为落盘根（`--out-root`），导出的 `opencad-selfcheck.dxf` 就落在那里、也从那里读回 ——
**不往安装目录里写任何东西**。默认跑完即删；`OPENCAD_SELFCHECK_KEEP=1` 保留该目录并打印路径。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# 本脚本会 import 安装目录里的包（`app/packages/dxf` 用于把导出的 DXF 读回来数实体）。
# **必须在任何这种导入之前**关掉字节码落盘：否则解释器会往安装目录写 `__pycache__/*.pyc`，
# 使暂存树与 `MANIFEST.sha256` 不一致（2026-09-30 被判定行 G11 抓到；只加 `-B` 给子进程不够，
# 父进程自己 import 时照样写）。
sys.dont_write_bytecode = True

import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
DXF_NAME = "opencad-selfcheck.dxf"


def _say(line: str) -> None:
    """打印一行；路径里的非 ASCII 字符在窄编码控制台上不许把自检自己弄崩。"""
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(line.encode(encoding, "backslashreplace").decode(encoding, "replace"), flush=True)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait(port: int, seconds: float = 25.0) -> bool:
    import socket

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def main() -> int:
    started = time.time()
    if not SERVER.is_file():
        _say(f"OPENCAD_WIN_SELFCHECK verdict=FAIL reason=server_missing path={SERVER}")
        return 2
    keep = os.environ.get("OPENCAD_SELFCHECK_KEEP") == "1"
    # 自己挑一个一次性工作目录：服务端以它为 cwd、以它为落盘根，导出的图纸落在它里面。
    # **为什么必须显式给落盘根**：服务端的 `_safe_path` 用的是 `out_root`（= server.py 所在目录），
    # 跟进程 cwd 无关 —— 实测「cwd=临时目录 + 相对导出路径」时，cwd 里没有文件、安装目录里才有。
    try:
        work = Path(tempfile.mkdtemp(prefix="opencad-selfcheck-"))
    except OSError as exc:
        _say(f"OPENCAD_WIN_SELFCHECK verdict=FAIL reason=workdir_unavailable "
             f"temp={tempfile.gettempdir()} detail={type(exc).__name__}: {exc}")
        return 2
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = work / "server.log"
    handle = log.open("wb")
    # `-B` + `PYTHONDONTWRITEBYTECODE=1`：**不许往安装目录写字节码**。
    # 2026-09-30 实撞（由 G11 打包门抓到）：在暂存树里跑过自检后，解释器就地写下 13 个
    # `__pycache__/*.pyc`，使暂存树与它自己的 MANIFEST.sha256 不一致；若此时重打安装包
    # （NSIS 是 `File /r ${STAGE_DIR}\*.*`）还会把 pyc 一起封进载荷。目标机上同理：用户跑一次
    # 自检就多出一堆缓存文件，卸载也不干净。
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        child = subprocess.Popen([sys.executable, "-B", str(SERVER), "--host", "127.0.0.1",
                                  "--port", str(port), "--out-root", str(work)],
                                 stdout=handle, stderr=subprocess.STDOUT, cwd=str(work), env=env)
    finally:
        handle.close()   # 子进程有自己的句柄；父进程这份要及时关，否则 Windows 上删不掉临时目录
    try:
        if not _wait(port):
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:] if log.is_file() else ""
            _say(f"OPENCAD_WIN_SELFCHECK verdict=FAIL reason=server_not_ready port={port} "
                 f"workdir={work} log_tail={tail!r}")
            return 1
        state = _get(f"{base}/api/state")
        catalogs = state.get("catalogs") or []
        strings_before = state.get("strings") or {}
        # 先真建一条实体：导出接口对空文档不产出文件（第一次实跑暴露：不建实体直接导出 ⇒
        # dxf_bytes=-1，看起来像"打包坏了"，其实是自检步骤漏了前置动作）。
        created = _post(f"{base}/api/command",
                        {"command": "add_line", "args": {"p1": [0, 0], "p2": [50_000_000, 0]}})
        entities_in_doc = int((_get(f"{base}/api/state").get("entities") or 0))
        target = next((code for code in catalogs if code != state.get("locale")), None)
        switched = 0
        if target:
            after = _post(f"{base}/api/locale", {"locale": target})
            changed = sum(1 for key, value in (after.get("strings") or {}).items()
                          if strings_before.get(key) != value)
            switched = 1 if (after.get("ok") and changed >= 5) else 0
        work_file = work / DXF_NAME
        # 相对路径：服务端只收相对路径（绝对路径 / 反斜杠一律 path_rejected）。
        exported = _post(f"{base}/api/export/dxf", {"path": DXF_NAME})
        dxf_bytes = work_file.stat().st_size if work_file.is_file() else -1
        reader = HERE.parent / "packages" / "dxf"
        entities = -1
        if dxf_bytes > 0 and reader.is_dir():
            sys.path.insert(0, str(reader))
            try:
                import dxf  # type: ignore

                entities = int(dxf.read(str(work_file)).entity_count())
            except Exception:  # noqa: BLE001 - 读回失败要如实报 -1，不假装
                entities = -1
        api_state = 1 if state.get("ok") else 0
        ok = bool(api_state and len(catalogs) >= 3 and dxf_bytes > 0 and entities >= 1 and switched
                  and created.get("ok") and entities_in_doc >= 1)
        if not ok or keep:
            _say(f"OPENCAD_SELFCHECK workdir={work} export_ok={1 if exported.get('ok') else 0} "
                 f"export_reported_bytes={exported.get('bytes')} export_path={exported.get('path')!r} "
                 f"export_error={exported.get('error')!r}")
        _say("OPENCAD_WIN_SELFCHECK verdict={v} python={py} platform={plat} host=127.0.0.1 port={port} "
             "api_state={api} catalogs={cats} created={created} entities_in_doc={indoc} "
             "locale_switch={sw} dxf_bytes={dxf} dxf_entities={ents} seconds={sec:.1f}".format(
                 v="PASS" if ok else "FAIL", py=sys.version.split()[0], plat=sys.platform, port=port,
                 api=api_state, cats=len(catalogs), created=1 if created.get("ok") else 0,
                 indoc=entities_in_doc, sw=switched, dxf=dxf_bytes, ents=entities,
                 sec=time.time() - started))
        return 0 if ok else 1
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
        if keep:
            _say(f"OPENCAD_SELFCHECK kept workdir={work} (OPENCAD_SELFCHECK_KEEP=1)")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
