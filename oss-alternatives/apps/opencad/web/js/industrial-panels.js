/* Mount only against the authenticated project transport, never the demo. */
(function () {
  "use strict";
  async function transport(operation) {
    const response = await window.fetch(operation.path, operation.method === "POST" ? {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(operation.body)
    } : {});
    const value = await response.json();
    if (!response.ok || value.ok !== true) {
      throw new Error(value.first_cause || value.detail || value.error || "请求未确认");
    }
    return value;
  }
  const surfaceLabels = {
    heading: "精确曲面", limits: "独立曲面项目：NURBS、矩形裁剪、缝合和增厚。G0/G1/G2 为采样检查，不是全域证明。",
    recipe: "曲面配方（整数纳米坐标）", plane: "10 mm 平面",
    cylinder: "R5 四分之一圆柱面", bounds: "裁剪 UV 范围 [u0,u1,v0,v1]",
    thickness: "厚度（mm）", inward: "向内", trim: "包裹裁剪",
    thicken: "包裹增厚", sew: "两平面接缝", apply: "验证并保存",
    reload: "重读已保存曲面", preview: "真实曲面网格预览",
    empty: "尚未保存曲面", ready: "精确内核结果已保存"
  };
  async function boot() {
    await window.OpenCADProjectSession.ready;
    const workbench = document.querySelector(".industrial-workbench");
    if (!workbench) { return; }
    workbench.querySelectorAll("details").forEach(function (details) {
      let mounted = false;
      details.addEventListener("toggle", function () {
        if (!details.open || mounted) { return; }
        mounted = true;
        const host = details.querySelector("div");
        try {
          if (host.hasAttribute("data-surfaces-host")) {
            window.opencadSurfacesPanel = window.OpenCADSurfaces.mount(host, {
              labels: surfaceLabels
            });
            window.opencadSurfacesPanel.reload().catch(function (error) {
              host.querySelector("output").textContent = String(error.message || error);
            });
          } else if (host.hasAttribute("data-assembly-host")) {
            window.opencadAssemblyPanel = window.opencadAssembly.mount(host, {
              transport: transport
            });
          } else if (host.hasAttribute("data-drawing-host")) {
            window.opencadDrawing = window.opencadDrawingPanel.mount(host);
          } else if (host.hasAttribute("data-render-host")) {
            window.OpenCADRenderPanel.mount(host);
          }
        } catch (error) {
          mounted = false;
          const message = document.createElement("p");
          message.setAttribute("role", "alert");
          message.textContent = String(error.message || error);
          host.append(message);
        }
      });
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else { boot(); }
}());
