# opencad — 浏览器里的开源 2D 制图与 3D 实体建模

## 产品形态

主交付是在线 Web 服务，最终用户无需安装本地 CAD 软件。2026-10-11
再次确认以 CATIA／SolidWorks 全功能范围为长期目标；Windows／macOS
安装包与 Qt 桌面壳不列入当前产品开发或 Web 发布前提。
服务部署、现有能力边界和逐模块验收见
[`Web 产品范围与验收契约`](../../docs/design/opencad-web-product-scope.md)。
以下本地启动命令用于服务开发／运维，不是最终用户的安装要求。

## 3D 使用入口

打开 `/web/3d.html`，从闭合草图生成真实 FreeCAD 实体，而非仅显示示意模型。
已有拉伸、旋转和加减布尔；高级面板支持扫掠、放样、实体圆角／倒角、
抽壳、阵列／镜像、STEP 导入导出、参数编辑及特征抑制／恢复。
正视、顶视、右视和等轴视图支持拖动、平移、缩放与适合窗口。
长度输入为毫米，内部使用整数纳米。实体边／面的索引从 1 开始，
编辑后拓扑索引可能变化，不承诺工业级持久拓扑命名。

“间隙／干涉”比较当前完整实体与其平移副本，读取真实表面距离、
最近点及相交体积；不是装配原点测距，也不是装配配合求解器。
扫掠路径是开放折线，首段需垂直于轮廓平面；放样当前支持单闭环，
带孔／多环放样及非零拉伸拔模角明确拒绝，不静默退化。
几何失败不会保留半成品，也不会改写撤销／重做历史。

简体中文界面使用服务端独立语言包。下拉菜单里的 `日本語` 是可选语言
的自称，不代表简体中文正在混用日文；普通按钮和提示必须随当前语言整包更新。
浏览器 WebGL 使用浏览器所在机器的 GPU，服务端实体计算仍由 CPU 内核完成。
现有功能与测试通过不表示已达到 CATIA／SolidWorks 全功能或工业性能对等。

Web 完整回归入口为 `tools/cad3d/web_release_profile.py --stage full`，
只运行明确登记的 Web 阶段，日志和精确源码指纹默认外置。
该入口通过不等于生产部署、多用户安全或工业全功能已验收。
`opencad_acceptance.py` 的安装包与硬件阶段保留为独立历史／专项验证，
不作为 Web 服务发布的前提。
生产重启使用 `tools/cad3d/release_workspace.py --authorized-restart`，
先保存并冻结当前图纸，新进程在接受请求前恢复图纸、语言和标准。
重启会丢失内存撤销／重做历史，必须先取得用户授权。

以下原 2D 壳说明中带日期的测试数字为历史快照，当前状态以复验结果为准。

> **历史桌面资料**：原 Windows 安装／卸载说明保留在
> [`packaging/windows/README-WINDOWS.md`](packaging/windows/README-WINDOWS.md)。
> 它不是当前 Web 产品入口，也不是继续开发桌面安装包的承诺。

> 一句话：**把仓内 `cadcore` 的 2D 制图真件包成一套零安装的 CAD 制图界面 / 2D drafting UI** ——
> 打开就是绘图区、工具栏、图层列表与命令行，**多语言界面 i18n 能在不刷新页面的情况下切语言**，
> 画完一键 **DXF 导出**（真字节落盘），文档真源是可读的 L1 JSON 文本。
> 关键词：AutoCAD 平替 / open-source CAD / 2D drafting UI / 多语言界面 i18n / runtime locale switch /
> DXF export / cross-platform / 零框架零 CDN / 本地 Web 外壳。
>
> 与 Autodesk 无任何关联；`AutoCAD`/`DXF` 是其权利人商标。本目录只**调用**仓内自研的
> [`../../packages/cadcore/`](../../packages/cadcore/) 与 [`../../packages/dxf/`](../../packages/dxf/)，
> 不含任何第三方 CAD 源码与素材（Apache-2.0 主体，无 GPL 代码）。

## 1. 它解决什么

* **2D 制图不该以"装一个几百 MB 的桌面软件"为前提**：本外壳是纯标准库 Python 服务 + 原生
  HTML/CSS/JS 页面，`python3 server.py` 一条命令就能在 Linux / macOS / Windows 上起同一个界面。
* **CAD 的文档不该是黑盒二进制**：这里的真源是 `cadcore` 的 **L1 文本**（`docmodel` 的
  `document_to_l1` / `load_l1`），`/api/save` 存的就是人能读、能 diff、能进 git 的那份文本。
* **界面语言不该重启才生效**：`/api/locale` 调 `cadcore.i18n.catalog.set_locale`，进程不重启即可整包换译文；
  页面所有面向用户的文字节点都带 `data-i18n`，由服务端返回的 `strings` 填充 —— 所以加一门语言**不用改一行代码**。
* **未核实的字段一律说 NA**：投影角来自 `cadcore.i18n.standards`，`iso` / `jis` 未核 ⇒ 如实回字符串
  `"NA"`，绝不为凑一个好看的界面而编角度。

## 2. 用法

### 2.1 起服务

```bash
# 从仓库根跑；--port 0 让 OS 分配端口，实际端口从它自己打的那行判定行里读
python3 oss-alternatives/apps/opencad/server.py --port 0
# OPENCAD_SERVER verdict=PASS port=41234 routes=8
# 浏览器打开 http://127.0.0.1:41234/

# 固定端口 / 指定初始语言与标准档 / 每次请求都打日志
python3 oss-alternatives/apps/opencad/server.py --port 8766 --locale ja --standard jis --verbose
```

页面零依赖：**没有框架、没有 CDN、没有外链**（响应头带 `Content-Security-Policy: default-src 'self'`，
是结构性保证而不是"记得别写"）。Ctrl-C 停服务，会再打一行 `OPENCAD_SERVER verdict=STOP port=<n>`。

### 2.2 G9 判定行（界面壳这一层的可复跑判据）

```bash
cd /data/dev/infra/oss-alternatives/apps/opencad

# ① 壳自己的判定行：起一次真服务、真打一遍 API 再断言（rc=0 通过 / rc=1 失败）
python3 server.py --selftest
# OPENCAD_SERVER selftest verdict=PASS checks=15 failed=0

# ② 契约测试判定行：真起进程、真打 HTTP（含 DXF 用 packages/dxf 读回、路径穿越的阳性对照）
python3 -m unittest discover -s tests -v
# Ran 97 tests in 29.6s   ← 条数以当次 `Ran N tests` 为准（2026-10-01 本机实测 97 例 OK）
# OK

# ③ 起不来的门必须 rc≠0（阴性对照，防止"失败也返 0"）
python3 server.py --port 70000 ; echo "rc=$?"
# OPENCAD_SERVER verdict=FAIL reason=port_range detail=--port must be in 0..65535 (got 70000)
# rc=2
```

> ✅ **订正（2026-10-01 本机实跑，取代原文的"已知契约缺口"）**：`tests/__init__.py` **已在盘**（90 B），
> 跨席冻结契约里那条 `python3 -m unittest discover -s oss-alternatives/apps/opencad/tests -t oss-alternatives/apps/opencad`
> 与本文第 ② 条的 `-s tests` **两种写法都跑得通**（各 97 例 OK，rc=0），
> 原文的 `ImportError: Start directory is not importable` 在当刻盘上**已不复现** ⇒ 不再作为已知缺口列出。

### 2.3 加一门语言（**不改代码**，只加数据）

1. 复制一个现成语言包当模板：`cp oss-alternatives/packages/cadcore/cadcore/data/i18n/en.json \
   oss-alternatives/packages/cadcore/cadcore/data/i18n/eo.json`，把 `_meta` 里的
   `code` / `native_name`（用该语言自己的写法，例如 `"Esperanto"`）改掉，逐条填译文；
2. 在同一目录的 `languages.json` 里加一条（`code` / `native_name` / `file` / `font_fallback` / `default_standard`）；
3. 重启服务（或在 `/api/locale` 里切一次）后：
   * `GET /api/catalogs` 会多出这门语言，`GET /api/state` 的 `catalogs` 也跟着变；
   * 语言下拉 `#lang-select` 的选项文案取各语言包的 **`native_name`**（所以是 `English` / `简体中文` / `日本語`）；
   * `POST /api/locale {"locale":"eo"}` ⇒ `changed=1` 且 `strings` 整包换新，页面**不刷新**即切换。

   一行验证（与本目录测试同源的读法）：

   ```bash
   python3 -m unittest discover -s oss-alternatives/apps/opencad/tests -k locale -v
   ```

   注意：`strings` 里缺词条的 key 会落在 `strings_missing`（**未采 ≠ 已翻译**，显式留痕），
   而不是拿别的语言糊上去。

### 2.4 目录与契约

| 路径 | 是什么 |
|---|---|
| [`server.py`](server.py) | 纯标准库 `http.server` 的 JSON API + `web/` 静态面（禁 Flask 等框架） |
| [`web/index.html`](web/index.html) | 页面骨架：`#canvas` `#lang-select` `#standard-select` `#status` `#layers` `#cmd-line` `#toolbar` |
| [`web/js/app.js`](web/js/app.js) · [`web/css/app.css`](web/css/app.css) | 原生 JS/CSS，无框架无 CDN；译文全部来自 `/api/state` 的 `strings` |
| [`tests/test_server.py`](tests/test_server.py) | 契约测试（`subprocess` 起真服务 + 等判定行 + `http.client` 打真 HTTP）；**条数以当次 `Ran N tests` 为准**（2026-10-01 整目录 97 例 OK） |
| [`tests/test_b6_product_defects.py`](tests/test_b6_product_defects.py) | 13 例产品面缺陷回归（B6）：standard 参数校验 400 / undo-redo 逐字节恢复 / save-open 往返 / 极值坐标拒收与导出不裸崩 |
| [`tools/staged_probe_20260930.py`](tools/staged_probe_20260930.py) | 交付树探针：导入配方判定行 `STAGED_IMPORT` + 导出路径策略判定行 `EXPORT_PATH_POLICY`（详见 §2.5） |

API（未知路由 404；业务失败 HTTP 200 + 响应体 `ok:false`；**请求参数非法 400**，
响应体同样带 `ok:false` + 机读 `error` + `detail`，`/api/standard` 另带 `known` 可用值清单）：
`GET /api/state` · `GET /api/catalogs` · `GET /api/open?path=` · `POST /api/locale` ·
`POST /api/standard` · `POST /api/command` · `POST /api/export/dxf` · `POST /api/save`。
3D 另有 `GET /api/mesh`、`GET /api/step`、`POST /api/export/step`、
`POST /api/clearance`；维护有 `POST /api/release/prepare`、
`POST /api/release/restore`。维护恢复只允许比较摘要后恢复未编辑的初始工作区，
不得用旧保存文件覆盖已接受的新编辑。

例：`POST /api/standard {"standard":"bogus"}` ⇒ `400`
`{"ok":false,"error":"unknown_standard","detail":"unknown standard profile 'bogus'",`
`"known":["iso","gbt","ansi","din","jis"],"standard":"bogus"}` —— 参数合法性是 API 层的职责，
不许落到兜底 `internal_error`（那会把"参数不对"说成"服务端崩了"）。

计数类字段遵守三态口径：**`0` = 真为零 / `-1` = 未采（读不出来）/ `NA` = 不适用**。
例：`/api/catalogs` 里某个语言包读不出来时 `keys=-1`（不是 0）；`/api/standard` 的未核标
`projection="NA"`（不是 `"first"` 也不是 `null`）；`/api/export/dxf` 的 `dimension_entities`
是**从落盘字节里数出来的** `DIMENSION` 记录数 —— 草图尺寸没有图纸定位信息，一律记成 999 注释，
所以那里诚实地报 `0` 而 `document_dimensions=1`。

### 2.5 导出/保存落在哪里（`out-root`，用户可换）

* **落盘根缺省 = 本应用目录**（源码里就是本目录 `oss-alternatives/apps/opencad/`，安装包里的 `…\OpenCAD\app`）。
  `/api/save`、`/api/open`、`/api/export/dxf`、`/api/command export_pdf` 的路径都钉在这个根内：
  相对名以**根**为基准（**不看进程 cwd**），根内的绝对路径同样收，根外的绝对路径与 `..` 逃逸一律 `path_rejected`。
  > Windows 安装包**不走这个缺省**：启动器每次启动都会把落盘根指到 `%LOCALAPPDATA%\OpenCAD\workspace`
  > （安装目录之外，所以卸载不会丢图纸）；`OPENCAD_OUT_ROOT` 仍是最高优先级。详见
  > [`packaging/windows/README-WINDOWS.md`](packaging/windows/README-WINDOWS.md) §5。
* **要导到自己的文档目录，就用这个开关**（起服务时定根，之后 API 里给根内的相对名或绝对路径）：

  ```bash
  # Linux / macOS：把根换成 ~/Documents/OpenCAD
  python3 oss-alternatives/apps/opencad/server.py --port 8766 --out-root ~/Documents/OpenCAD
  # 或用环境变量（对 opencad.exe / opencad.cmd 同样生效，设一次即可）
  export OPENCAD_OUT_ROOT=~/Documents/OpenCAD   # Windows: set OPENCAD_OUT_ROOT=%USERPROFILE%\Documents\OpenCAD

  # 导出 PDF（相对名或根内绝对路径都行）
  curl -s -X POST http://127.0.0.1:8766/api/command -H 'Content-Type: application/json' \
       -d '{"command":"export_pdf","args":{"out":"drawing.pdf"}}'
  # => {"ok":true,"path":"<out-root>/drawing.pdf","magic":"%PDF-","bytes":N,...}
  # 根外一律拒：{"out":"../escape.pdf"} / {"out":"/etc/passwd"} => {"error":"path_rejected"}
  ```
* ⚠️ **实测过的坑（已修）**：旧实现根走 `abspath`、候选走 `realpath`，若 `--out-root`（或其任一层）
  是软链接（Windows 的"文档"重定向、Linux 的 `/hf3fs-jd -> /3fs/research3fs`），**根内的相对名也会被
  误判成 `path escapes the app directory`** —— 于是"换了 out-root 还是导不出来"。现在两边同口径
  `realpath`，可复跑判据：`python3 tools/staged_probe_20260930.py export --out-root <目录>`
  ⇒ `EXPORT_PATH_POLICY verdict=PASS out_root_honored=1`。

## 3. 背景（为什么交付 Web 服务，而不是 Qt）

* **免客户端 CAD 安装**：用户只需浏览器，不要求安装 Python 或内核。
  前端使用原生浏览器能力，无 CDN 或前端构建链；服务端部署仍需 Python、
  FreeCAD 等真实 3D 依赖。免客户端打包不等于服务部署零依赖。
* **复用仓内已验证的 UI 判定行范式**：同目录的 [`../freechat/`](../freechat/) 已经把这套范式跑通
  （本地 Web 外壳 + 判定行 + 零出网静态腿 + 契约测试）。opencad 直接沿用同一套骨架与同一种验证方式，
  省掉一次"界面怎么验"的重新发明，也让两边的测试读法一致。
* **真源与呈现解耦**：界面只认 `/api/state` 返回的 L1 文本与 `strings`，制图逻辑全在 `cadcore`。
  因此 `packages/cadcore` 的判定行（G1–G8）与本壳的判定行（§2.2）可以各自独立复跑。
* **Qt 与安装包路线已暂停**：早期选型和 roadmap 中的桌面规划属于历史，
  当前以 Web-only 决定及上面的产品契约为准。内核、语言包与呈现解耦
  仍保留，但不据此承诺下一期开发 Qt。
* **已知限制**：没有 DWG 读写、不做完整约束求解 UI；
  尺寸在草图模式下没有图纸定位信息，DXF 里以注释记录（见 §2.4）。
