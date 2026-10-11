"""cadcore.precision —— E3 精度辅助包：对象捕捉（osnap）/ 正交锁定 / 极轴追踪 / 栅格步进。

一句话用途
----------
二维草图里的"精度族"基本集：把光标点吸附到已有几何特征或规则轴上，让画出来的坐标是**整数纳米**的
干净数（AutoCAD 的 Object Snap / Ortho / Polar Tracking / Snap and Grid 对应物）。

口径
----
- 纯几何、**无状态**：不改草图、不写盘、无 IO、无随机；同一输入必得同一输出。
- 坐标一律整数纳米；实体契约见 L1：`{"entities":[{uuid,kind,layer,construction,geom}], "constraints":[], "dimensions":[]}`，
  `kind ∈ line/arc/circle/polyline`。
- 命中优先级：`endpoint > midpoint > center > quadrant > intersection > perpendicular > nearest`。
- `intersection` 只做 `line×line` 与 `line×circle`，无解返回 `None`，**绝不抛异常**。

本包只有 `osnap` 一个实现文件；对外入口全部在这里再导出一次，两条 import 路径都可用。
"""

from __future__ import annotations

from .osnap import (
    QUADRANT_DEG as QUADRANT_DEG,
    SNAP_KINDS as SNAP_KINDS,
    SnapResult as SnapResult,
    grid_snap as grid_snap,
    ortho_lock as ortho_lock,
    polar_track as polar_track,
    snap as snap,
)

__all__ = [
    "QUADRANT_DEG",
    "SNAP_KINDS",
    "SnapResult",
    "grid_snap",
    "ortho_lock",
    "polar_track",
    "snap",
]
