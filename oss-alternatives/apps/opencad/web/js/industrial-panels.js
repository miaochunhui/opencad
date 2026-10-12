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
    locale: function () {
      if (!window.OpenCADLocale) throw new Error("surface_locale_required");
      return window.OpenCADLocale;
    }
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
            const locale = surfaceLabels.locale();
            locale.ready.then(function () {
              window.opencadSurfacesPanel = window.OpenCADSurfaces.mount(host, {locale: locale});
              return window.opencadSurfacesPanel.reload();
            }).catch(function (error) {
              const panel = window.opencadSurfacesPanel;
              if (panel && panel.root.parentElement === host) {
                panel.reportError(error);
              } else {
                mounted = false;
                const message = document.createElement("p");
                message.setAttribute("role", "alert");
                message.textContent = String(error.message || error);
                host.append(message);
                if (locale.snapshot().status === "ready") {
                  try {
                    message.textContent = locale.t("panel.surfaces.error", {
                      detail: String(error.message || error)
                    });
                  } catch (translationError) {
                    const localeFailure = document.createElement("p");
                    localeFailure.setAttribute("role", "alert");
                    localeFailure.textContent = String(translationError.message || translationError);
                    host.append(localeFailure);
                  }
                }
                throw error;
              }
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
