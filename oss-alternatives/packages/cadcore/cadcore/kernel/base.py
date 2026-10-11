#!/usr/bin/env python3
"""A6 内核适配层：**唯一换芯口**。

为什么要有这一层：我们明确"不自研 B-Rep/NURBS"（见 `research/cad-prestudy.md` 结论 4），几何要么
用可选重依赖（OCCT / 进程外 FreeCAD），要么用自带的纯 Python 兜底后端。上层（`draw/sheet.py` 等）
只认这里的 `KernelAdapter` 协议，**不认识任何具体内核**；换芯只改注册表，不改业务代码。

三条硬口径：
1. **缺席必须可观测**：`get_kernel("auto")` 按 `KERNEL_ORDER` 找第一个 `available()=True` 的后端；
   全都不可用 ⇒ 抛 `DependencyMissing(reason="kernel_absent")`，绝不返回 None 让上层画出空图。
2. **能力是集合不是布尔**：`capabilities()` 返回 `frozenset[str]`；上层按 `"project2d" in caps` 判，
   不靠 `hasattr` 猜（缺能力要能与"实现了但返回空"区分开）。
3. **降级链固定**：`occt → freecad → nano`。`nano` 是纯标准库后端，永远 `available()`，保证
   "三平台 + 重依赖全缺席"时仍能出图（判定行 G2 的兜底路径）。

自动注册是**惰性**的（首次 `get_kernel`/`available_kernels` 才导入子模块）：导入本模块不应拉起
子进程探测（`kernel/subproc.py` 会去找外部可执行文件）。
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Protocol, runtime_checkable

from ..errors import CadError, DependencyMissing

__all__ = ["KernelAdapter", "KERNEL_ORDER", "register_kernel", "registered_kernels", "get_kernel",
           "available_kernels", "kernel_report", "unregister_kernel", "reset_registry",
           "curve_key", "dedupe_curves"]

KERNEL_ORDER: tuple[str, ...] = ("occt", "freecad", "nano")

_SUBMODULES: tuple[str, ...] = (".occt", ".nano", ".subproc")

_REGISTRY: dict[str, Callable[[], Any]] = {}
_REGISTERED = False


def curve_key(item: Any) -> tuple[str, tuple[tuple[int, int], ...]] | None:
    """投影曲线项的判等键 `(kind, 点串)`；读不出点串 ⇒ `None`（调用方原样保留，不丢几何）。

    只认**完全相同**：点串逐点整数相等、`kind` 相等 —— 不做容差合并、不做共线合并
    （那是几何化简，会改变图面；本函数只负责去掉"同一条线画了两遍"）。
    """
    if isinstance(item, dict):
        kind = str(item.get("kind") or "")
        points = item.get("points_nm")
        if points is None:
            points = item.get("points")
    else:
        kind = str(getattr(item, "kind", "") or "")
        points = getattr(item, "points", None)
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return None
    try:
        return (kind, tuple((int(p[0]), int(p[1])) for p in points))
    except (TypeError, ValueError, IndexError):
        return None


def dedupe_curves(curves: Any) -> list[Any]:
    """投影曲线去重：`(kind, 点串)` 完全相同的项**只留第一份**（顺序稳定，其余原样）。

    为什么归内核管（B6 缺陷 RC-V1）：真消隐（OCCT HLR）会把同一条棱分别从
    `VCompound` / `OutLineVCompound` / `Rg1LineVCompound` 里各吐一份，投影结果里就出现
    逐点重复的曲线；它一路进视图、进图纸实体、进 DXF，用户看到的是"同一条线画了两遍"，
    下游按实体去重还会把数量算错。**合并重影线是投影的职责**，不是出图层的补丁 ——
    所以放在唯一换芯口这里，任何内核都走同一条尺子。

    读不出点串的项（自定义形状）一律保留：宁可多留，也不许静默丢几何。
    """
    out: list[Any] = []
    seen: set[tuple[str, tuple[tuple[int, int], ...]]] = set()
    for item in curves or ():
        key = curve_key(item)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out


@runtime_checkable
class KernelAdapter(Protocol):
    """几何后端协议。实现者只需满足这些成员，**不需要继承任何基类**。"""

    name: str
    version: str

    def available(self) -> bool:
        """后端此刻是否真的可用（缺依赖必须 `False`，不许假装）。"""
        ...

    def capabilities(self) -> frozenset[str]:
        """能力集：`sketch` / `extrude` / `revolve` / `boolean` / `project2d` / `mass` / `step`。"""
        ...

    def sketch_to_wire(self, sketch: Any) -> Any:
        """草图 → 线框形状引用。"""
        ...

    def extrude(self, profile: Any, *, distance_nm: int, symmetric: bool = False) -> Any:
        """拉伸轮廓成实体。"""
        ...

    def revolve(self, profile: Any, *, axis: str, angle_deg: float) -> Any:
        """绕轴旋转成实体。"""
        ...

    def boolean(self, op: str, a: Any, b: Any) -> Any:
        """布尔运算 `union` / `cut` / `intersect`。"""
        ...

    def project_to_2d(self, shape: Any, *, view: str, hidden: bool) -> Any:
        """把形状投影成二维曲线（可见/隐藏分桶）。"""
        ...

    def mass_properties(self, shape: Any) -> Any:
        """质量属性（面积/质心/体积）。"""
        ...

    def export(self, shape: Any, *, fmt: str, path: str) -> str:
        """导出 `step` / `iges` / `stl`。"""
        ...


def register_kernel(name: str, factory: Callable[[], Any]) -> None:
    """注册一个后端工厂。同名重复注册 = 覆盖（后者赢，便于实验期换实现并留痕在 report 里）。"""
    key = str(name or "").strip()
    if not key:
        raise CadError("kernel name is required", reason="bad_kernel_name")
    if not callable(factory):
        raise CadError("kernel factory must be callable", reason="bad_kernel_factory", kernel=key)
    _REGISTRY[key] = factory


def unregister_kernel(name: str) -> bool:
    """摘掉一个后端（返回是否真的摘掉）。测试与实验用。"""
    return _REGISTRY.pop(str(name or "").strip(), None) is not None


def reset_registry() -> None:
    """清空注册表**并允许重新惰性注册**（测试隔离用；生产代码不要调）。"""
    global _REGISTERED
    _REGISTRY.clear()
    _REGISTERED = False


def registered_kernels() -> tuple[str, ...]:
    """已注册的后端名（按 `KERNEL_ORDER` 优先、其余按字母序，**确定性**）。"""
    _ensure_registered()
    known = [name for name in KERNEL_ORDER if name in _REGISTRY]
    extra = sorted(name for name in _REGISTRY if name not in KERNEL_ORDER)
    return tuple(known + extra)


def _ensure_registered() -> None:
    """惰性导入子模块并调用它们的 `register()`（缺席/导入失败**不抛**，只记进 report）。"""
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    for relative in _SUBMODULES:
        try:
            module = importlib.import_module(relative, __package__)
        except Exception:  # noqa: BLE001 - 子模块缺席是正常状态（可选后端）
            continue
        hook = getattr(module, "register", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # noqa: BLE001 - 注册失败不能拖垮整个内核层
                continue
            continue
        factory = getattr(module, "make_kernel", None)
        if callable(factory):
            register_kernel(str(getattr(module, "KERNEL_NAME", relative.strip("."))), factory)


def _instantiate(name: str) -> Any:
    factory = _REGISTRY[name]
    return factory()


def _available(kernel: Any) -> tuple[bool, str]:
    """`available()` 的三态判断：True / False / 探测失败（都当不可用，但要能说出原因）。"""
    probe = getattr(kernel, "available", None)
    if not callable(probe):
        return False, "no available()"
    try:
        return bool(probe()), ""
    except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理，原因是证据
        return False, f"available() raised {type(exc).__name__}: {exc}"


def get_kernel(name: str = "auto") -> Any:
    """取一个可用后端。`auto` = `KERNEL_ORDER` 里第一个可用的；显式指定但不可用 ⇒ 抛错。"""
    _ensure_registered()
    wanted = str(name or "auto").strip() or "auto"
    if wanted != "auto":
        if wanted not in _REGISTRY:
            raise DependencyMissing("kernel is not registered", reason="kernel_not_registered",
                                    kernel=wanted, registered=list(registered_kernels()))
        kernel = _instantiate(wanted)
        ok, why = _available(kernel)
        if not ok:
            raise DependencyMissing("kernel is registered but not available",
                                    reason="kernel_unavailable", kernel=wanted, detail=why)
        return kernel
    tried: dict[str, str] = {}
    for candidate in registered_kernels():
        try:
            kernel = _instantiate(candidate)
        except Exception as exc:  # noqa: BLE001 - 工厂炸了要留证，然后试下一个
            tried[candidate] = f"factory raised {type(exc).__name__}: {exc}"
            continue
        ok, why = _available(kernel)
        if ok:
            return kernel
        tried[candidate] = why or "available()=False"
    raise DependencyMissing("no usable geometry kernel", reason="kernel_absent", tried=tried)


def available_kernels() -> dict[str, bool]:
    """逐后端可用性（判定行取证用；**探测失败记 `False`，不记 0/None**）。"""
    _ensure_registered()
    out: dict[str, bool] = {}
    for name in registered_kernels():
        try:
            kernel = _instantiate(name)
        except Exception:  # noqa: BLE001
            out[name] = False
            continue
        out[name] = _available(kernel)[0]
    return out


def kernel_report() -> dict[str, Any]:
    """机读体检报告：注册表 + 逐后端能力集 + 为什么不可用。给判定行与排障用。"""
    _ensure_registered()
    report: dict[str, Any] = {"order": list(KERNEL_ORDER), "registered": list(registered_kernels()),
                              "kernels": {}}
    for name in registered_kernels():
        entry: dict[str, Any] = {}
        try:
            kernel = _instantiate(name)
        except Exception as exc:  # noqa: BLE001
            report["kernels"][name] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            continue
        ok, why = _available(kernel)
        entry["available"] = ok
        if why:
            entry["reason"] = why
        caps = getattr(kernel, "capabilities", None)
        if callable(caps):
            try:
                entry["capabilities"] = sorted(str(item) for item in caps())
            except Exception as exc:  # noqa: BLE001
                entry["capabilities"] = f"NA({type(exc).__name__})"
        entry["version"] = str(getattr(kernel, "version", "NA"))
        report["kernels"][name] = entry
    return report
