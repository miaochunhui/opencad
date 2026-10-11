#!/usr/bin/env python3
"""opencad.server —— OpenCAD 本地 Web 外壳的服务端（纯标准库 `http.server`，禁 Flask 等框架）。

一句话用途
----------
把仓内 `oss-alternatives/packages/cadcore/` 的真件（`docmodel` L1 文本真源、`sketch.commands`
二维编辑命令、`i18n.catalog` 语言包与运行时切换、`i18n.standards` 标准档、`io.dxf_bridge` DXF 导出）
暴露成一组 JSON HTTP 路由，供 `web/` 里的**原生 HTML/CSS/JS** 外壳（零框架、零 CDN）调用。

设计要点（每条都对应一条硬纪律）
--------------------------------
1. **文档真源是 L1 文本**：进程内持有一份 `docmodel.Document`，`/api/state` 返回
   `document_to_l1` 的解析结果；`/api/save` / `/api/open` 分别走 `save_l1` / `load_l1`。
2. **命令走真实 API**：`/api/command` 的 `add_line` / `add_circle` / `add_arc` / `add_dimension`
   直接调 `cadcore.sketch.commands` 的同名入口（校验在那边，坏输入当场报错，本文件不重写几何规则）。
   `delete_entity` / `undo` / `redo` 是**文档级**操作，cadcore 没有对应命令 ⇒ 本文件自己实现：
   `delete_entity` 从 `sketch_entities()` 里按 uuid 摘除（并同步丢掉引用它的尺寸条目），
   `undo`/`redo` 走 **L1 文本快照栈**（`document_to_l1` → `document_from_l1`）—— 恢复的是真源本身，
   不是"再反向调一次命令"这种会漂移的伪撤销。
3. **语言真的能切**：`/api/locale` 调 `catalog.set_locale`（进程不重启），并把新语言包的 strings
   **整包返回**给前端；响应里带 `changed`（0/1）、`resolved`、`degraded`，降级可观测。
4. **未核标就是 NA，不许编数**：`/api/standard` 的投影角来自 `standards.projection_angle_for`，
   返回 `None`（未核标）时如实回字符串 `"NA"`，绝不写死一个角度。
5. **DXF 是真字节**：`/api/export/dxf` 调 `io.dxf_bridge.sketch_to_dxf` 真出字节并落盘，
   `dimension_entities` 是**从落盘字节里数出来的** `DIMENSION` 记录条数（真为零就报 0，不粉饰）。
6. **只写自己名下**：静态文件从 `web/` 读；`/api/save` / `/api/open` / `/api/export/dxf` /
   `/api/command export_pdf` 的路径一律钉在**落盘根**内（NUL、反斜杠、`..` 逃逸与根外绝对路径
   当场 `path_rejected`）。落盘根缺省是**本应用目录**，用 `--out-root <目录>` 或环境变量
   `OPENCAD_OUT_ROOT` 换成用户自己的可写目录（`%USERPROFILE%\Documents`、`~/Documents` 等）；
   **根内**的相对路径与绝对路径都收（相对名以落盘根为基准，**不看进程 cwd** —— 实撞：调用方以为
   改 cwd 就能换落盘位置，其实不行）。根与候选统一按 `realpath` 比较，所以 out-root 自身是软链接
   也算数（实撞：`/hf3fs-jd` 是软链接时，根内的相对名被误判成"逃逸"）。
7. **口径三态**：计数类字段严格遵守 `0=真为零 / -1=未采 / NA=不适用`；例如 `/api/catalogs` 里
   读不出来的语言包 `keys=-1`（未采），而不是 0。

判定行
------
启动成功打一行：``OPENCAD_SERVER verdict=PASS port=<n> routes=<n>``（`--port 0` 时从这里读实际端口）；
起不来打 ``OPENCAD_SERVER verdict=FAIL reason=<code>`` 且 rc=2；Ctrl-C 打 ``verdict=STOP``。

用法
----
    python3 oss-alternatives/apps/opencad/server.py --port 0
    python3 oss-alternatives/apps/opencad/server.py --selftest
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import sys
import threading
import time
import urllib.request
import uuid as _uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(APP_DIR, "web")

#: 语言包 / 标准档目录覆盖（缺省用 cadcore 自带数据目录；测试与"换一套语言包"时用）。
ENV_I18N_DIRS = "OPENCAD_I18N_DIRS"

#: 落盘根覆盖（`/api/save` `/api/open` `/api/export/dxf` 相对路径的基准）。
#: 优先级：`--out-root` > 本环境变量 > 本应用目录。
ENV_OUT_ROOT = "OPENCAD_OUT_ROOT"


def default_out_root() -> str:
    """CLI 落盘根缺省值：`OPENCAD_OUT_ROOT` 非空就用它（绝对化），否则本应用目录。

    为什么需要这个开关：`out_root` 由**本文件所在目录**决定，跟服务进程的 cwd 无关 ——
    启动器把 cwd 改成可写工作区并**不会**让导出落到那里（实测：cwd 里没有文件，安装目录里有）。
    """
    override = os.environ.get(ENV_OUT_ROOT, "").strip()
    return os.path.abspath(override) if override else APP_DIR


# --------------------------------------------------------------------------------------
# cadcore 引导（真件不在 site-packages 里：从本文件位置向上找 packages/cadcore）
# --------------------------------------------------------------------------------------


class BootstrapError(RuntimeError):
    """cadcore 真件缺席 —— 显式失败，绝不静默降级成 0/空实现。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {self.detail}" if self.detail else self.reason)


def _candidate_package_roots() -> list[str]:
    """可能的 `packages/cadcore` 目录：环境变量优先，其次从本文件向上逐级找。"""
    out: list[str] = []
    override = os.environ.get("OPENCAD_CADCORE_ROOT", "").strip()
    if override:
        out.append(os.path.abspath(override))
    base = APP_DIR
    for _ in range(6):
        base = os.path.dirname(base)
        if not base or base == os.sep:
            break
        out.append(os.path.join(base, "oss-alternatives", "packages", "cadcore"))
        out.append(os.path.join(base, "packages", "cadcore"))
    return out


def _load_cadcore() -> Mapping[str, Any]:
    """import cadcore 真件；失败抛 `BootstrapError`（带已试过的路径，便于排障）。"""
    tried: list[str] = []
    for root in _candidate_package_roots():
        tried.append(root)
        if not os.path.isfile(os.path.join(root, "cadcore", "docmodel.py")):
            continue
        if root not in sys.path:
            sys.path.insert(0, root)
        break
    try:
        from cadcore import docmodel  # noqa: PLC0415 - 路径引导完成后再导入
        from cadcore.draw import sheet as draw_sheet  # noqa: PLC0415
        from cadcore.i18n import catalog as i18n_catalog  # noqa: PLC0415
        from cadcore.i18n import standards as i18n_standards  # noqa: PLC0415
        from cadcore.io import dxf_bridge  # noqa: PLC0415
        from cadcore.sketch import commands as sketch_commands  # noqa: PLC0415
        from cadcore.sketch import constraints as sketch_constraints  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - 引导失败必须显式（含 ImportError）
        raise BootstrapError("cadcore_import_failed", f"{type(exc).__name__}: {exc}") from exc
    return {
        "docmodel": docmodel,
        "draw_sheet": draw_sheet,
        "i18n_catalog": i18n_catalog,
        "i18n_standards": i18n_standards,
        "dxf_bridge": dxf_bridge,
        "commands": sketch_commands,
        "constraints": sketch_constraints,
    }


# --------------------------------------------------------------------------------------
# 错误口径（缺省 HTTP 200 + ok:false；**参数校验失败**用 400，见 `ApiError.status`）
# --------------------------------------------------------------------------------------


class ApiError(Exception):
    """一次 API 调用的显式失败：`code` 是 ASCII 机读码，`detail` 是 ASCII 技术细节。

    `status` 缺省 200（本应用的历史契约：业务失败也回 200 + `ok:false`，前端按 `error` 分流）；
    **请求参数本身不合法**时才显式给 400 —— 那种失败不是"命令没跑成"，是"请求没被接受"。
    """

    def __init__(self, code: str, detail: str = "", *, status: int = 200, **extra: Any) -> None:
        self.code = str(code)
        self.detail = str(detail)
        self.status = int(status)
        self.extra: dict[str, Any] = dict(extra)
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": False, "error": self.code}
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extra)
        return body


#: 前端 DOM 契约里必须拿得到译文的 key（语言包里已有的用真译文，缺的走 `catalog.t` 的派生兜底）。
UI_KEYS: tuple[str, ...] = (
    "app.title",
    "toolbar.label",
    "tool.select",
    "tool.line",
    "tool.circle",
    "tool.arc",
    "tool.dimension",
    "tool.delete",
    "panel.layers",
    "cmd.placeholder",
    "cmd.hint",
    "status.ready",
    "canvas.label",
    "dimension.linear",
    "dimension.radial",
    "dimension.diameter",
    "dimension.angular",
    "layer.default",
)

#: DOM 契约 key → 语言包里**已核译文**的 key（`cmd.*` 是各语言包的真实入口）。
#: 只做"改名接线"，**不新造任何译文**：这样切语言时工具栏文字真的会变；
#: 语言包里确实没有对应词条的 key 落进 `strings_missing`（未采 ≠ 已翻译，显式留痕）。
UI_ALIASES: dict[str, str] = {
    "toolbar.label": "menu.tools",
    "dimension.linear": "cmd.dimension.linear",
    "dimension.radial": "cmd.dimension.radial",
    "dimension.diameter": "cmd.dimension.diameter",
    "dimension.angular": "cmd.dimension.angular",
    "tool.line": "cmd.line",
    "tool.circle": "cmd.circle",
    "tool.arc": "cmd.arc",
    "tool.dimension": "cmd.dimension",
    "tool.delete": "cmd.delete",
}


def _count_sketch_entities(constraints: Any, sketch: Mapping[str, Any]) -> int:
    return len(constraints.sketch_entities(sketch))


def _count_sketch_dimensions(constraints: Any, sketch: Mapping[str, Any]) -> int:
    return len(constraints.sketch_dimensions(sketch))


def _point_segment_distance(point: Sequence[Any], geom: Mapping[str, Any]) -> float:
    """点到线段的距离（纳米）；`geom` 缺端点字段 ⇒ `inf`（不猜，不静默当 0）。"""
    try:
        x1, y1 = float(geom["x1"]), float(geom["y1"])
        x2, y2 = float(geom["x2"]), float(geom["y2"])
    except (KeyError, TypeError, ValueError):
        return float("inf")
    px, py = float(point[0]), float(point[1])
    dx, dy = x2 - x1, y2 - y1
    span = dx * dx + dy * dy
    if span <= 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / span))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def _count_dxf_records(payload: bytes, record: str) -> int:
    """从 DXF 字节里**数**出某类记录（组码 0 + 记录名）—— 数出来的，不是猜出来的。"""
    text = payload.decode("utf-8", errors="replace").splitlines()
    want = record.strip().upper()
    total = 0
    for index in range(len(text) - 1):
        if text[index].strip() == "0" and text[index + 1].strip().upper() == want:
            total += 1
    return total


# --------------------------------------------------------------------------------------
# 工作区（进程内唯一的一份真源 + 语言/标准档状态）
# --------------------------------------------------------------------------------------


class Workspace:
    """OpenCAD 服务端状态：一份 L1 文档 + 活动语言 + 标准档 + 撤销栈。

    线程安全：所有读写都在 `self._lock`（`RLock`）里；`http.server` 是多线程的，
    而 `catalog` 的活动语言是进程级全局状态 —— 这一层锁把两者都盖住。
    """

    def __init__(self, core: Mapping[str, Any], *, locale: str, standard: str,
                 web_dir: str = WEB_DIR, out_root: str = APP_DIR) -> None:
        self.core = core
        self.docmodel = core["docmodel"]
        self.commands = core["commands"]
        self.constraints = core["constraints"]
        self.catalog = core["i18n_catalog"]
        self.standards = core["i18n_standards"]
        self.dxf_bridge = core["dxf_bridge"]
        self.web_dir = os.path.abspath(web_dir)
        self.out_root = os.path.abspath(out_root)
        self._lock = threading.RLock()
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.doc = self.docmodel.new_document(unit="mm", standard_profile=str(standard))
        self.doc.sketches.append(self._new_sketch())
        self.locale_change = self._activate_locale(locale)
        self.standard = str(standard)
        self._apply_standard(str(standard))
        from release_state import recover_pending, digest
        self._release_initial_document_sha256 = digest(self.l1_text())
        recover_pending(self)

    # ── 文档与草图 ──────────────────────────────────────────────────────────────────
    def _new_sketch(self) -> dict[str, Any]:
        """新建草图：字段照 L1（`uuid` / `entities` / `dimensions` / `constraints`）。"""
        return {
            "uuid": str(_uuid.uuid4()),
            "entities": [],
            "dimensions": [],
            "constraints": [],
        }

    @property
    def sketch(self) -> dict[str, Any]:
        """当前草图（本应用只有一张：文档级命令都落在它上面）。"""
        if not self.doc.sketches:
            self.doc.sketches.append(self._new_sketch())
        return self.doc.sketches[0]

    def l1_text(self) -> str:
        return self.docmodel.document_to_l1(self.doc)

    def entity_count(self) -> int:
        return sum(_count_sketch_entities(self.constraints, sk) for sk in self.doc.sketches)

    def dimension_count(self) -> int:
        return sum(_count_sketch_dimensions(self.constraints, sk) for sk in self.doc.sketches)

    # ── 语言 ────────────────────────────────────────────────────────────────────────
    def _catalog_dirs(self) -> list[str] | None:
        raw = os.environ.get(ENV_I18N_DIRS, "").strip()
        if not raw:
            return None
        return [item for item in raw.split(os.pathsep) if item]

    def _activate_locale(self, locale: str) -> Any:
        try:
            return self.catalog.set_locale(str(locale), dirs=self._catalog_dirs())
        except Exception as exc:  # noqa: BLE001 - 语言包缺席要显式回 NA/降级，不许静默
            raise ApiError("locale_unavailable", f"{type(exc).__name__}: {exc}",
                           available=self.available_locales()) from exc

    def available_locales(self) -> list[str]:
        try:
            return list(self.catalog.available_locales(dirs=self._catalog_dirs()))
        except Exception:  # noqa: BLE001 - 扫盘失败按"未采"返回空表（由调用方区分 0 与未采）
            return []

    def strings(self) -> dict[str, str]:
        """当前活动语言包的 strings（整包）+ DOM 契约 key 的**真译文别名**，全部来自真件。"""
        return self.strings_report()[0]

    def strings_report(self) -> tuple[dict[str, str], list[str]]:
        """返回 `(strings, missing)`：`missing` = 语言包里确实没有词条的 key（未采，非"已翻译"）。"""
        active = self.catalog.active_catalog()
        strings: dict[str, str] = dict(active.strings) if active is not None else {}
        missing: list[str] = []
        for key in UI_KEYS:
            if key in strings:
                continue
            source = UI_ALIASES.get(key)
            if source and source in strings:
                strings[key] = strings[source]
                continue
            try:
                derived = self.catalog.t(key)
            except Exception:  # noqa: BLE001 - 单条 key 取词失败不该拖垮整包
                missing.append(key)
                continue
            strings[key] = derived
            missing.append(key)
        return strings, missing

    def set_locale(self, locale: str) -> dict[str, Any]:
        with self._lock:
            change = self._activate_locale(locale)
            strings, missing = self.strings_report()
            return {
                "ok": True,
                "locale": change.current,
                "changed": 1 if change.changed else 0,
                "resolved": change.resolved,
                "catalog_present": bool(change.catalog_present),
                "degraded": bool(change.degraded),
                "keys": int(change.keys),
                "strings": strings,
                "strings_missing": missing,
            }

    # ── 标准档 ──────────────────────────────────────────────────────────────────────
    def _projection_for(self, standard: str) -> str:
        """投影角：`first` / `third` / `"NA"`（未核标）—— 未核一律 NA，不编数。"""
        angle = self.standards.projection_angle_for(str(standard))
        return str(angle) if angle else "NA"

    def _apply_standard(self, standard: str) -> str:
        projection = self._projection_for(standard)
        self.doc.standard_profile = str(standard)
        self.doc.projection_angle = None if projection == "NA" else projection
        self.standard = str(standard)
        return projection

    def set_standard(self, standard: Any) -> dict[str, Any]:
        """切标准档。**先把入参验完再动文档**：非法/缺失 ⇒ 结构化 400 + 可用值清单。

        为什么在这里验：`cadcore.i18n.standards.get_profile` 对未知 id 抛 `I18nError`（它做得对 ——
        "未知 ≠ 未核"，不许回落），但那是**内部异常**，落到 HTTP 层会变成兜底 `internal_error`，
        把"你给的参数不对"说成"服务端崩了"，还把可用值藏在异常串里。参数合法性是 API 层的职责。
        """
        with self._lock:
            known = list(self.standards.load_profiles().keys())  # 坏档/空目录 ⇒ 抛 I18nError，不静默当 0 档
            name = self._validated_standard(standard, known)
            projection = self._apply_standard(name)
            profile = self.standards.get_profile(name)
            return {
                "ok": True,
                "standard": name,
                "projection": projection,
                "na_fields": list(profile.na_fields),
                "standards": known,
            }

    @staticmethod
    def _validated_standard(standard: Any, known: list[str]) -> str:
        """标准档入参 → 规范化 id；缺失/类型不对/未知一律 `ApiError(status=400)`。"""
        if standard is None or (isinstance(standard, str) and not standard.strip()):
            raise ApiError("missing_standard", "standard is required and must be a non-empty string",
                           status=400, known=list(known), standard=standard)
        if not isinstance(standard, str):
            raise ApiError("invalid_standard",
                           "standard must be a string, got %s" % type(standard).__name__,
                           status=400, known=list(known), standard=standard)
        name = standard.strip()
        if name not in known:
            raise ApiError("unknown_standard", "unknown standard profile %r" % name,
                           status=400, known=list(known), standard=name)
        return name

    def standards_list(self) -> list[str]:
        try:
            return list(self.standards.load_profiles().keys())
        except Exception:  # noqa: BLE001 - 标准档目录缺席 ⇒ 空表（未采）
            return []

    # ── 状态 ────────────────────────────────────────────────────────────────────────
    def state(self) -> dict[str, Any]:
        with self._lock:
            change = self.locale_change
            strings, missing = self.strings_report()
            return {
                "ok": True,
                "doc": self.l1_text(),
                "locale": self.catalog.active_locale(),
                "standard": self.standard,
                "projection": self._projection_for(self.standard),
                "catalogs": self.available_locales(),
                "standards": self.standards_list(),
                "strings": strings,
                "strings_missing": missing,
                "entities": self.entity_count(),
                "dimensions": self.dimension_count(),
                "sketch_uuid": self.sketch.get("uuid"),
                "locale_resolved": getattr(change, "resolved", None),
                "locale_degraded": bool(getattr(change, "degraded", False)),
            }

    def mesh(self) -> dict[str, Any]:
        """Read-only mesh of the actual feature tree, cached by exact L1 bytes."""
        import hashlib
        from cadcore.errors import CadError, DependencyMissing
        from cadcore.kernel.subproc import SubprocessKernel

        with self._lock:
            text = self.l1_text()
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cached = getattr(self, "_mesh_cache", None)
            if cached and cached["document_sha256"] == digest:
                return cached
            kernel = SubprocessKernel()
            try:
                shape = kernel.shape_from_document(json.loads(text))
                mesh = kernel.tessellate(shape)
                result = {"ok": True, "document_sha256": digest, "kernel": kernel.name,
                          "unit": "mm", "solids": [mesh], "reason": "ok"}
            except DependencyMissing as exc:
                if exc.reason not in ("no_solid_feature", "empty_document"):
                    raise
                result = {"ok": True, "document_sha256": digest, "solids": [], "reason": exc.reason}
            except CadError as exc:
                raise ApiError("geometry_failed", f"{type(exc).__name__}: {exc}",
                               cadcore_key=exc.reason) from exc
            finally:
                kernel.close()
            self._mesh_cache = result
            return result

    def clearance(self, args):
        """Measure the current BRep against its explicitly translated copy."""
        import hashlib
        from cadcore.kernel.features_clearance import measure
        from cadcore.kernel.subproc import SubprocessKernel, SubprocessShape

        if not isinstance(args, Mapping) or set(args) != {"translation_nm"}:
            raise ApiError("bad_arguments", "clearance requires translation_nm", status=400)
        vector = args["translation_nm"]
        if not isinstance(vector, list) or len(vector) != 3 or any(
                isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2**53
                for value in vector):
            raise ApiError("bad_arguments", "translation_nm must contain three safe integer lengths", status=400)
        with self._lock:
            text = self.l1_text()
            kernel = SubprocessKernel()
            try:
                shape = kernel.shape_from_document(json.loads(text))
                from cadcore.kernel.features_pattern import make_pattern_step
                shifted = SubprocessShape(recipe=shape.recipe + (
                    make_pattern_step(translations_nm=[vector], fuse=False),), kernel=kernel.name)
                result = measure(kernel, shape.to_dict(), shifted.to_dict())
            finally:
                kernel.close()
            return {"ok": True, "measurement": result,
                    "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "translation_nm": vector}

    def export_step(self, raw_path: Any) -> dict[str, Any]:
        """Export a fresh STEP artifact through the same B-Rep used for display."""
        from cadcore.kernel.subproc import SubprocessKernel

        with self._lock:
            path = self._safe_write_path(raw_path)
            kernel = SubprocessKernel()
            try:
                shape = kernel.shape_from_document(json.loads(self.l1_text()))
                kernel.export(shape, fmt="step", path=path)
            finally:
                kernel.close()
            return {"ok": True, "path": path, "bytes": os.path.getsize(path)}

    def step_bytes(self) -> bytes:
        """Download the current solid without leaving persistent export files."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="opencad-step-") as directory:
            with self._lock:
                from cadcore.kernel.subproc import SubprocessKernel

                kernel = SubprocessKernel()
                try:
                    shape = kernel.shape_from_document(json.loads(self.l1_text()))
                    path = os.path.join(directory, "opencad.step")
                    kernel.export(shape, fmt="step", path=path)
                    with open(path, "rb") as handle:
                        result = handle.read()
                finally:
                    kernel.close()
        if not result.startswith(b"ISO-10303-21;"):
            raise ApiError("invalid_step_export")
        return result

    def catalogs(self) -> dict[str, Any]:
        with self._lock:
            dirs = self._catalog_dirs()
            infos = self.catalog.scan_catalogs(self.catalog.default_dirs() if dirs is None else dirs)
            out: list[dict[str, Any]] = []
            for info in infos:
                keys = -1  # -1 = 未采（读不出来），不是"0 条译文"
                try:
                    keys = len(self.catalog.load_catalog(info.code, dirs=dirs).strings)
                except Exception:  # noqa: BLE001 - 坏语言包如实留 -1 + valid=False
                    keys = -1
                out.append({
                    "code": info.code,
                    "native_name": info.native_name,
                    "keys": keys,
                    "valid": bool(info.valid),
                    "error": info.error,
                })
            return {"ok": True, "catalogs": out}

    # ── 命令 ────────────────────────────────────────────────────────────────────────
    def _snapshot(self) -> str:
        return self.l1_text()

    def _restore(self, text: str) -> None:
        self.doc = self.docmodel.document_from_l1(text)

    def _mutate(self, action: Callable[[], Any]) -> Any:
        """跑一次文档改动：**成功才压撤销栈，失败必须把文档回滚到进入前的样子**。

        为什么必须回滚（B6 实测缺陷 `partial_state_on_reject`）：`action()` 是**原地改共享文档**
        （`sketch_entities(sketch).append(...)` 一类），中途抛异常时前面的改动已经落进 L1 真源 ——
        请求回 `ok:false` 但文档被改了一半，"拒绝"与"生效"同时发生，比直接报错坏得多。
        回滚用的是 undo/redo 同一条路（`_snapshot()` → `_restore()`），不是"再反向调一次命令"。
        """
        before = self._snapshot()
        try:
            result = action()
        except BaseException as action_exc:
            # 回滚本身失败（真源已不可解析）要**显式留下**：吞掉会把"文档坏了"伪装成"命令被拒"。
            try:
                self._restore(before)
            except Exception as restore_exc:  # noqa: BLE001
                sys.stderr.write(f"opencad: rollback_failed {type(restore_exc).__name__}: {restore_exc}\n")
                raise restore_exc from action_exc
            raise
        self.undo_stack.append(before)
        self.redo_stack.clear()
        return result

    def _arg(self, args: Mapping[str, Any], name: str) -> Any:
        if name not in args:
            raise ApiError("missing_argument", f"command argument {name!r} is required",
                           argument=name)
        return args[name]

    #: `/api/command` 直通 cadcore 的动词 → 承载模块。
    #: **实测口径**：这些入口**不在** `cadcore.sketch.commands.__all__` 里（那份 `__all__` 只有
    #: add_line/add_circle/add_arc/add_dimension/add_constraint/solve/closed_profiles），
    #: 所以 `self.commands.add_rectangle` 会 AttributeError —— 必须从
    #: `cadcore.sketch.primitives` / `cadcore.edit.modify` 直接取（几何规则一行不改）。
    _PASSTHROUGH: dict[str, str] = {
        "add_rectangle": "sketch.primitives",
        "add_polygon": "sketch.primitives",
        "add_slot": "sketch.primitives",
        "add_polyline": "sketch.primitives",
        "move": "edit.modify",
        "copy": "edit.modify",
        "rotate": "edit.modify",
        "scale": "edit.modify",
        "mirror": "edit.modify",
        "offset": "edit.modify",
        "trim": "edit.modify",
        "extend": "edit.modify",
        "fillet": "edit.modify",
        "chamfer": "edit.modify",
        "array_linear": "edit.modify",
    }

    #: `/api/command` 直通 cadcore 的**文档级**动词 → 承载模块（第一实参 = `self.doc`，不是草图）。
    #: 为什么另立一张表：图层面（layer_* / current_layer* / xref_*）与出图面（plot_* / publish /
    #: layout / viewport）都建在**文档**上（组织表落 `doc.extra["organization"]`，布局落
    #: `document.sheets[]`），拿草图当第一实参够不着这些桶 —— 直通口径本身不改：校验/几何/边界
    #: 全在 cadcore 那边，本文件只接线（同一份 `_command_result` 响应）。
    _DOC_PASSTHROUGH: dict[str, str] = {
        # 组织/图层面：autocad-organization #1（开关/冻结/锁定/当前层）与 #2（对象改层）
        "layer_create": "ops.organization",
        "layer_set": "ops.organization",
        "layer_assign": "ops.organization",
        "layer_delete": "ops.organization",
        "current_layer": "ops.organization",
        "layer_off": "ops.features.organization__w10",
        "layer_freeze": "ops.features.organization__w10",
        "layer_state_set": "ops.features.organization__w10",
        "layer_state": "ops.features.organization__w10",
        "layer_visible_entities": "ops.features.organization__w10",
        # 出图面：autocad-output 的族冻结接口（区域/比例/线宽/可打印/模型出图/布局/视口/发布/预览）
        "plot_area": "ops.output",
        "plot_scale": "ops.output",
        "plot_flag": "ops.output",
        "lineweight": "ops.output",
        "model_space_plot": "ops.output",
        "create_layout": "ops.output",
        "manage_layout": "ops.output",
        "create_viewport": "ops.output",
        "set_viewport_scale": "ops.output",
        "set_viewport_layer_override": "ops.output",
        "plot": "ops.output",
        "plot_pdf": "ops.output",
        "preview": "ops.output",
        "publish": "ops.output",
    }

    #: 文档级直通里的**只读**动词：不进 `_mutate`（不压撤销栈、不动文档）——
    #: 把查询压进撤销栈会让"撤销"撤掉一次读，是假副作用（与 `snap_query` 同一取舍）。
    _DOC_QUERY: frozenset[str] = frozenset({"layer_state", "layer_visible_entities"})

    #: 文档级直通里**要写盘**的动词 → 路径参数名：先过落盘根及恢复文件保护。
    #: 不加这条，新开的出图口就是**越权写盘**的洞（实测：`preview {"out": "preview_1"}` 把
    #: `preview_1.svg` 写进了**进程 cwd** = 应用目录，仓库里留下未跟踪产物）。
    _DOC_PATH_ARGS: dict[str, tuple[str, ...]] = {
        "plot": ("out", "output"),
        "plot_pdf": ("out", "out_path"),
        "preview": ("out", "out_path"),
        "publish": ("out_dir", "out_path"),
        "model_space_plot": ("output",),
    }

    #: `snap_query` 的缺省命中半径（纳米 = 1 mm）；响应里**原样回显** `tolerance_nm`，不藏在代码里。
    SNAP_TOLERANCE_DEFAULT_NM: int = 1_000_000

    #: 追加直通动词 → (承载子模块, 真函数名)。与 `_PASSTHROUGH` 同规矩（走 `_mutate` + uuid 列表），
    #: 只是动词名与函数名不总同名（`matchprop` → `match_properties` 等），所以表里写两个字段。
    #: 全部落点是**当刻真件**：几何规则一行不在这里重写。
    _PASSTHROUGH_EXTRA: dict[str, tuple[str, str]] = {
        "add_point": ("ops.draw_extra", "add_point"),
        # ERASE 的族接口形态：一次删多个 refs（选择集的直接下游）。单实体删除仍走 delete_entity。
        "delete": ("ops.edit_ops", "delete"),
        "stretch": ("ops.draw_extra", "stretch"),
        "explode": ("ops.edit_ops", "explode"),
        "matchprop": ("ops.hatch_measure", "match_properties"),
        "grip_edit": ("ops.edit_ops", "grip_edit"),
        "revcloud": ("ops.draw_ops_extra", "revcloud"),
        "revcloud_rect": ("ops.draw_ops_extra", "revcloud_rect"),
        "dyninput_point": ("ops.draw_ops_extra", "dyninput_point"),
    }

    #: 精度设置动词 → `ops.precision_ops` 的函数名。目标 = **文档**（设置落 `doc.extra["precision"]`，
    #: 随 L1 往返存活；见 cadcore.docmodel 的 `_KNOWN_EXTRA_KEYS`）。它们不改几何，但仍走 `_mutate`：
    #: 失败必须回滚，不许在真源里留半截设置。
    _PRECISION_SETTINGS: dict[str, str] = {
        "snap_enable": "snap_enable",
        "snap_kinds_set": "snap_kinds_set",
        "snap_priority_set": "snap_priority_set",
        "snap_tolerance_set": "snap_tolerance_set",
        "ortho_set": "ortho_set",
        "polar_set": "polar_set",
        "otrack_set": "otrack_set",
        "dyninput_set": "dyninput_set",
        "grid_set": "grid_set",
    }

    #: 只读测量动词 → (承载子模块, 真函数名)。**不进 `_mutate`**：查询不许压撤销栈、不许改文档。
    _MEASURE_VERBS: dict[str, tuple[str, str]] = {
        "measure_area": ("ops.hatch_measure", "measure_area"),
        "measure_perimeter": ("ops.hatch_measure", "measure_perimeter"),
        "measure_distance": ("ops.hatch_measure", "measure_distance"),
    }

    #: 悬停捕捉里 `kinds` 可以点名、但**不在** `precision.osnap.SNAP_KINDS` 的追加类型。
    #: 它们由 `ops.precision_ops` 的独立 op 承担（切点/外观交点/延长线/平行），本文件只做合并与排序。
    SNAP_EXTRA_KINDS: tuple[str, ...] = ("tangent", "apparent", "extension", "parallel")

    #: 平行捕捉的角度容差（度，与 cadcore.precision.osnap 的缺省同口径）。
    SNAP_PARALLEL_TOLERANCE_DEG: float = 3.0

    @staticmethod
    def _cadcore_module(name: str) -> Any:
        """按名取 cadcore 子模块；`cadcore` 的 `sys.path` 引导已由 `_load_cadcore()` 完成。"""
        from importlib import import_module  # noqa: PLC0415 - 必须等引导完成后再导入

        return import_module("cadcore." + name)

    @staticmethod
    def _uuid_list(result: Any) -> list[str]:
        """cadcore 命令返回 `str`（单实体）或 `list[str]`（多实体）—— 统一成 uuid 文本表。"""
        if isinstance(result, str):
            return [result]
        if isinstance(result, Sequence):
            return [str(item) for item in result]
        return [str(result)]

    @staticmethod
    def _snap_payload(hit: Any) -> dict[str, Any] | None:
        """`SnapResult` → 机读字典；没命中就是 `None`（不是"距离 0 的假命中"）。"""
        if hit is None:
            return None
        point = tuple(getattr(hit, "point_nm", None) or ())
        ref = tuple(getattr(hit, "ref", None) or ())
        return {
            "kind": str(getattr(hit, "kind", "")),
            "point_nm": [int(point[0]), int(point[1])] if len(point) == 2 else [],
            "ref": [str(part) for part in ref],
            "distance_nm": int(getattr(hit, "distance_nm", 0)),
        }

    @staticmethod
    def _solve_payload(result: Any) -> dict[str, Any]:
        """`SolveResult` → 机读摘要：**只报解算器真给出的字段**，取不到写 `"NA"`（未采），不补零。"""
        out: dict[str, Any] = {}
        for name in ("converged", "status", "iterations", "variables", "equations",
                     "rank", "redundant", "dof"):
            value = getattr(result, name, None)
            out[name] = value if value is not None else "NA"
        residual = getattr(result, "residual", None)
        out["residual_nm"] = float(residual) if isinstance(residual, (int, float)) else "NA"
        return out

    def _command_result(self, verb: str, uuid: str, **extra: Any) -> dict[str, Any]:
        """命令统一响应：计数是**真计数**，`undo_depth`/`redo_depth` 是撤销栈真深度。"""
        payload: dict[str, Any] = {
            "ok": True,
            "uuid": str(uuid),
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": verb,
            "undo_depth": len(self.undo_stack),
            "redo_depth": len(self.redo_stack),
        }
        payload.update(extra)
        return payload

    def constraint_count(self) -> int:
        """约束条数（`0` 就是真为零；数不出来才会抛，不静默返 0）。"""
        total = 0
        for sketch in self.doc.sketches:
            total += len(self.constraints.sketch_constraints(sketch))
        return total

    def _delete_entity(self, entity_uuid: str) -> dict[str, Any]:
        target = str(entity_uuid or "").strip()
        if not target:
            raise ApiError("missing_argument", "delete_entity requires a non-empty uuid")
        removed = 0
        removed_dimensions = 0
        for sketch in self.doc.sketches:
            entities = self.constraints.sketch_entities(sketch)
            keep = [item for item in entities if str(item.get("uuid")) != target]
            removed += len(entities) - len(keep)
            entities[:] = keep
            dimensions = self.constraints.sketch_dimensions(sketch)
            keep_dims = []
            for item in dimensions:
                refs = item.get("refs") or []
                if any(str(pair[0]) == target for pair in refs if isinstance(pair, Sequence)
                       and len(pair) == 2):
                    removed_dimensions += 1
                    continue
                keep_dims.append(item)
            dimensions[:] = keep_dims
        if not removed:
            raise ApiError("unknown_reference", f"no entity with uuid {target!r}", uuid=target)
        return {"uuid": target, "removed_dimensions": removed_dimensions}

    # ── 精度 / 捕捉（只读查询；真源一律不动） ────────────────────────────────────────
    def _precision_store(self) -> dict[str, Any] | None:
        """`doc.extra["precision"]`（cadcore 的精度设置表落点，随 L1 往返存活）；没表 ⇒ `None`。"""
        extra = getattr(self.doc, "extra", None)
        store = extra.get("precision") if isinstance(extra, dict) else None
        return store if isinstance(store, dict) else None

    def _precision_kind_order(self) -> list[str]:
        """持久化的捕捉类型**全序**（列表顺序 = 优先级）。表缺席 ⇒ 空表（未采，不拿缺省冒充已设置）。"""
        store = self._precision_store()
        records = store.get("snap_kinds") if store else None
        out: list[str] = []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and isinstance(record.get("kind"), str):
                    out.append(record["kind"])
        return out

    def _precision_enabled_kinds(self) -> list[str]:
        """持久化的**已启用**捕捉类型，按优先级顺序；表缺席 ⇒ `[]`（未采）。"""
        store = self._precision_store()
        records = store.get("snap_kinds") if store else None
        out: list[str] = []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("enabled") and isinstance(record.get("kind"), str):
                    out.append(record["kind"])
        return out

    def _precision_priority(self) -> list[str]:
        """合并优先级：表里已有顺序优先，缺项按 `osnap.SNAP_KINDS` 补全。

        为什么要有这一步：`precision.osnap.snap()` 的优先级是**冻结**的（`kinds` 只当过滤器），
        所以"优先级可设"这件事必须由**本层**承担 —— 否则设置面就是个假开关（E2：不许假绿）。
        """
        osnap = self._cadcore_module("precision.osnap")
        ordered = [kind for kind in self._precision_kind_order() if kind in osnap.SNAP_KINDS]
        for kind in osnap.SNAP_KINDS:
            if kind not in ordered:
                ordered.append(kind)
        return ordered

    def _merged_priority(self) -> list[str]:
        """可达**全序**：基础类型按持久化顺序，追加类型插在最近的同族基础类型之后。

        插入位置（写死，别顺手改）：`apparent` 跟 `intersection`、`tangent` 跟 `perpendicular`、
        `extension`/`parallel` 跟 `nearest` —— 顺序在响应 `priority=` 里原样回显，调用方不必猜。
        """
        out: list[str] = []
        for kind in self._precision_priority():
            out.append(kind)
            if kind == "intersection":
                out.append("apparent")
            elif kind == "perpendicular":
                out.append("tangent")
            elif kind == "nearest":
                out.extend(["extension", "parallel"])
        return out

    def _snap_hit_for_kind(self, kind: str, point: tuple[int, int], tolerance: int,
                           work: dict[str, Any], base: Any, ref: Any) -> dict[str, Any] | None:
        """跑**一个**捕捉类型，命中 ⇒ 机读 hit；没命中 ⇒ `None`（不伪造命中）。

        `work` 必须是**副本**：追加类型（切点/外观交点/延长线/平行）会把结果写进
        `work["_ops"]["precision_ops"][<op>]` 通道，写在真源上就是脏写。
        """
        osnap = self._cadcore_module("precision.osnap")
        if kind in osnap.SNAP_KINDS:
            hit = osnap.snap(work, point, kinds=(kind,), tolerance_nm=tolerance)
            if hit is None:
                return None
            return {"kind": kind, "point_nm": [int(hit.point_nm[0]), int(hit.point_nm[1])],
                    "ref": [str(part) for part in hit.ref], "distance_nm": int(hit.distance_nm)}
        precision = self._cadcore_module("ops.precision_ops")

        def channel(name: str) -> dict[str, Any]:
            node = (work.get("_ops") or {}).get("precision_ops") or {}
            record = node.get(name) or {}
            return record if isinstance(record, dict) else {}

        if kind == "tangent":
            uuids = precision.osnap_tangent(work, from_nm=point, tolerance_nm=tolerance)
            best: dict[str, Any] | None = None
            for item in channel("osnap_tangent").get("points_nm") or []:
                px, py = int(item[0]), int(item[1])
                distance = int(round(((px - point[0]) ** 2 + (py - point[1]) ** 2) ** 0.5))
                if distance > tolerance:
                    continue
                if best is None or distance < best["distance_nm"]:
                    best = {"kind": kind, "point_nm": [px, py],
                            "ref": [str(uuids[0]) if uuids else "", "tangent"], "distance_nm": distance}
            return best
        if kind == "apparent":
            lines: list[tuple[float, str]] = []
            for entity in self.constraints.sketch_entities(work):
                if str(entity.get("kind") or "") != "line":
                    continue
                geom = entity.get("geom") or {}
                distance = _point_segment_distance(point, geom)
                lines.append((distance, str(entity.get("uuid") or "")))
            lines.sort(key=lambda item: item[0])
            if len(lines) < 2:
                return None
            pair = precision.osnap_apparent(work, a_ref=lines[0][1], b_ref=lines[1][1],
                                            tolerance_nm=tolerance)
            if not pair:
                return None
            record = channel("osnap_apparent")
            points = record.get("point_nm") or []
            if len(points) < 2:
                return None
            px, py = int(points[0]), int(points[1])
            distance = int(round(((px - point[0]) ** 2 + (py - point[1]) ** 2) ** 0.5))
            if distance > tolerance:
                return None
            return {"kind": kind, "point_nm": [px, py], "ref": [str(part) for part in pair],
                    "distance_nm": distance}
        if kind == "extension":
            precision.osnap_extension(work, point_nm=point, tolerance_nm=tolerance)
            record = channel("osnap_extension")
            points = record.get("point_nm") or []
            if len(points) < 2:
                return None
            px, py = int(points[0]), int(points[1])
            distance = int(round(((px - point[0]) ** 2 + (py - point[1]) ** 2) ** 0.5))
            if distance > tolerance:
                return None
            return {"kind": kind, "point_nm": [px, py],
                    "ref": [str(part) for part in (record.get("ref") or [])], "distance_nm": distance}
        if kind == "parallel":
            if base is None or ref in (None, ""):
                return None
            precision.osnap_parallel(work, ref=ref, base_nm=base, point_nm=point,
                                     tolerance_deg=self.SNAP_PARALLEL_TOLERANCE_DEG)
            record = channel("osnap_parallel")
            points = record.get("point_nm") or []
            if len(points) < 2:
                return None
            px, py = int(points[0]), int(points[1])
            distance = int(round(((px - point[0]) ** 2 + (py - point[1]) ** 2) ** 0.5))
            if distance > tolerance:
                return None
            return {"kind": kind, "point_nm": [px, py],
                    "ref": [str(part) for part in (record.get("ref") or [])], "distance_nm": distance}
        return None

    def _entity_kind_map(self, sketch: Mapping[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for entity in self.constraints.sketch_entities(sketch):
            out[str(entity.get("uuid") or "")] = str(entity.get("kind") or "")
        return out

    def _entity_layer_map(self, sketch: Mapping[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for entity in self.constraints.sketch_entities(sketch):
            out[str(entity.get("uuid") or "")] = str(entity.get("layer") or "")
        return out

    def _ignored_layers(self, ignore_kinds: set[str]) -> set[str]:
        """`ignore_kinds=["hatch"]` ⇒ 图案填充层上的实体不参与捕捉。

        本 L1 的填充就是**落在填充层上的真实线段**（`ops.hatch_measure.hatch_create` 的口径），
        所以"忽略图案填充对象"在这里等价于"忽略该族默认填充层上的实体"；层名取自族模块常量，
        本文件不另写一份。
        """
        if "hatch" not in ignore_kinds:
            return set()
        module = self._cadcore_module("ops.hatch_measure")
        return {str(getattr(module, "DEFAULT_HATCH_LAYER", "HATCH"))}

    def _query_kinds(self, raw_kinds: Any) -> list[str]:
        """`snap_query.kinds` → 去重后的合法类型表；非法形状 / 未知类型**当场报错**。

        为什么把关必须在本层：改造后的 `_snap_query` 不再把用户 `kinds` 原样交给
        `precision.osnap.snap`（内核那把尺子在 `_as_kinds` 里把关），而是按合并优先级逐类型
        试跑 —— 入参把关的唯一入口因此落到本文件。修前这里用 `str(kind)` 强转 + 成员过滤把
        未知类型 / 裸字符串 / 非字符串项静默吞成空集合，HTTP 200 收下（G27 的 silent_illegal
        三例的根因）。

        合法集合 = `osnap.SNAP_KINDS`（8 类）+ `SNAP_EXTRA_KINDS`（本层承担的切点 / 外观交点 /
        延长线 / 平行）；空集合合法（= 谁都不捕捉，与 `osnap.snap(kinds=())` 同口径）；重复项
        去重（与 `osnap._as_kinds` 同口径）。报错文案照抄内核那把尺子，由外层
        `except (TypeError, ValueError)` 转成与 `tolerance_nm` 同一个结构化 400
        （`invalid_snap_argument` + detail），不泄漏内建异常名。
        """
        osnap = self._cadcore_module("precision.osnap")
        if isinstance(raw_kinds, (str, bytes, bytearray)) or not isinstance(raw_kinds, Iterable):
            raise ValueError("kinds must be an iterable of snap kind names, got %r" % (raw_kinds,))
        allowed = tuple(osnap.SNAP_KINDS) + tuple(self.SNAP_EXTRA_KINDS)
        out: list[str] = []
        for name in raw_kinds:
            if not isinstance(name, str) or name not in allowed:
                raise ValueError("unknown snap kind %r; known kinds: %s" % (name, list(allowed)))
            if name not in out:
                out.append(name)
        return out

    def _snap_query(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """悬停捕捉查询：按**可达优先级**逐类型试，第一个命中的类型胜出（AutoCAD 口径）。

        只读：追加类型跑在 `self.sketch` 的**深拷贝**上，真源 L1 一个字节都不动（响应里带
        `doc_unchanged` 供调用方现读自证）。返回 `hit=1/0` + `snap`（没命中就是 `None`）。
        """
        osnap = self._cadcore_module("precision.osnap")
        point = args.get("point_nm")
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2:
            raise ApiError("missing_argument", "snap_query requires point_nm=[x,y]", argument="point_nm")
        px, py = int(point[0]), int(point[1])
        # 容差把关**照抄 osnap 的口径**（该层抛内建 ValueError，本层转结构化 400）：非有限数 /
        # 负值 / 字符串 / bool / null 一律拒。不许 int() 静默强转 —— 那是把"参数不对"吞成"能跑"。
        raw_tolerance = args.get("tolerance_nm", self.SNAP_TOLERANCE_DEFAULT_NM)
        if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)):
            raise ValueError("tolerance_nm must be a finite number, got %r" % (raw_tolerance,))
        if raw_tolerance != raw_tolerance or raw_tolerance in (float("inf"), float("-inf")):
            raise ValueError("tolerance_nm must be finite, got %r" % (raw_tolerance,))
        if raw_tolerance < 0:
            raise ValueError("tolerance_nm must be >= 0, got %r" % (raw_tolerance,))
        tolerance = int(raw_tolerance)
        # `None` / 缺席 = 未指定 ⇒ 用持久化设置；其余一律过 `_query_kinds` 把关：
        # 未知类型 / 裸字符串 / 非字符串项必须 400，**不许**静默过滤成空集合（G27 silent_illegal）。
        raw_kinds = args.get("kinds")
        if raw_kinds is None:
            wanted = self._precision_enabled_kinds() or list(osnap.SNAP_KINDS)
        else:
            wanted = self._query_kinds(raw_kinds)
        base = args.get("base_nm")
        base_point = (int(base[0]), int(base[1])) if isinstance(base, Sequence) and len(base) == 2 else None
        ref = args.get("ref")
        ignore = {str(kind) for kind in (args.get("ignore_kinds") or [])}
        priority = self._merged_priority()
        index = {kind: position for position, kind in enumerate(priority)}
        # 入参 `kinds` 是**允许集合**（与 osnap.snap 同口径）；真正的先后由 `priority` 决定，
        # 这样"优先级可设"才是真的（osnap.snap 内部优先级是冻结的，见 `_precision_priority`）。
        # 允许集合与优先级全序同源（都从 SNAP_KINDS + SNAP_EXTRA_KINDS 派生）：到这里还不在
        # index 里 = 新类型只加了一边（产品自相矛盾）⇒ 显式炸，**不许**再静默丢弃请求里的类型。
        unsortable = [kind for kind in wanted if kind not in index]
        if unsortable:
            raise ValueError("snap kinds missing from priority order: %r" % (unsortable,))
        wanted.sort(key=lambda kind: index[kind])
        before = self.l1_text()
        work = copy.deepcopy(self.sketch)
        kinds_map = self._entity_kind_map(work)
        layers_map = self._entity_layer_map(work)
        ignore_layers = self._ignored_layers(ignore)
        hit: dict[str, Any] | None = None
        evaluated: list[str] = []
        for kind in wanted:
            evaluated.append(kind)
            if kind not in osnap.SNAP_KINDS and kind not in self.SNAP_EXTRA_KINDS:
                continue
            found = self._snap_hit_for_kind(kind, (px, py), tolerance, work, base_point, ref)
            if found is None:
                continue
            owner = found["ref"][0] if found["ref"] else ""
            if ignore and kinds_map.get(owner) in ignore:
                continue
            if ignore_layers and layers_map.get(owner) in ignore_layers:
                continue
            hit = found
            break
        unchanged = 1 if self.l1_text() == before else 0
        return {
            "ok": True,
            "uuid": "",
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": "snap_query",
            "hit": 1 if hit is not None else 0,
            "snap": hit,
            "tolerance_nm": int(tolerance),
            "priority": list(priority),
            "kinds_evaluated": evaluated,
            "kinds_requested": list(wanted),
            "ignore_kinds": sorted(ignore),
            "doc_unchanged": unchanged,
        }

    def _precision_pick(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """按**持久化设置**解析一条光标点（对象捕捉 → 栅格步进 → 正交/极轴 → 捕捉追踪）。

        权威实现在 `cadcore.ops.precision_ops.precision_apply`；本层**只在 L1 副本上**跑它
        （该函数会写 `cursor` 读数记录），因此真源逐字节不变 —— 响应里的 `doc_unchanged`
        是现读前后 L1 文本比出来的，不是自述。
        """
        point = args.get("point_nm")
        if not isinstance(point, Sequence) or isinstance(point, (str, bytes)) or len(point) != 2:
            raise ApiError("missing_argument", "precision_pick requires point_nm=[x,y]", argument="point_nm")
        base = args.get("base_nm")
        precision = self._cadcore_module("ops.precision_ops")
        before = self.l1_text()
        copy_doc = self.docmodel.document_from_l1(before)
        kwargs: dict[str, Any] = {"point_nm": [int(point[0]), int(point[1])]}
        if isinstance(base, Sequence) and not isinstance(base, (str, bytes)) and len(base) == 2:
            kwargs["base_nm"] = [int(base[0]), int(base[1])]
        fired = precision.precision_apply(copy_doc, **kwargs)
        readout = precision.cursor_readout(copy_doc)
        after = self.l1_text()
        payload = {
            "ok": True,
            "uuid": "",
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": "precision_pick",
            "fired": [str(item) for item in fired],
            "doc_unchanged": 1 if after == before else 0,
        }
        payload.update(readout or {})
        return payload

    def _precision_status(self) -> dict[str, Any]:
        """状态栏读的精度设置快照。表缺席 ⇒ 各字段 `-1`（未采）/`[]`，绝不冒充"全关"。"""
        store = self._precision_store()
        if store is None:
            return {"ok": True, "uuid": "", "command": "precision_status", "store": 0,
                    "modes": {name: -1 for name in self._cadcore_module("ops.precision_ops").MODE_NAMES},
                    "kinds": [], "priority": [], "spacing_nm": -1, "tolerance_nm": -1}
        precision = self._cadcore_module("ops.precision_ops")
        modes: dict[str, int] = {}
        records = store.get("modes") if isinstance(store.get("modes"), list) else []
        by_name = {str(record.get("name")): record for record in records if isinstance(record, dict)}
        for name in precision.MODE_NAMES:
            record = by_name.get(name)
            modes[name] = -1 if record is None else (1 if record.get("enabled") else 0)
        kinds: list[str] = []
        priority: list[str] = []
        for record in store.get("snap_kinds") or []:
            if not isinstance(record, dict):
                continue
            kind = str(record.get("kind") or "")
            if not kind:
                continue
            priority.append(kind)
            if record.get("enabled"):
                kinds.append(kind)
        grid = by_name.get("grid") or {}
        grid_cfg = grid.get("config") if isinstance(grid.get("config"), dict) else {}
        osnap = by_name.get("osnap") or {}
        osnap_cfg = osnap.get("config") if isinstance(osnap.get("config"), dict) else {}
        polar = by_name.get("polar") or {}
        polar_cfg = polar.get("config") if isinstance(polar.get("config"), dict) else {}
        ortho = by_name.get("ortho") or {}
        ortho_cfg = ortho.get("config") if isinstance(ortho.get("config"), dict) else {}
        return {
            "ok": True,
            "uuid": "",
            "command": "precision_status",
            "store": 1,
            "modes": modes,
            "kinds": kinds,
            "priority": priority,
            "spacing_nm": int(grid_cfg.get("spacing_nm", -1)),
            "grid_snap_enabled": (1 if grid_cfg.get("snap_enabled") else 0) if "snap_enabled" in grid_cfg else -1,
            "tolerance_nm": int(osnap_cfg.get("tolerance_nm", -1)),
            "polar_increments_deg": list(polar_cfg.get("increments_deg") or []),
            "ortho_tolerance_deg": float(ortho_cfg.get("tolerance_deg", -1.0)),
            "doc_unchanged": 1,
        }

    def _measure(self, verb: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """只读测量：`ops.hatch_measure` 的 token 原样回给前端（`NA` = 不适用，`0` = 真为零）。"""
        module_name, function_name = self._MEASURE_VERBS[verb]
        module = self._cadcore_module(module_name)
        before = self.l1_text()
        tokens = getattr(module, function_name)(self.sketch, **dict(args))
        payload = {
            "ok": True,
            "uuid": "",
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": verb,
            "tokens": [str(token) for token in tokens],
            "doc_unchanged": 1 if self.l1_text() == before else 0,
        }
        for token in tokens:
            if "=" in token:
                key, _, value = str(token).partition("=")
                payload[key] = value
        return payload

    def _boundary_at_point(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """边界创建（BPOLY 口径）：从**内部点**反求闭合环，再把它落成闭合折线实体。"""
        point = self._arg(args, "point_nm")
        module = self._cadcore_module("ops.hatch_measure")
        members = module.boundary_from_point(self.sketch, point=point, tol_nm=args.get("tol_nm"))
        created = self._mutate(lambda: module.boundary_create(
            self.sketch, boundary=list(members), index=0, layer=str(args.get("layer", "0")),
            tol_nm=args.get("tol_nm") if args.get("tol_nm") is not None else 0))
        return self._command_result("boundary", self._uuid_list(created)[0],
                                    uuids=self._uuid_list(created), members=[str(m) for m in members])

    def _hatch_at_point(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """图案填充（HATCH 口径）：从**内部点**反求边界再填充（边界也可以直接给 uuid 序列）。"""
        module = self._cadcore_module("ops.hatch_measure")
        boundary = args.get("boundary")
        if boundary in (None, ""):
            point = self._arg(args, "point_nm")
            boundary = list(module.boundary_from_point(self.sketch, point=point,
                                                       tol_nm=args.get("tol_nm")))
        kwargs = {key: args[key] for key in ("pattern", "angle_deg", "spacing_nm", "scale", "linetype",
                                             "layer", "island", "assoc", "color", "transparency",
                                             "tol_nm", "max_lines") if key in args}
        created = self._mutate(lambda: module.hatch_create(self.sketch, boundary=boundary, **kwargs))
        uuids = self._uuid_list(created)
        return self._command_result("hatch", uuids[0] if uuids else "", uuids=uuids,
                                    created=len(uuids), boundary=[str(item) for item in boundary])

    def _boundary_probe(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """内部点 → 闭合环成员（**只读**，不建实体）：给测量/填充的拾取点用。

        `no_boundary` 在这里不是错误：拾取点不在任何闭合环里 ⇒ `found=0` + 空表（真为零）。
        """
        point = self._arg(args, "point_nm")
        module = self._cadcore_module("ops.hatch_measure")
        before = self.l1_text()
        try:
            members = module.boundary_from_point(self.sketch, point=point, tol_nm=args.get("tol_nm"))
        except Exception as exc:  # noqa: BLE001 - no_boundary 是"没找到"，不是"服务端坏了"
            if "no_boundary" in str(exc):
                return {"ok": True, "uuid": "", "command": "boundary_probe", "found": 0, "members": [],
                        "doc_unchanged": 1 if self.l1_text() == before else 0}
            raise
        return {"ok": True, "uuid": "", "command": "boundary_probe", "found": 1,
                "members": [str(item) for item in members],
                "doc_unchanged": 1 if self.l1_text() == before else 0}

    def _gradient_at_point(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """渐变色填充（GRADIENT 口径）：从**内部点**反求边界再记定义（边界也可直接给 uuid 序列）。

        本层 L1 没有颜色字段 ⇒ 真件**新建 0 个几何**（`created=0` 是真为零，不是"没算"）；
        定义落进填充表的 gradients 列表，L1 因此确实变了。
        """
        module = self._cadcore_module("ops.hatch_measure")
        boundary = args.get("boundary")
        if boundary in (None, ""):
            point = self._arg(args, "point_nm")
            boundary = list(module.boundary_from_point(self.sketch, point=point,
                                                       tol_nm=args.get("tol_nm")))
        kwargs = {key: args[key] for key in ("colors", "gradient_name", "angle_deg", "centered",
                                             "layer", "transparency", "island", "tol_nm")
                  if key in args}
        created = self._mutate(lambda: module.gradient_fill(self.sketch, boundary=boundary, **kwargs))
        uuids = self._uuid_list(created)
        return self._command_result("gradient", "", uuids=uuids, created=len(uuids),
                                    boundary=[str(item) for item in boundary])

    def _grip_positions(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """夹点查询（只读）：`ref` 指向实体的可拖拽夹点表（`名称 → [x,y]` 整数纳米）。"""
        ref = str(self._arg(args, "ref"))
        module = self._cadcore_module("ops.edit_ops")
        before = self.l1_text()
        positions = module.grip_positions(self.sketch, ref=ref)
        return {
            "ok": True,
            "uuid": ref,
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": "grip_positions",
            "grips": {str(key): [int(value[0]), int(value[1])] for key, value in dict(positions).items()},
            "doc_unchanged": 1 if self.l1_text() == before else 0,
        }

    def command(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """执行一条编辑命令；几何规则全部由 `cadcore.sketch.commands` 把关。"""
        with self._lock:
            verb = str(name or "").strip()
            if not isinstance(args, Mapping):
                raise ApiError("bad_arguments", "args must be a JSON object")
            try:
                return self._command_locked(verb, args)
            except ApiError:
                raise
            except Exception as exc:  # noqa: BLE001 - cadcore 的 SketchError 等统一转机读码
                # cadcore 的 `CadError` 把机读码放在 **`reason`**（`errors.py:32`），不叫 `key`；
                # 只读 `key` ⇒ 文档级直通（图层面/出图面）的拒绝理由在响应里一律变 `null`，
                # 下游只能去解析文案。两个都认（`key` 优先：草图侧既有口径一字不变）。
                key = getattr(exc, "key", None) or getattr(exc, "reason", None)
                raise ApiError("command_failed", f"{type(exc).__name__}: {exc}",
                               command=verb, cadcore_key=key) from exc

    def _command_locked(self, verb: str, args: Mapping[str, Any]) -> dict[str, Any]:
        sketch = self.sketch
        from solid_commands import COMMANDS as SOLID_COMMANDS, EDIT_COMMANDS, execute as solid_execute

        if verb in SOLID_COMMANDS or verb in EDIT_COMMANDS:
            return solid_execute(self, verb, args)
        if verb in ("extrude", "revolve", "edit_solid"):
            from cadcore.ops import sw_features
            from solid_commands import validate as validate_solids

            def solid_action():
                if verb == "edit_solid":
                    result = sw_features.edit_feature(self.doc, **args)
                elif verb == "revolve":
                    result = sw_features.revolve_boss(self.doc, **args)
                else:
                    result = sw_features.extrude_boss(self.doc, **args)
                validate_solids(self)
                return result

            uuid = self._mutate(solid_action)
        elif verb == "add_line":
            uuid = self._mutate(lambda: self.commands.add_line(
                sketch,
                p1=self._arg(args, "p1"),
                p2=self._arg(args, "p2"),
                layer=str(args.get("layer", "0")),
                construction=bool(args.get("construction", False)),
            ))
        elif verb == "add_circle":
            uuid = self._mutate(lambda: self.commands.add_circle(
                sketch,
                center=self._arg(args, "center"),
                radius_nm=self._arg(args, "radius_nm"),
                layer=str(args.get("layer", "0")),
                construction=bool(args.get("construction", False)),
            ))
        elif verb == "add_arc":
            uuid = self._mutate(lambda: self.commands.add_arc(
                sketch,
                center=self._arg(args, "center"),
                radius_nm=self._arg(args, "radius_nm"),
                start_deg=self._arg(args, "start_deg"),
                end_deg=self._arg(args, "end_deg"),
                layer=str(args.get("layer", "0")),
                construction=bool(args.get("construction", False)),
            ))
        elif verb == "add_dimension":
            uuid = self._mutate(lambda: self.commands.add_dimension(
                sketch,
                kind=str(self._arg(args, "kind")),
                refs=self._arg(args, "refs"),
                value_nm=args.get("value_nm"),
                text_key=args.get("text_key"),
            ))
        elif verb in self._PASSTHROUGH:
            # 直通 cadcore：几何规则、入参校验、退化判定全在那边，本文件只接线。
            module = self._cadcore_module(self._PASSTHROUGH[verb])
            result = self._mutate(lambda: getattr(module, verb)(sketch, **dict(args)))
            uuids = self._uuid_list(result)
            return self._command_result(verb, uuids[0] if uuids else "",
                                        uuids=uuids, created=len(uuids))
        elif verb in self._DOC_PASSTHROUGH:
            # 文档级直通（图层面 / 出图面）：第一实参是真文档；只读动词不压撤销栈。
            # **取 OPERATIONS 里的冻结入口**，不是模块同名属性：族模块里常有"宽窄合一"的一对
            # （例：`create_viewport` 需要显式 kernel，而 `OPERATIONS["create_viewport"]` =
            # `create_viewport_registered` 会自己走 auto 内核并补缺省布局）——直接 getattr 同名函数
            # 会绕过家族冻结口径（实测：服务端 create_viewport 一律 `dependency_missing`）。
            module = self._cadcore_module(self._DOC_PASSTHROUGH[verb])
            operations = getattr(module, "OPERATIONS", None) or {}
            entry = operations.get(verb) or getattr(module, verb)
            # 写盘动词的路径参数先钉进落盘根（根外绝对路径/`..`/NUL/反斜杠一律 `path_rejected`）：
            # 与 `/api/save` / `/api/export/dxf` 同一口径，别让新开的口子成为越权写盘的旁门。
            payload = dict(args)
            for key in self._DOC_PATH_ARGS.get(verb, ()):
                if payload.get(key):
                    payload[key] = self._safe_write_path(payload[key])
            if verb in self._DOC_QUERY:
                result = entry(self.doc, **payload)
                uuids = self._uuid_list(result)
                return self._command_result(verb, uuids[0] if uuids else "",
                                            uuids=uuids, count=len(uuids))
            from cadcore.io.write_guard import output_guard
            with output_guard(self._safe_write_path):
                result = self._mutate(lambda: entry(self.doc, **payload))
            uuids = self._uuid_list(result)
            return self._command_result(verb, uuids[0] if uuids else "",
                                        uuids=uuids, created=len(uuids))
        elif verb == "delete_constraint":
            constraint_uuid = str(self._arg(args, "constraint_uuid"))
            result = self._mutate(
                lambda: self.constraints.delete_constraint(sketch, constraint_uuid))
            return self._command_result(verb, constraint_uuid,
                                        constraints=self.constraint_count(),
                                        solve=self._solve_payload(result))
        elif verb == "snap_query":
            # 这是**查询**：不进 `_mutate`（不压撤销栈、不动文档），命不中就如实回 hit=0/None。
            # 合并优先级 + 追加类型（切点/外观交点/延长线/平行）见 `_snap_query`；真源跑在深拷贝上。
            try:
                return self._snap_query(args)
            except (TypeError, ValueError) as exc:
                # `precision.osnap` 的入参把关抛**内建**异常（该层的既有约定）。直接放它出去会变成
                # `command_failed` + `"detail": "ValueError: …"`：既把"你参数不对"说成"服务端失败"，
                # 又把内部异常类型泄漏进响应（B6 缺陷 `contract_leak`，实测 9 条同形）。
                # 参数合法性是 API 层的职责（与 `/api/standard` 的 `_validated_standard` 同一取舍）：
                # 在这里转成结构化 400，detail 保留**原文**，异常链 `from exc` 不吞根因。
                raise ApiError("invalid_snap_argument", str(exc), status=400, command=verb) from exc
        elif verb in self._PASSTHROUGH_EXTRA:
            # 追加直通（族里已实现、只差用户面入口的那些命令）：几何规则全在 cadcore，本层只接线。
            module_name, function_name = self._PASSTHROUGH_EXTRA[verb]
            module = self._cadcore_module(module_name)
            result = self._mutate(lambda: getattr(module, function_name)(sketch, **dict(args)))
            uuids = self._uuid_list(result)
            return self._command_result(verb, uuids[0] if uuids else "",
                                        uuids=uuids, created=len(uuids))
        elif verb in self._PRECISION_SETTINGS:
            # 精度设置：目标 = **文档**（设置落 doc.extra["precision"]，随 L1 往返存活）。
            # 不改几何，但仍走 `_mutate` —— 失败必须回滚，真源里不留半截设置。
            module = self._cadcore_module("ops.precision_ops")
            result = self._mutate(lambda: getattr(module, self._PRECISION_SETTINGS[verb])(
                self.doc, **dict(args)))
            uuids = self._uuid_list(result)
            return self._command_result(verb, uuids[0] if uuids else "",
                                        uuids=uuids, count=len(uuids))
        elif verb in self._MEASURE_VERBS:
            # 只读测量（MEASUREGEOM 的三条腿）：token 原样回给前端，NA/0 分形可见。
            return self._measure(verb, args)
        elif verb == "boundary":
            return self._boundary_at_point(args)
        elif verb == "boundary_probe":
            return self._boundary_probe(args)
        elif verb == "hatch":
            return self._hatch_at_point(args)
        elif verb == "gradient":
            return self._gradient_at_point(args)
        elif verb == "precision_pick":
            return self._precision_pick(args)
        elif verb == "precision_status":
            return self._precision_status()
        elif verb == "grip_positions":
            return self._grip_positions(args)
        elif verb == "delete_entity":
            with self._lock:
                before = self._snapshot()
                result = self._delete_entity(str(self._arg(args, "uuid")))
                self.undo_stack.append(before)
                self.redo_stack.clear()
            return {
                "ok": True,
                "uuid": result["uuid"],
                "entities": self.entity_count(),
                "dimensions": self.dimension_count(),
                "removed_dimensions": result["removed_dimensions"],
                "command": verb,
            }
        elif verb == "undo":
            if not self.undo_stack:
                raise ApiError("nothing_to_undo", "undo stack is empty")
            text = self.undo_stack.pop()
            self.redo_stack.append(self._snapshot())
            self._restore(text)
            return {"ok": True, "uuid": "", "entities": self.entity_count(),
                    "dimensions": self.dimension_count(), "command": verb,
                    "undo_depth": len(self.undo_stack), "redo_depth": len(self.redo_stack)}
        elif verb == "redo":
            if not self.redo_stack:
                raise ApiError("nothing_to_redo", "redo stack is empty")
            text = self.redo_stack.pop()
            self.undo_stack.append(self._snapshot())
            self._restore(text)
            return {"ok": True, "uuid": "", "entities": self.entity_count(),
                    "dimensions": self.dimension_count(), "command": verb,
                    "undo_depth": len(self.undo_stack), "redo_depth": len(self.redo_stack)}
        elif verb == "export_pdf":
            out = args.get("out")
            return self.export_pdf(out if out not in (None, "") else self.DEFAULT_PDF_OUT,
                                   sheet_uuid=args.get("sheet_uuid"))
        else:
            raise ApiError("unknown_command", f"unsupported command {verb!r}", command=verb)
        return {
            "ok": True,
            "uuid": str(uuid),
            "entities": self.entity_count(),
            "dimensions": self.dimension_count(),
            "command": verb,
            "undo_depth": len(self.undo_stack),
            "redo_depth": len(self.redo_stack),
        }

    #: `/api/command` 的 `export_pdf` 缺省落盘名（相对 `out_root`，仍是相对路径 ⇒ 逃逸判定不放宽）。
    DEFAULT_PDF_OUT: str = "build/opencad.pdf"

    def active_language(self) -> str:
        """当前活动语言码；取不到时回落 `en`（语言只影响标题栏文案，不该阻断出图）。"""
        try:
            return str(self.catalog.active_locale() or "en")
        except Exception:  # noqa: BLE001 - 语言层缺席不阻断 PDF 导出
            return "en"

    def _sheet_for_pdf(self, sheet_uuid: Any = None) -> Any:
        """当前 L1 文档 → **一张真图纸**（幅面/标准档/标题栏由 `draw.sheet.make_sheet` 给）。

        `sheet_uuid` 缺省 = 当前图纸；给了一个文档里不存在的 uuid ⇒ 显式 `sheet_not_found`，
        **不许**静默改画别的图（"画错图纸还报成功"比报错坏得多）。本文件不造几何。
        """
        known = [str(item.get("uuid") or "") for item in self.doc.sketches]
        requested = "" if sheet_uuid in (None, "") else str(sheet_uuid)
        if requested and requested not in known:
            raise ApiError("sheet_not_found", f"no sketch with uuid {requested!r}",
                           sheet_uuid=requested)
        sheet_mod = self._cadcore_module("draw.sheet")
        return sheet_mod.make_sheet(self.doc, standard=self.standard,
                                    language=self.active_language())

    def export_pdf(self, raw_path: Any = None, *, sheet_uuid: Any = None) -> dict[str, Any]:
        """真出 PDF 字节并落盘（`ops.sw_drawings_extra.render_pdf`，纯标准库写出器）。

        `magic` 是**现读落盘文件头**得到的（读文件头 `len(PDF_MAGIC)` 字节），
        不是把常量回显一遍 —— 写出器真坏了这里就会露出来。
        """
        with self._lock:
            target = self._safe_write_path(self.DEFAULT_PDF_OUT if raw_path in (None, "") else raw_path)
            module = self._cadcore_module("ops.sw_drawings_extra")
            sheet = self._sheet_for_pdf(sheet_uuid)
            try:
                payload = module.render_pdf(sheet)
            except Exception as exc:  # noqa: BLE001 - 导出失败如实报错
                raise ApiError("export_failed", f"{type(exc).__name__}: {exc}") from exc
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(payload)
            with open(target, "rb") as handle:  # 真读文件头，不是信 payload 的前缀
                head = handle.read(len(module.PDF_MAGIC))
            facts = module.pdf_facts(sheet)
            return {
                "ok": True,
                "path": target,
                "bytes": os.path.getsize(target),
                "magic": head.decode("latin-1"),
                "writer": str(facts["writer"]),
                "pages": int(facts["pages"]),
                "sheet_uuid": (str(sheet_uuid) if sheet_uuid not in (None, "") else
                               str(self.sketch.get("uuid") or "")),
                "pdf_sheet_uuid": str(getattr(sheet, "uuid", "") or ""),
                "entities": int(facts["entities"]),
                "paths": int(facts["paths"]),
                "stream_bytes": int(facts["stream_bytes"]),
                "document_entities": self.entity_count(),
            }

    # ── 落盘 / 读回 / 导出 ──────────────────────────────────────────────────────────
    def _safe_path(self, raw: Any, *, base: str | None = None) -> str:
        """把请求里的路径钉在**落盘根**内：`..` 逃逸 / 根外绝对路径 / NUL / 反斜杠一律拒绝。

        落盘根 = `base or self.out_root`（`--out-root` / `OPENCAD_OUT_ROOT` 给定，缺省本应用目录）。
        两条口径写在这里，别再改回去：

        1. **根与候选同口径 `realpath`**：用户给的 out-root 常常本身是软链接（Windows 的"文档"
           重定向、Linux 的 `/hf3fs-jd -> /3fs/research3fs`）。旧实现根走 `abspath`、候选走
           `realpath` ⇒ 把**根自己**判成"逃逸"（实测：out-root 内的 `staged-e2e.pdf` 也报
           `path escapes the app directory`）。
        2. **根内允许绝对路径**：这正是"导出到用户自己的文档目录"的用法 —— 起服务时
           `--out-root <文档目录>`，API 里给该目录下的完整路径或相对名（相对名以落盘根为基准，
           **不看进程 cwd**）。根之外的绝对路径照样 `path_rejected`：防越权判据是"在不在 out-root 内"，
           与路径怎么写无关。
        """
        text = "" if raw is None else str(raw).strip()
        if not text or "\x00" in text or "\\" in text:
            raise ApiError("path_rejected", f"unsafe path {text!r}")
        root = os.path.realpath(base or self.out_root)
        candidate = os.path.realpath(text if os.path.isabs(text) else os.path.join(root, text))
        if candidate != root and not candidate.startswith(root + os.sep):
            raise ApiError("path_rejected", f"path escapes the out-root {root!r}: {text!r}")
        return candidate

    def _safe_write_path(self, raw: Any) -> str:
        from release_state import assert_writable
        with self._lock:
            target = self._safe_path(raw)
            assert_writable(self, target)
            return target

    def save(self, raw_path: Any) -> dict[str, Any]:
        with self._lock:
            target = self._safe_write_path(raw_path)
            path = self.docmodel.save_l1(self.doc, target)
            size = len(self.l1_text().encode("utf-8"))
            return {"ok": True, "path": path, "bytes": size, "entities": self.entity_count()}

    def open_doc(self, raw_path: Any) -> dict[str, Any]:
        with self._lock:
            target = self._safe_path(raw_path)
            if not os.path.isfile(target):
                raise ApiError("file_not_found", f"no such document: {target}")
            try:
                doc = self.docmodel.load_l1(target)
            except Exception as exc:  # noqa: BLE001 - 坏文件如实报错，不静默换成空文档
                raise ApiError("document_unreadable", f"{type(exc).__name__}: {exc}") from exc
            # 装载闸（B6 缺陷 RC1b/RC9）：非法真源（布尔/字符串/浮点长度、越界值、非 i18n key 的 text_key、
            # 引用形状错）**在装进工作区之前**结构化拒收 —— 否则它们静默落库，直到 /api/state 序列化或导出才崩。
            # 判据来自产品自己的 `check_invariants` + `nm_in_range`（同一把尺子，不另立一套）。
            problems = self.docmodel.load_blocking_problems(doc)
            if problems:
                raise ApiError("invalid_document",
                               "document violates the L1 value contract: " + "; ".join(problems[:8]),
                               problems=problems, command="open", path=target)
            self.doc = doc
            if not self.doc.sketches:
                self.doc.sketches.append(self._new_sketch())
            self.undo_stack.clear()
            self.redo_stack.clear()
            self._apply_standard(self.standard)
            return {"ok": True, "doc": self.l1_text(), "entities": self.entity_count(),
                    "path": target, "dimensions": self.dimension_count()}

    def _merged_sketch(self) -> dict[str, Any]:
        """把文档里所有草图的实体/尺寸并成一份"导出用草图"（几何一条不少，只做拼接）。"""
        merged = self._new_sketch()
        for sketch in self.doc.sketches:
            merged["entities"].extend(sketch.get("entities") or [])
            merged["dimensions"].extend(sketch.get("dimensions") or [])
        return merged

    def export_dxf(self, raw_path: Any) -> dict[str, Any]:
        """真出 DXF 字节并落盘；`dimension_entities` 从字节里数出来（数出 0 就报 0）。"""
        with self._lock:
            target = self._safe_write_path(raw_path)
            merged = self._merged_sketch()
            # L1 标注（`doc.annotations`）是**文档级**真源，草图导出也要消费它（B6 缺陷 RC7：
            # 旧行为是"接受 + 往返保留 + 产物里一个字都没有 + 无诊断"）。承载方式见
            # `dxf_bridge.annotation_text_lines`：走 `999` 注释，不伪造位置。
            annotation_lines = self.dxf_bridge.annotation_text_lines(self.doc.annotations)
            try:
                payload = self.dxf_bridge.sketch_to_dxf(merged, annotations=self.doc.annotations)
            except Exception as exc:  # noqa: BLE001 - 导出失败如实报错
                raise ApiError("export_failed", f"{type(exc).__name__}: {exc}") from exc
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(payload)
            written = os.path.getsize(target)
            return {
                "ok": True,
                "path": target,
                "bytes": written,
                "dimension_entities": _count_dxf_records(payload, "DIMENSION"),
                "dxf_mode": "sketch",
                "document_dimensions": self.dimension_count(),
                "document_annotations": len(self.doc.annotations),
                "annotation_texts": len(annotation_lines),
                "note": ("sketch-mode export: L1 sketch dimensions carry no sheet placement, so "
                         "dxf_bridge records them as a 999 comment instead of DIMENSION entities "
                         "(fabricating coordinates is not allowed); annotation texts are carried "
                         "the same way"),
                "entities": self.entity_count(),
            }


# --------------------------------------------------------------------------------------
# HTTP 层
# --------------------------------------------------------------------------------------

ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/state"),
    ("GET", "/api/mesh"),
    ("GET", "/api/step"),
    ("GET", "/api/catalogs"),
    ("GET", "/api/open"),
    ("POST", "/api/locale"),
    ("POST", "/api/standard"),
    ("POST", "/api/command"),
    ("POST", "/api/export/dxf"),
    ("POST", "/api/export/step"),
    ("POST", "/api/save"),
    ("POST", "/api/clearance"),
    ("POST", "/api/release/prepare"),
    ("POST", "/api/release/restore"),
)

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

#: 禁外部 CDN / 外链：由响应头**结构性**保证（不是靠"记得别写"）。
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    """JSON API + `web/` 静态文件。未知路由 404；业务失败 200 + 响应体 `ok:false`；
    **请求参数不合法 400**（`ApiError(status=400)`，如 `/api/standard` 的非法 standard）。"""

    server_version = "OpenCAD/0.1"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ── 基础收发 ────────────────────────────────────────────────────────────────────
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib 命名
        if getattr(self.server, "verbose", False):  # 缺省安静（判定行才是机器读的那份）
            sys.stderr.write("opencad: " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Mapping[str, Any], status: int = 200) -> None:
        body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _not_found(self) -> None:
        self._json({"ok": False, "error": "not_found", "path": urlsplit(self.path).path}, status=404)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > 16 * 1024 * 1024:
            raise ApiError("body_too_large", "JSON request body exceeds 16 MiB", status=413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("bad_json_body", f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ApiError("bad_json_body", "request body must be a JSON object")
        return dict(payload)

    # ── 静态文件 ────────────────────────────────────────────────────────────────────
    def _serve_static(self, rel: str) -> None:
        workspace: Workspace = self.server.workspace  # type: ignore[attr-defined]
        if not rel:
            rel = "index.html"
        try:
            target = workspace._safe_path(rel, base=workspace.web_dir)
        except ApiError:
            self._not_found()
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self._not_found()
            return
        with open(target, "rb") as handle:
            body = handle.read()
        ext = os.path.splitext(target)[1].lower()
        self._send(200, body, _MIME.get(ext, "application/octet-stream"))

    # ── GET ─────────────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 - stdlib 命名
        workspace: Workspace = self.server.workspace  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
                return
            if path == "/web/3d.html":
                from viewer3d_page import render

                self._send(200, render(advanced=True).encode("utf-8"), _MIME[".html"])
                return
            if path.startswith("/web/"):
                self._serve_static(path[len("/web/"):])
                return
            if path == "/api/state":
                self._json(workspace.state())
                return
            if path == "/api/mesh":
                self._json(workspace.mesh())
                return
            if path == "/api/step":
                self._send(200, workspace.step_bytes(), "application/step")
                return
            if path == "/api/catalogs":
                self._json(workspace.catalogs())
                return
            if path == "/api/open":
                query = parse_qs(parts.query)
                raw = (query.get("path") or [""])[0]
                self._json(workspace.open_doc(raw))
                return
        except ApiError as exc:
            self._json(exc.payload(), status=exc.status)
            return
        except Exception as exc:  # noqa: BLE001 - 未预期异常也要带 ok:false 回，不许 500 空体
            self._json({"ok": False, "error": "internal_error",
                        "detail": f"{type(exc).__name__}: {exc}"})
            return
        self._not_found()

    # ── POST ────────────────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802 - stdlib 命名
        workspace: Workspace = self.server.workspace  # type: ignore[attr-defined]
        path = urlsplit(self.path).path
        try:
            body = self._read_json_body()
            if path == "/api/clearance":
                self._json(workspace.clearance(body))
                return
            if path == "/api/release/prepare":
                from release_state import prepare
                self._json(prepare(workspace, body))
                return
            if path == "/api/release/restore":
                from release_state import restore_empty
                self._json(restore_empty(workspace, body))
                return
            if path == "/api/locale":
                self._json(workspace.set_locale(str(body.get("locale", ""))))
                return
            if path == "/api/export/step":
                self._json(workspace.export_step(body.get("path", "build/opencad.step")))
                return
            if path == "/api/standard":
                # 入参**原样**传下去（不先 `str()`）：None / 数字 / 缺失要能各自被认出来报 400，
                # 而不是被 `str(None)` 变成字面量 "None" 再当成"未知档"。
                self._json(workspace.set_standard(body.get("standard", None)))
                return
            if path == "/api/command":
                self._json(workspace.command(str(body.get("command", "")),
                                             body.get("args") or {}))
                return
            if path == "/api/export/dxf":
                self._json(workspace.export_dxf(body.get("path", "build/opencad.dxf")))
                return
            if path == "/api/save":
                self._json(workspace.save(body.get("path", "build/opencad.json")))
                return
        except ApiError as exc:
            self._json(exc.payload(), status=exc.status)
            return
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": "internal_error",
                        "detail": f"{type(exc).__name__}: {exc}"})
            return
        self._not_found()


def build_server(core: Mapping[str, Any] | None, *, host: str, port: int, locale: str,
                 standard: str, web_dir: str = WEB_DIR, out_root: str = APP_DIR,
                 verbose: bool = False) -> ThreadingHTTPServer:
    """装好一个可 `serve_forever()` 的服务器（端口 0 由 OS 分配，实际端口读 `server_address`）。"""
    workspace = Workspace(core if core is not None else _load_cadcore(), locale=locale,
                          standard=standard, web_dir=web_dir, out_root=out_root)
    httpd = ThreadingHTTPServer((host, int(port)), Handler)
    httpd.daemon_threads = True
    httpd.workspace = workspace  # type: ignore[attr-defined]
    httpd.verbose = bool(verbose)  # type: ignore[attr-defined]
    try:
        from release_state import acknowledge_startup
        acknowledge_startup(workspace)
    except Exception:
        httpd.server_close()
        raise
    return httpd


# --------------------------------------------------------------------------------------
# 自检（起服务 → 真打 API → 断言 ok）
# --------------------------------------------------------------------------------------


def _post(base: str, route: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base + route, data=json.dumps(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(base: str, route: str) -> dict[str, Any]:
    with urllib.request.urlopen(base + route, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def selftest() -> int:
    """内置最小自检：起服务、真打 API、逐条断言 ok。rc=0 通过 / rc=1 失败。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    try:
        core = _load_cadcore()
    except BootstrapError as exc:
        print(f"OPENCAD_SERVER selftest verdict=FAIL reason={exc.reason} detail={exc.detail}")
        return 1
    httpd = build_server(core, host="127.0.0.1", port=0, locale="zh-CN", standard="gbt")
    port = int(httpd.server_address[1])
    base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1},
                              daemon=True)
    thread.start()
    try:
        state = _get(base, "/api/state")
        check("state.ok", state.get("ok") is True, str(state.get("error", "")))
        check("state.doc_is_l1", isinstance(state.get("doc"), str) and '"schema_version"' in state["doc"])
        check("state.strings", isinstance(state.get("strings"), Mapping) and bool(state.get("strings")))
        check("state.projection", state.get("projection") in ("first", "third", "NA"),
              str(state.get("projection")))

        locale = _post(base, "/api/locale", {"locale": "ja"})
        check("locale.ok", locale.get("ok") is True, str(locale.get("error", "")))
        check("locale.changed", locale.get("locale") == "ja" and locale.get("changed") in (0, 1))
        check("locale.strings_replaced", isinstance(locale.get("strings"), Mapping)
              and bool(locale.get("strings")))

        standard = _post(base, "/api/standard", {"standard": "ansi"})
        check("standard.ok", standard.get("ok") is True, str(standard.get("error", "")))
        check("standard.projection", standard.get("projection") in ("first", "third", "NA"))

        line = _post(base, "/api/command", {"command": "add_line",
                                            "args": {"p1": [0, 0], "p2": [10000000, 0]}})
        check("command.ok", line.get("ok") is True, str(line.get("error", "")))
        check("command.uuid", isinstance(line.get("uuid"), str) and bool(line.get("uuid")))
        check("command.entities", line.get("entities") == 1, str(line.get("entities")))

        exported = _post(base, "/api/export/dxf", {"path": "build/opencad.dxf"})
        check("export.ok", exported.get("ok") is True, str(exported.get("error", "")))
        check("export.bytes", isinstance(exported.get("bytes"), int) and exported["bytes"] > 0,
              str(exported.get("bytes")))
        check("export.dimension_entities",
              isinstance(exported.get("dimension_entities"), int))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    failed = [name for name, passed, _ in checks if not passed]
    verdict = "PASS" if not failed else "FAIL"
    print(f"OPENCAD_SERVER selftest verdict={verdict} checks={len(checks)} failed={len(failed)}")
    for name, passed, detail in checks:
        if not passed:
            print(f"OPENCAD_SERVER selftest check={name} status=FAIL detail={detail}")
    return 0 if not failed else 1


# --------------------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------------------


def _fail(reason: str, detail: str = "") -> int:
    line = f"OPENCAD_SERVER verdict=FAIL reason={reason}"
    if detail:
        line += f" detail={detail}"
    print(line, flush=True)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OpenCAD local web shell server (stdlib only).",
        epilog="startup prints `OPENCAD_SERVER verdict=PASS port=<n> routes=<n>`")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766,
                        help="listen port; 0 lets the OS pick (read it from the verdict line)")
    parser.add_argument("--locale", default="zh-CN")
    parser.add_argument("--standard", default="gbt")
    parser.add_argument("--web", default=WEB_DIR)
    parser.add_argument("--out-root", default=default_out_root(),
                        help="base directory that /api/save, /api/open, /api/export/dxf and "
                             "/api/command export_pdf resolve paths against; paths inside it "
                             "(relative or absolute) are accepted, anything outside is rejected. "
                             "Point it at your own folder to export to e.g. Documents "
                             "(default: $OPENCAD_OUT_ROOT or the app directory)")
    parser.add_argument("--verbose", action="store_true", help="log every request to stderr")
    parser.add_argument("--selftest", action="store_true",
                        help="start a throwaway server and assert the API contract")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if not 0 <= int(args.port) <= 65535:
        return _fail("port_range", f"--port must be in 0..65535 (got {args.port})")
    try:
        core = _load_cadcore()
    except BootstrapError as exc:
        return _fail(exc.reason, exc.detail)
    try:
        httpd = build_server(core, host=args.host, port=args.port, locale=args.locale,
                             standard=args.standard, web_dir=args.web, verbose=args.verbose,
                             out_root=args.out_root)
    except ApiError as exc:
        return _fail(exc.code, exc.detail)
    except OSError as exc:
        return _fail("bind_failed", f"{type(exc).__name__}: {exc}")

    port = int(httpd.server_address[1])
    print(f"OPENCAD_SERVER verdict=PASS port={port} routes={len(ROUTES)}", flush=True)

    stopping = False

    def _stop(_signum: int, _frame: Any) -> None:
        # A signal can interrupt Event.wait while its non-reentrant lock is held.
        nonlocal stopping
        stopping = True

    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            try:
                signal.signal(getattr(signal, signame), _stop)
            except (ValueError, OSError):  # pragma: no cover - 非主线程/受限平台
                pass
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2},
                              daemon=True)
    thread.start()
    try:
        while not stopping:
            time.sleep(0.2)
    except KeyboardInterrupt:  # pragma: no cover - 兜底
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    print(f"OPENCAD_SERVER verdict=STOP port={port}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
