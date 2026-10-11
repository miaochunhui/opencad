"""cadcore.kernel.subproc -- A8 可选后端：进程外调外部 CAD CLI（FreeCADCmd 等），实现 capabilities 子集。

一句话用途
----------
把「3D 参数化 + STEP 出口」这类重能力做成**进程外**调用：本模块生成一个脚本 → 交给外部 CLI 跑 → 文件交换回结果；
外部程序装没装、能不能跑，用一次**短超时探测**裁决，探不到就 `available()=False`（不抛异常、不长时间阻塞），
由上层降级到纯 Python 的 nano 后端。

为什么这样设计（背景）
----------------------
1. **许可与崩溃隔离**：FreeCAD/OCCT 是 LGPL 系（`DECISION.md` §3 B1/B2），只允许「子进程 + 文件级契约」接入。
   所以本模块进程内**不 import 任何重依赖**（缺席也不硬失败），只依赖 CLI 的参数/退出码/产物文件三件事。
   `import FreeCAD` / `import Part` 只出现在**生成给外部进程的脚本文本**里（`_PROBE_SCRIPT` / `_ADAPTER_SCRIPT`），
   本进程从头到尾不 import 它们 —— 这也是内核纯净门（G4）要的口径。
2. **探不到就是不可用**：`available()` 只认「可执行文件在 ∧ 探测脚本跑通 ∧ 回显了我们的机读标记」三条同时成立；
   任何一条不成立 ⇒ `False` 并记下机读原因码（`timeout` / `marker_missing` / `nonzero_exit` …）。
   一个"大概装了"的猜测换来的是一次用户面前崩溃，代价远大于误判不可用（误判可诊断、可用环境变量调宽）。
3. **无状态配方（recipe）**：每个 `SubprocessShape` 是一串**可重放的算子配方**（而不是跨进程句柄）。
   `sketch_to_wire` / `extrude` / `revolve` / `boolean` **只追加配方、不起进程**；真正需要具体几何的三处
   —— `mass_properties()` / `project_to_2d()` / `export()` —— 才起一个新进程**从头重放整条配方**。
   于是不回读 `FCStd`、不写回上游（`DECISION.md` §2.2「单向」），外部工具崩了也不污染主进程状态；
   代价是同一个形状被问两次就重算两次（要跨调用缓存请上游自己在 `work_dir` 之外做）。
4. **失败必须响**：外部程序非 0 退出、结果 JSON 缺失、产物为空、投影为空 ⇒ **抛错**，
   不许静默降级成空图（`DECISION.md` §2.2）。`0` / `-1` / `NA` 三态在质量属性与探测记账里逐字段可辨：
   `-1` 表示**未采**，`None`/`NA` 表示**不适用**，绝不用 0 冒充"没测到"。

对外契约（照抄 `MVP-INTERFACES.md` §B A8 与 A6 的 `KernelAdapter`）
------------------------------------------------------------------
`SubprocessKernel`：`name` / `version` / `available()` / `capabilities()` / `sketch_to_wire()` / `extrude()` /
`revolve()` / `boolean()` / `project_to_2d()` / `mass_properties()` / `export()`；
另附本后端自己的观测面：`probe_report()` / `stats()` / `close()`，以及模块级 `probe()` / `find_executable()` /
`make_kernel()` / `register()` / `reset_cache()` / `profiles()`。

**未采声明（不许当已实现）**：本机没有 `FreeCADCmd`，所以「生成脚本交给真 FreeCAD 跑出几何」这条端到端链
**本轮未采**（`capabilities_measured=False`、探测 rc 记账为 `-1`）；已实测的是**边界行为**
（探测/超时/杀进程/非 0 退出/产物校验/脚本协议），用假可执行文件在 `tests/test_subproc_step.py` 里逐条钉死。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

LOG = logging.getLogger("cadcore.kernel.subproc")

__all__ = [
    "ALL_CAPABILITIES",
    "DECLARED_CAPABILITIES",
    "KERNEL_NAME",
    "NA",
    "PROBE_ONLY_CAPABILITIES",
    "PROFILES",
    "UNMEASURED",
    "CadError",
    "DependencyMissing",
    "KernelExecutionError",
    "MassProperties",
    "ProbeResult",
    "Projection",
    "SubprocError",
    "SubprocProfile",
    "SubprocessKernel",
    "SubprocessShape",
    "find_executable",
    "get_profile",
    "make_kernel",
    "probe",
    "reset_cache",
    "register",
    "verdict_line",
]

# ── A3 显式降级信号（真件优先；A3 尚未落盘时用同名同语义的兜底，落盘后自动让位）──────────────
from ..errors import CadError, DependencyMissing


# ── 口径常量：三态记账 ────────────────────────────────────────────────────────────────
# 未采：没测到（≠ 测到 0）。凡计数/时长的缺省值都用它。
UNMEASURED: int = -1

# 不适用：这个字段对本对象没有意义（≠ 未采、≠ 0）。
NA: str = "NA"

SCHEMA: int = 1
KERNEL_NAME: str = "freecad"
PROBE_MARKER: str = "cadcore-subproc-probe"
ADAPTER_MARKER: str = "cadcore-subproc-adapter"

ENV_EXECUTABLE: str = "CADCORE_SUBPROC_EXECUTABLE"
ENV_DISABLE: str = "CADCORE_SUBPROC_DISABLE"
ENV_PROBE_TIMEOUT: str = "CADCORE_SUBPROC_PROBE_TIMEOUT_S"
ENV_OP_TIMEOUT: str = "CADCORE_SUBPROC_OP_TIMEOUT_S"
ENV_KEEP_WORK: str = "CADCORE_SUBPROC_KEEP_WORK"
ENV_PARAMS: str = "CADCORE_OP_PARAMS"
ENV_RESULT: str = "CADCORE_OP_RESULT"
ENV_PROBE_RESULT: str = "CADCORE_PROBE_RESULT"

# 缺省探测超时（秒）。**宁可短到会误判不可用**（可诊断、可调），也不长到卡住应用启动。
# 真机上 `FreeCADCmd` 冷启动 + import Part/TechDraw 可能接近这个量级，装了就调大 `CADCORE_SUBPROC_PROBE_TIMEOUT_S`。
DEFAULT_PROBE_TIMEOUT_S: float = 4.0

PROBE_TIMEOUT_CAP_S: float = 15.0
DEFAULT_OP_TIMEOUT_S: float = 60.0
OP_TIMEOUT_CAP_S: float = 3600.0
KILL_GRACE_S: float = 5.0
MIN_TIMEOUT_S: float = 0.05

REASON_OK: str = "ok"
REASON_DISABLED: str = "disabled_by_env"
REASON_NOT_FOUND: str = "executable_not_found"
REASON_LAUNCH_FAILED: str = "launch_failed"
REASON_TIMEOUT: str = "timeout"
REASON_NONZERO_EXIT: str = "nonzero_exit"
REASON_MARKER_MISSING: str = "marker_missing"
REASON_PROBE_FAILED: str = "probe_script_failed"
REASON_OP_FAILED: str = "op_failed"
REASON_RESULT_MISSING: str = "result_missing"
REASON_ARTIFACT_MISSING: str = "artifact_missing"
REASON_ARTIFACT_UNCHANGED: str = "artifact_unchanged"
REASON_EMPTY_PROJECTION: str = "empty_projection"
REASON_FOREIGN_SHAPE: str = "foreign_shape_ref"
REASON_UNKNOWN_KERNEL: str = "unknown_kernel"
REASON_UNKNOWN_PROFILE: str = "unknown_profile"
REASON_UNVERIFIED_CAPABILITY: str = "capability_not_measured"

# A6 契约里 `capabilities()` 的完整词汇表（见 `MVP-INTERFACES.md` §B A6）。
ALL_CAPABILITIES: frozenset[str] = frozenset(
    {"sketch", "extrude", "revolve", "boolean", "project2d", "step", "mass"}
)

PART_CAPABILITIES: frozenset[str] = frozenset({"sketch", "extrude", "revolve", "boolean", "mass", "step"})
TECHDRAW_CAPABILITIES: frozenset[str] = frozenset({"project2d"})

# **没实测就不许声称**的能力：HLR 投影要 `TechDraw` 模块，而它在无头 `FreeCADCmd` 里能不能 import 未取证
# （`ARCHITECTURE.md` §2 的 P3 自带已知缺陷）。所以只有探测明确报告 `TechDraw` 可用时才把它列进 `capabilities()`。
# 2026-10-07 本机取证（freecadcmd 0.19.2 + OCC 7.5.1）把口径收紧了一格：**import 成功 ≠ 能投影**。
#   实测 `import TechDraw` 通过，但模块里没有 `projectEx`（0.20+ 才有）、也没有 `project`/`projectToView`，
#   只有 `findShapeOutline(shape, scale, direction)`；旧实现直接调 `projectEx` ⇒ 6/6 视图 `AttributeError`。
#   现在：脚本侧按版本回退（`projectEx` 优先，缺席用 `findShapeOutline`，并把 `hidden_measured=False`
#   如实报上去——0.19 只有可见轮廓，虚隐线是**未采**），探测侧新增 `project2d_api` 字段，
#   由 `_capabilities_from_modules()` 决定到底声称不声称 `project2d`（空串＝模块在但无入口 ⇒ 不声称）。
PROBE_ONLY_CAPABILITIES: frozenset[str] = frozenset({"project2d"})

# 本适配器**有脚本模板**的算子集合（能力下限）。真实可用性以探测结果为准，见 `PROBE_ONLY_CAPABILITIES`。
DECLARED_CAPABILITIES: frozenset[str] = frozenset(
    (PART_CAPABILITIES | TECHDRAW_CAPABILITIES) & ALL_CAPABILITIES
)

# 为什么两个生成脚本都**无条件**调 main()（2026-10-07 本机实撞，freecadcmd 0.19.2 + OCC 7.5.1）：
#   当**脚本所在目录 == 子进程 cwd** 时，FreeCAD 把脚本当模块 import，而不是当 `__main__` 执行 ——
#   常规写法 `if __name__ == "__main__": sys.exit(main())` **永不成立**，于是探测/算子静默返回 rc=0
#   （现场只留下一个 `__pycache__`），上层读成 `marker_missing`，看起来像"内核压根没装"。
#   改法：模板内**无条件** `main()`，只在真是 `__main__` 时才把返回码变成进程退出码；
#   被误 import 时也不抛 SystemExit 去污染宿主进程。
#   ⚠️ 模板正文是**运行时字符串**：按 G4/源码纪律不许出现 CJK（G6 的 TestSourceDiscipline 会报红），
#   所以这段解释只能待在这层普通注释里，不能搬进模板。
_PROBE_SCRIPT: str = '''# cadcore kernel subproc probe script (A8) -- generated by cadcore.kernel.subproc, do not edit
# contract: CADCORE_PROBE_RESULT -> result JSON path; last stdout line carries the machine marker.
import json
import os
import sys

MARKER = "cadcore-subproc-probe"
SCHEMA = 1


def main():
    payload = {"ok": False, "schema": SCHEMA, "marker": MARKER, "modules": {}}
    try:
        import FreeCAD as App

        payload["modules"]["FreeCAD"] = True
        version = getattr(App, "Version", None)
        value = None
        if callable(version):
            try:
                value = version()
            except Exception:
                value = None
        if isinstance(value, (list, tuple)):
            value = ".".join(str(item) for item in value[:3])
        payload["version"] = str(value) if value else ""
        payload["ok"] = True
    except Exception as exc:
        payload["error"] = "%s: %s" % (type(exc).__name__, exc)
    for name in ("Part", "TechDraw"):
        try:
            __import__(name)
            payload["modules"][name] = True
        except Exception:
            payload["modules"][name] = False
    # Which HLR entry point this TechDraw really offers. projectEx(shape, direction) -> (visible,
    # hidden) exists from FreeCAD 0.20 on; 0.19 has neither projectEx nor project and only exposes
    # findShapeOutline(shape, scale, direction) -> visible outline wire. Empty string = module
    # importable but no usable projector, so the project2d capability must not be claimed blind.
    payload["project2d_api"] = ""
    if payload["modules"].get("TechDraw"):
        try:
            import TechDraw

            if getattr(TechDraw, "projectEx", None) is not None:
                payload["project2d_api"] = "projectEx"
            elif getattr(TechDraw, "findShapeOutline", None) is not None:
                payload["project2d_api"] = "findShapeOutline"
        except Exception:
            payload["project2d_api"] = ""
    if payload["project2d_api"] != "projectEx":
        try:
            import Drawing

            if callable(getattr(Drawing, "projectEx", None)):
                payload["project2d_api"] = "Drawing.projectEx"
                payload["modules"]["TechDraw"] = True
        except ImportError:
            pass
    path = os.environ.get("CADCORE_PROBE_RESULT") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
    print(MARKER, json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload.get("ok") else 3


# NOTE (FreeCAD 0.19 quirk, measured 2026-10-07 on freecadcmd 0.19.2 + OCC 7.5.1): when this script
# lives in the subprocess cwd, FreeCAD imports it as a module instead of running it as __main__, so a
# __main__ guard never fires and the run exits rc=0 with no marker (leaving a __pycache__ behind).
# Call main() unconditionally; only a real __main__ maps the return code onto the process exit code.
_rc = main()
if __name__ == "__main__":
    sys.exit(_rc)
'''

_ADAPTER_SCRIPT: str = '''# cadcore kernel subproc adapter script (A8) -- generated by cadcore.kernel.subproc, do not edit
# contract in : CADCORE_OP_PARAMS -> job JSON (op / shape recipe / parameters)
# contract out: CADCORE_OP_RESULT -> result JSON (ok / payload)
# exit code   : 0 = ok, 3 = operation failed (result JSON still written), 2 = protocol error
import json
import math
import os
import sys
import traceback

SCHEMA = 1
MARKER = "cadcore-subproc-adapter"
NM_PER_MM = 1000000.0

import FreeCAD as App
import Part


def _paths():
    params = os.environ.get("CADCORE_OP_PARAMS") or (sys.argv[1] if len(sys.argv) > 1 else "")
    result = os.environ.get("CADCORE_OP_RESULT") or (sys.argv[2] if len(sys.argv) > 2 else "")
    return params, result


def _write(result_path, payload):
    tmp = result_path + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, result_path)


def _mm(value_nm):
    return float(value_nm) / NM_PER_MM


def _vector(values):
    nums = [_mm(item) for item in values]
    while len(nums) < 3:
        nums.append(0.0)
    return App.Vector(nums[0], nums[1], nums[2])


def _axis(name):
    table = {"x": App.Vector(1, 0, 0), "y": App.Vector(0, 1, 0), "z": App.Vector(0, 0, 1)}
    if str(name) not in table:
        raise ValueError("unsupported axis: %s" % name)
    return table[str(name)]


def _sketch_wire(spec):
    edges = []
    for entity in spec.get("entities", []):
        kind = str(entity.get("kind", ""))
        geom = entity.get("geom", {}) or {}
        if kind == "line":
            edges.append(Part.makeLine(_vector(geom["p1"]), _vector(geom["p2"])))
        elif kind == "polyline":
            edges.append(Part.makePolygon([_vector(item) for item in geom["points"]]))
        elif kind == "circle":
            edges.append(Part.makeCircle(_mm(geom["radius_nm"]), _vector(geom["center"]), App.Vector(0, 0, 1)))
        elif kind == "arc":
            circle = Part.Circle(_vector(geom["center"]), App.Vector(0, 0, 1), _mm(geom["radius_nm"]))
            arc = Part.Arc(circle, math.radians(float(geom["start_deg"])), math.radians(float(geom["end_deg"])))
            edges.append(arc.toShape())
        else:
            raise ValueError("unsupported sketch entity kind: %s" % kind)
    if not edges:
        raise ValueError("sketch has no entities")
    try:
        groups = Part.sortEdges(edges)
    except Exception:
        groups = [edges]
    wires = [Part.Wire(group) for group in groups if group]
    if not wires:
        raise ValueError("sketch produced no wire")
    shape = wires[0] if len(wires) == 1 else Part.makeCompound(wires)
    plane = spec.get("plane", "XY")
    if plane == "XZ":
        shape.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    elif plane == "YZ":
        shape.rotate(App.Vector(0, 0, 0), App.Vector(1, 1, 1), 120)
    return shape


def _loop_area(wire, face_api):
    """Area of a loop; 0.0 when the build cannot measure it (ranking only, never a result)."""
    if face_api is not None:
        try:
            return float(face_api(wire).Area)
        except Exception:
            pass
    try:
        return float(wire.Area)
    except Exception:
        return 0.0


def _profile_face(shape):
    """Wire or multi-loop compound -> Face: largest loop = outer, every other loop = hole.

    A sketch with holes must be a Face (outer + inner wires) before extruding, otherwise the
    result is a shell with no material removed. Already-faced input passes through unchanged.

    Kept world-agnostic on purpose: the test stub CAD module has no Faces/Wires/ShapeType and no
    Part.Face/Part.makeFace, so a shape it produced is passed through untouched (its old contract).
    Only a real build gets a face built, and multi-loop nesting always goes through
    FaceMakerBullseye -- Part.Face(list) treats every loop as OUTER (measured 2026-10-07: the
    plate's four d8 holes came back as +1206.37 mm^3 of extra material instead of holes).
    """
    if list(getattr(shape, "Faces", []) or []):
        return shape
    face_api = getattr(Part, "Face", None)
    maker = getattr(Part, "makeFace", None)
    if face_api is None and maker is None:
        return shape
    wires = list(getattr(shape, "Wires", []) or [])
    if not wires and str(getattr(shape, "ShapeType", "")) == "Wire":
        wires = [shape]
    if not wires:
        edges = list(getattr(shape, "Edges", []) or [])
        sorter = getattr(Part, "sortEdges", None)
        wire_api = getattr(Part, "Wire", None)
        if edges and sorter is not None and wire_api is not None:
            try:
                groups = sorter(edges)
            except Exception:
                groups = [edges]
            wires = [wire_api(group) for group in groups if group]
    if not wires:
        return shape
    if len(wires) == 1:
        if face_api is not None:
            return face_api(wires[0])
        return maker(wires, "Part::FaceMakerBullseye")
    ranked = sorted(wires, key=lambda item: _loop_area(item, face_api), reverse=True)
    if maker is not None:
        return maker(ranked, "Part::FaceMakerBullseye")
    return face_api(ranked)


def _build_recipe(recipe):
    shape = None
    for item in recipe:
        shape = _build_step(item, shape)
    if shape is None:
        raise ValueError("empty recipe")
    return shape


def _build_step(step, previous):
    op = str(step.get("op", ""))
    if op == "sketch":
        return _sketch_wire(step.get("sketch", {}))
    if op == "extrude":
        if previous is None:
            raise ValueError("extrude needs a previous profile")
        distance = _mm(step["distance_nm"])
        face = _profile_face(previous)
        plane = str(step.get("plane", "XY"))
        direction = {"XY": App.Vector(0, 0, 1), "XZ": App.Vector(0, -1, 0),
                     "YZ": App.Vector(1, 0, 0)}[plane]
        vector = App.Vector(direction.x * distance, direction.y * distance, direction.z * distance)
        if bool(step.get("symmetric")):
            solid = face.extrude(vector)
            solid.translate(App.Vector(-vector.x / 2.0, -vector.y / 2.0, -vector.z / 2.0))
            return solid
        return face.extrude(vector)
    if op == "revolve":
        if previous is None:
            raise ValueError("revolve needs a previous profile")
        return _profile_face(previous).revolve(App.Vector(0, 0, 0), _axis(step.get("axis", "z")), float(step.get("angle_deg", 360.0)))
    if op == "boolean":
        left = _build_recipe(step["a"]["recipe"])
        right = _build_recipe(step["b"]["recipe"])
        kind = str(step.get("op_kind", ""))
        if kind == "union":
            return left.fuse(right)
        if kind == "cut":
            return left.cut(right)
        if kind == "intersect":
            return left.common(right)
        raise ValueError("unsupported boolean op: %s" % kind)
    raise ValueError("unsupported build op: %s" % op)


def _take(shape, name, getter, missing):
    try:
        value = getter()
    except Exception:
        missing.append(name)
        return None
    if value is None:
        missing.append(name)
        return None
    return value


def _profile_area_nm2(recipe):
    """Outer contour area (nm^2) of the recipe's first sketch: the LARGEST loop's face.

    Same meaning as occt.py's profile_area_nm2 (the outer face, holes NOT subtracted), so the two
    backends can be compared loop-for-loop. None = not derivable here (no sketch step, or this
    build has no Part.Face to build a face with) -- the caller decides NA vs unmeasured.
    """
    for step in recipe or []:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op", ""))
        if op == "sketch":
            face_api = getattr(Part, "Face", None)
            if face_api is None:
                return None
            try:
                shape = _sketch_wire(step.get("sketch", {}))
                wires = list(getattr(shape, "Wires", []) or [])
                if not wires and str(getattr(shape, "ShapeType", "")) == "Wire":
                    wires = [shape]
                if not wires:
                    return None
                outer = max(wires, key=lambda item: float(face_api(item).Area))
                return int(round(float(face_api(outer).Area) * (NM_PER_MM ** 2)))
            except Exception:
                return None
        if op == "boolean":
            found = _profile_area_nm2((step.get("a") or {}).get("recipe") or [])
            if found is not None:
                return found
    return None


def _mass_center(shape):
    if hasattr(shape, "CenterOfMass"):
        return tuple(shape.CenterOfMass)[:3]
    solids = list(shape.Solids)
    if not solids:
        raise ValueError("shape has no measured mass center")
    volumes = [float(solid.Volume) for solid in solids]
    if any(not math.isfinite(value) or value <= 0 for value in volumes):
        raise ValueError("shape has invalid component masses")
    total = sum(volumes)
    centers = [tuple(solid.CenterOfMass)[:3] for solid in solids]
    return [sum(center[axis] * volume for center, volume in zip(centers, volumes)) / total
            for axis in range(3)]


def _mass_properties(shape, recipe=None):
    missing = []
    volume = _take(shape, "volume_nm3", lambda: int(round(float(shape.Volume) * (NM_PER_MM ** 3))), missing)
    area = _take(shape, "area_nm2", lambda: int(round(float(shape.Area) * (NM_PER_MM ** 2))), missing)
    center = _take(
        shape,
        "center_nm",
        lambda: [int(round(float(item) * NM_PER_MM)) for item in _mass_center(shape)],
        missing,
    )
    payload = {"source": "external", "unmeasured": sorted(set(missing))}
    if volume is not None:
        payload["volume_nm3"] = volume
    if area is not None:
        payload["area_nm2"] = area
    if center is not None:
        payload["center_nm"] = center
    box = getattr(shape, "BoundBox", None)
    if box is not None:
        payload["bbox_nm"] = [int(round(float(getattr(box, key)) * NM_PER_MM))
                              for key in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")]
    # profile_area_nm2 is the outer-contour face area (holes NOT subtracted), reported so a
    # cross-kernel gate can tell "area_nm2 is B-Rep surface area" from "profile area". Three
    # states, not two: a recipe WITH a sketch on a build that HAS Part.Face but failed to build
    # the face is UNMEASURED (goes into the ledger); a build with no Part.Face at all (the test
    # stub) cannot express the number, so it is NA -- absent AND not in the ledger.
    if recipe is not None:
        profile = _profile_area_nm2(recipe)
        if profile is not None:
            payload["profile_area_nm2"] = int(profile)
        elif getattr(Part, "Face", None) is not None and any(
            isinstance(step, dict) and str(step.get("op", "")) == "sketch" for step in recipe
        ):
            missing.append("profile_area_nm2")
            payload["unmeasured"] = sorted(set(missing))
    return payload


def _project_2d(shape, view, hidden):
    import TechDraw

    table = {
        "front": App.Vector(0, -1, 0),
        "top": App.Vector(0, 0, 1),
        "right": App.Vector(1, 0, 0),
        "iso": App.Vector(1, -1, 1),
    }
    if str(view) not in table:
        raise ValueError("unsupported view: %s" % view)
    direction = table[str(view)]
    curves = []
    if not list(getattr(shape, "Faces", []) or []) and getattr(shape, "ShapeType", ""):
        # A wire has no visibility classification. Project real edges onto the view basis.
        bases = {"front": ((1, 0, 0), (0, 0, 1)), "top": ((1, 0, 0), (0, 1, 0)),
                 "right": ((0, 1, 0), (0, 0, 1)),
                 "iso": ((2 ** -0.5, 2 ** -0.5, 0),
                         (-6 ** -0.5, 6 ** -0.5, 2 / (6 ** 0.5)))}
        u, v = bases[str(view)]
        for edge in shape.Edges:
            points = []
            for point in edge.discretize(16):
                xyz = tuple(point)[:3]
                flat = [int(round(sum(xyz[i] * basis[i] for i in range(3)) * NM_PER_MM))
                        for basis in (u, v)]
                if not points or points[-1] != flat:
                    points.append(flat)
            if len(points) >= 2:
                curves.append({"kind": "visible", "points_nm": points})
        return {"view": str(view), "hidden": False, "curves": curves,
                "source": "wire_projection", "hidden_measured": False}
    # FreeCAD 0.19 has no TechDraw.projectEx (measured 2026-10-07 on freecadcmd 0.19.2):
    # fall back to findShapeOutline(), which yields the VISIBLE outline only. Hidden lines are then
    # UNMEASURED, not zero -- report hidden_measured=False so nobody reads "0 hidden curves" as
    # "this part has no hidden edges".
    projector = getattr(TechDraw, "projectEx", None)
    if projector is not None:
        projected = projector(shape, direction)
        visible = projected[0] if len(projected) > 0 else None
        invisible = projected[1] if len(projected) > 1 else None
        hidden_measured = True
        source = "projectEx"
    else:
        try:
            import Drawing
        except ImportError:
            Drawing = None
        legacy = getattr(Drawing, "projectEx", None)
        if legacy is not None:
            # Legacy FreeCAD has ten HLR buckets: five visible, then five hidden.
            projected = legacy(shape, direction)
            if len(projected) != 10:
                raise ValueError("Drawing.projectEx returned an invalid HLR bucket count")
            visible = Part.makeCompound(list(projected[:5]))
            invisible = Part.makeCompound(list(projected[5:]))
            hidden_measured = True
            source = "Drawing.projectEx"
        else:
            visible = TechDraw.findShapeOutline(shape, 1.0, direction)
            invisible = None
            hidden_measured = False
            source = "findShapeOutline"
    for group, kind in ((visible, "visible"), (invisible, "hidden")):
        if group is None:
            continue
        if kind == "hidden" and not bool(hidden):
            continue
        for edge in getattr(group, "Edges", []):
            points = [[int(round(float(item) * NM_PER_MM)) for item in tuple(point)[:2]] for point in edge.discretize(16)]
            if source == "Drawing.projectEx" and str(view) in ("front", "right"):
                # Legacy HLR selects its own in-plane axes; normalize to width, height.
                points = [[point[1], -point[0]] for point in points]
            curves.append({"kind": kind, "points_nm": points})
    return {"view": str(view), "hidden": bool(hidden), "curves": curves, "source": source,
            "hidden_measured": hidden_measured}


def main():
    params_path, result_path = _paths()
    payload = {"ok": False, "schema": SCHEMA, "marker": MARKER, "op": "", "error": ""}
    if not params_path or not result_path:
        sys.stderr.write("missing CADCORE_OP_PARAMS / CADCORE_OP_RESULT" + chr(10))
        return 2
    try:
        with open(params_path, "r", encoding="utf-8") as handle:
            job = json.load(handle)
    except Exception as exc:
        sys.stderr.write("cannot read job parameters: %s" % exc)
        return 2
    payload["op"] = str(job.get("op", ""))
    try:
        op = payload["op"]
        if op == "export":
            fmt = str(job.get("fmt", ""))
            path = str(job.get("path", ""))
            shape = _build_recipe(job["shape"]["recipe"])
            if fmt == "step":
                shape.exportStep(path)
            elif fmt == "iges":
                shape.exportIges(path)
            elif fmt == "stl":
                shape.exportStl(path)
            else:
                raise ValueError("unsupported export format: %s" % fmt)
            payload["out_path"] = path
        elif op == "mass":
            payload["properties"] = _mass_properties(_build_recipe(job["shape"]["recipe"]), job["shape"]["recipe"])
        elif op == "mesh":
            shape = _build_recipe(job["shape"]["recipe"])
            if not shape.isValid() or not shape.Solids or shape.Volume <= 0:
                raise ValueError("mesh requires a valid positive-volume solid")
            vertices, triangles = shape.tessellate(float(job["deflection_mm"]))
            if not vertices or not triangles or len(triangles) > 250000:
                raise ValueError("empty or oversized mesh")
            payload["mesh"] = {"vertices_mm": [list(tuple(point)[:3]) for point in vertices],
                               "triangles": [list(triangle) for triangle in triangles],
                               "properties": _mass_properties(shape, job["shape"]["recipe"]),
                               "solid_count": len(shape.Solids)}
        elif op == "project2d":
            payload["projection"] = _project_2d(_build_recipe(job["shape"]["recipe"]), job.get("view", "front"), job.get("hidden"))
        else:
            raise ValueError("unsupported op: %s" % op)
        payload["ok"] = True
    except Exception as exc:
        payload["error"] = "%s: %s" % (type(exc).__name__, exc)
        payload["traceback_tail"] = traceback.format_exc()[-2000:]
    _write(result_path, payload)
    return 0 if payload["ok"] else 3


# NOTE (FreeCAD 0.19 quirk, measured 2026-10-07 on freecadcmd 0.19.2 + OCC 7.5.1): when this script
# lives in the subprocess cwd, FreeCAD imports it as a module instead of running it as __main__, so a
# __main__ guard never fires and the run exits rc=0 with no marker (leaving a __pycache__ behind).
# Call main() unconditionally; only a real __main__ maps the return code onto the process exit code.
_rc = main()
if __name__ == "__main__":
    sys.exit(_rc)
'''


# ── 语言包取文案（A16）；A16 缺席或键未登记 ⇒ 由 key 派生的兜底串，绝不返回空串 ────────────────
def _derive_text(key: str) -> str:
    tail = key.rsplit(".", 1)[-1] if key else ""
    text = tail.replace("_", " ").replace("-", " ").strip()
    return (text[:1].upper() + text[1:]) if text else "?"


def _t(key: str, **args: Any) -> str:
    from cadcore.i18n import catalog

    try:
        text = catalog.t(key, **args)
        if isinstance(text, str) and text.strip():
            return text
    except Exception as exc:  # pragma: no cover - 语言层自身出问题不许拖垮内核
        LOG.debug("i18n lookup failed for %r: %s", key, exc)
    text = _derive_text(key)
    if args:
        text = "%s [%s]" % (text, ", ".join("%s=%s" % (name, args[name]) for name in sorted(args)))
    return text


def _err_text(key: str, **args: Any) -> str:
    return _t(key, **args)


def _dependency_missing(message: str) -> DependencyMissing:
    """构造显式降级信号。

    A3（`cadcore/errors.py`）是**兄弟席位**的文件：它落盘后本模块会自动改用真件，但真件的构造签名
    未必是"只吃一个消息"。所以这里按 位置参数 → 关键字 `name` → 绕过 `__init__` 三级兜底，
    保证"无 step 能力 ⇒ DependencyMissing"这条契约在 A3 换实现后**仍然成立**（不许退化成 TypeError）。
    """
    for attempt in (lambda: DependencyMissing(message), lambda: DependencyMissing(name=message)):
        try:
            return attempt()
        except TypeError:  # pragma: no cover - 只有 A3 用了别的构造签名才会走到
            continue
    exc = DependencyMissing.__new__(DependencyMissing)  # pragma: no cover - 兜底
    Exception.__init__(exc, message)
    return exc


# ── 错误族（都挂在 A3 的 CadError 下；`DependencyMissing` 表示"显式降级"，不是失败）──────────────
class SubprocError(CadError):
    """本后端的基类错误：参数/协议层面的问题（重放配方坏了、产物校验不通过…）。"""

    def __init__(self, message: str, *, reason: str = "", **details: Any) -> None:
        super().__init__(message)
        self.reason = reason or REASON_OK
        self.details = details


class KernelExecutionError(SubprocError):
    """外部程序**跑了但没成功**：超时 / 非 0 退出 / 结果缺失 / 算子失败。一律硬失败，不许静默降级成空图。"""


# ── 结果载体（A6 的协议由兄弟模块定义；这里给本后端的**具体**载体，字段与 L2 契约对齐）──────────
def _recipe_depth_nm(recipe: Any) -> int:
    """配方里**最后一个** `op=="extrude"` 步的 `distance_nm`；顶层没有就往 `boolean` 的 `a` 分支下钻。

    "最后一个"而不是"第一个"：`sketch → extrude → …` 之后再叠拉伸时，决定实体厚度的是最后那一步。
    找不到 ⇒ `0`，含义是"没有拉伸 / 未采"，**不是**"深度为零"（上游据此判断该不该期待一个实体）。
    """
    depth = 0
    for step in recipe or ():
        if not isinstance(step, Mapping):
            continue
        op = str(step.get("op", ""))
        if op == "extrude":
            try:
                depth = int(step.get("distance_nm", 0) or 0)
            except (TypeError, ValueError):
                depth = 0
        elif op == "boolean" and not depth:
            depth = _recipe_depth_nm((step.get("a") or {}).get("recipe") or ())
    return depth


@dataclass(frozen=True)
class SubprocessShape:
    """进程外形状引用：不是句柄，而是一串**可重放的算子配方**（见模块 docstring 第 3 条）。

    配方格式（跨进程的**唯一数据契约**，生成脚本必须照此重放）：
    `{"recipe": [step, ...]}`，`step = {"op": "sketch"|"extrude"|"revolve"|"boolean", ...}`。
    **线性语义**：`sketch` 起手，其后每一步默认消费**上一步的结果**；只有 `boolean` 例外 ——
    它自带 `a` / `b` 两棵子配方，显式指名两个操作数（配方因此是一棵可 JSON 化的树，而不是句柄链）。

    **协议对齐面**（`depth_nm` / `kind` / `curve_count`）是给跨内核判定门用的只读派生属性：
    `nano.Shape` 有它们，真适配器若一个都没有，同一张门就会把真内核读成 `depth_nm=0` 而假红。
    全部从 `self.recipe` 现算 —— frozen dataclass，不原地改任何字段。
    """

    recipe: tuple[Mapping[str, Any], ...] = ()
    kernel: str = KERNEL_NAME

    @property
    def depth_nm(self) -> int:
        """最后一步拉伸的深度（nm）；没有拉伸 ⇒ `0`（未采，不是"深度为零"）。"""
        return _recipe_depth_nm(self.recipe)

    @property
    def symmetric(self) -> bool:
        """最后一次拉伸是否对称（同 `nano.Shape.symmetric`）；没有 extrude 步 ⇒ False。"""
        symmetric = False
        for step in self.recipe:
            if isinstance(step, Mapping) and str(step.get("op", "")) == "extrude":
                symmetric = bool(step.get("symmetric", False))
        return symmetric

    @property
    def kind(self) -> str:
        """配方末尾定位到的算子决定形态：`extrude`/`revolve`/`boolean` ⇒ `"solid"`，只有 `sketch` ⇒ `"wire"`。

        空配方（或认不出的算子）⇒ `""`：**不猜成 solid** —— 猜错会让判定门以为拿到了实体。
        """
        for step in reversed(self.recipe):
            if not isinstance(step, Mapping):
                continue
            op = str(step.get("op", ""))
            if op in ("extrude", "revolve", "boolean"):
                return "solid"
            if op == "sketch":
                return "wire"
        return ""

    def curve_count(self) -> int:
        """恒返回 `-1`＝**未采**：进程外形状不持有几何，曲线数只有重放后在结果 JSON 里才拿得到。

        不编一个数：编出来的曲线数会被判定门当成真几何读走。要曲线数请走
        `project_to_2d()` 的 `Projection.curves` / `mass_properties()` 的实测字段。
        """
        return UNMEASURED

    def to_dict(self) -> dict[str, Any]:
        return {"kernel": self.kernel, "recipe": [dict(step) for step in self.recipe]}

    def __len__(self) -> int:
        return len(self.recipe)


@dataclass(frozen=True)
class MassProperties:
    """质量属性（长度单位一律整数纳米）；采不到的字段是 `-1`=未采并列进 `unmeasured`，不是 0。

    `profile_area_nm2` 与 `occt.py` 的 `OcctMassProperties` **同名同义**：外轮廓面（孔**不**减掉），
    用来跟 `nano` 的轮廓级面积对拍；`area_nm2` 是 B-Rep 真表面积（含孔），两者别混着比 ——
    `volume_nm3 == profile_area_nm2 × depth_nm` 才是该成立的那条恒等式。
    """

    volume_nm3: int = UNMEASURED
    area_nm2: int = UNMEASURED
    center_nm: tuple[int, int, int] | None = None
    source: str = "external"
    unmeasured: tuple[str, ...] = ()
    density_kg_m3: Any = NA
    profile_area_nm2: int = UNMEASURED
    bbox_nm: tuple[int, ...] | None = None
    # 本后端不猜密度（不适用）；要质量请上游显式给密度再乘，避免"默认钢密度"这类静默假设。

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_nm3": self.volume_nm3,
            "area_nm2": self.area_nm2,
            "profile_area_nm2": self.profile_area_nm2,
            "center_nm": list(self.center_nm) if self.center_nm is not None else None,
            "bbox_nm": list(self.bbox_nm) if self.bbox_nm is not None else None,
            "density_kg_m3": self.density_kg_m3,
            "source": self.source,
            "unmeasured": list(self.unmeasured),
        }


@dataclass(frozen=True)
class Projection:
    """2D 投影结果（HLR）：折线点串一律整数纳米；`curves` 为空视为失败，见 `_require_curves`。

    `hidden_measured` 是本后端的三态记账：`True` = 外部工具真的分出了可见/虚隐两组（`projectEx` 路径）；
    `False` = **虚隐线未采**（FreeCAD 0.19 只有 `findShapeOutline`，只能给可见轮廓）。
    此时 `hidden_curves == 0` 是"没测到"，**不是**"这个视图没有虚隐边"。
    """

    view: str
    hidden: bool
    curves: tuple[Mapping[str, Any], ...] = ()
    source: str = "external"
    hidden_measured: bool = True

    @property
    def visible_curves(self) -> int:
        return sum(1 for curve in self.curves if curve.get("kind") == "visible")

    @property
    def hidden_curves(self) -> int:
        return sum(1 for curve in self.curves if curve.get("kind") == "hidden")


@dataclass(frozen=True)
class ProbeResult:
    """一次探测的**全部**读数（可机读、可复跑）。三态：`rc=-1` 或 `capabilities_measured=False` 都是**未采**。"""

    available: bool
    reason: str
    executable: str | None = None
    profile: str = KERNEL_NAME
    version: str | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    capabilities_measured: bool = False
    capabilities_source: str = "declared"
    rc: int = UNMEASURED
    rc_measured: bool = False
    timed_out: bool = False
    marker_seen: bool = False
    elapsed_ms: int = UNMEASURED
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "executable": self.executable,
            "profile": self.profile,
            "version": self.version if self.version is not None else NA,
            "modules": dict(self.modules),
            "capabilities": sorted(self.capabilities),
            "capabilities_measured": self.capabilities_measured,
            "capabilities_source": self.capabilities_source,
            "rc": self.rc if self.rc_measured else UNMEASURED,
            "timed_out": self.timed_out,
            "marker_seen": self.marker_seen,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SubprocProfile:
    """一个外部 CLI 的接线：可执行文件名候选 + 两个脚本模板 + 环境变量前缀。"""

    name: str
    executable_names: tuple[str, ...]
    env_var: str
    probe_script: str
    adapter_script: str


# 外部 CLI 档案表。加一个后端 = 加一条档案（可执行名 + 探测脚本 + 适配脚本），不改调用方。
PROFILES: Mapping[str, SubprocProfile] = {
    KERNEL_NAME: SubprocProfile(
        name=KERNEL_NAME,
        executable_names=("FreeCADCmd", "freecadcmd", "FreeCADCmd.exe", "freecad-cmd"),
        env_var=ENV_EXECUTABLE,
        probe_script=_PROBE_SCRIPT,
        adapter_script=_ADAPTER_SCRIPT,
    )
}


def profiles() -> tuple[str, ...]:
    """已登记的外部 CLI 档案名（判定行取证用）。"""
    return tuple(sorted(PROFILES))


def get_profile(profile: str | None = None) -> SubprocProfile:
    name = str(profile or KERNEL_NAME)
    try:
        return PROFILES[name]
    except KeyError:
        raise SubprocError(
            _err_text("err.not_implemented", feature="subproc-profile:%s" % name),
            reason=REASON_UNKNOWN_PROFILE,
            profile=name,
        ) from None


# ── 进程原语：短超时 + 杀整棵进程树（外部工具常 fork 子进程，杀父不杀子会漏进程）────────────────
@dataclass(frozen=True)
class RunOutcome:
    rc: int = UNMEASURED
    rc_measured: bool = False
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    launch_error: str = ""
    elapsed_ms: int = UNMEASURED


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000.0))


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """杀掉整个进程组（POSIX）或进程（其它平台）；放弃收割也不许阻塞调用方。"""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - 非 POSIX 分支本机不验证
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:  # pragma: no cover - 已经没了
            pass


def _run(
    cmd: Sequence[str], *, timeout_s: float, cwd: str | None = None, env: Mapping[str, str] | None = None
) -> RunOutcome:
    """起外部进程并**在 timeout_s 内收口**：超时就杀进程组，绝不让调用方挂住。"""
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # pragma: no cover - 非 POSIX 分支本机不验证
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    try:
        proc = subprocess.Popen(list(cmd), **kwargs)  # type: ignore[arg-type]
    except (OSError, ValueError) as exc:
        return RunOutcome(launch_error="%s: %s" % (type(exc).__name__, exc), elapsed_ms=_elapsed_ms(started))
    timed_out = False
    try:
        out, err = proc.communicate(timeout=max(MIN_TIMEOUT_S, float(timeout_s)))
    except subprocess.TimeoutExpired:
        timed_out = True
        out, err = "", ""
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=KILL_GRACE_S)
        except (subprocess.TimeoutExpired, ValueError):  # pragma: no cover - SIGKILL 后仍不收口
            out, err = "", ""
    rc = proc.returncode if proc.returncode is not None else UNMEASURED
    return RunOutcome(
        rc=rc,
        rc_measured=proc.returncode is not None and not timed_out,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        elapsed_ms=_elapsed_ms(started),
    )


def _tail(text: str, limit: int = 400) -> str:
    clean = " ".join((text or "").split())
    return clean[-limit:]


# ── 可执行文件定位（显式入参 > 环境变量 > PATH 候选名）────────────────────────────────
def find_executable(explicit: str | os.PathLike[str] | None = None, *, profile: str | None = None) -> str | None:
    """定位外部 CLI；找不到返回 `None`（**不是**空串、**不抛**）。"""
    prof = get_profile(profile)
    candidates: list[str] = []
    if explicit:
        candidates.append(str(explicit))
    env_value = os.environ.get(prof.env_var, "").strip()
    if env_value:
        candidates.append(env_value)
    candidates.extend(prof.executable_names)
    for candidate in candidates:
        if not candidate:
            continue
        if os.sep in candidate or (os.altsep and os.altsep in candidate):
            path = Path(candidate).expanduser()
            if path.is_file() and (os.name == "nt" or os.access(str(path), os.X_OK)):
                return str(path)
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


# ── 探测：一次短超时子进程，三条同时成立才算可用 ────────────────────────────────────────
_PROBE_CACHE: dict[tuple[str, str, float], ProbeResult] = {}
_PROBE_LOCK = threading.Lock()


def reset_cache() -> None:
    """清空探测缓存（测试与"装了新工具后重探"用；判定行复跑前应清）。"""
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()


def _resolve_timeout(value: float | None, env_name: str, default: float, cap: float) -> tuple[float, str]:
    """解析超时：入参 > 环境变量 > 缺省，再夹到 [MIN_TIMEOUT_S, cap]；非法环境变量留痕不静默。"""
    note = ""
    raw: Any = value
    if raw is None:
        text = os.environ.get(env_name, "").strip()
        if text:
            try:
                raw = float(text)
            except ValueError:
                note = "%s=%r ignored" % (env_name, text)
                raw = None
    if raw is None:
        raw = default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        note = (note + "; " if note else "") + "non-numeric timeout %r ignored" % (raw,)
        number = default
    if number <= 0:
        note = (note + "; " if note else "") + "non-positive timeout %r ignored" % (number,)
        number = default
    return max(MIN_TIMEOUT_S, min(cap, number)), note


def _parse_probe_payload(text: str) -> tuple[dict[str, Any] | None, bool]:
    """从 stdout 里取探测标记后那一段 JSON；返回 (payload, marker_seen)。"""
    marker_seen = False
    for line in (text or "").splitlines():
        index = line.find(PROBE_MARKER)
        if index < 0:
            continue
        marker_seen = True
        blob = line[index + len(PROBE_MARKER) :].strip()
        if not blob:
            continue
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload, True
    return None, marker_seen


def _capabilities_from_modules(modules: Mapping[str, bool], project2d_api: str | None = None) -> frozenset[str]:
    """模块在场 → 能力。`project2d` 另需 `TechDraw` **真有投影入口**，不是 import 成功就算。

    2026-10-07 本机实测（freecadcmd 0.19.2 + OCC 7.5.1）：`import TechDraw` 成功，但模块里**没有**
    `projectEx`（0.20+ 才有），只有 `findShapeOutline` —— 只按"模块在不在"声称能力，会得到一个
    一调就崩的 `project2d`（实测 6/6 视图 `AttributeError`）。所以探测载荷带 `project2d_api`
    （`projectEx` | `findShapeOutline` | 空串）时以它为准；空串＝模块在但一个入口都没有 ⇒ 不声称。

    `project2d_api=None` ＝载荷里没有这个字段（老假 CLI / 老载荷）：维持"模块在场即声称"的旧口径，
    不因为载荷升级把既有测试判红。
    """
    caps: set[str] = set()
    if modules.get("Part"):
        caps |= PART_CAPABILITIES
    if modules.get("TechDraw") and (project2d_api is None or project2d_api):
        caps |= TECHDRAW_CAPABILITIES
    return frozenset(caps & ALL_CAPABILITIES)


def probe(
    executable: str | os.PathLike[str] | None = None,
    *,
    profile: str | None = None,
    timeout_s: float | None = None,
    force: bool = False,
) -> ProbeResult:
    """探测外部 CLI 是否可用（**短超时、绝不抛异常**）。结果按 (档案, 可执行路径, 超时) 缓存。

    判据三条同时成立才算 `available=True`：① 找到可执行文件 ② 探测脚本退出码 0 ③ stdout 回显机读标记。
    少任何一条 ⇒ `False` + 机读原因码；**"装了个同名程序但没跑我们的脚本"必须判不可用**。
    """
    prof = get_profile(profile)
    if os.environ.get(ENV_DISABLE, "").strip() not in ("", "0", "false", "no"):
        return ProbeResult(available=False, reason=REASON_DISABLED, profile=prof.name)
    resolved_timeout, note = _resolve_timeout(timeout_s, ENV_PROBE_TIMEOUT, DEFAULT_PROBE_TIMEOUT_S, PROBE_TIMEOUT_CAP_S)
    exe = find_executable(executable, profile=prof.name)
    if exe is None:
        return ProbeResult(
            available=False,
            reason=REASON_NOT_FOUND,
            profile=prof.name,
            detail=note,
        )
    key = (prof.name, os.path.abspath(exe), resolved_timeout)
    if not force:
        with _PROBE_LOCK:
            cached = _PROBE_CACHE.get(key)
        if cached is not None:
            return cached
    result = _run_probe(exe, prof=prof, timeout_s=resolved_timeout, note=note)
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = result
    return result


def _run_probe(exe: str, *, prof: SubprocProfile, timeout_s: float, note: str = "") -> ProbeResult:
    work = tempfile.mkdtemp(prefix="cadcore-probe-")
    try:
        # 脚本放进 work/scripts/，cwd 留在 work：**脚本所在目录 ≠ 子进程 cwd**。
        # 为什么（2026-10-07 本机实撞，freecadcmd 0.19.2 + OCC 7.5.1）：两者相同时 FreeCAD 把脚本当模块
        # import，`__name__ != "__main__"` ⇒ 模板里的 `sys.exit(_rc)` 永不执行 ⇒ **失败也算 rc=0**，
        # 退出码这条契约整个失效（只剩 result JSON 能兜）。错开一层后它是真 `__main__`，失败算子 rc≠0。
        scripts_dir = Path(work) / "scripts"
        scripts_dir.mkdir()
        script_path = scripts_dir / "probe.py"
        script_path.write_text(prof.probe_script, encoding="utf-8")
        result_path = Path(work) / "probe.json"
        env = dict(os.environ)
        env[ENV_PROBE_RESULT] = str(result_path)
        outcome = _run([exe, str(script_path)], timeout_s=timeout_s, cwd=work, env=env)
        extra = note
        if outcome.launch_error:
            return ProbeResult(
                available=False,
                reason=REASON_LAUNCH_FAILED,
                executable=exe,
                profile=prof.name,
                rc=UNMEASURED,
                elapsed_ms=outcome.elapsed_ms,
                detail="; ".join(item for item in (outcome.launch_error, extra) if item),
            )
        if outcome.timed_out:
            return ProbeResult(
                available=False,
                reason=REASON_TIMEOUT,
                executable=exe,
                profile=prof.name,
                rc=UNMEASURED,
                timed_out=True,
                elapsed_ms=outcome.elapsed_ms,
                detail="; ".join(item for item in ("probe exceeded %.3gs" % timeout_s, extra) if item),
            )
        payload, marker_seen = _parse_probe_payload(outcome.stdout)
        if payload is None:
            payload = _read_json(result_path)
            if payload is not None and payload.get("marker") == PROBE_MARKER:
                marker_seen = True
        if payload is None or not marker_seen:
            reason = REASON_NONZERO_EXIT if outcome.rc != 0 else REASON_MARKER_MISSING
            return ProbeResult(
                available=False,
                reason=reason,
                executable=exe,
                profile=prof.name,
                rc=outcome.rc,
                rc_measured=outcome.rc_measured,
                marker_seen=marker_seen,
                elapsed_ms=outcome.elapsed_ms,
                detail="; ".join(
                    item
                    for item in (
                        "rc=%s" % outcome.rc,
                        "marker_seen=%s" % marker_seen,
                        _tail(outcome.stderr),
                        extra,
                    )
                    if item
                ),
            )
        modules = payload.get("modules")
        measured = isinstance(modules, Mapping)
        api = payload.get("project2d_api")
        version = payload.get("version")
        return ProbeResult(
            available=payload.get("ok") is True,
            reason=REASON_OK if payload.get("ok") is True else REASON_PROBE_FAILED,
            executable=exe,
            profile=prof.name,
            version=str(version) if isinstance(version, str) and version else None,
            modules=dict(modules) if measured else {},
            capabilities=(
                _capabilities_from_modules(modules, str(api) if isinstance(api, str) else None)
                if measured
                else frozenset()
            ),
            capabilities_measured=measured,
            capabilities_source="modules" if measured else "declared",
            rc=outcome.rc,
            rc_measured=outcome.rc_measured,
            marker_seen=True,
            elapsed_ms=outcome.elapsed_ms,
            detail="; ".join(
                item for item in (_tail(payload.get("error", "")), extra) if item
            ),
        )
    finally:
        if os.environ.get(ENV_KEEP_WORK, "").strip() in ("", "0", "false", "no"):
            shutil.rmtree(work, ignore_errors=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ── 调用方 API：内核适配器 ───────────────────────────────────────────────────────────
class SubprocessKernel:
    """进程外后端（默认档案 `freecad`）。`KernelAdapter` 协议**结构**实现：不继承，只对齐方法名与语义。"""

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] | None = None,
        profile: str | None = None,
        probe_timeout_s: float | None = None,
        op_timeout_s: float | None = None,
        work_dir: str | os.PathLike[str] | None = None,
        keep_work_dir: bool | None = None,
        extra_env: Mapping[str, str] | None = None,
        refresh_probe: bool = False,
    ) -> None:
        prof = get_profile(profile)
        from dataclasses import replace
        from .features_registry import compose_adapter

        prof = replace(prof, adapter_script=compose_adapter(prof.adapter_script))
        self.name: str = prof.name
        self._profile = prof
        self._executable = str(executable) if executable else None
        self._probe_timeout_s = probe_timeout_s
        self.op_timeout_s, self._op_timeout_note = _resolve_timeout(
            op_timeout_s, ENV_OP_TIMEOUT, DEFAULT_OP_TIMEOUT_S, OP_TIMEOUT_CAP_S
        )
        self._work_dir = str(work_dir) if work_dir else None
        self._owns_work_dir = work_dir is None
        if keep_work_dir is None:
            keep_work_dir = os.environ.get(ENV_KEEP_WORK, "").strip() not in ("", "0", "false", "no")
        self._keep_work_dir = bool(keep_work_dir)
        self._extra_env = dict(extra_env or {})
        self._refresh_probe = bool(refresh_probe)
        self._probe_result: ProbeResult | None = None
        self._lock = threading.Lock()
        self._stats: dict[str, Any] = {
            "runs": 0,
            "failures": 0,
            "timeouts": 0,
            "artifacts_missing": 0,
            "last_rc": UNMEASURED,
            "last_reason": "",
        }

    # —— 探测与能力 —————————————————───────────────────────────────────────────────
    def probe_report(self, *, force: bool = False) -> ProbeResult:
        """本实例的探测读数（缓存；`force=True` 重探）。**任何情况下不抛异常**。"""
        with self._lock:
            if self._probe_result is not None and not force and not self._refresh_probe:
                return self._probe_result
            self._refresh_probe = False
        try:
            result = probe(self._executable, profile=self._profile.name, timeout_s=self._probe_timeout_s, force=force)
        except SubprocError:
            raise
        except Exception as exc:  # pragma: no cover - 探测层自身的意外不许冒泡给调用方
            result = ProbeResult(available=False, reason=REASON_LAUNCH_FAILED, profile=self._profile.name, detail=str(exc))
        with self._lock:
            self._probe_result = result
        return result

    def available(self) -> bool:
        """外部程序是否**真的**可用（找不到/超时/没回显标记 ⇒ False）。不抛异常、不长时间阻塞。"""
        return bool(self.probe_report().available)

    def capabilities(self) -> frozenset[str]:
        """实测到的能力子集；不可用 ⇒ 空集；**没实测的能力不声称**（见 `PROBE_ONLY_CAPABILITIES`）。"""
        report = self.probe_report()
        if not report.available:
            return frozenset()
        if report.capabilities_measured:
            return frozenset(report.capabilities & ALL_CAPABILITIES)
        return frozenset(DECLARED_CAPABILITIES - PROBE_ONLY_CAPABILITIES)

    @property
    def version(self) -> str:
        """外部程序版本；未采到返回空串（`probe_report()['version'] == 'NA'` 表示未采，不是"版本是空"）。"""
        report = self.probe_report()
        return report.version or ""

    def stats(self) -> dict[str, Any]:
        """观测面（可写进判定行）。**没探过就是未采**：`available=None`、`probe_rc=-1`，绝不当 0/False 报。"""
        report = self._probe_result
        measured_caps = self.capabilities() if report is not None else frozenset()
        return {
            "kernel": self.name,
            "profile": self._profile.name,
            "available": None if report is None else bool(report.available),
            "probe_rc": UNMEASURED if report is None or not report.rc_measured else report.rc,
            "probe_reason": "" if report is None else report.reason,
            "probe_elapsed_ms": UNMEASURED if report is None else report.elapsed_ms,
            "probe_timed_out": False if report is None else bool(report.timed_out),
            "capabilities": sorted(measured_caps),
            "capabilities_measured": False if report is None else bool(report.capabilities_measured),
            "capabilities_source": "unmeasured" if report is None else report.capabilities_source,
            "executable": None if report is None else report.executable,
            "op_timeout_s": self.op_timeout_s,
            "runs": self._stats["runs"],
            "failures": self._stats["failures"],
            "timeouts": self._stats["timeouts"],
            "artifacts_missing": self._stats["artifacts_missing"],
            "last_rc": self._stats["last_rc"],
            "last_reason": self._stats["last_reason"],
        }

    # —— 算子 —————————————————───────────────────────────────────────────────────
    def sketch_to_wire(self, sketch: Any) -> SubprocessShape:
        """草图 → 线框（进程外重放）。接受 L1 映射或带 `entities` 的对象；未知实体类型**报错**不跳过。"""
        self._require("sketch")
        return SubprocessShape(recipe=({"op": "sketch", "sketch": _normalize_sketch(sketch)},), kernel=self.name)

    def extrude(self, profile: Any, *, distance_nm: int, symmetric: bool = False) -> SubprocessShape:
        """拉伸：`distance_nm` 为整数纳米（单位换算只在生成脚本里做）。"""
        self._require("extrude")
        if (isinstance(distance_nm, bool) or not isinstance(distance_nm, int)
                or distance_nm <= 0 or distance_nm > 2**53):
            raise SubprocError("extrusion depth must be a positive integer in nm",
                               reason="invalid_extrusion_depth")
        if not isinstance(symmetric, bool):
            raise SubprocError("symmetric must be boolean", reason="bad_feature_params")
        source = self._shape(profile)
        plane = next((item["sketch"].get("plane", "XY") for item in source.recipe
                      if item.get("op") == "sketch"), "XY")
        step = {"op": "extrude", "distance_nm": int(distance_nm), "symmetric": bool(symmetric),
                "plane": plane}
        return self._extend(profile, step)

    def shape_from_document(self, document: Any) -> SubprocessShape:
        """L1 文档 → 实体（与 `nano.shape_from_document` **同口径**，让真内核能当 nano 的替身）。

        按未抑制特征的顺序重放，并按引用取草图；未知特征显式拒绝，不能只显示首个实体。
        缺席一律**显式**报 `DependencyMissing`（`no_solid_feature` / `empty_document`），
        绝不静默返回空实体 —— 与 `nano` 同一套 reason 码，G10 双基座对拍才能逐项对齐。
        """
        if not isinstance(document, Mapping):
            raise SubprocError(_err_text("err.geometry_invalid", detail="document must be a mapping"),
                               reason=REASON_OP_FAILED)
        sketches = document.get("sketches", [])
        features = document.get("features", [])
        if not isinstance(sketches, list) or not isinstance(features, list):
            raise SubprocError("sketches and features must be lists", reason="bad_features_table")
        if not sketches and not any(isinstance(item, Mapping) and item.get("kind") == "import_step"
                                    for item in features):
            raise DependencyMissing("document has no sketch", reason="empty_document",
                                    capability="extrude", kernel=self.name)
        from .features_registry import validate_document
        try:
            validate_document(document)
        except CadError as exc:
            raise SubprocError(str(exc), reason=exc.reason) from exc
        shape = None
        for feature in (document.get("features") or []):
            if not isinstance(feature, Mapping):
                raise SubprocError("feature must be a mapping", reason="bad_features_table")
            suppressed = feature.get("suppressed", False)
            if not isinstance(suppressed, bool):
                raise SubprocError("suppressed must be boolean", reason="bad_feature_params")
            kind = str(feature.get("kind", ""))
            params = feature.get("params", {})
            from .features_registry import KINDS, feature_step, validate_parameters

            try:
                validate_parameters(kind, params)
            except CadError as exc:
                raise SubprocError(str(exc), reason=exc.reason) from exc

            if kind in KINDS:
                resolved = dict(params)
                if kind == "sweep" and "sketch_uuid" not in resolved:
                    resolved["sketch_uuid"] = feature.get("sketch_uuid")
                # Suppression skips construction, never the stored value contract.
                try:
                    step = feature_step(kind, resolved, object() if suppressed else shape, document, self)
                except CadError as exc:
                    raise SubprocError(str(exc), reason=exc.reason) from exc
                if suppressed:
                    continue
                if kind in ("sweep", "loft", "import_step"):
                    operand = SubprocessShape(recipe=(step,), kernel=self.name)
                    mode = params.get("op", "new")
                    if shape is None:
                        if mode not in ("new", "add", "union", "join"):
                            raise SubprocError("cut requires a prior solid", reason="missing_base_solid")
                        shape = operand
                    elif mode in ("cut", "pocket"):
                        shape = self.boolean("cut", shape, operand)
                    elif mode == "intersect":
                        shape = self.boolean("intersect", shape, operand)
                    elif mode in ("new", "add", "union", "join"):
                        shape = self.boolean("union", shape, operand)
                    else:
                        raise SubprocError("unsupported feature operation", reason="unsupported_solid_feature")
                else:
                    shape = self._extend(shape, step)
                continue
            if kind not in ("extrude", "revolve") or params.get("thin") or params.get("contours"):
                raise SubprocError("unsupported solid feature: %s" % kind,
                                   reason="unsupported_solid_feature", feature=kind)
            if (params.get("end_condition", "blind") != "blind" or params.get("through_all")
                    or params.get("flip") or params.get("merge") is False):
                raise SubprocError("unsupported solid feature parameters",
                                   reason="unsupported_solid_feature", feature=kind)
            reference = feature.get("sketch_uuid")
            if reference is not None and not isinstance(reference, str):
                raise SubprocError("sketch reference must be a string", reason="unknown_sketch")
            if not reference:
                inputs = feature.get("inputs", [])
                if not isinstance(inputs, list) or any(not isinstance(ref, str) for ref in inputs):
                    raise SubprocError("feature inputs must be string references", reason="unknown_sketch")
                reference = next((ref for ref in inputs
                                  if any(item.get("uuid") == ref for item in sketches)), None)
            sketch = next((item for item in sketches if item.get("uuid") == reference), None) if reference else sketches[0]
            if sketch is None:
                raise SubprocError("feature refers to missing sketch", reason="unknown_sketch")
            wire = self.sketch_to_wire(sketch)
            if kind == "extrude":
                depth = params.get("distance_nm", 0)
                operand = self.extrude(wire, distance_nm=depth, symmetric=params.get("symmetric", False))
            else:
                operand = self.revolve(wire, axis=params.get("axis", "z"),
                                       angle_deg=params.get("angle_deg", 360.0))
            if suppressed:
                continue
            mode = params.get("op", "new")
            if shape is None:
                if mode not in ("new", "add", "union", "join"):
                    raise SubprocError("cut requires a prior solid", reason="missing_base_solid")
                shape = operand
            elif mode in ("cut", "pocket"):
                shape = self.boolean("cut", shape, operand)
            elif mode in ("new", "add", "union", "join"):
                shape = self.boolean("union", shape, operand)
            else:
                raise SubprocError("unsupported feature operation", reason="unsupported_solid_feature")
        if shape is None:
            raise DependencyMissing("document has no extrude feature", reason="no_solid_feature",
                                    capability="extrude", kernel=self.name,
                                    hint="add a feature of kind=extrude with params.distance_nm>0")
        # 这里**不再**写 `shape.meta.update(...)`：`SubprocessShape` 只有 recipe/kernel 两字段，
        # 没有 meta ⇒ 每次调用 AttributeError（A 席能力门 e2e.kernel_ran 当场判红，2026-10-07）。
        # 配方自身已带得下全部信息（sketch + distance_nm + symmetric），要追溯就查配方，不另立字段。
        return shape

    def revolve(self, profile: Any, *, axis: str, angle_deg: float) -> SubprocessShape:
        """旋转：`axis` 取 `x|y|z`（非法值在生成脚本里硬失败，不静默取默认轴）。"""
        self._require("revolve")
        import math

        if (axis not in ("x", "y", "z") or isinstance(angle_deg, bool)
                or not isinstance(angle_deg, (int, float)) or not math.isfinite(angle_deg)
                or not 0 < angle_deg <= 360):
            raise SubprocError("revolve requires x/y/z and a finite angle in (0,360]",
                               reason="invalid_revolve_parameters")
        step = {"op": "revolve", "axis": str(axis), "angle_deg": float(angle_deg)}
        return self._extend(profile, step)

    def tessellate(self, shape: Any, *, deflection_mm: float = 0.1) -> dict[str, Any]:
        """Measure a real B-Rep mesh; no bounding-box substitute."""
        import math

        if (isinstance(deflection_mm, bool) or not math.isfinite(deflection_mm)
                or not 0.01 <= deflection_mm <= 2):
            raise SubprocError("invalid mesh deflection", reason="bad_mesh_deflection")
        self._require("mass")
        payload = self._run_job({"schema": SCHEMA, "op": "mesh",
                                "deflection_mm": float(deflection_mm), "shape": _wrap(self._shape(shape))})
        mesh = payload.get("mesh")
        if not isinstance(mesh, dict) or not mesh.get("triangles") or not mesh.get("vertices_mm"):
            raise KernelExecutionError("missing real mesh", reason=REASON_RESULT_MISSING)
        return mesh

    def boolean(self, op: str, a: Any, b: Any) -> SubprocessShape:
        """布尔：配方里同时嵌 a/b 两棵子树（无状态重放的关键）。"""
        self._require("boolean")
        kind = str(op)
        if kind not in ("union", "cut", "intersect"):
            raise SubprocError(_err_text("err.not_implemented", feature="boolean:%s" % kind), reason=REASON_OP_FAILED)
        step = {"op": "boolean", "op_kind": kind, "a": _wrap(self._shape(a)), "b": _wrap(self._shape(b))}
        return SubprocessShape(recipe=(step,), kernel=self.name)

    def project_to_2d(self, shape: Any, *, view: str, hidden: bool) -> Projection:
        """HLR 投影。空结果 ⇒ **报错**（空图是缺陷，不是"没有可见边"）。"""
        self._require("project2d")
        target = self._shape(shape)
        if target.kind == "wire" and hidden:
            raise DependencyMissing("a wire has no hidden geometry", reason="wire_has_no_hidden_geometry",
                                    kernel=self.name, capability="project2d")
        job = {"schema": SCHEMA, "op": "project2d", "view": str(view), "hidden": bool(hidden), "shape": _wrap(target)}
        payload = self._run_job(job)
        data = payload.get("projection")
        if not isinstance(data, Mapping):
            raise KernelExecutionError(
                _err_text("err.internal", detail="project2d returned no projection payload"),
                reason=REASON_RESULT_MISSING,
                op="project2d",
            )
        from .base import dedupe_curves

        curves = tuple(dedupe_curves(dict(item) for item in data.get("curves", []) if isinstance(item, Mapping)))
        if not curves:
            self._stats["failures"] += 1
            raise KernelExecutionError(
                _err_text("err.internal", detail="project2d produced no curve (empty drawing is a defect)"),
                reason=REASON_EMPTY_PROJECTION,
                op="project2d",
                view=str(view),
            )
        # `hidden_measured` 缺省 True：老载荷/假 CLI 不分两组时按"分过了"记账（不改变既有行为）；
        # findShapeOutline 回退路径显式报 False ⇒ 虚隐线是**未采**，不是 0。
        return Projection(
            view=str(data.get("view", view)),
            hidden=bool(data.get("hidden", hidden)),
            curves=curves,
            source=str(data.get("source", "external")),
            hidden_measured=bool(data.get("hidden_measured", True)),
        )

    def mass_properties(self, shape: Any) -> MassProperties:
        """质量属性：工具没报的字段一律 `-1`=未采并列进 `unmeasured`（**不许用 0 冒充"没测到"**）。"""
        self._require("mass")
        payload = self._run_job({"schema": SCHEMA, "op": "mass", "shape": _wrap(self._shape(shape))})
        data = payload.get("properties")
        if not isinstance(data, Mapping):
            raise KernelExecutionError(
                _err_text("err.internal", detail="mass returned no properties payload"),
                reason=REASON_RESULT_MISSING,
                op="mass",
            )
        center = data.get("center_nm")
        return MassProperties(
            volume_nm3=int(data.get("volume_nm3", UNMEASURED)),
            area_nm2=int(data.get("area_nm2", UNMEASURED)),
            profile_area_nm2=int(data.get("profile_area_nm2", UNMEASURED)),
            center_nm=tuple(int(item) for item in center) if isinstance(center, (list, tuple)) and len(center) == 3 else None,
            bbox_nm=tuple(int(item) for item in data["bbox_nm"]) if data.get("bbox_nm") else None,
            source=str(data.get("source", "external")),
            unmeasured=tuple(sorted(str(item) for item in data.get("unmeasured", []))),
        )

    def export(self, shape: Any, *, fmt: str, path: str) -> str:
        """导出到 `path`（`step|iges|stl`）。产物必须**本次真的被写出来**：预存文件不算成功（防假绿）。"""
        fmt_name = str(fmt).lower()
        if fmt_name not in ("step", "stp", "iges", "igs", "stl"):
            raise SubprocError(
                _err_text("err.not_implemented", feature="export-format:%s" % fmt_name), reason=REASON_OP_FAILED
            )
        # A6 的能力词汇表里"文件出口"只有一个 token（`step`）；iges/stl 同属该出口能力，不另立 token。
        self._require("step")
        target = Path(str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        before = _file_state(target)
        payload = self._run_job(
            {"schema": SCHEMA, "op": "export", "fmt": "stp" if fmt_name == "stp" else fmt_name, "path": str(target), "shape": _wrap(self._shape(shape))}
        )
        reported = payload.get("out_path")
        artifact = Path(str(reported)) if isinstance(reported, str) and reported else target
        try:
            _require_artifact(artifact, op="export", fmt=fmt_name)
            # 目标路径上**必须**真的有产物：外部工具"报到别处去了"不算导出成功（否则目标不存在也会假绿）。
            _require_artifact(target, op="export", fmt=fmt_name)
        except KernelExecutionError:
            self._stats["artifacts_missing"] += 1
            self._stats["failures"] += 1
            self._stats["last_reason"] = REASON_ARTIFACT_MISSING
            raise
        after = _file_state(target)
        if before is not None and after == before:
            self._stats["failures"] += 1
            raise KernelExecutionError(
                _err_text("err.export_failed", format=fmt_name, detail="artifact unchanged: kernel did not write"),
                reason=REASON_ARTIFACT_UNCHANGED,
                op="export",
                path=str(target),
            )
        return str(target)

    def close(self) -> None:
        """删掉本实例的工作目录（含调用方传入的 scratch；`keep_work_dir=True` 时保留以便取证）。"""
        if self._work_dir and not self._keep_work_dir:
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None

    # —— 内部 —————————————————───────────────────────────────────────────────────
    def _shape(self, value: Any) -> SubprocessShape:
        """Normalize the same L1 shape references accepted by the other adapters."""
        if isinstance(value, SubprocessShape):
            if value.kernel != self.name:
                raise SubprocError("foreign kernel shape", reason=REASON_FOREIGN_SHAPE)
            return value
        if isinstance(value, Mapping):
            if "recipe" in value:
                return SubprocessShape(recipe=tuple(_recipe_of(value)), kernel=self.name)
            if "sketches" in value or "features" in value:
                return self.shape_from_document(value)
            if "entities" in value:
                return self.sketch_to_wire(value)
            if isinstance(value.get("sketch"), Mapping):
                return self.sketch_to_wire(value["sketch"])
        raise SubprocError(
            _err_text("err.not_implemented", feature="shape-ref:%s" % type(value).__name__),
            reason=REASON_FOREIGN_SHAPE)

    def _extend(self, profile: Any, step: Mapping[str, Any]) -> SubprocessShape:
        recipe = list(_recipe_of(self._shape(profile)))
        recipe.append(dict(step))
        return SubprocessShape(recipe=tuple(recipe), kernel=self.name)

    def _require(self, capability: str) -> None:
        report = self.probe_report()
        if not report.available:
            if report.reason == REASON_NOT_FOUND:
                raise _dependency_missing(_err_text("err.dependency_missing", name=self._profile.executable_names[0]))
            raise _dependency_missing(_err_text("err.kernel_unavailable", kernel=self.name))
        if capability not in self.capabilities():
            raise _dependency_missing(_err_text("err.capability_missing", capability=capability))

    def _work_root(self) -> str:
        if self._work_dir is None:
            self._work_dir = tempfile.mkdtemp(prefix="cadcore-subproc-")
            if self._owns_work_dir and not self._keep_work_dir:
                atexit.register(shutil.rmtree, self._work_dir, True)
        else:
            os.makedirs(self._work_dir, exist_ok=True)  # 调用方给的 scratch 目录允许还不存在
        return self._work_dir

    def _run_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        """落地参数+脚本 → 起外部进程 → 收结果 JSON。任何一步不成立都**硬失败**。"""
        report = self.probe_report()
        if not report.available or not report.executable:
            raise _dependency_missing(_err_text("err.kernel_unavailable", kernel=self.name))
        job_dir = tempfile.mkdtemp(prefix="job-", dir=self._work_root())
        # 同 `_run_probe`：脚本落在 job_dir/scripts/ 而 cwd 留 job_dir，避免 FreeCAD 把脚本当模块
        # import 而丢掉退出码（失败恒 rc=0）。参数/结果 JSON 仍在 job_dir 根下（绝对路径传递）。
        scripts_dir = Path(job_dir) / "scripts"
        scripts_dir.mkdir()
        params_path = Path(job_dir) / "job.json"
        script_path = scripts_dir / "adapter.py"
        result_path = Path(job_dir) / "result.json"
        params_path.write_text(json.dumps(dict(job), ensure_ascii=True, sort_keys=True, indent=1), encoding="utf-8")
        script_path.write_text(self._profile.adapter_script, encoding="utf-8")
        env = dict(os.environ)
        env.update(self._extra_env)
        env[ENV_PARAMS] = str(params_path)
        env[ENV_RESULT] = str(result_path)
        outcome = _run(
            [report.executable, str(script_path)], timeout_s=self.op_timeout_s, cwd=job_dir, env=env
        )
        self._stats["runs"] += 1
        self._stats["last_rc"] = outcome.rc if outcome.rc_measured else UNMEASURED
        try:
            return self._collect_job_result(job, outcome, result_path)
        finally:
            # 取证要留就留（keep_work_dir），否则失败路径也不许在盘上攒垃圾
            if not self._keep_work_dir:
                shutil.rmtree(job_dir, ignore_errors=True)

    def _collect_job_result(self, job: Mapping[str, Any], outcome: RunOutcome, result_path: Path) -> dict[str, Any]:
        """把外部进程的退出码/结果 JSON 收口成成功或**硬失败**（三态：超时与启动失败都记 `rc=-1` 未采）。"""
        if outcome.timed_out:
            self._stats["timeouts"] += 1
            self._stats["failures"] += 1
            self._stats["last_reason"] = REASON_TIMEOUT
            raise KernelExecutionError(
                _err_text("err.internal", detail="external op exceeded %.3gs" % self.op_timeout_s),
                reason=REASON_TIMEOUT,
                op=str(job.get("op", "")),
                timed_out=True,
                elapsed_ms=outcome.elapsed_ms,
            )
        if outcome.launch_error:
            self._stats["failures"] += 1
            self._stats["last_reason"] = REASON_LAUNCH_FAILED
            raise KernelExecutionError(
                _err_text("err.internal", detail=outcome.launch_error),
                reason=REASON_LAUNCH_FAILED,
                op=str(job.get("op", "")),
            )
        payload = _read_json(result_path)
        if payload is None:
            self._stats["failures"] += 1
            reason = REASON_RESULT_MISSING if outcome.rc == 0 else REASON_NONZERO_EXIT
            self._stats["last_reason"] = reason
            raise KernelExecutionError(
                _err_text("err.internal", detail="rc=%s stderr=%s" % (outcome.rc, _tail(outcome.stderr))),
                reason=reason,
                op=str(job.get("op", "")),
                rc=outcome.rc,
            )
        if payload.get("ok") is not True:
            self._stats["failures"] += 1
            self._stats["last_reason"] = REASON_OP_FAILED
            raise KernelExecutionError(
                _err_text("err.internal", detail=str(payload.get("error", "")) or "op reported failure"),
                reason=REASON_OP_FAILED,
                op=str(job.get("op", "")),
                rc=outcome.rc,
                traceback_tail=str(payload.get("traceback_tail", "")),
            )
        self._stats["last_reason"] = REASON_OK
        return payload


# ── 配方与草图规范化（纯数据，不 import 几何件）──────────────────────────────────────────
def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _point_nm(value: Any, *, what: str) -> list[int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SubprocError(_err_text("err.geometry_invalid", detail="%s is not a point" % what), reason=REASON_OP_FAILED)
    items = list(value)
    if len(items) not in (2, 3):
        raise SubprocError(
            _err_text("err.geometry_invalid", detail="%s must have 2 or 3 components" % what), reason=REASON_OP_FAILED
        )
    try:
        if any(isinstance(item, bool) or not isinstance(item, int) or abs(item) > 2**53
               for item in items):
            raise ValueError("unsafe integer length")
        return list(items)
    except (TypeError, ValueError):
        raise SubprocError(
            _err_text("err.geometry_invalid", detail="%s is not integer nanometres" % what), reason=REASON_OP_FAILED
        ) from None


def _center_nm(geom: Mapping[str, Any], *, what: str) -> list[int]:
    """圆心：配方形态 `center=[x,y]` 与 L1 文档形态 `cx/cy` 都收。"""
    center = _field(geom, "center", None)
    if center is None and _field(geom, "cx", None) is not None:
        center = [_field(geom, "cx"), _field(geom, "cy")]
    return _point_nm(center, what=what + ".center")


def _radius_nm(geom: Mapping[str, Any], *, what: str) -> int:
    """半径：配方形态 `radius_nm` 与 L1 文档形态 `r` 都收（都是整数纳米）。"""
    value = _field(geom, "radius_nm", None)
    if value is None:
        value = _field(geom, "r", None)
    if value is None:
        raise SubprocError(
            _err_text("err.geometry_invalid", detail=what + ".radius_nm is missing"), reason=REASON_OP_FAILED
        )
    try:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**53:
            raise ValueError("unsafe integer radius")
        return value
    except (TypeError, ValueError):
        raise SubprocError(
            _err_text("err.geometry_invalid", detail=what + ".radius_nm is not integer nanometres"),
            reason=REASON_OP_FAILED,
        ) from None


def _normalize_sketch(sketch: Any) -> dict[str, Any]:
    """把草图归一成**可 JSON 化**的配方数据；未知实体类型直接报错（不静默丢实体 —— 丢一个实体等于出错的图）。"""
    entities = _field(sketch, "entities", None)
    if entities is None:
        raise SubprocError(
            _err_text("err.geometry_invalid", detail="sketch has no entities"), reason=REASON_OP_FAILED
        )
    plane = str(_field(sketch, "plane", "XY"))
    if plane not in ("XY", "XZ", "YZ"):
        raise SubprocError("unsupported sketch plane: %s" % plane, reason="unsupported_sketch_plane")

    normalized: list[dict[str, Any]] = []
    for index, entity in enumerate(entities):
        if bool(_field(entity, "construction", False)):
            continue
        kind = str(_field(entity, "kind", ""))
        geom = _field(entity, "geom", {}) or {}
        what = "entity[%d]" % index
        if kind == "line":
            # 两种形态都收：配方形态 p1/p2=[x,y]；L1 文档形态 x1/y1/x2/y2（平铺纳米整数）。
            p1 = _field(geom, "p1", None)
            p2 = _field(geom, "p2", None)
            if p1 is None and _field(geom, "x1", None) is not None:
                p1 = [_field(geom, "x1"), _field(geom, "y1")]
            if p2 is None and _field(geom, "x2", None) is not None:
                p2 = [_field(geom, "x2"), _field(geom, "y2")]
            item = {"kind": "line", "geom": {"p1": _point_nm(p1, what=what + ".p1"),
                                             "p2": _point_nm(p2, what=what + ".p2")}}
        elif kind == "polyline":
            points = _field(geom, "points", None)
            if not points:
                raise SubprocError(
                    _err_text("err.geometry_invalid", detail=what + ".points is empty"), reason=REASON_OP_FAILED
                )
            item = {"kind": "polyline", "geom": {"points": [_point_nm(p, what=what + ".points") for p in points]}}
        elif kind == "circle":
            center = _center_nm(geom, what=what)
            item = {"kind": "circle", "geom": {"center": center, "radius_nm": _radius_nm(geom, what=what)}}
        elif kind == "arc":
            item = {
                "kind": "arc",
                "geom": {
                    "center": _center_nm(geom, what=what),
                    "radius_nm": _radius_nm(geom, what=what),
                    "start_deg": float(_field(geom, "start_deg")),
                    "end_deg": float(_field(geom, "end_deg")),
                },
            }
        else:
            raise SubprocError(
                _err_text("err.not_implemented", feature="sketch-entity:%s" % (kind or "?")), reason=REASON_OP_FAILED
            )
        normalized.append(item)
    if not normalized:
        raise SubprocError(_err_text("err.geometry_invalid", detail="sketch is empty"), reason=REASON_OP_FAILED)
    return {"plane": plane, "entities": normalized}


def _recipe_of(shape: Any) -> list[dict[str, Any]]:
    if isinstance(shape, SubprocessShape):
        return [dict(step) for step in shape.recipe]
    if isinstance(shape, Mapping) and "recipe" in shape:
        return [dict(step) for step in shape["recipe"]]
    raise SubprocError(
        _err_text("err.not_implemented", feature="shape-ref:%s" % type(shape).__name__), reason=REASON_FOREIGN_SHAPE
    )


def _wrap(shape: Any) -> dict[str, Any]:
    return {"recipe": _recipe_of(shape)}


# ── 产物校验（防假绿：文件在 ≠ 本次写出来了）────────────────────────────────────────────
def _file_state(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (int(info.st_size), int(info.st_mtime_ns))


def _require_artifact(path: Path, *, op: str, fmt: str) -> None:
    state = _file_state(path)
    if state is None or state[0] <= 0:
        raise KernelExecutionError(
            _err_text("err.export_failed", format=fmt, detail="artifact missing or empty: %s" % path),
            reason=REASON_ARTIFACT_MISSING,
            op=op,
            path=str(path),
        )


# ── 工厂与注册（A6 的换芯口；A6 缺席不硬失败）────────────────────────────────────────────
def make_kernel(**kwargs: Any) -> SubprocessKernel:
    """工厂：`register_kernel(KERNEL_NAME, make_kernel)` 的入参形态。"""
    return SubprocessKernel(**kwargs)


def register(name: str | None = None, *, factory: Any = None) -> bool:
    """把本后端登记进 A6 的注册表；A6（`kernel/base.py`）尚未落盘 ⇒ 返回 `False`，**不抛异常**。"""
    from cadcore.kernel import base as base_module

    registrar = getattr(base_module, "register_kernel", None)
    if registrar is None:
        return False
    registrar(str(name or KERNEL_NAME), factory or make_kernel)
    return True


# 别名：`SubprocessKernel` 即本模块的默认档案后端。
DEFAULT_KERNEL = SubprocessKernel


# ── 可复跑判定行（E1④：一条命令给 PASS/FAIL/NA，数字以当次输出为准）──────────────────────
def verdict_line(
    executable: str | os.PathLike[str] | None = None, *, profile: str | None = None, timeout_s: float | None = None
) -> tuple[int, str]:
    """探测一次并给判定行。三态 rc：`0`=可用 / `1`=**装了但跑不通**（真红）/ `2`=未采（本机没这个可选依赖）。

    为什么"缺席"记 rc=2 而不是 rc=1：这台机器没有 FreeCAD 是**设计内的正常状态**（降级到 nano 出 2D 图），
    把它报成"失败"会让判定行天天红、最后被豁免成摆设；而"装了却跑不通"必须红 —— 那是真的装坏了。
    """
    result = probe(executable, profile=profile, timeout_s=timeout_s, force=True)
    if result.available:
        rc, verdict = 0, "AVAILABLE"
    elif result.reason in (REASON_NOT_FOUND, REASON_DISABLED):
        rc, verdict = 2, "NA"
    else:
        rc, verdict = 1, "FAIL"
    line = (
        "SUBPROC_KERNEL: verdict=%s rc=%d available=%d reason=%s profile=%s executable=%s version=%s "
        "capabilities_measured=%d capabilities=%s probe_rc=%d timed_out=%d elapsed_ms=%d"
        % (
            verdict,
            rc,
            1 if result.available else 0,
            result.reason,
            result.profile,
            result.executable or NA,
            result.version or NA,
            1 if result.capabilities_measured else 0,
            ",".join(sorted(result.capabilities)) if result.capabilities_measured else "-",
            result.rc if result.rc_measured else UNMEASURED,
            1 if result.timed_out else 0,
            result.elapsed_ms,
        )
    )
    return rc, line


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Probe the optional out-of-process CAD backend (A8).")
    parser.add_argument("--executable", default=None, help="path to the external CLI (default: search PATH)")
    parser.add_argument("--profile", default=None, help="profile name (default: %s)" % KERNEL_NAME)
    parser.add_argument("--timeout-s", type=float, default=None, help="probe timeout in seconds (short by design)")
    parser.add_argument("--json", action="store_true", help="also dump the full probe reading as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    rc, line = verdict_line(args.executable, profile=args.profile, timeout_s=args.timeout_s)
    print(line)
    if args.json:
        # 复用上一步的探测缓存（force=True 也会写缓存）⇒ 不为了打印 JSON 再起一个子进程
        cached = probe(args.executable, profile=args.profile, timeout_s=args.timeout_s)
        print(json.dumps(cached.as_dict(), ensure_ascii=True, sort_keys=True))
    return rc


if __name__ == "__main__":  # pragma: no cover - CLI 入口（测试用 subprocess 跑真件）
    raise SystemExit(_main())
