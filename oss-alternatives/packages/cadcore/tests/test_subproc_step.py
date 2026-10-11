"""tests.test_subproc_step -- A8 进程外内核 + A14 STEP 边界的单测（覆盖探测/超时/杀进程/硬失败/产物校验/脚本协议）。

设计一句话
----------
**这两块最危险的事故都是"静默"与"假绿"**：外部 CAD 明明没装却声称可用、外部程序非 0 退出了却出一张空图、
导出"成功"了其实写的是上次留下的旧文件、STEP 文件里塞的是随便一段文本。所以本文件的尺子成对出现：
既测"该绿时绿"（假可执行文件走通协议、产物真被写出来），也测"该红时红"（超时/非 0/标记缺失/空产物/旧文件）。

为什么用假可执行文件而不是真 FreeCADCmd
--------------------------------------
本机没有 `FreeCADCmd`（`command -v` 空），而且**不许为了测试去装**。所以：
①**边界行为**（探测、超时、杀进程、退出码、结果 JSON、产物校验）用假 CLI 逐条钉死 —— 它严格照本模块的
协议说话，不产生任何几何；②**生成的 FreeCAD 脚本**用桩模块（`FreeCAD.py`/`Part.py`/`TechDraw.py`）
在真 `python3` 里跑一遍，证明脚本体的参数读取、单位换算与结果写回都对；③**真 FreeCAD 的几何**未采（NA），
不写成"已验证"。

夹具自证
--------
每个夹具都断言**真件在场**：被测模块就是仓里那两个文件（比对 `__file__`）、假 CLI 确实落盘且可执行、
桩目录确实被 `sys.executable` 选中（trace 里有调用记录）。测试全部落在 `tempfile` 临时目录里，
不碰仓库内任何别人的文件、不产生副作用、不留进程（超时用例反查子进程 PID 确实已死）。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
_SUBPROC_FILE = _PKG_ROOT / "cadcore" / "kernel" / "subproc.py"
_STEP_FILE = _PKG_ROOT / "cadcore" / "io" / "step.py"
_I18N_DIR = _PKG_ROOT / "cadcore" / "data" / "i18n"


def _load_module(dotted: str, path: Path, fallback_name: str):
    """优先按包导入（真实布局）；兄弟席位的 `__init__.py` 还没落盘时按文件路径装载（缺席降级）。"""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))
    try:
        return importlib.import_module(dotted)
    except Exception:
        spec = importlib.util.spec_from_file_location(fallback_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - 只有文件被删才会走到
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


subproc = _load_module("cadcore.kernel.subproc", _SUBPROC_FILE, "cadcore_kernel_subproc_standalone")
step = _load_module("cadcore.io.step", _STEP_FILE, "cadcore_io_step_standalone")

CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
STEP_TEXT = (
    "ISO-10303-21;\n"
    "HEADER;\n"
    "FILE_DESCRIPTION(('fixture'),'2;1');\n"
    "FILE_NAME('fake.step','2026-01-01T00:00:00',(''),(''),'','','');\n"
    "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
    "ENDSEC;\n"
    "DATA;\n"
    "#1=CARTESIAN_POINT('',(0.,0.,0.));\n"
    "#2=CARTESIAN_POINT('',(1.,0.,0.));\n"
    "ENDSEC;\n"
    "END-ISO-10303-21;\n"
)


# ── 尺子自证用的小工具 ────────────────────────────────────────────────────────────────
def _string_literals(path: Path) -> list[str]:
    """源码里**所有非 docstring 的字符串字面量**（docstring 是开发期文档，不算面向用户的文案）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - 同用户下不会发生
        return True
    return True


# ── 假外部 CLI（照协议说话，不产生几何）──────────────────────────────────────────────
_FAKE_TOOL = '''#!@@PYTHON@@
"""Fake external CAD CLI (test fixture for cadcore.kernel.subproc): speaks the module protocol, no geometry."""
import json
import os
import sys
import time

PROBE_MARKER = @@PROBE_MARKER@@
ADAPTER_MARKER = @@ADAPTER_MARKER@@
PROBE_PAYLOAD = @@PROBE_PAYLOAD@@
EMIT_MARKER = @@EMIT_MARKER@@
WRITE_PROBE_FILE = @@WRITE_PROBE_FILE@@
SLEEP_S = @@SLEEP_S@@
PROBE_SLEEP_S = @@PROBE_SLEEP_S@@
OP_SLEEP_S = @@OP_SLEEP_S@@
EXIT_CODE = @@EXIT_CODE@@
STDERR_TEXT = @@STDERR_TEXT@@
RESULT_MODE = @@RESULT_MODE@@
ARTIFACT = @@ARTIFACT@@
ARTIFACT_ELSEWHERE = @@ARTIFACT_ELSEWHERE@@
MASS_PROPS = @@MASS_PROPS@@
PROJECTION = @@PROJECTION@@
CALLS = @@CALLS@@
STEP_TEXT = @@STEP_TEXT@@


def log(line):
    if CALLS:
        with open(CALLS, "a", encoding="utf-8") as handle:
            handle.write(line + chr(10))


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)


def main():
    script = sys.argv[1] if len(sys.argv) > 1 else ""
    log("script=%s" % script)
    if CALLS:
        with open(CALLS + ".pid", "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    if SLEEP_S:
        time.sleep(SLEEP_S)
    probe_path = os.environ.get("CADCORE_PROBE_RESULT", "")
    if probe_path:
        if PROBE_SLEEP_S:
            time.sleep(PROBE_SLEEP_S)
        payload = json.loads(PROBE_PAYLOAD)
        if WRITE_PROBE_FILE:
            write_json(probe_path, payload)
        if EMIT_MARKER:
            print(PROBE_MARKER, json.dumps(payload, sort_keys=True))
        if STDERR_TEXT:
            sys.stderr.write(STDERR_TEXT)
        return EXIT_CODE
    params = os.environ.get("CADCORE_OP_PARAMS", "")
    result = os.environ.get("CADCORE_OP_RESULT", "")
    if not params or not result:
        sys.stderr.write("fake-tool: protocol env missing")
        return 2
    if OP_SLEEP_S:
        time.sleep(OP_SLEEP_S)
    with open(params, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    log("op=%s" % job.get("op"))
    if CALLS:
        write_json(CALLS + ".job", job)
    if RESULT_MODE == "none":
        if STDERR_TEXT:
            sys.stderr.write(STDERR_TEXT)
        return EXIT_CODE
    payload = {"ok": True, "schema": 1, "marker": ADAPTER_MARKER, "op": job.get("op", "")}
    if job.get("op") == "export":
        path = job.get("path", "")
        if ARTIFACT_ELSEWHERE:
            with open(path, "w", encoding="utf-8") as handle:  # 故意先留一个空壳
                handle.write("")
            path = ARTIFACT_ELSEWHERE
        if ARTIFACT == "step":
            with open(path, "w", encoding="latin-1") as handle:
                handle.write(STEP_TEXT)
        elif ARTIFACT == "junk":
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("definitely not a step file")
        elif ARTIFACT == "empty":
            open(path, "w", encoding="utf-8").close()
        payload["out_path"] = path
    elif job.get("op") == "mass":
        payload["properties"] = json.loads(MASS_PROPS)
    elif job.get("op") == "project2d":
        payload["projection"] = json.loads(PROJECTION)
    if RESULT_MODE == "bad":
        payload["ok"] = False
        payload["error"] = "fake op failure"
    write_json(result, payload)
    if STDERR_TEXT:
        sys.stderr.write(STDERR_TEXT)
    return EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
'''

_DEFAULT_PROBE_PAYLOAD = {"ok": True, "schema": 1, "marker": subproc.PROBE_MARKER, "version": "0.21.2",
                          "modules": {"FreeCAD": True, "Part": True, "TechDraw": False}}


def _render(template: str, **values: object) -> str:
    """填模板并自证：留一个没替换的占位符就当场炸（防夹具与真件漂移）。"""
    text = template
    for key, value in values.items():
        text = text.replace("@@%s@@" % key, str(value))
    leftover = re.findall(r"@@[A-Z_]+@@", text)
    if leftover:  # pragma: no cover - 只会在测试写错时命中
        raise AssertionError("unrendered fixture placeholders: %s" % sorted(set(leftover)))
    return text


def _write_fake_tool(directory: str, name: str = "FreeCADCmd", *, trace: bool = True, **knobs: object) -> tuple[str, str]:
    """落一个假 CLI（可执行、带 shebang），返回 (路径, trace 前缀)。"""
    calls = os.path.join(directory, "calls.log") if trace else ""
    probe_payload = dict(_DEFAULT_PROBE_PAYLOAD)
    probe_payload.update(knobs.pop("probe_payload", {}) or {})
    sleep_s = float(knobs.pop("sleep_s", 0.0))
    source = _render(
        _FAKE_TOOL,
        PYTHON=sys.executable,
        PROBE_MARKER=repr(subproc.PROBE_MARKER),
        ADAPTER_MARKER=repr(subproc.ADAPTER_MARKER),
        PROBE_PAYLOAD=repr(json.dumps(probe_payload, sort_keys=True)),
        EMIT_MARKER=repr(bool(knobs.pop("emit_marker", True))),
        WRITE_PROBE_FILE=repr(bool(knobs.pop("write_probe_file", True))),
        SLEEP_S=repr(sleep_s),
        PROBE_SLEEP_S=repr(float(knobs.pop("probe_sleep_s", sleep_s))),
        OP_SLEEP_S=repr(float(knobs.pop("op_sleep_s", sleep_s))),
        EXIT_CODE=repr(int(knobs.pop("exit_code", 0))),
        STDERR_TEXT=repr(str(knobs.pop("stderr_text", ""))),
        RESULT_MODE=repr(str(knobs.pop("result_mode", "ok"))),
        ARTIFACT=repr(str(knobs.pop("artifact", "step"))),
        ARTIFACT_ELSEWHERE=repr(str(knobs.pop("artifact_elsewhere", ""))),
        MASS_PROPS=repr(json.dumps(knobs.pop("mass_props", {"source": "external"}), sort_keys=True)),
        PROJECTION=repr(json.dumps(knobs.pop("projection", {"view": "front", "hidden": False,
                                                             "curves": [{"kind": "visible", "points_nm": [[0, 0], [1, 1]]}]}),
                                 sort_keys=True)),
        CALLS=repr(calls),
        STEP_TEXT=repr(STEP_TEXT),
    )
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(source)
    os.chmod(path, 0o755)
    if knobs:  # pragma: no cover - 测试写错 knob 名时当场报错，而不是静默忽略
        raise AssertionError("unknown fixture knobs: %s" % sorted(knobs))
    return path, calls


def _kernel(exe: str, **kwargs: object) -> object:
    kwargs.setdefault("probe_timeout_s", 0.6)
    return subproc.SubprocessKernel(executable=exe, **kwargs)


# ── 桩 CAD 模块（真 python3 跑生成脚本；证明脚本协议与单位换算）─────────────────────────
_STUB_SHARED = '''"""Shared recorder for the stub CAD modules (test fixture)."""
import os


def record(name, detail=""):
    path = os.environ.get("STUB_TRACE", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(("%s %s" % (name, detail)).strip() + chr(10))
'''

_STUB_FREECAD = '''"""Stub FreeCAD module (test fixture): millimetre-scale numbers, no geometry."""


class Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __repr__(self):
        return "Vector(%s, %s, %s)" % (
            ("%.6g" % self.x), ("%.6g" % self.y), ("%.6g" % self.z))


def Version():
    return ["0", "21", "2"]
'''

_STUB_PART = '''"""Stub Part module (test fixture): records calls, returns millimetre-scale properties."""
import os

import _stub

BROKEN = [item for item in (os.environ.get("STUB_BROKEN") or "").split(",") if item]


class Shape(object):
    def __init__(self, kind="wire"):
        self.kind = kind
        _stub.record("Shape", kind)

    @property
    def Volume(self):
        if "volume" in BROKEN:
            raise RuntimeError("stub volume unavailable")
        return 1000.0

    @property
    def Area(self):
        if "area" in BROKEN:
            raise RuntimeError("stub area unavailable")
        return 600.0

    @property
    def CenterOfMass(self):
        if "center" in BROKEN:
            raise RuntimeError("stub center unavailable")
        return (1.0, 2.0, 3.0)

    def extrude(self, vector):
        _stub.record("extrude", repr(vector))
        return Shape("solid")

    def revolve(self, center, axis, angle):
        _stub.record("revolve", "angle=%.6g" % float(angle))
        return Shape("solid")

    def translate(self, vector):
        _stub.record("translate", repr(vector))
        return self

    def fuse(self, other):
        _stub.record("fuse")
        return Shape("fused")

    def cut(self, other):
        _stub.record("cut")
        return Shape("cut")

    def common(self, other):
        _stub.record("common")
        return Shape("common")

    def exportStep(self, path):
        _stub.record("exportStep", path)
        with open(path, "w", encoding="latin-1") as handle:
            handle.write(STEP_TEXT)


STEP_TEXT = (
    "ISO-10303-21;" + chr(10) + "HEADER;" + chr(10)
    + "FILE_DESCRIPTION(('stub'),'2;1');" + chr(10)
    + "FILE_NAME('stub.step','2026-01-01T00:00:00',(''),(''),'','','');" + chr(10)
    + "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));" + chr(10)
    + "ENDSEC;" + chr(10) + "DATA;" + chr(10)
    + "#1=CARTESIAN_POINT('',(0.,0.,0.));" + chr(10)
    + "ENDSEC;" + chr(10) + "END-ISO-10303-21;" + chr(10)
)


class _Edge(object):
    def discretize(self, count):
        return [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]


class _Compound(object):
    def __init__(self):
        self.Edges = [_Edge(), _Edge()]


def makeLine(p1, p2):
    _stub.record("makeLine", "%r -> %r" % (p1, p2))
    return Shape("edge")


def makePolygon(points):
    _stub.record("makePolygon", str(len(points)))
    return Shape("polygon")


def makeCircle(radius, center, normal):
    _stub.record("makeCircle", "r=%.6g" % float(radius))
    return Shape("circle")


def Wire(edges):
    _stub.record("Wire", "n=%d" % len(edges))
    return Shape("wire")


class Circle(object):
    def __init__(self, center, normal, radius):
        self.center, self.normal, self.radius = center, normal, radius


class Arc(object):
    def __init__(self, circle, start, end):
        self.circle, self.start, self.end = circle, start, end

    def toShape(self):
        _stub.record("Arc.toShape")
        return Shape("arc")
'''

_STUB_TECHDRAW = '''"""Stub TechDraw module (test fixture): HLR projection without geometry."""
import _stub


class _Edge(object):
    def discretize(self, count):
        return [(0.0, 0.0, 0.0), (2.0, 2.0, 0.0)]


class _Compound(object):
    def __init__(self, label):
        self.label = label
        self.Edges = [_Edge()]


def projectEx(shape, direction):
    _stub.record("projectEx", repr(direction))
    return (_Compound("visible"), _Compound("hidden"))
'''


def _write_stub_cad(directory: str) -> str:
    os.makedirs(directory, exist_ok=True)
    for name, source in (
        ("_stub.py", _STUB_SHARED),
        ("FreeCAD.py", _STUB_FREECAD),
        ("Part.py", _STUB_PART),
        ("TechDraw.py", _STUB_TECHDRAW),
    ):
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
    return directory


# ── 测试基类：临时目录 + 探测缓存清零 ────────────────────────────────────────────────
class _TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        subproc.reset_cache()
        self._tmp = tempfile.mkdtemp(prefix="cadcore-test-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.dir = self._tmp
        # PATH 清空：这些用例只许走显式路径/假 CLI，**不许**意外捡到宿主机上真装的 FreeCADCmd
        # （捡到就会把"缺席路径"测成"在场路径"，是本文件最想避免的假绿）。
        self._empty_bin = os.path.join(self._tmp, "empty-bin")
        os.makedirs(self._empty_bin, exist_ok=True)
        patcher = mock.patch.dict(os.environ, {"PATH": self._empty_bin})
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, *parts: str) -> str:
        return os.path.join(self._tmp, *parts)


# ── A8：探测边界 ──────────────────────────────────────────────────────────────────
class TestProbeBoundary(_TmpCase):
    def test_missing_executable_is_unavailable_not_found_and_never_raises(self):
        kernel = _kernel(self.path("no-such-FreeCADCmd"))
        self.assertFalse(kernel.available())
        report = kernel.probe_report()
        self.assertEqual(report.reason, subproc.REASON_NOT_FOUND)
        self.assertEqual(report.rc, subproc.UNMEASURED)  # 未采，不是"退出码 0"
        self.assertFalse(report.rc_measured)

    def test_stats_before_any_probe_reports_unmeasured_not_false(self):
        kernel = _kernel(self.path("never-probed"))
        stats = kernel.stats()
        self.assertIsNone(stats["available"])  # 未采
        self.assertEqual(stats["probe_rc"], subproc.UNMEASURED)
        self.assertEqual(stats["capabilities"], [])
        self.assertFalse(stats["capabilities_measured"])

    def test_executable_that_is_a_directory_is_not_found(self):
        os.makedirs(self.path("FreeCADCmd"))
        self.assertFalse(_kernel(self.path("FreeCADCmd")).available())
        self.assertEqual(subproc.probe(self.path("FreeCADCmd"), timeout_s=0.3).reason, subproc.REASON_NOT_FOUND)

    def test_non_runnable_file_is_launch_failed(self):
        bad = self.path("FreeCADCmd")
        with open(bad, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("this is not an executable format\n")
        os.chmod(bad, 0o755)
        kernel = _kernel(bad)
        self.assertFalse(kernel.available())
        self.assertEqual(kernel.probe_report().reason, subproc.REASON_LAUNCH_FAILED)

    def test_nonzero_exit_is_unavailable_with_recorded_rc(self):
        exe, _ = _write_fake_tool(self.dir, exit_code=1, emit_marker=False, write_probe_file=False,
                                  stderr_text="boom")
        kernel = _kernel(exe)
        self.assertFalse(kernel.available())
        report = kernel.probe_report()
        self.assertEqual(report.reason, subproc.REASON_NONZERO_EXIT)
        self.assertEqual(report.rc, 1)
        self.assertTrue(report.rc_measured)
        self.assertIn("boom", report.detail)

    def test_exit_zero_without_marker_is_unavailable(self):
        """阴性对照：装了同名程序但没跑我们的脚本 ⇒ 必须判不可用（不许因为 rc=0 就当可用）。"""
        exe, _ = _write_fake_tool(self.dir, emit_marker=False, write_probe_file=False)
        kernel = _kernel(exe)
        self.assertFalse(kernel.available())
        report = kernel.probe_report()
        self.assertEqual(report.reason, subproc.REASON_MARKER_MISSING)
        self.assertEqual(report.rc, 0)
        self.assertFalse(report.marker_seen)

    def test_timeout_is_bounded_and_child_is_killed(self):
        exe, calls = _write_fake_tool(self.dir, probe_sleep_s=30.0)
        kernel = _kernel(exe, probe_timeout_s=0.4)
        started = os.times().elapsed
        self.assertFalse(kernel.available())
        elapsed = os.times().elapsed - started
        report = kernel.probe_report()
        self.assertEqual(report.reason, subproc.REASON_TIMEOUT)
        self.assertTrue(report.timed_out)
        self.assertEqual(report.rc, subproc.UNMEASURED)  # 没拿到退出码 ⇒ 未采
        self.assertLess(elapsed, 8.0, "probe must not block far beyond its timeout")
        pid_file = calls + ".pid"
        self.assertTrue(os.path.isfile(pid_file), "fixture must have started and recorded its pid")
        pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        self.assertFalse(_pid_alive(pid), "timed-out child must be killed, not left running")

    def test_available_fake_tool_reports_version_and_capabilities(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        self.assertTrue(kernel.available())
        report = kernel.probe_report()
        self.assertEqual(report.reason, subproc.REASON_OK)
        self.assertEqual(report.version, "0.21.2")
        self.assertEqual(kernel.version, "0.21.2")
        self.assertTrue(report.capabilities_measured)
        self.assertEqual(report.capabilities_source, "modules")
        self.assertIn("extrude", kernel.capabilities())
        self.assertIn("step", kernel.capabilities())

    def test_probe_only_capability_requires_techdraw(self):
        """`project2d` 是"没实测就不许声称"的能力：TechDraw 不在 ⇒ 不进能力集。"""
        os.makedirs(self.path("plain"), exist_ok=True)
        plain, _ = _write_fake_tool(self.path("plain"), name="FreeCADCmd")
        with_hlr, _ = _write_fake_tool(self.dir, name="FreeCADCmd2", probe_payload={"modules": {"Part": True, "TechDraw": True}})
        self.assertNotIn("project2d", _kernel(plain).capabilities())
        self.assertIn("project2d", _kernel(with_hlr).capabilities())
        self.assertFalse(subproc.PROBE_ONLY_CAPABILITIES - {"project2d"})

    def test_capabilities_empty_when_unavailable(self):
        kernel = _kernel(self.path("missing"))
        self.assertEqual(kernel.capabilities(), frozenset())

    def test_probe_result_is_cached_between_calls(self):
        exe, calls = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        self.assertTrue(kernel.available())
        self.assertTrue(kernel.available())
        self.assertEqual(kernel.stats()["runs"], 0)
        runs = Path(calls).read_text(encoding="utf-8").count("script=")
        self.assertEqual(runs, 1, "probe must be cached (one subprocess), not re-run per call")

    def test_disable_env_switch_short_circuits_without_running_tool(self):
        exe, calls = _write_fake_tool(self.dir)
        with mock.patch.dict(os.environ, {subproc.ENV_DISABLE: "1"}):
            subproc.reset_cache()
            kernel = _kernel(exe)
            self.assertFalse(kernel.available())
            self.assertEqual(kernel.probe_report().reason, subproc.REASON_DISABLED)
        self.assertFalse(os.path.exists(calls), "disabled backend must not spawn the tool at all")

    def test_explicit_executable_beats_env_and_path(self):
        exe, _ = _write_fake_tool(self.dir, name="ExplicitCad")
        with mock.patch.dict(os.environ, {subproc.ENV_EXECUTABLE: self.path("from-env")}):
            self.assertEqual(subproc.find_executable(exe), exe)

    def test_probe_timeout_env_is_honoured_and_clamped(self):
        with mock.patch.dict(os.environ, {subproc.ENV_PROBE_TIMEOUT: "0.25"}):
            resolved, note = subproc._resolve_timeout(None, subproc.ENV_PROBE_TIMEOUT, 4.0, subproc.PROBE_TIMEOUT_CAP_S)
        self.assertEqual(resolved, 0.25)
        self.assertEqual(note, "")
        with mock.patch.dict(os.environ, {subproc.ENV_PROBE_TIMEOUT: "999"}):
            resolved, _ = subproc._resolve_timeout(None, subproc.ENV_PROBE_TIMEOUT, 4.0, subproc.PROBE_TIMEOUT_CAP_S)
        self.assertEqual(resolved, subproc.PROBE_TIMEOUT_CAP_S)  # 夹住上限：不许被环境变量放大成"长阻塞"
        with mock.patch.dict(os.environ, {subproc.ENV_PROBE_TIMEOUT: "abc"}):
            resolved, note = subproc._resolve_timeout(None, subproc.ENV_PROBE_TIMEOUT, 4.0, subproc.PROBE_TIMEOUT_CAP_S)
        self.assertEqual(resolved, 4.0)
        self.assertIn("ignored", note)  # 非法环境变量要留痕，不许静默

    def test_unknown_profile_is_loud(self):
        with self.assertRaises(subproc.SubprocError):
            subproc.get_profile("no-such-profile")


# ── A8：算子协议与硬失败 ────────────────────────────────────────────────────────────
class TestOperatorProtocol(_TmpCase):
    def test_ops_raise_dependency_missing_when_unavailable(self):
        kernel = _kernel(self.path("missing"))
        with self.assertRaises(subproc.DependencyMissing) as ctx:
            kernel.extrude(None, distance_nm=1_000_000)
        self.assertIn("FreeCADCmd", str(ctx.exception))
        self.assertEqual(kernel.stats()["runs"], 0, "unavailable backend must not spawn anything")

    def test_capability_missing_raises_dependency_missing(self):
        exe, _ = _write_fake_tool(self.dir, probe_payload={"modules": {"Part": False, "TechDraw": False}})
        kernel = _kernel(exe)
        self.assertTrue(kernel.available())
        self.assertEqual(kernel.capabilities(), frozenset())
        with self.assertRaises(subproc.DependencyMissing):
            kernel.extrude(None, distance_nm=1)

    def test_geometry_ops_are_lazy_recipes_and_spawn_nothing(self):
        """设计口径：`sketch_to_wire`/`extrude`/`revolve`/`boolean` 只追加配方，**不起进程**。

        起进程的只有需要具体几何的三处（`mass_properties` / `project_to_2d` / `export`）。
        这条钉死它，免得以后有人"顺手"在每个算子里起一次子进程（把无状态重放变成 N 次冷启动）。
        """
        exe, calls = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        wire = _minimal_wire(kernel)
        solid = kernel.extrude(wire, distance_nm=1_000_000)
        kernel.revolve(wire, axis="z", angle_deg=90.0)
        kernel.boolean("union", wire, solid)
        self.assertEqual(kernel.stats()["runs"], 0)
        self.assertFalse(os.path.exists(calls + ".job"), "lazy ops must not run the external tool")

    def test_extrude_job_carries_recipe_and_integer_nanometres(self):
        exe, calls = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        wire = kernel.sketch_to_wire({"plane": "XY", "entities": [
            {"kind": "line", "geom": {"p1": [0, 0], "p2": [10_000_000, 0]}},
            {"kind": "line", "geom": {"p1": [10_000_000, 0], "p2": [0, 5_000_000]}},
        ]})
        solid = kernel.extrude(wire, distance_nm=5_000_000)
        self.assertEqual(len(solid), 2)
        kernel.mass_properties(solid)  # 配方在这里才被重放
        job = json.loads(Path(calls + ".job").read_text(encoding="utf-8"))
        self.assertEqual(job["op"], "mass")
        recipe = job["shape"]["recipe"]
        self.assertEqual(recipe[0]["op"], "sketch")
        self.assertEqual(recipe[0]["sketch"]["entities"][0]["geom"]["p2"], [10_000_000, 0])
        self.assertEqual(recipe[1]["op"], "extrude")
        self.assertEqual(recipe[1]["distance_nm"], 5_000_000)
        self.assertEqual(recipe[1]["symmetric"], False)

    def test_boolean_recipe_nests_both_operands(self):
        exe, calls = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        wire = kernel.sketch_to_wire({"entities": [{"kind": "line", "geom": {"p1": [0, 0], "p2": [1, 0]}}]})
        a = kernel.extrude(wire, distance_nm=1_000_000)
        b = kernel.revolve(wire, axis="z", angle_deg=90.0)
        kernel.mass_properties(kernel.boolean("cut", a, b))
        job = json.loads(Path(calls + ".job").read_text(encoding="utf-8"))
        recipe = job["shape"]["recipe"]
        self.assertEqual(recipe[0]["op"], "boolean")
        self.assertEqual(recipe[0]["op_kind"], "cut")
        self.assertEqual(recipe[0]["a"]["recipe"][-1]["op"], "extrude")
        self.assertEqual(recipe[0]["b"]["recipe"][-1]["op"], "revolve")
        self.assertEqual(recipe[0]["b"]["recipe"][-1]["axis"], "z")

    def test_unknown_boolean_op_is_loud(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        with self.assertRaises(subproc.SubprocError):
            kernel.boolean("subtract", None, None)

    def test_unknown_sketch_entity_is_loud(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        with self.assertRaises(subproc.SubprocError) as ctx:
            kernel.sketch_to_wire({"entities": [{"kind": "spline", "geom": {}}]})
        self.assertIn("spline", str(ctx.exception))

    def test_empty_sketch_is_loud(self):
        exe, _ = _write_fake_tool(self.dir)
        with self.assertRaises(subproc.SubprocError):
            _kernel(exe).sketch_to_wire({"entities": []})

    def test_foreign_shape_ref_is_loud(self):
        exe, _ = _write_fake_tool(self.dir)
        with self.assertRaises(subproc.SubprocError) as ctx:
            _kernel(exe).extrude({"not": "a recipe"}, distance_nm=1)
        self.assertEqual(ctx.exception.reason, subproc.REASON_FOREIGN_SHAPE)

    def test_nonzero_op_exit_is_hard_failure_not_empty_shape(self):
        exe, _ = _write_fake_tool(self.dir, result_mode="none", exit_code=1, stderr_text="segfault-ish")
        kernel = _kernel(exe)
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            kernel.export(_minimal_wire(kernel), fmt="step", path=self.path("out.step"))
        self.assertEqual(ctx.exception.reason, subproc.REASON_NONZERO_EXIT)
        self.assertEqual(ctx.exception.details.get("rc"), 1)
        self.assertIn("segfault-ish", str(ctx.exception))
        self.assertEqual(kernel.stats()["failures"], 1)
        self.assertFalse(os.path.exists(self.path("out.step")), "failed export must not leave an artifact")

    def test_missing_result_json_with_zero_rc_is_hard_failure(self):
        exe, _ = _write_fake_tool(self.dir, result_mode="none", exit_code=0)
        kernel = _kernel(exe)
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            kernel.mass_properties(_minimal_wire(kernel))
        self.assertEqual(ctx.exception.reason, subproc.REASON_RESULT_MISSING)

    def test_op_reported_failure_is_hard_failure(self):
        exe, _ = _write_fake_tool(self.dir, result_mode="bad", exit_code=3)
        kernel = _kernel(exe)
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            kernel.mass_properties(_minimal_wire(kernel))
        self.assertEqual(ctx.exception.reason, subproc.REASON_OP_FAILED)
        self.assertIn("fake op failure", str(ctx.exception))

    def test_op_timeout_kills_child_and_reports_timeout(self):
        exe, calls = _write_fake_tool(self.dir, op_sleep_s=30.0)
        kernel = _kernel(exe, op_timeout_s=0.4)
        started = os.times().elapsed
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            kernel.mass_properties(_minimal_wire(kernel))
        self.assertEqual(ctx.exception.reason, subproc.REASON_TIMEOUT)
        self.assertLess(os.times().elapsed - started, 8.0)
        pid = int(Path(calls + ".pid").read_text(encoding="utf-8").strip())
        self.assertFalse(_pid_alive(pid), "timed-out op child must be killed")
        self.assertEqual(kernel.stats()["timeouts"], 1)

    def test_export_requires_artifact_to_be_written_now(self):
        exe, _ = _write_fake_tool(self.dir, artifact="none")
        kernel = _kernel(exe)
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            kernel.export(_minimal_wire(kernel), fmt="step", path=self.path("out.step"))
        self.assertEqual(ctx.exception.reason, subproc.REASON_ARTIFACT_MISSING)
        self.assertEqual(kernel.stats()["artifacts_missing"], 1)
        self.assertFalse(Path(self.path("out.step")).exists())

    def test_export_rejects_empty_artifact(self):
        exe, _ = _write_fake_tool(self.dir, artifact="empty")
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            _kernel(exe).export(_minimal_wire(_kernel(exe)), fmt="step", path=self.path("out.step"))
        self.assertEqual(ctx.exception.reason, subproc.REASON_ARTIFACT_MISSING)

    def test_export_does_not_accept_pre_existing_file_as_success(self):
        """防假绿：目标路径已经有一个旧文件 ⇒ 内核没写也不许当成功。"""
        target = self.path("out.step")
        Path(target).write_text(STEP_TEXT, encoding="latin-1")
        exe, _ = _write_fake_tool(self.dir, artifact="none")
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            _kernel(exe).export(_minimal_wire(_kernel(exe)), fmt="step", path=target)
        self.assertEqual(ctx.exception.reason, subproc.REASON_ARTIFACT_UNCHANGED)

    def test_export_does_not_accept_an_artifact_written_elsewhere(self):
        """防假绿：外部工具把文件写到别处、`out_path` 也报别处 ⇒ 目标路径没产物，不算导出成功。"""
        elsewhere = self.path("elsewhere.step")
        exe, _ = _write_fake_tool(self.dir, artifact_elsewhere=elsewhere)
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            _kernel(exe).export(_minimal_wire(_kernel(exe)), fmt="step", path=self.path("target.step"))
        self.assertEqual(ctx.exception.reason, subproc.REASON_ARTIFACT_MISSING)
        self.assertTrue(Path(elsewhere).is_file())  # 工具确实写了，只是写错了地方

    def test_export_success_writes_and_returns_path(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        target = self.path("sub", "out.step")
        result = kernel.export(_minimal_wire(kernel), fmt="step", path=target)
        self.assertEqual(Path(result), Path(target))
        self.assertTrue(Path(target).is_file())
        self.assertGreater(Path(target).stat().st_size, 0)

    def test_export_rejects_unknown_format_loudly(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        with self.assertRaises(subproc.SubprocError):
            kernel.export(_minimal_wire(kernel), fmt="dwg", path=self.path("out.dwg"))

    def test_mass_properties_three_state(self):
        exe, _ = _write_fake_tool(self.dir, mass_props={"source": "external", "volume_nm3": 10 ** 21,
                                                        "unmeasured": ["area_nm2", "center_nm"]})
        props = _kernel(exe).mass_properties(_minimal_wire(_kernel(exe)))
        self.assertEqual(props.volume_nm3, 10 ** 21)
        self.assertEqual(props.area_nm2, subproc.UNMEASURED)  # 未采，不是 0
        self.assertIsNone(props.center_nm)
        self.assertEqual(props.unmeasured, ("area_nm2", "center_nm"))  # 未采必须留痕
        self.assertEqual(props.density_kg_m3, subproc.NA)  # 不适用
        self.assertIn("unmeasured", props.as_dict())

    def test_empty_projection_is_a_failure(self):
        exe, _ = _write_fake_tool(self.dir, probe_payload={"modules": {"Part": True, "TechDraw": True}},
                                  projection={"view": "front", "hidden": False, "curves": []})
        with self.assertRaises(subproc.KernelExecutionError) as ctx:
            _kernel(exe).project_to_2d(_minimal_wire(_kernel(exe)), view="front", hidden=False)
        self.assertEqual(ctx.exception.reason, subproc.REASON_EMPTY_PROJECTION)

    def test_projection_counts_visible_and_hidden(self):
        exe, _ = _write_fake_tool(self.dir, probe_payload={"modules": {"Part": True, "TechDraw": True}},
                                  projection={"view": "top", "hidden": True, "curves": [
                                      {"kind": "visible", "points_nm": [[0, 0], [1, 1]]},
                                      {"kind": "hidden", "points_nm": [[0, 0], [2, 2]]},
                                  ]})
        kernel = _kernel(exe)
        solid = kernel.extrude(_minimal_wire(kernel), distance_nm=1_000_000)
        projection = kernel.project_to_2d(solid, view="top", hidden=True)
        self.assertEqual(projection.view, "top")
        self.assertEqual(projection.visible_curves, 1)
        self.assertEqual(projection.hidden_curves, 1)
        self.assertEqual(projection.source, "external")

    def test_make_kernel_is_protocol_shaped(self):
        kernel = subproc.make_kernel(executable=self.path("missing"), probe_timeout_s=0.2)
        for name in ("available", "capabilities", "sketch_to_wire", "extrude", "revolve", "boolean",
                     "project_to_2d", "mass_properties", "export"):
            self.assertTrue(callable(getattr(kernel, name)), name)
        self.assertIsInstance(kernel.name, str)
        self.assertIsInstance(kernel.version, str)

    def test_register_is_best_effort_and_never_raises(self):
        result = subproc.register()
        self.assertIsInstance(result, bool)

    def test_register_calls_the_a6_registry_when_it_exists(self):
        """A6 换芯口在时，`register()` 必须真的把工厂登记进去（A6 缺席时它只是返回 False，不抛）。

        夹具自证（2026-09-29 实撞）：只 patch `sys.modules` **不够** —— `subproc.register()` 用的是
        `from . import base`，而 CPython 在包属性已存在时直接取 `cadcore.kernel.base` 属性、绕过
        `sys.modules`。于是"别的用例先 import 过真 base"就会让本用例静默拿到真注册表：
        `recorded` 为空 ⇒ IndexError。原来它只是**碰巧**在孤立运行时通过（假绿）。
        所以两处一起 patch：模块对象与包属性。
        """
        recorded: list[tuple] = []
        stub = types.ModuleType("cadcore.kernel.base")
        stub.register_kernel = lambda name, factory: recorded.append((name, factory))
        with mock.patch.dict(sys.modules, {"cadcore.kernel.base": stub}), \
                mock.patch("cadcore.kernel.base", stub, create=True):
            ok = subproc.register()
        self.assertTrue(ok)
        self.assertEqual(recorded[0][0], subproc.KERNEL_NAME)
        self.assertIs(recorded[0][1], subproc.make_kernel)

    def test_work_dir_is_removed_on_close(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe, work_dir=self.path("work"))
        kernel.mass_properties(_minimal_wire(kernel))
        self.assertTrue(os.path.isdir(self.path("work")))
        kernel.close()
        self.assertFalse(os.path.exists(self.path("work")))


def _minimal_wire(kernel: object) -> object:
    return kernel.sketch_to_wire({"entities": [{"kind": "line", "geom": {"p1": [0, 0], "p2": [1_000_000, 0]}}]})


# ── A8：生成的脚本可编译 + 在桩 CAD 上真跑 ────────────────────────────────────────────
class TestGeneratedScripts(_TmpCase):
    def test_both_templates_compile(self):
        compile(subproc._PROBE_SCRIPT, "probe.py", "exec")
        compile(subproc._ADAPTER_SCRIPT, "adapter.py", "exec")

    def test_templates_carry_markers_and_protocol_env(self):
        self.assertIn(subproc.PROBE_MARKER, subproc._PROBE_SCRIPT)
        self.assertIn(subproc.ADAPTER_MARKER, subproc._ADAPTER_SCRIPT)
        for env_name in (subproc.ENV_PARAMS, subproc.ENV_RESULT):
            self.assertIn(env_name, subproc._ADAPTER_SCRIPT)
        self.assertIn(subproc.ENV_PROBE_RESULT, subproc._PROBE_SCRIPT)

    def test_probe_script_runs_under_stub_freecad(self):
        stubs = _write_stub_cad(self.path("stubs"))
        rc, payload, _ = self._run_script(subproc._PROBE_SCRIPT, job=None, stubs=stubs)
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["modules"].get("Part"), True)
        self.assertEqual(payload["marker"], subproc.PROBE_MARKER)

    def test_adapter_script_builds_and_exports_step_end_to_end(self):
        stubs = _write_stub_cad(self.path("stubs"))
        trace = self.path("trace.log")
        artifact = self.path("out.step")
        job = {
            "schema": 1,
            "op": "export",
            "fmt": "step",
            "path": artifact,
            "shape": {"recipe": [
                {"op": "sketch", "sketch": {"plane": "XY", "entities": [
                    {"kind": "line", "geom": {"p1": [0, 0], "p2": [10_000_000, 0]}},
                    {"kind": "line", "geom": {"p1": [10_000_000, 0], "p2": [0, 5_000_000]}},
                ]}},
                {"op": "extrude", "distance_nm": 5_000_000, "symmetric": False},
            ]},
        }
        rc, payload, text = self._run_script(subproc._ADAPTER_SCRIPT, job=job, stubs=stubs, trace=trace)
        self.assertEqual(rc, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["op"], "export")
        self.assertTrue(Path(artifact).is_file())
        self.assertTrue(Path(artifact).read_text(encoding="latin-1").startswith(subproc_step_magic()))
        self.assertIn("makeLine", text)
        self.assertIn("Wire", text)
        self.assertIn("extrude Vector(0, 0, 5)", text)  # 5_000_000 nm -> 5 mm
        self.assertIn("exportStep", text)

    def test_adapter_script_mass_properties_convert_units(self):
        stubs = _write_stub_cad(self.path("stubs"))
        job = {"schema": 1, "op": "mass", "shape": {"recipe": [
            {"op": "sketch", "sketch": {"entities": [{"kind": "line", "geom": {"p1": [0, 0], "p2": [1, 0]}}]}},
        ]}}
        rc, payload, _ = self._run_script(subproc._ADAPTER_SCRIPT, job=job, stubs=stubs)
        self.assertEqual(rc, 0, payload)
        props = payload["properties"]
        self.assertEqual(props["volume_nm3"], 1000 * 10 ** 18)  # 1000 mm^3
        self.assertEqual(props["area_nm2"], 600 * 10 ** 12)  # 600 mm^2
        self.assertEqual(props["center_nm"], [1_000_000, 2_000_000, 3_000_000])  # mm -> nm
        self.assertEqual(props["unmeasured"], [])

    def test_adapter_script_reports_unmeasured_fields_as_missing(self):
        stubs = _write_stub_cad(self.path("stubs"))
        job = {"schema": 1, "op": "mass", "shape": {"recipe": [
            {"op": "sketch", "sketch": {"entities": [{"kind": "line", "geom": {"p1": [0, 0], "p2": [1, 0]}}]}},
        ]}}
        rc, payload, _ = self._run_script(subproc._ADAPTER_SCRIPT, job=job, stubs=stubs,
                                         extra_env={"STUB_BROKEN": "volume,area,center"})
        self.assertEqual(rc, 0, payload)
        props = payload["properties"]
        self.assertNotIn("volume_nm3", props)  # 采不到就不给值（由上层记 -1）
        self.assertEqual(props["unmeasured"], ["area_nm2", "center_nm", "volume_nm3"])

    def test_adapter_script_projects_with_hidden_lines(self):
        stubs = _write_stub_cad(self.path("stubs"))
        job = {"schema": 1, "op": "project2d", "view": "front", "hidden": True, "shape": {"recipe": [
            {"op": "sketch", "sketch": {"entities": [{"kind": "line", "geom": {"p1": [0, 0], "p2": [1, 0]}}]}},
        ]}}
        rc, payload, text = self._run_script(subproc._ADAPTER_SCRIPT, job=job, stubs=stubs)
        self.assertEqual(rc, 0, payload)
        curves = payload["projection"]["curves"]
        self.assertEqual([item["kind"] for item in curves], ["visible", "hidden"])
        self.assertEqual(curves[0]["points_nm"][0], [0, 0])
        self.assertIn("projectEx", text)

    def test_adapter_script_fails_loudly_on_unknown_op(self):
        stubs = _write_stub_cad(self.path("stubs"))
        rc, payload, _ = self._run_script(subproc._ADAPTER_SCRIPT, job={"op": "warp"}, stubs=stubs)
        self.assertEqual(rc, 3)
        self.assertFalse(payload["ok"])
        self.assertIn("unsupported op", payload["error"])

    def test_adapter_script_without_protocol_env_is_protocol_error(self):
        stubs = _write_stub_cad(self.path("stubs"))
        script = self.path("adapter.py")
        Path(script).write_text(subproc._ADAPTER_SCRIPT, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = stubs + os.pathsep + env.get("PYTHONPATH", "")
        env.pop(subproc.ENV_PARAMS, None)
        env.pop(subproc.ENV_RESULT, None)
        proc = subprocess.run([sys.executable, script], env=env, cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def _run_script(self, script_text: str, *, job: dict | None, stubs: str, trace: str | None = None,
                    extra_env: dict | None = None) -> tuple[int, dict, str]:
        script = self.path("script_%d.py" % len(os.listdir(self.dir)))
        Path(script).write_text(script_text, encoding="utf-8")
        result = script + ".result.json"
        env = dict(os.environ)
        env["PYTHONPATH"] = stubs + os.pathsep + env.get("PYTHONPATH", "")
        env["STUB_TRACE"] = trace or self.path("trace.log")
        env[subproc.ENV_RESULT] = result
        if job is None:
            env[subproc.ENV_PROBE_RESULT] = result
        else:
            params = script + ".job.json"
            Path(params).write_text(json.dumps(job), encoding="utf-8")
            env[subproc.ENV_PARAMS] = params
        env.update(extra_env or {})
        proc = subprocess.run([sys.executable, script], env=env, cwd=self.dir, capture_output=True, text=True,
                              timeout=60)
        payload: dict = {}
        if os.path.isfile(result):
            payload = json.loads(Path(result).read_text(encoding="utf-8"))
        text = Path(env["STUB_TRACE"]).read_text(encoding="utf-8") if os.path.isfile(env["STUB_TRACE"]) else ""
        if proc.returncode not in (0, 2, 3):  # pragma: no cover - 脚本崩了要看得见 stderr
            self.fail("generated script crashed rc=%s stderr=%s" % (proc.returncode, proc.stderr))
        return proc.returncode, payload, text


def subproc_step_magic() -> str:
    return "ISO-10303-21;"


# ── A14：export_step 能力门与产物校验 ────────────────────────────────────────────────
class _StubKernel:
    """最小内核替身：只实现 STEP 边界要看的三件事（available / capabilities / export）。"""

    def __init__(self, *, available: bool = True, capabilities: object = ("step",), writer=None, name="stub") -> None:
        self.name = name
        self._available = available
        self._capabilities = frozenset(capabilities)
        self._writer = writer
        self.calls: list[tuple] = []

    def available(self) -> bool:
        return self._available

    def capabilities(self) -> frozenset:
        return self._capabilities

    def export(self, shape, *, fmt: str, path: str) -> str:
        self.calls.append((shape, fmt, path))
        if self._writer is None:
            return path
        return self._writer(Path(path))


class TestStepExport(_TmpCase):
    def test_export_step_raises_dependency_missing_without_capability(self):
        kernel = _StubKernel(capabilities=("sketch", "extrude"))
        with self.assertRaises(step.DependencyMissing):
            step.export_step(object(), self.path("out.step"), kernel=kernel)
        self.assertEqual(kernel.calls, [], "no capability ⇒ must not call the kernel")

    def test_export_step_raises_dependency_missing_when_kernel_unavailable(self):
        kernel = _StubKernel(available=False, capabilities=("step",))
        with self.assertRaises(step.DependencyMissing):
            step.export_step(object(), self.path("out.step"), kernel=kernel)
        self.assertEqual(kernel.calls, [])

    def test_export_step_not_implemented_maps_to_dependency_missing(self):
        def boom(path: Path) -> str:
            raise NotImplementedError("no step here")

        kernel = _StubKernel(writer=boom)
        with self.assertRaises(step.DependencyMissing):
            step.export_step(object(), self.path("out.step"), kernel=kernel)

    def test_export_step_lets_kernel_dependency_missing_through(self):
        """内核自己抛的降级信号必须原样透传（被包成普通失败就没人会去换内核了）。"""

        def boom(path: Path) -> str:
            raise step.DependencyMissing("kernel cannot export")

        with self.assertRaises(step.DependencyMissing) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=_StubKernel(writer=boom))
        self.assertNotIsInstance(ctx.exception, step.StepExportError)

    def test_dependency_missing_construction_survives_a_stricter_a3_signature(self):
        """A3 落盘后若换了构造签名，降级信号不许退化成 TypeError（三级兜底要顶住）。"""

        class Strict(step.DependencyMissing):  # type: ignore[misc, valid-type]
            def __init__(self, *, name: str = "") -> None:  # 只吃关键字
                super().__init__(name)

        with mock.patch.object(step, "DependencyMissing", Strict):
            made = step._dependency_missing("boom")
        self.assertIsInstance(made, Strict)
        self.assertEqual(str(made), "boom")

    def test_export_step_wraps_kernel_errors_keeping_cause(self):
        def boom(path: Path) -> str:
            raise OSError("disk on fire")

        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=_StubKernel(writer=boom))
        self.assertIsInstance(ctx.exception.__cause__, OSError)
        self.assertEqual(ctx.exception.reason, "kernel_raised")

    def test_export_step_happy_path_validates_header(self):
        kernel = _StubKernel(writer=lambda path: (path.write_text(STEP_TEXT, encoding="latin-1"), str(path))[1])
        result = step.export_step(object(), self.path("out.step"), kernel=kernel)
        self.assertEqual(result, self.path("out.step"))
        self.assertTrue(Path(result).is_file())
        facts = step.step_header_facts(result)
        self.assertTrue(facts["header_present"])
        self.assertEqual(facts["schema"], "AUTOMOTIVE_DESIGN")

    def test_export_step_rejects_kernel_that_writes_nothing(self):
        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=_StubKernel(writer=lambda path: str(path)))
        self.assertEqual(ctx.exception.reason, "artifact_missing")

    def test_export_step_rejects_empty_file(self):
        kernel = _StubKernel(writer=lambda path: (path.write_text("", encoding="utf-8"), str(path))[1])
        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=kernel)
        self.assertEqual(ctx.exception.reason, "artifact_empty")

    def test_export_step_rejects_non_step_content(self):
        kernel = _StubKernel(writer=lambda path: (path.write_text("hello world", encoding="utf-8"), str(path))[1])
        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=kernel)
        self.assertEqual(ctx.exception.reason, "not_iso_10303_21")

    def test_export_step_does_not_accept_stale_target_as_success(self):
        """防假绿：目标路径已有合法旧文件，内核却没写 ⇒ 必须报错，不许把旧文件当本次产物。"""
        target = Path(self.path("out.step"))
        target.write_text(STEP_TEXT, encoding="latin-1")
        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), str(target), kernel=_StubKernel(writer=lambda path: str(path)))
        self.assertEqual(ctx.exception.reason, "artifact_missing")
        self.assertEqual(target.read_text(encoding="latin-1"), STEP_TEXT)  # 旧文件没被破坏

    def test_export_step_leaves_no_temp_droppings(self):
        kernel = _StubKernel(writer=lambda path: (path.write_text(STEP_TEXT, encoding="latin-1"), str(path))[1])
        step.export_step(object(), self.path("out.step"), kernel=kernel)
        leftovers = [name for name in os.listdir(self.dir) if name.startswith(".cadcore-step-")]
        self.assertEqual(leftovers, [])

    def test_export_step_leaves_no_temp_droppings_on_failure(self):
        with self.assertRaises(step.StepExportError):
            step.export_step(object(), self.path("out.step"), kernel=_StubKernel(writer=lambda path: str(path)))
        leftovers = [name for name in os.listdir(self.dir) if name.startswith(".cadcore-step-")]
        self.assertEqual(leftovers, [])

    def test_export_step_creates_parent_and_accepts_pathlib(self):
        kernel = _StubKernel(writer=lambda path: (path.write_text(STEP_TEXT, encoding="latin-1"), str(path))[1])
        target = Path(self.path("deep", "nested", "out.step"))
        result = step.export_step(object(), target, kernel=kernel)
        self.assertIsInstance(result, str)
        self.assertTrue(target.is_file())

    def test_export_step_reports_kernel_available_failure(self):
        class Broken:
            name = "broken"

            def available(self):
                raise RuntimeError("probe exploded")

            def capabilities(self):  # pragma: no cover - 不该走到
                return frozenset({"step"})

        with self.assertRaises(step.StepExportError) as ctx:
            step.export_step(object(), self.path("out.step"), kernel=Broken())
        self.assertEqual(ctx.exception.reason, "available_check_failed")

    def test_export_step_without_capabilities_method_still_gates(self):
        class Silent:
            name = "silent"

            def available(self) -> bool:
                return True

        with self.assertRaises(step.DependencyMissing):
            step.export_step(object(), self.path("out.step"), kernel=Silent())


# ── A14："入"侧头部元数据 ────────────────────────────────────────────────────────────
class TestStepHeaderFacts(_TmpCase):
    def test_parses_minimal_step_header(self):
        target = self.path("a.step")
        Path(target).write_text(STEP_TEXT, encoding="latin-1")
        facts = step.step_header_facts(target)
        self.assertTrue(facts["header_present"])
        self.assertEqual(facts["file_name"], "fake.step")
        self.assertEqual(facts["time_stamp"], "2026-01-01T00:00:00")
        self.assertEqual(facts["schema"], "AUTOMOTIVE_DESIGN")
        self.assertEqual(facts["description"], ["fixture"])
        self.assertEqual(facts["entity_count"], 2)
        self.assertFalse(facts["truncated"])
        self.assertEqual(facts["missing_fields"], [])
        self.assertIn("header=present", step.write_step_facts(facts))
        self.assertIn("entities=2", step.write_step_facts(facts))

    def test_missing_file_is_loud(self):
        with self.assertRaises(step.StepExportError) as ctx:
            step.step_header_facts(self.path("nope.step"))
        self.assertEqual(ctx.exception.reason, "file_not_found")

    def test_non_step_content_records_missing_fields_instead_of_faking(self):
        target = self.path("junk.step")
        Path(target).write_text("not a step file at all", encoding="utf-8")
        facts = step.step_header_facts(target)
        self.assertFalse(facts["header_present"])
        self.assertIsNone(facts["schema"])
        self.assertIsNone(facts["file_name"])
        self.assertEqual(facts["entity_count"], 0)  # 真为零（文件读完了，一个实体都没有）
        self.assertEqual(set(facts["missing_fields"]),
                         {"header", "description", "file_name", "schema", "time_stamp"})
        rendered = step.write_step_facts(facts)
        self.assertIn("header=absent", rendered)
        self.assertIn("schema=NA", rendered)

    def test_truncated_read_reports_unmeasured_entity_count(self):
        target = self.path("big.step")
        body = STEP_TEXT + "".join("#%d=CARTESIAN_POINT('',(0.,0.,0.));\n" % index for index in range(5000))
        Path(target).write_text(body, encoding="latin-1")
        facts = step.step_header_facts(target, max_bytes=256)
        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["entity_count"], step.UNMEASURED)  # 未采，不是"数到的那些"
        self.assertIn("entities=-1", step.write_step_facts(facts))
        self.assertTrue(facts["header_present"], "header must still be read from the head window")

    def test_entity_count_streams_across_chunk_boundaries(self):
        """流式计数不许在分块边界上重复或漏数：把分块改到 7 字节，逼它跨块。"""
        target = self.path("many.step")
        body = STEP_TEXT + "".join("#%d=CARTESIAN_POINT('',(0.,0.,0.));\n" % index for index in range(500))
        Path(target).write_text(body, encoding="latin-1")
        expected = 2 + 500
        with mock.patch.object(step, "_SCAN_CHUNK_BYTES", 7):
            facts = step.step_header_facts(target, max_bytes=1 << 20)
        self.assertFalse(facts["truncated"])
        self.assertEqual(facts["entity_count"], expected)
        self.assertEqual(step.step_header_facts(target)["entity_count"], expected)  # 缺省分块下同一把尺子

    def test_bom_and_leading_blank_lines_are_tolerated(self):
        target = self.path("bom.step")
        Path(target).write_bytes(b"\xef\xbb\xbf\r\n" + STEP_TEXT.encode("latin-1"))
        self.assertTrue(step.step_header_facts(target)["header_present"])

    def test_is_step_path_only_judges_the_name(self):
        self.assertTrue(step.is_step_path("a.STEP"))
        self.assertTrue(step.is_step_path("/x/y.stp"))
        self.assertFalse(step.is_step_path("a.step.txt"))
        self.assertFalse(step.is_step_path("a.dxf"))


# ── 源码纪律与尺子自证 ──────────────────────────────────────────────────────────────
class TestSourceDiscipline(unittest.TestCase):
    def test_modules_under_test_are_the_real_files(self):
        self.assertEqual(Path(subproc.__file__).resolve(), _SUBPROC_FILE)
        self.assertEqual(Path(step.__file__).resolve(), _STEP_FILE)

    def test_no_cjk_in_runtime_string_literals(self):
        for path in (_SUBPROC_FILE, _STEP_FILE):
            hits = [text for text in _string_literals(path) if CJK_RE.search(text)]
            self.assertEqual(hits, [], "%s must keep user-facing copy in language packs: %s" % (path.name, hits))

    def test_cjk_ruler_sees_an_injected_mutation(self):
        """尺子自证：往真源码里注入一条中文文案，这把尺子必须看得见。"""
        mutated = Path(tempfile.mkdtemp(prefix="cadcore-mut-")) / "subproc.py"
        self.addCleanup(shutil.rmtree, str(mutated.parent), True)
        mutated.write_text(
            _SUBPROC_FILE.read_text(encoding="utf-8") + '\n_LEAK = "\u56fe\u5c42"\n', encoding="utf-8"
        )
        hits = [text for text in _string_literals(mutated) if CJK_RE.search(text)]
        self.assertEqual(hits, ["\u56fe\u5c42"])

    def test_docstring_filter_really_excludes_docstrings(self):
        """尺子自证：模块 docstring 里那句中文若被当字面量，CJK 扫描就会命中它 —— 它没命中即证明过滤生效。"""
        literals = _string_literals(_SUBPROC_FILE)
        self.assertTrue(any("cadcore" in text for text in literals))
        self.assertFalse(any("可选后端" in text for text in literals))
        self.assertIn("可选后端", _SUBPROC_FILE.read_text(encoding="utf-8"))

    def test_message_keys_exist_in_every_language_pack(self):
        """消息必须能查到时真语言包；查不到会退化成 key 派生串 —— 这里把"退化"当红。"""
        used: set[str] = set()
        for path in (_SUBPROC_FILE, _STEP_FILE):
            used.update(re.findall(r'(?:_err_text|_t)\(\s*"([^"]+)"', path.read_text(encoding="utf-8")))
        self.assertTrue(used, "the scan must actually find message keys")
        packs = sorted(_I18N_DIR.glob("*.json"))
        catalog_files = [path for path in packs if path.name != "languages.json"]
        self.assertGreaterEqual(len(catalog_files), 3)
        for path in catalog_files:
            strings = json.loads(path.read_text(encoding="utf-8")).get("strings", {})
            missing = sorted(used - set(strings))
            self.assertEqual(missing, [], "%s lacks keys: %s" % (path.name, missing))

    def test_language_pack_key_shape_is_identical(self):
        """G1 口径的轻量自检：三包键集合必须完全一致（否则文案会静默回落）。"""
        catalog_files = sorted(path for path in _I18N_DIR.glob("*.json") if path.name != "languages.json")
        keysets = [frozenset(json.loads(path.read_text(encoding="utf-8")).get("strings", {})) for path in catalog_files]
        self.assertTrue(all(keys == keysets[0] for keys in keysets))


class TestVerdictLines(_TmpCase):
    """判定行（E1④）：一条命令给 PASS/FAIL/NA；这里跑的是真件（`python -m`），不是打桩。"""

    def _run_module(self, module: str, *args: str) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_PKG_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, "-m", module, *args], cwd=str(_PKG_ROOT), env=env,
                              capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout + proc.stderr

    def test_subproc_verdict_is_na_when_the_optional_cli_is_absent(self):
        rc, out = self._run_module("cadcore.kernel.subproc", "--executable", self.path("no-FreeCADCmd"))
        self.assertEqual(rc, 2, out)
        self.assertIn("SUBPROC_KERNEL: verdict=NA", out)
        self.assertIn("available=0", out)
        self.assertIn("reason=executable_not_found", out)
        self.assertIn("capabilities_measured=0", out)  # 未采，不是"0 项能力"
        self.assertIn("probe_rc=-1", out)

    def test_subproc_verdict_is_available_for_a_working_cli(self):
        exe, _ = _write_fake_tool(self.dir)
        rc, out = self._run_module("cadcore.kernel.subproc", "--executable", exe, "--timeout-s", "1.0")
        self.assertEqual(rc, 0, out)
        self.assertIn("SUBPROC_KERNEL: verdict=AVAILABLE", out)
        self.assertIn("capabilities_measured=1", out)
        self.assertIn("step", out)

    def test_subproc_verdict_is_fail_when_the_cli_is_broken(self):
        exe, _ = _write_fake_tool(self.dir, emit_marker=False, write_probe_file=False)
        rc, out = self._run_module("cadcore.kernel.subproc", "--executable", exe, "--timeout-s", "1.0")
        self.assertEqual(rc, 1, out)  # 装了但跑不通 ⇒ 真红，不是 NA
        self.assertIn("SUBPROC_KERNEL: verdict=FAIL", out)
        self.assertIn("reason=marker_missing", out)

    def test_step_verdict_passes_on_a_valid_file(self):
        target = self.path("ok.step")
        Path(target).write_text(STEP_TEXT, encoding="latin-1")
        rc, out = self._run_module("cadcore.io.step", target)
        self.assertEqual(rc, 0, out)
        self.assertIn("STEP_FACTS: verdict=PASS", out)
        self.assertIn("entities=2", out)
        self.assertIn("schema=AUTOMOTIVE_DESIGN", out)

    def test_step_verdict_fails_on_junk_and_is_na_when_missing(self):
        junk = self.path("junk.step")
        Path(junk).write_text("nope", encoding="utf-8")
        rc, out = self._run_module("cadcore.io.step", junk)
        self.assertEqual(rc, 1, out)
        self.assertIn("verdict=FAIL", out)
        self.assertIn("schema=NA", out)
        rc, out = self._run_module("cadcore.io.step", self.path("absent.step"))
        self.assertEqual(rc, 2, out)
        self.assertIn("verdict=NA", out)
        self.assertIn("reason=file_not_found", out)


class TestFixtureSelfProof(_TmpCase):
    def test_fake_tool_fixture_really_runs(self):
        exe, calls = _write_fake_tool(self.dir)
        self.assertTrue(os.path.isfile(exe))
        self.assertTrue(os.access(exe, os.X_OK))
        proc = subprocess.run([exe], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)  # 没有协议环境变量 ⇒ 协议错误（证明它真被执行了）
        self.assertTrue(os.path.isfile(calls + ".pid"))

    def test_fake_tool_is_protocol_faithful(self):
        exe, _ = _write_fake_tool(self.dir)
        kernel = _kernel(exe)
        self.assertTrue(kernel.available())
        self.assertIn("mass", kernel.capabilities())

    def test_stub_cad_modules_shadow_nothing_and_record(self):
        stubs = _write_stub_cad(self.path("stubs"))
        env = dict(os.environ)
        env["PYTHONPATH"] = stubs + os.pathsep + env.get("PYTHONPATH", "")
        env["STUB_TRACE"] = self.path("stub-trace.log")
        proc = subprocess.run(
            [sys.executable, "-c", "import FreeCAD, Part; print(Part.Shape('x').kind)"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("x", proc.stdout)
        self.assertIn("Shape x", Path(env["STUB_TRACE"]).read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
