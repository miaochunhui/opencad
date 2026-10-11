# cadcore —— 跨平台 CAD 内核（2D 制图 + 3D 能力边界 + 工程图 + 多语言界面）

> **一句话用途**：给跨平台开源 2D/3D 工程制图软件提供**内核层** —— 草图编辑与约束求解、可插拔 3D 内核边界、
> 工程图出图（图框/标题栏/尺寸/DXF 导出）与语言层（多语言界面，语言包为纯 JSON 数据）。
> **关键词**：CAD / CAD kernel / 2D 制图 2D drafting / 3D 建模 3D modeling / 工程图 drawing sheet / DXF /
> 多语言界面 i18n localization / cross-platform / 纯标准库 standard-library-only / 整数纳米 integer nanometer。
> 主体许可 Apache-2.0；**零第三方依赖**（进程内只 import 标准库），详见「背景」第 1 条。

## 用法

命令一律在**仓根** `/data/dev/infra` 或**包目录** `oss-alternatives/packages/cadcore` 执行，下面每条都标了前缀。

### 1. 跑单测（G6）

```bash
cd oss-alternatives/packages/cadcore && python3 -m unittest discover -s tests -t .        # 全量
cd oss-alternatives/packages/cadcore && python3 -m unittest tests.test_i18n_catalog -v    # 单模块
```

`-t .`（顶层目录）与 `tests/__init__.py` 一个都不能省：缺了它 discover 直接抛
`ImportError: Start directory is not importable` —— 一条测试都没跑，却跟"没失败"同形。

### 2. 跑 G1–G6 判定行

```bash
cd /data/dev/infra
python3 oss-alternatives/tools/cad/i18n_gate.py --check --langs en,zh-CN,ja --tree oss-alternatives/packages/cadcore  # G1
python3 oss-alternatives/tools/cad/drawing_smoke.py --model oss-alternatives/packages/cadcore/tests/data/plate.json \
        --standard gbt --lang zh-CN --out build/plate.dxf                                                            # G2
python3 oss-alternatives/tools/cad/kernel_purity_gate.py --tree oss-alternatives/packages/cadcore --selftest         # G4
python3 oss-alternatives/tools/sbom-check.py && python3 oss-alternatives/tools/compliance-check.py                   # G5
cd oss-alternatives/packages/cadcore && python3 -m unittest discover -s tests -t .                                   # G6
```

G3 的正式门脚本 `tools/cad/dxf_fidelity_gate.py` **尚未落盘**，本包语料当前用既有往返门顶着（属替身，不等于 G3 达标）：

```bash
python3 oss-alternatives/tools/dxf-roundtrip-check.py --dir oss-alternatives/packages/cadcore/tests/corpus/dxf --selftest
```

逐门的期望输出与当次实测见文末「判定行」表。统一三态：`rc=0` PASS / `rc=1` 不达标 / `rc=2` 未采(NA)；
**采不到数记 NA 且 rc≠0，不许落进 PASS**。

### 3. 新增一门语言：只加 JSON，不改一行代码

```bash
cd oss-alternatives/packages/cadcore/cadcore/data/i18n
cp en.json de.json                 # ① _meta.code 改成 "de"，strings 的「值」逐条译；键名与 {占位符} 一个都不许动
$EDITOR de.json languages.json      # ② languages.json 增一条：code/native_name/file/font_fallback/default_standard
cd /data/dev/infra
python3 oss-alternatives/tools/cad/i18n_gate.py --check --langs en,zh-CN,ja,de \
        --tree oss-alternatives/packages/cadcore                 # ③ 期望 verdict=PASS，catalogs 随语言数变
cd oss-alternatives/packages/cadcore && python3 -m unittest tests.test_i18n_catalog -v   # ④ 整包照抄 en 会在这里红
```

硬要求（`tests/test_i18n_catalog.py` 逐条打）：`_meta` 八个字段齐全
（`code` / `native_name` / `fallback` / `font_fallback` / `default_standard` / `license` / `source` / `version`）；
键集合与 `en` **逐条相同**（当前 161 键，以文件当次为准）、每键占位符名与数量一致、值不许空白、
**不许整包照抄 `en`**（会被判"像 en 的副本"）。
"不改代码"的依据：`scan_catalogs()` 只扫目录里的 `*.json`（跳过 `languages.json` 与 `_` 前缀文件），
语言码取自 `_meta.code`，`languages.json` 只补显示名与字体回退 —— **源码里没有语言码数组、没有 `if lang == ...` 分支**。
只想临时试一门语言、不想进仓：设 `CADCORE_I18N_DIRS=<目录>`（或放到 `$XDG_DATA_HOME/cadcore/i18n`），
用户级目录优先于随包目录。

## 背景（为什么这么设计）

1. **纯标准库 + 可缺席降级**：3D 重件（OCCT / FreeCAD / Qt）是 LGPL/GPL 系或在许可红线上（R14），所以进程内
   **只 import 标准库**（`json` / `sqlite3` / `subprocess` / `math` / `threading` …），重能力只走**子进程 + 文件级契约**
   （`kernel/subproc.py` 生成脚本交给外部 CLI，本进程从不 `import FreeCAD` / `Part`）。缺席一律**显式**：
   `available()=False` 或抛 `DependencyMissing`，**绝不静默出空图 / 空文件** —— 一个"大概装了"的猜测换来的是
   用户面前的一次崩溃。同理，兄弟模块未落盘时本包仍可导入（`store.py`、`i18n/catalog.py` 零包内硬依赖），
   于是各模块能并行落地、单测能单独跑起来。
2. **语言包数据化**：语言是**数据**不是代码。加一门语言 = 丢一个 JSON，不重编译、不改代码、不重启进程；
   而语言集合是**运行期还在长**的东西（用户级附加目录优先于随包目录），把语言码写成源码数组或按语言分支，
   等于把"支持哪些语言"钉成编译期决定。反向约束同样硬：源码里出现语言码字面量、或出现任何语言的用户文案，
   都算缺陷 —— G1 的 `locale_literals` / `hardcoded_literals` 直接报红。
3. **`0` / `-1` / `NA` 三态**：`0` = 真为零，`-1` = **未采到**，`NA` = 不适用，三者**不许同形** —— 否则"没测"
   会伪装成"测到 0"，下游再也分不出缺陷与事实。落点举例：索引表缺失时 `documents=-1` 而不是 0；
   标准档没取到标准正文的字段写 `null` 并登记进 `na_fields`（**不许编数**）；`projection_angle` 未核标给 `None`
   逼调用方显式处理；出图时没有视图记 `hidden_layer=NA`、标题栏尚未渲染记 `titleblock_lang=-1`（不套用图纸语言）。

## 判定行（G1–G6）

口径抄自 `oss-alternatives/docs/research/cad-base-selection/synthesis/MVP-INTERFACES.md` §D，**不自创**。
**判定一律以脚本当次输出为准**；下表"当次实测"只为说明现状，**不要手抄其中数字**，也不要拿它当复跑证据。

| # | 命令（仓根 `/data/dev/infra` 执行） | 期望判定行 | 当次实测（2026-09-29） |
|---|---|---|---|
| G1 | `python3 oss-alternatives/tools/cad/i18n_gate.py --check --langs en,zh-CN,ja --tree oss-alternatives/packages/cadcore` | `I18N_VERDICT: verdict=PASS catalogs=3 keys_missing=0 keys_extra=0 placeholder_mismatch=0 hardcoded_literals=0 locale_literals=0 standards=5 runtime_switch=1` | rc=0，逐字命中 |
| G2 | `python3 oss-alternatives/tools/cad/drawing_smoke.py --model oss-alternatives/packages/cadcore/tests/data/plate.json --standard gbt --lang zh-CN --out build/plate.dxf` | `DRAWING_SMOKE: verdict=PASS kernel=nano dim_entities=8 projection=first_angle hidden_layer=present titleblock_lang=zh-CN` | **rc=2 `verdict=NA kernel=NA dim_entities=-1`**：`cadcore/kernel/nano.py` 与 `kernel/base.py` 未落盘 ⇒ 内核缺席（未采，不是绿） |
| G3 | `python3 oss-alternatives/tools/cad/dxf_fidelity_gate.py --corpus oss-alternatives/packages/cadcore/tests/corpus/dxf --tolerance 1e-9 --require-unknown-tag-preserved --selftest` | `DXF_FIDELITY: verdict=PASS files=<n> entities=<n> type_counts_match=1 max_coord_delta=0 lost_tags=0 audit_errors=0` | **门脚本未落盘（未采）**；替身往返门 rc=0 `DXF_ROUNDTRIP_SELFTEST verdict=PASS cases=6 failed=0` |
| G4 | `python3 oss-alternatives/tools/cad/kernel_purity_gate.py --tree oss-alternatives/packages/cadcore --selftest` | `KERNEL_PURITY_VERDICT: verdict=PASS heavy_imports_outside_adapter=0 standard_branches=0 gpl_deps=0` | rc=0；另 `files=15 adapter_files=1 dep_tables=0 note=no_dep_tables_found`、自检 `fixtures=6/6 negative_caught=3/3` |
| G5 | `python3 oss-alternatives/tools/sbom-check.py`；`python3 oss-alternatives/tools/compliance-check.py` | 以脚本当次输出为准（**禁手抄**）；`COMPLIANCE … trusted_dwg_hits=0` | compliance rc=0 `verdict=PASS … trusted_dwg_hits=0 trusted_dwg_waived=1`（豁免项是本包测试里的注释）；sbom rc=1 `verdict=FAIL red_deps=4`，红项在 `packages/vault-rs` 与 `apps/freechat-desktop` 的 `Cargo.toml`，**本包无 manifest、零三方依赖** |
| G6 | `cd oss-alternatives/packages/cadcore && python3 -m unittest discover -s tests -t .`（`packages/dxf` 同办） | `OK`，`Ran N tests`（N 以当次输出为准，**不写死**） | rc=0 `Ran 281 tests` + `CADCORE_STORE_TESTS verdict=PASS tests=32 failures=0 errors=0` |

**任一门报红 ⇒ 停在该档修，不许带病铺开**（B2/B5）；门脚本的 `--selftest` 阴性对照必须真能报红，
尺子看不见红则该门不算数。
