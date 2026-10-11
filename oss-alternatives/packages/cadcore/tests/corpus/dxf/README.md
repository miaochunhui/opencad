# tests/corpus/dxf —— DXF 语料（clean-room 手写）+ 机读清单

> 一句话：给 G3 `oss-alternatives/tools/cad/dxf_fidelity_gate.py --corpus <本目录>` 与
> `packages/dxf` 的读/写判据当输入的**四份手写 DXF R12 ASCII 语料**，外加一张"声明了几个实体"的机读清单
> —— 清单与 reader 现算的数字由 `tests/test_fixtures.py` 逐项对账，**声明与事实脱节必红**。
> 关键词：DXF corpus / clean-room fixture / 未知实体保留 / XDATA 保留 / CJK TrueType text / round-trip 语料。

## 出处（clean-room 自证）

* **每一个字节都是手写的**：组码布局取自公开的 DXF 组码表（组码/值成对、`0/SECTION`…`0/ENDSEC`…`0/EOF`），
  **没有**从任何第三方样例图纸、AutoCAD 模板、国标图集或开源 CAD 仓的素材里复制内容，也**没有**用任何
  DXF 导出器生成后再改。
* 每份文件第一条标签都是 `999`（注释）出处行，里面写明 clean-room 与 hand-written；
  `tests/test_fixtures.py::test_01_fixtures_are_real_and_nonempty` 会对这条自证逐文件核。
* 语料里出现的 `MYAPP_*` / `ACAD_PROXY_ENTITY` 是**故意造的"本程序不认识的东西"**，
  用来打"认不出就只保留、不解释、不丢弃"这条判据（`packages/dxf` 的第一属性）。

## 机读清单

下表是**声明**；`entities`/`children`/`unknown`/`xdata`/`layers`/`types` 六列由
`tests/test_fixtures.py::test_03_declared_counts_match_reader` 用 reader **现算**后逐项比对，
文件集合另做**双向差集**（清单里有而盘上没有 = 红；盘上有而清单没写 = 也红）。

<!-- CORPUS-MANIFEST:BEGIN -->
| file | entities | children | unknown | xdata | layers | types | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cjk_text.dxf | 6 | 0 | 0 | 0 | 3 | LINE=4+TEXT=2 | CJK 文字（TrueType 样式 HZ）+ 中文图层名；UTF-8 落盘、GBK 重编码对照 |
| simple_entities.dxf | 9 | 4 | 0 | 0 | 3 | ARC=1+CIRCLE=1+LINE=4+POINT=1+POLYLINE=1+TEXT=1 | 常规实体全家桶 + 老式 POLYLINE/VERTEX/SEQEND 子实体 |
| with_unknown_entity.dxf | 5 | 0 | 2 | 0 | 3 | ACAD_PROXY_ENTITY=1+CIRCLE=1+LINE=2+MYAPP_NOTE=1 | 未知实体 + 代理对象 + 自定义段 MYAPP_META 原样保留 |
| with_xdata.dxf | 3 | 0 | 0 | 3 | 3 | CIRCLE=1+LINE=1+TEXT=1 | XDATA（1001 应用名 + 1000/1040/1070）；一个实体挂两个应用 |
<!-- CORPUS-MANIFEST:END -->

### 口径（这几列各数的是什么）

| 列 | 定义 | 取自 |
|---|---|---|
| `entities` | `ENTITIES` 段的**顶层**实体数（不含跟随子实体） | `Document.entity_count()` |
| `children` | 跟随子实体数（`POLYLINE`→`VERTEX`/`SEQEND` 等） | `entity_count(include_children=True) - entity_count()` |
| `unknown` | 不属于 `KNOWN_ENTITY_TYPES` 的实体数（**只保留不解释**） | `Document.unknown_entities()` |
| `xdata` | 至少带一个组码 ≥1000 标签的实体数 | `Document.xdata_entities()` |
| `layers` | `LAYER` 表里声明的图层数（每份都被实体实际用到） | `Document.layer_names()` |
| `types` | 顶层实体的类型计数，`TYPE=count`，`+` 分隔（**不能**用 `\|`，会咬坏 markdown 表） | `Document.entity_type_counts()` |

口径与 MVP 一致：`0`=真为零、`-1`=未采、`NA`=不适用；**采不到就报 NA，不许静默返 0**。

## 各文件覆盖什么 / 不覆盖什么

| 文件 | 覆盖 | **不**覆盖（别拿它当已测） |
|---|---|---|
| `simple_entities.dxf` | `LINE`×4 闭合矩形、`CIRCLE`、`ARC`、`POINT`、`TEXT`、老式 `POLYLINE`+3×`VERTEX`+`SEQEND`；3 层图层表；TrueType 文字样式表 | 块（`BLOCKS`/`INSERT`）、属性块、3D 实体、多版本头（只有 `$ACADVER=AC1009`） |
| `with_unknown_entity.dxf` | 自定义未知实体 `MYAPP_NOTE`、代理对象 `ACAD_PROXY_ENTITY`、自定义段 `MYAPP_META`；读入时留告警、写出时逐字节带回 | 真实 AutoCAD 产出的代理对象（带二进制代理数据的那种）；本文件是手写的最小形态 |
| `with_xdata.dxf` | XDATA 组码 1000/1040/1070、一个实体挂两个应用（`ACAD` + `MYAPP`）、`APPID` 表登记两个应用名 | 全部 XDATA 组码区间（1002 控制串、1005 句柄、1010/1020/1030 点、1041/1042 距离、1071 长整数）未逐项覆盖 |
| `cjk_text.dxf` | 中文 `TEXT` 正文 + 中文图层名 + TrueType 样式 `HZ`；UTF-8 落盘 + GBK 重编码读回对照 | 竖排/字距/字体缺失回落（那是出图层的事，不是解析层）；**不含任何 SHX 专有字体** |

## 字体与编码（两条硬约束）

* **不许出现 SHX 专有字体**：`STYLE` 表的主字体（组码 3）一律是 TrueType/OpenType（`.ttf`/`.otf`/`.ttc`），
  组码 4（大字体，SHX 专用）**一律缺省**；语料全文搜不到 `.shx` 子串。
  尺子：`tests/test_fixtures.py::test_06_no_shx_proprietary_fonts`（逐文件 + 逐 STYLE 条目）。
* **编码**：四份语料都以 **UTF-8** 字节落盘（仓内可直接 review、可 diff）。
  `cjk_text.dxf` 的 `$DWGCODEPAGE` 按 R12 惯例写 `ANSI_936`（中文码页），
  `tests/test_fixtures.py::test_10_cjk_survives_gbk_reencode` 会把同一份内容**现编成 GBK** 再喂给 reader，
  要求文本、实体计数、读→写字节全部一致 —— 即"UTF-8 落盘"不是靠运气，GBK 路径也真被打到。

## CJK 语料的期望文本（人读版）

`cjk_text.dxf` 的中文正文与图层名是**夹具数据**。为了让 `.py` 里不出现中文字面量
（i18n 门的源码尺子扫的正是"字面量的值里有没有中文"），单测里锁的是 **sha256 指纹**；
下面这张表就是那些指纹的**人读版** —— 改语料时对着它核，再用 `--emit-cjk-digests` 重打指纹：

| 位置 | 正文 | sha256（前 12 位） |
|---|---|---|
| TEXT #1（图层 `标注`，样式 `HZ`） | 底板轮廓 | `e405b12cc9bd` |
| TEXT #2（图层 `标注`，样式 `HZ`） | 四角圆角 R10 | `d2de7450871a` |
| 图层名 A | 轮廓 | `574301cad850` |
| 图层名 B | 标注 | `9875b1f82509` |

三条硬约束（单测逐条打）：① 两个 `TEXT` 都用 TrueType 样式 `HZ`；② 非 ASCII 文本能被 reader 原样读回
（解成纯 ASCII = 乱码，必红）；③ 同一份内容重编成 GBK 后，文本指纹、实体计数、读→写字节全部不变。

## 怎么跑

```bash
# 语料 + plate.json 的对账单测（本目录清单就是它的期望值）
cd oss-alternatives/packages/cadcore && python3 -m unittest tests.test_fixtures -v

# 只读地看一眼现算的清单（改了语料后用它重新生成上面的表）
cd oss-alternatives/packages/cadcore && python3 tests/test_fixtures.py --emit-manifest

# 改了 CJK 语料后重打指纹（连同可读正文一起打印，核对完贴回 test_fixtures.py 的两个常量）
cd oss-alternatives/packages/cadcore && python3 tests/test_fixtures.py --emit-cjk-digests

# 既有往返门（对任意目录，本目录可直接喂）
python3 oss-alternatives/tools/dxf-roundtrip-check.py --dir oss-alternatives/packages/cadcore/tests/corpus/dxf
```

## 改了语料之后（照这个顺序，别跳）

1. 改 `.dxf`（手写，保持 clean-room：**不要**从外部样例复制，也不要用导出器生成）；
2. `python3 tests/test_fixtures.py --emit-manifest` 拿到现算的数字，贴回上面的清单表（`coverage` 列沿用原文即可）；
3. 若动的是 `cjk_text.dxf`：`--emit-cjk-digests` 重打指纹贴回 `test_fixtures.py`，
   并同步上面"CJK 语料的期望文本"表（人读版与指纹必须同时更新，否则又是"两套话"）；
4. `python3 -m unittest tests.test_fixtures` 跑到全绿；
5. 若动了实体类型构成，同步本页"各文件覆盖什么"一节 —— 否则声明又会与事实脱节。

## 已知边界（显式登记，不得当成已完成）

* 语料是**手写 R12**：没有真实 AutoCAD 产出的多版本样本（R14/2000/2007/2018），
  也没有带二进制代理数据的 `ACAD_PROXY_ENTITY`；边界与 `packages/dxf/README.md` 的"已知限制"一致。
* 字节级判据只在**未修改**的前提下成立（`packages/dxf` 的"读→写逐字节相等"）；
  本目录不测"改了实体之后写回"的那条路径。
* 清单只声明**计数**，不声明几何语义；几何量（长度/半径/角度）由 G3 的坐标容差判据去管。
