/* Bind private workbench copy without rebuilding its stateful CAD panels. */
(function () {
  "use strict";
  const domReady = document.readyState === "loading" ? new Promise(function (resolve) {
    document.addEventListener("DOMContentLoaded", resolve, {once: true});
  }) : Promise.resolve();

  async function boot() {
    await domReady;
    const locale = window.OpenCADLocale;
    if (!locale || !locale.ready || typeof locale.bind !== "function") {
      throw new Error("opencad_locale_service_missing");
    }
    await locale.ready;
    const workbench = document.querySelector(".industrial-workbench");
    if (!workbench) throw new Error("opencad_private_workbench_missing");
    const unbind = locale.bind(workbench);
    if (typeof unbind !== "function") throw new Error("opencad_locale_binding_invalid");
    return unbind;
  }

  const ready = boot();
  ready.catch(function (error) {
    const message = document.createElement("output");
    message.setAttribute("role", "alert");
    message.setAttribute("lang", "en");
    message.setAttribute("data-opencad-public-locale-error", "private");
    message.textContent = "private_locale_initialization_failed";
    document.body.append(message);
    console.error("private_locale_initialization_failed", error);
  });
  window.OpenCADPrivateLocale = Object.freeze({ready: ready});
}());
