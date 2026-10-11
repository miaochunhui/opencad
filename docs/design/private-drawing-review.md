# 私有工程图独立复核

## 范围

独占 `tools/review_private_drawing.py` 与本文件。只读
`/data/dev/opencad` 的真实产品/浏览器代码；测试使用新隔离
HTTP服务、随机账号/口令、cookie会话、SQLite账号/项目DB及工程图worker。
不使用生产账号、现有项目或共享demo，不修改产品、权限或生产。
测试产生的新专属数据保留外置；浏览器helper管理其自身进程/profile。
权限事故仍OPEN，功能验收不构成部署放行。

工具 `--source-root` 明确绑定应用及cadcore/dxf/vault源码，
前后检查上述源码和浏览器辅助代码集合的SHA差集。
自生成口令、cookie不写日志、截图或CDP事件。
隔离DB在 `/data/dev/opencad-private-fixtures/drawing-review-<uuid>/`；
日志和下载产物在调用指定的3FS外置目录。

## 已实测HTTP业务与安全

首轮证据：
`/hf3fs-jd/data/tmp/opencad-drawing-web-20261011/private-review-20261011T0340Z/`。
真实73项检查均通过，但整轮 **FAIL**，因工具浏览器选择器错误未完成
浏览器链；不能将此目录的outer FAIL改记PASS。
实际时间03:29:48Z附近至03:30:24.373524Z，目录标签不是采集时刻。
该轮产品源零漂移。

真实参数板宽40→60 mm，关联定义不变，无需再次保存定义：
宽40→60、厚8→12、孔径8→12 mm；y=10 mm剖面实测面积256→576 mm²，
均2材料面。top/front/right均由 `Drawing.projectEx` 真HLR；
hidden_curves为5/8/13，visible为5/4/4。模型、文档、图纸SHA与revision
绑定，SVG几何发生变化。

每版独立HTTP下载PDF/DXF/SVG，验证七个版本/字节SHA响应头。
SVG解析真实节点/尺寸正文；DXF解析709个实体、坐标有限、byte roundtrip
一致，VISIBLE/HIDDEN/SECTION/DIMENSIONS/ LABEL与SVG层计数一致。
PDF由Poppler实际抽正文/光栅化，尺寸及source SHA在正文中，
非白像素7250→7442，不仅检查magic。

负控包括：失效base CAS409、坏feature ref422，文档SHA/内容/revision/
undo深度保持；匿名401/他人403拒绝三种导出。
在真实PDF生成之后、真实gateway最终 `_current` 检查之前，独立
subclass仅插入Event屏障：另一请求提交使导出409，
logout撤销原cookie使导出401；错误响应均无artifact SHA、无PDF字节。
几何、auth、store、CAS、HTTP响应均真实，屏障不是几何替身或判词stub。

## 浏览器首因与未收项

首轮工具错误：打开已有项目后误点隐藏状态栏按钮，未打开新项目表单。
修为登录后直接在真实项目选择表单创建新项目。历史FAIL完整保留。

第二轮：
`private-browser-review-20261011T0334Z/result.json`，outer FAIL。
等待width参数表失败，且运行期间 `tests/test_project_gateway.py`
变化产生源漂移，不能签同源PASS。按钮可见不保证3D已就绪。
工具补3D就绪前置与失败DOM/截图采集，不原样重投整HTTP。

补验：
`private-browser-review-ready-20261011T0338Z/`，
只运行browser stage，03:39:11.018075Z终态FAIL、原rc1，源零漂移。
空项目/3D就绪检查通过，但width参数表未出现；`browser/failure-state.json`
及 `browser/failure.png`保留现场。不能把超时归因于FreeCAD失败。

为取证而非交付，又加真实click命中校验（elementFromPoint及两帧布局稳定，
鼠标仍为实际CDP，不执行DOM click）及脱敏API状态记录：
`private-browser-hit-review-20261011T0343Z/result.json`，
03:44:17.297902Z终态FAIL、原rc1。实际按钮命中true，width表仍未出现。
日志没有请求method，不能从两个parametric 200判定哪个是POST或是否提交。
运行期间 `project_store.py` 和 `tests/test_project_store.py`又产生源漂移。
全部首因与历史产物保留，不重复投递。当前没有在飞reviewer。

**私有browser链未验收**。需主产品源停止变更，再在独立外置批收真实表单
保存、改尺寸自动刷新与三格式下载终态；现有HTTP73项不能替代这条。
工具不修改产品、放宽条件或以脚本触发表单来掩盖失败。

## 精确入口与声明边界

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-drawings-20261011/tools/review_private_drawing.py --source-root /data/dev/opencad --stage all --out /3fs/research3fs/data/tmp/opencad-drawing-web-20261011/private-drawing-final
```

调用要求out为尚不存在的新目录、父目录已存在。`--stage http|browser`
可拆分复核，但单腿PASS不得冒充all PASS。结果须核result、review.log、
源漂移及原进程退出码；不自动重试。
浏览器使用现有软件Chromium/CDP，只证明本工作流交互，不证明GPU性能。
未测生产TLS/反向代理、多租户部署、完整GB/ISO/GD&T/MBD、
native Windows或OCP。当前审计不授权上线、补权限或清理。

当前已有证据是cookie认证、owner隔离、CAS负控和真实导出重鉴权；
账号/项目DB为新建本地SQLite，不是部署级多租户隔离证明。
HTTP成功部分留在原outer FAIL下，不重新包装成正式PASS。
