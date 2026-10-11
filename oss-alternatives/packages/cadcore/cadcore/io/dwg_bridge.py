#!/usr/bin/env python3
"""dwg_bridge.py —— DWG 只读适配桥（A 案：LibreDWG **只读子集**、**进程外**调用）。

一句话用途
----------
把 DWG（AutoCAD 二进制图纸）**只读**接进 cadcore：`dwg_to_dxf(src, dst)` 经 `dwg2dxf` 转 DXF 落盘、
`dwg_entities(src)` 只读解析出实体摘要（首选 `dwgread -O JSON`，缺席时回落 `dwg2dxf` + 本模块的
纯 stdlib ASCII DXF 扫描）。**只输出 DXF / JSON，绝不链接任何 DWG 库**。

许可边界（硬约束，不许放宽）
--------------------------
LibreDWG 是 **GPL-3.0**：本模块**只**通过 `subprocess` 调外部二进制 + 临时文件交互，
**不 import、不链接、不复制**其任何代码进本包（进程边界即许可边界）。
判据（可现读复核）：本文件 `import` 只有标准库 + 本仓自己的 `cadcore.*`；全文不含
`import libredwg` / `ctypes` / `cffi` 一类链接面。要写 DWG 位流本桥**不做**（`WRITE_READY = False`），
写半边由注册层 `cadcore.ops.output.save_dwg` 如实抛 `dependency_missing` —— **绝不落假 .dwg**。

三态（E2：`0（真为零）` / `-1（未采）` / `NA` 不许同形）
--------------------------------------------------------
* `status="ok"`      —— 真读到；实体数可以是 0，那是**真为零**；
* `status="partial"` —— 只读到一部分（如：DWG 头签名（真字节）读到了，实体层因后端缺席**未采**）；
* `status="na"`      —— 未采/做不到：`entities=None`、`reason=` 点名**缺什么**。

**禁止**用 `entities=[]` 冒充"零实体"—— 未采一律 `None` + `reason`。

用法
----
    python3 cadcore/io/dwg_bridge.py            # 自检 + 判定行 CAD_DWG_BRIDGE
    from cadcore.io.dwg_bridge import availability, dwg_to_dxf, dwg_entities
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

TOOL_DWG2DXF = "dwg2dxf"
TOOL_DWGREAD = "dwgread"
DEFAULT_TIMEOUT_S = 120.0
MAX_ENTITY_SUMMARY = 200

#: 三态结论（与全包 E2 口径一致）。
STATUS_OK, STATUS_PARTIAL, STATUS_NA = "ok", "partial", "na"

#: 本桥**不做写**：DWG 位流写出永远 external（判据 = 这个常量是 False 且 `save_dwg` 仍抛 dependency_missing）。
WRITE_READY = False

#: DWG 头版本签名（文件前 6 字节，公开格式）：纯 stdlib 真读文件字节，不依赖任何外部二进制。
DWG_HEADER_VERSIONS: dict[str, str] = {
    "AC1009": "R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "R2000",
    "AC1018": "R2004", "AC1021": "R2007", "AC1024": "R2010",
    "AC1027": "R2013", "AC1032": "R2018",
}

#: ASCII DXF 组码 -> 实体摘要字段（本模块自带扫描器，不依赖 ezdxf）。
_GEOM_CODES: dict[int, str] = {8: "layer", 10: "x", 20: "y", 11: "x2", 21: "y2", 40: "r", 62: "color"}


def tool_path(name: str) -> str:
    """外部二进制的绝对路径（缺席返回空串，**不抛**）。"""
    return shutil.which(str(name)) or ""


def availability() -> dict[str, Any]:
    """现读后端在位情况（真源 = `shutil.which`，不是常量，不许缓存成静态声明）。"""
    dwg2dxf, dwgread = tool_path(TOOL_DWG2DXF), tool_path(TOOL_DWGREAD)
    return {
        "dwg2dxf": dwg2dxf,
        "dwgread": dwgread,
        "read_ready": bool(dwg2dxf or dwgread),
        "write_ready": WRITE_READY,
        "linkage": "subprocess",
        "license": "GPL-3.0 (LibreDWG) - process boundary only: subprocess + files, never linked/copied",
        "hint": "apt/libredwg-tools absent => install the host package (C3: host, not pod) to enable live conversion",
    }


def _tail(blob: Any, limit: int = 400) -> str:
    text = blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else str(blob or "")
    return text[-limit:].strip()


def probe_container(src: Any) -> dict[str, Any]:
    """只读 DWG 头：真读文件字节 → 版本签名（`AC10xx`）。纯 stdlib，无外部依赖。"""
    path = Path(str(src))
    out: dict[str, Any] = {"status": STATUS_NA, "reason": "", "source": str(path), "signature": "",
                           "version": "", "bytes": -1, "sha256": ""}
    if not path.is_file():
        out["reason"] = "src_missing:%s" % path
        return out
    blob = path.read_bytes()
    signature = blob[:6].decode("ascii", "replace")
    version = DWG_HEADER_VERSIONS.get(signature, "")
    out.update({"signature": signature, "version": version, "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest()})
    if not version:
        out["reason"] = "not_a_dwg:signature=%r" % signature
        return out
    out["status"] = STATUS_OK
    return out


def dwg_to_dxf(src: Any, dst: Any, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """subprocess 调 `dwg2dxf` 把 DWG 转成 DXF 文件（**只落 DXF，不链接 GPL 代码**）。

    三态：`ok` = rc=0 且产物是 DXF 文本；`na` = 工具缺席 / 源不是 DWG / 转换失败（**此时不落 dst**）。
    """
    src_path, dst_path = Path(str(src)), Path(str(dst))
    out: dict[str, Any] = {"op": "dwg_to_dxf", "src": str(src_path), "dst": str(dst_path),
                           "status": STATUS_NA, "reason": "", "rc": -1, "tool": "",
                           "container": probe_container(src_path), "dst_bytes": -1, "dst_sha256": "",
                           "stdout_tail": "", "stderr_tail": ""}
    container = out["container"]
    if container["status"] != STATUS_OK:
        out["reason"] = container["reason"]
        return out
    tool = tool_path(TOOL_DWG2DXF)
    out["tool"] = tool
    if not tool:
        out["reason"] = "external_missing:%s" % TOOL_DWG2DXF
        return out
    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([tool, "-o", str(dst_path), str(src_path)],
                              capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["reason"] = "dwg2dxf_spawn_failed:%s" % type(exc).__name__
        return out
    out["rc"] = int(proc.returncode)
    out["stdout_tail"], out["stderr_tail"] = _tail(proc.stdout), _tail(proc.stderr)
    if proc.returncode != 0 or not dst_path.is_file() or dst_path.stat().st_size == 0:
        out["reason"] = "dwg2dxf_failed:rc=%d" % int(proc.returncode)
        return out
    blob = dst_path.read_bytes()
    out["dst_bytes"], out["dst_sha256"] = len(blob), hashlib.sha256(blob).hexdigest()
    if "SECTION" not in blob.decode("latin-1").upper():
        out["status"] = STATUS_PARTIAL
        out["reason"] = "dst_not_dxf_text"
        return out
    out["status"] = STATUS_OK
    return out


def _dxf_pairs(text: str):
    """ASCII DXF 组码流 → `(code, value)`（每两行一组，纯 stdlib）。"""
    lines = text.splitlines()
    for index in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[index].strip())
        except ValueError:
            continue
        yield code, lines[index + 1].strip()


def dxf_entities(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """ENTITIES 段 → `(实体摘要, 实际出现的图层名)`；空段是**真为零**（调用方据此分三态）。"""
    ents: list[dict[str, Any]] = []
    layers: list[str] = []
    section, want_name, cur = "", False, None
    for code, value in _dxf_pairs(text):
        if code == 0:
            if cur is not None:
                ents.append(cur)
                cur = None
            token = value.upper()
            if token == "SECTION":
                want_name = True
            elif token == "ENDSEC":
                section = ""
            elif section == "ENTITIES":
                cur = {"type": token}
            continue
        if want_name and code == 2:
            section, want_name = value.upper(), False
            continue
        if cur is not None and code in _GEOM_CODES:
            key = _GEOM_CODES[code]
            if key == "layer":
                cur["layer"] = value
                if value not in layers:
                    layers.append(value)
            elif key == "color":
                try:
                    cur["color"] = int(value)
                except ValueError:
                    pass
            else:
                try:
                    cur[key] = float(value)
                except ValueError:
                    cur[key] = value
    if cur is not None:
        ents.append(cur)
    return ents, sorted(layers)


def entities_from_libredwg_json(payload: Any) -> tuple[list[dict[str, Any]], list[str], str]:
    """LibreDWG `dwgread -O JSON` 载荷 → `(实体摘要, 图层, note)`。

    ⚠️ **未与真 dwgread 输出对拍**：本机既无 `dwgread` 也无真 DWG 样本（见 README/测试的 NA 记账），
    此形状按 LibreDWG JSON 的 `OBJECTS[*].entity/object` 约定写，真机对拍前**不许**当已验证。
    """
    objects = payload.get("OBJECTS") if isinstance(payload, Mapping) else None
    if not isinstance(objects, list):
        return [], [], "objects_absent"
    ents: list[dict[str, Any]] = []
    layers: list[str] = []
    for entry in objects:
        if not isinstance(entry, Mapping):
            continue
        type_name = entry.get("object") or entry.get("type_name") or ""
        if not entry.get("entity") and not type_name:
            continue
        item: dict[str, Any] = {"type": str(type_name or entry.get("type") or "?")}
        for key in ("layer", "handle", "color", "start", "end", "center", "radius"):
            if key in entry:
                item[key] = entry[key]
        if isinstance(item.get("layer"), str) and item["layer"] not in layers:
            layers.append(item["layer"])
        ents.append(item)
    return ents, sorted(layers), ""


def _counts(ents: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(e.get("type", "?")) for e in ents).items()))


def _via_dwgread(tool: str, src: str, timeout: float) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="dwg-bridge-") as tmp:
        raw = Path(tmp) / "entities.json"
        try:
            proc = subprocess.run([tool, "-O", "JSON", "-o", str(raw), src],
                                  capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not raw.is_file():
            return None
        try:
            payload = json.loads(raw.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, OSError):
            return None
    ents, layers, note = entities_from_libredwg_json(payload)
    if note:
        return {"status": STATUS_PARTIAL, "reason": "dwgread_json:%s" % note, "engine": "dwgread-json",
                "entities": None, "counts": None, "layers": None}
    return {"status": STATUS_OK, "reason": "", "engine": "dwgread-json",
            "entities": ents[:MAX_ENTITY_SUMMARY], "counts": _counts(ents), "layers": layers,
            "truncated": max(0, len(ents) - MAX_ENTITY_SUMMARY)}


def _via_dwg2dxf(tool: str, src: str, timeout: float) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="dwg-bridge-") as tmp:
        got = dwg_to_dxf(src, Path(tmp) / "out.dxf", timeout=timeout)
        if got["status"] != STATUS_OK:
            return None
        text = Path(got["dst"]).read_text(encoding="latin-1", errors="replace")
    ents, layers = dxf_entities(text)
    return {"status": STATUS_OK, "reason": "", "engine": "dwg2dxf+dxf-scan", "entities": ents[:MAX_ENTITY_SUMMARY],
            "counts": _counts(ents), "layers": layers, "truncated": max(0, len(ents) - MAX_ENTITY_SUMMARY)}


def dwg_entities(src: Any, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """**只读**解析 DWG → 实体摘要（不改输入；临时产物只在进程内 tempdir，跑完即删）。

    优先级：①`dwgread -O JSON` ②`dwg2dxf` + 纯 stdlib DXF 扫描。
    两件都缺席 ⇒ `status="partial"`：DWG 头签名（真字节）已读到，**实体层未采**（`entities=None`）。
    """
    out: dict[str, Any] = {"op": "dwg_entities", "src": str(src), "status": STATUS_NA, "reason": "",
                           "container": probe_container(src), "entities": None, "counts": None,
                           "layers": None, "engine": "", "entity_read_ready": False,
                           "write_ready": WRITE_READY}
    container = out["container"]
    if container["status"] != STATUS_OK:
        out["reason"] = container["reason"]
        return out
    av = availability()
    out["entity_read_ready"] = bool(av["read_ready"])
    if not av["read_ready"]:
        out["status"] = STATUS_PARTIAL
        out["reason"] = "external_missing:%s,%s" % (TOOL_DWG2DXF, TOOL_DWGREAD)
        return out
    engines = ([("dwgread", av["dwgread"], _via_dwgread)] if av["dwgread"] else []) + \
              ([("dwg2dxf", av["dwg2dxf"], _via_dwg2dxf)] if av["dwg2dxf"] else [])
    for _name, path, fn in engines:
        got = fn(path, str(src), timeout)
        if got is not None:
            out.update(got)
            return out
    out["status"] = STATUS_PARTIAL
    out["reason"] = "entity_backends_failed"
    return out


def selftest() -> int:
    """自检（不需要外部工具也能跑）：契约 + 三态 + 真头读 + 写半边恒 external。"""
    checks: list[tuple[str, bool]] = []
    av = availability()
    checks.append(("write_ready_false", av["write_ready"] is False))
    checks.append(("linkage_subprocess", av["linkage"] == "subprocess"))
    checks.append(("read_ready_is_bool", isinstance(av["read_ready"], bool)))
    with tempfile.TemporaryDirectory(prefix="dwg-bridge-selftest-") as tmp:
        root = Path(tmp)
        junk = root / "junk.dwg"
        junk.write_bytes(b"not a dwg at all")
        checks.append(("junk_not_dwg", probe_container(junk)["status"] == STATUS_NA))
        got = dwg_to_dxf(junk, root / "out.dxf")
        checks.append(("junk_no_artifact", got["status"] == STATUS_NA and not (root / "out.dxf").exists()))
        miss = dwg_entities(root / "nope.dwg")
        checks.append(("missing_src_na_none", miss["status"] == STATUS_NA and miss["entities"] is None))
        fake_dwg = root / "synthetic.dwg"
        fake_dwg.write_bytes(b"AC1032" + b"\0" * 64)
        checks.append(("header_version_read", probe_container(fake_dwg)["version"] == "R2018"))
        ent = dwg_entities(fake_dwg)
        if av["read_ready"]:
            checks.append(("entities_three_state", ent["status"] in (STATUS_OK, STATUS_PARTIAL)))
        else:
            checks.append(("no_backend_partial_not_zero",
                           ent["status"] == STATUS_PARTIAL and ent["entities"] is None
                           and "external_missing" in ent["reason"]))
    bad = [name for name, ok in checks if not ok]
    print("CAD_DWG_BRIDGE verdict=%s rc=%d checks=%d dwg2dxf=%s dwgread=%s read_ready=%d write_ready=0"
          % ("PASS" if not bad else "FAIL", 0 if not bad else 1, len(checks),
             av["dwg2dxf"] or "-", av["dwgread"] or "-", 1 if av["read_ready"] else 0))
    if bad:
        print("CAD_DWG_BRIDGE failed=" + ",".join(bad))
    return 0 if not bad else 1


def main(argv: Any = None) -> int:
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
