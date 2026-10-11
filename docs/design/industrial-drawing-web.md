# 关联工程图 Web 接线契约

本适配器只负责真实工程图候选、读取和导出，不建立第二套身份、CAS 或
undo 协议。主席独占 `secure_web`、`project_gateway`、会话脚本及 viewer 页面；
本席不改这些文件，也不发布生产。

## API

模块 `industrial_drawing`：

```python
prepare_drawing(workspace, body) -> DrawingCandidate
handler(workspace, body)         # prepare_drawing 的别名
read_drawing(workspace, body) -> dict
export_drawing(workspace, body) -> DrawingExport
```

prepare 的 body 恰为 `{"definition": spec, "model_version": next_revision}`。
`model_version` 是网关依据权威 store head 指派的正整数，不能使用客户端
自报值。候选含 `before_document_sha256`、`doc`（完整候选 L1 文本）与
`response`。候选对象 frozen，response 每次返回新副本。prepare 不改原
workspace 文档、undo 或 redo，也不确认保存。

GET `/api/drawing`：主席构造 `{"model_version": head["revision"]}`，
可另加布尔 `include_svg`。响应有定义、真实投影/剖面摘要、实测尺寸、
模型 SHA、完整文档 SHA、版本、源码绑定、SVG。没有定义时 `defined=false`，
不能凭空生成演示实体；GET 不改 store/doc/history。

POST `/api/drawing` 的客户端 body 为 `definition` 加主席现有
`expected_revision/expected_sha/request_id`。主席先鉴权、查幂等回执、
核 store 基版本，再调用 prepare。验证候选的 `before_document_sha256`
等于候选工作区的原 L1 SHA，统一追加 undo、清空 redo，将候选 doc
连同 history/standard 提交到现有 project_store CAS。只有真实 commit
回执可发给客户端；首因失败、版本冲突不得返回保存成功。

GET `/api/drawing/svg`、`/pdf`、`/dxf`：主席传
`{"model_version": head["revision"], "format": format}`。返回实际 bytes、
content_type、下载 headers。生成后再次鉴权并核当前 store revision/SHA，
不同则返回409，不发送旧图。headers 包含模型版本/SHA、完整文档 SHA、
drawing SHA 和实际导出字节 SHA。请求不能指定服务器文件路径。

## 模型与定义

定义放在 L1 厂商顶层扩展 `x_industrial_drawing`，只保存 schema、规格及
规格 SHA，不保存几何缓存。模型重放只排除这一项定义元数据，完整文档
SHA 仍包含它，因此既能用于存储 CAS 又不会把图纸元数据自引用成模型。
这两个 SHA 有不同口径，不能互换；完整文档 SHA 是候选基线与导出一致性，
模型 SHA 是真实 BRep 输入。`x_` 使用现有读取协议，不追加未知字段诊断，
保存后再读取必须保持 L1 与模型 SHA 稳定，不改共享 `docmodel` 注册表。

`drawing_sha256` 绑定实测 scene、定义、版本、模型与三个真实生产者指纹，
排除证据临时目录和 STEP 文件时间；独立读取/导出同图应一致。
另有 `snapshot_sha256` 保留模块完整不可变快照的证据完整性指纹，不能
拿含临时目录的快照指纹作跨请求图纸版本号。

每次读取/导出从当次 workspace 模型重建真实 FreeCAD worker，不依赖
旧持久图纸缓存。带孔板尺寸改变后隐藏线、材料截面面积、尺寸都重新
测量；失效引用或构造失败报错，不返回旧图。导出 PDF/DXF 继续走已验收
模块的独立正文、实体坐标和像素读回。SVG 从同一实测 scene 生成，正文
转义且只生成固定几何/文本元素；浏览器面板拒脚本、外链和事件属性。

当前适配器为关联工程图原型，不是完整工业制图：单实体、主轴剖切、
有限尺寸引用、ASCII PDF/DXF 正文、折线近似曲线渲染；标准符合性
NOT_ASSESSED，完整 GD&T/MBD NOT_IMPLEMENTED，不声称完整 PMI。

## 独立面板与验收

主席在自己的 viewer 页面加载 `drawing-panel.css/js` 后显式调用：

```javascript
window.opencadDrawingPanel.mount(hostElement);
```

面板使用现有 `window.fetch`，因此主席 project-session 层继续负责项目
头、CAS 信封、HttpOnly cookie 和保存确认。面板不存凭据、不修改会话层、
不自动插入既有页面。提供真实定义 form、重算、SVG 预览、尺寸表及同版本
SVG/PDF/DXF 下载；窄屏表格自身横滚，不让整页横向溢出。

测试文件启动隔离 HTTP 服务、真实 SQLite WAL 项目 store、真实 Workspace、
真实 FreeCAD/HLR/剖面及 Poppler/DXF 读回。fixture 的固定 bearer 仅用于
本地隔离路由，不是生产身份安全证据。SQLite 必须用独立本地目录
`/data/dev/opencad-drawing-web-fixtures-20261011`；日志/截图/模型/导出在3FS。
`--dependency-root` 只读加载主席真实 project_store，记录源 SHA 与漂移。

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-drawings-20261011/oss-alternatives/apps/opencad/tests/test_industrial_drawing.py --dependency-root /data/dev/opencad --out /hf3fs-jd/data/tmp/opencad-drawing-web-20261011/fresh-owned-run --independent-discovery
```

覆盖候选不改输入、真实 POST/CAS/read、幂等、坏引用/剖面保旧头、旧基线
及生成竞态拒绝、同版本真实导出、模型修改联动。浏览器用既有 CDP helper
实际点击 form，两视口检查 SVG 隐藏线/剖面、尺寸与页面边界。
另验真实 SVG 下载、错文档 SHA 拒绝下载、注入脚本拒绝渲染、模型变化
发生在读取期间时不发旧图且自动重建新版本。注入只变异真实 HTTP
结果，用来证伪拒绝门，不替换 BRep/HLR/剖面或读回。

`--independent-discovery` 先运行本文件真套件，再调用现有
`tools/acceptance.py --suite ... --pattern test_industrial_drawing.py`；
记录子进程原始退出码、独立计数和全部日志，不重跑历史全门。
源码 SHA 包含新五文件与实际 worker/docmodel/server/CDP/store 依赖，
前后不一致拒绝 PASS。普通 unittest discovery 也创建完整真夹具，不跳过。

纸面 SVG 在窄屏整体缩小，因此纸面正文可读性、触屏缩放、完整共享 Web
发布门/屏幕阅读器与性能尚未验收；尺寸表仍提供独立文本读取。
主网关正式集成后的真实用户授权/CSRF、多租户、上线和性能由主席另验，
这里不得冒称已交付。
