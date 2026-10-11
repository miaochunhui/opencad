# packages/dxf —— DXF R12 ASCII 解析 / 序列化（CAD 平替第一步）

> 一句话：**读得进、写得回、逐字节不丢**的 DXF 解析层 —— 未知实体与 XDATA 原样保留，
> 未修改的图纸 `read → write` 与输入**逐字节相同**。
> 关键词：DXF / R12 ASCII / CAD 平替 / AutoCAD alternative / round-trip / 未知实体原样保留 /
> clean-room 解析器。

## 它是什么、不是什么

| ✅ 做 | ❌ 不做（依据 [`../../docs/research/cad-prestudy.md`](../../docs/research/cad-prestudy.md)） |
|---|---|
| DXF R12 ASCII 的解析与序列化（LINE/CIRCLE/ARC/TEXT/POINT/LWPOLYLINE/POLYLINE + 表格/图层） | **不碰 DWG**（连读也不自研；只做"用户自备外部转换器"的进程外适配器） |
| 未知段 / 未知实体 / `ACAD_PROXY_ENTITY` / XDATA（组码 1001+）**逐字节原样保留** | 不解释块内实体、不做属性块/3D 实体语义、不做几何求交 |
| 用 `packages/vault` 做图纸/实体索引与 `.fcx` 往返（**不改底座**） | 不引入几何内核（v1 计划用 OCCT；本包只用标准库） |

## 用法

```bash
# 造演示图纸（手写 clean-room 样本，无任何第三方素材）
python3 oss-alternatives/tools/seed-demo-dxf.py --out /tmp/demo-dxf

# 承重判据：读→写逐字节相同 + 幂等
python3 oss-alternatives/tools/dxf-roundtrip-check.py --dir /tmp/demo-dxf
# DXF_ROUNDTRIP verdict=PASS files=2 bytes_equal=2 idempotent=1 entities=15 unknown_preserved=3
python3 oss-alternatives/tools/dxf-roundtrip-check.py --selftest     # 4 个做坏夹具必须 FAIL

cd oss-alternatives/packages/dxf && python3 -m unittest discover -s tests -t .   # 56 项
```

## 为什么"逐字节"能成立（结构性理由，不是碰运气）

DXF 是「组码行 + 值行」成对出现。本包**不重新格式化**：每个 `Tag` 攥着两行**原文与各自的行尾**，
写回时把同一批原文拼回去 ⇒ 未改动即逐字节相等；改动只覆盖**值行文本**（组码行与行尾不动）⇒
"只重写被改的实体"结构性成立。`writer` 还会拿输出与原始字节对撞，不等就抛 `DxfError`，**不静默写出"像"的文件**。

## 已知限制（显式登记，不得当成已完成）

* **没有真实 AutoCAD 产出的多版本样本**（R14/2000/2007/2018）：现有证据全部来自手写 R12 样本 + 单测 + 演示库。
* 按序号索引 ⇒ 中间删实体会让其后实体改号（记 `updated` 而非静默丢）；末尾删/整图删才是干净墓碑。
* 编码按 `utf-8 → gbk → latin-1` 逐个验证"能原样编回"，保证可逆；非这三种编码的图纸未覆盖。
