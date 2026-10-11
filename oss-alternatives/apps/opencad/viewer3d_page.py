"""Generate the 3D workspace page. Runtime copy and controls use catalog keys."""
from datetime import datetime, timedelta, timezone
from pathlib import Path


def render(*, advanced=False):
    stamp = datetime.fromtimestamp(Path(__file__).stat().st_mtime,
                                   timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08:00")
    page = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenCAD | 3D</title><link rel="stylesheet" href="/web/css/viewer3d.css">
<script src="/web/js/viewer3d.js" defer></script>
<script src="/web/js/advanced3d.js" defer></script></head><body>
<header><a href="/">OpenCAD / 2D</a><strong>3D</strong><select id="locale"></select></header>
<main><aside><h1 data-i18n="menu.view"></h1><div id="views">
<button data-view="iso" data-i18n="cmd.view.iso"></button>
<button data-view="front" data-i18n="cmd.view.front"></button>
<button data-view="top" data-i18n="cmd.view.top"></button>
<button data-view="right" data-i18n="cmd.view.right"></button></div>
<form id="extrusion"><label for="depth" data-i18n="cmd.extrude"></label>
<div class="depth"><input id="depth" type="number" min="0.001" max="10000" step="0.001" value="6" required><span>mm</span></div>
<button id="apply" data-i18n="cmd.extrude"></button></form>
<button id="undo" data-i18n="cmd.undo"></button><button id="redo" data-i18n="cmd.redo"></button>
<button id="step" data-i18n="cmd.export.step"></button><dl id="metrics"></dl>
<p id="renderer"></p><div id="advanced">
<h2 data-i18n="solid.features"></h2><ol id="feature-tree"></ol>
<label for="profile" data-i18n="solid.profile"></label><select id="profile"></select>
<p data-i18n="solid.units"></p>
<details><summary data-i18n="solid.sweep"></summary><form data-solid="sweep_solid">
<label data-i18n="solid.path"></label><textarea name="path" required>[[0,0,0],[0,0,20]]</textarea>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.loft"></summary><form data-solid="loft_solid">
<label data-i18n="solid.profiles"></label><input name="profiles" required>
<label data-i18n="solid.offsets"></label><textarea name="offsets" required>[[0,0,0],[0,0,20]]</textarea>
<label><input name="ruled" type="checkbox"><span data-i18n="solid.ruled"></span></label>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.fillet"></summary><form data-solid="fillet_solid">
<label data-i18n="solid.size"></label><input name="size" type="number" min="0.001" step="0.001" value="1" required>
<label data-i18n="solid.edges"></label><input name="edges" value="1" required>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.chamfer"></summary><form data-solid="chamfer_solid">
<label data-i18n="solid.size"></label><input name="size" type="number" min="0.001" step="0.001" value="1" required>
<label data-i18n="solid.edges"></label><input name="edges" value="1" required>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.shell"></summary><form data-solid="shell_solid">
<label data-i18n="solid.thickness"></label><input name="thickness" type="number" min="0.001" step="0.001" value="1" required>
<label data-i18n="solid.faces"></label><input name="faces" value="6" required>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.pattern"></summary><form data-solid="pattern_solid">
<label data-i18n="solid.translations"></label><textarea name="translations" required>[[0,0,0],[20,0,0]]</textarea>
<label><input type="checkbox" name="fuse"><span data-i18n="solid.fuse"></span></label>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.mirror"></summary><form data-solid="mirror_solid">
<select name="axis"><option>x</option><option>y</option><option>z</option></select>
<label data-i18n="solid.offset"></label><input name="offset" type="number" step="0.001" value="0" required>
<label><input type="checkbox" name="keep"><span data-i18n="solid.keep"></span></label>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="cmd.import.step"></summary><form data-solid="import_step">
<input type="file" accept=".step,.stp" required><button data-i18n="cmd.import.step"></button>
</form></details>
<details><summary data-i18n="solid.edit"></summary><form data-solid="edit_feature_params">
<input id="feature-edit-id" required><textarea id="feature-parameters" required>{}</textarea>
<button data-i18n="panel.apply"></button></form></details>
<details><summary data-i18n="solid.clearance"></summary><form data-solid="measure_clearance">
<label data-i18n="solid.clearance_copy"></label><textarea name="translation" required>[20,0,0]</textarea>
<button data-i18n="panel.apply"></button><output id="clearance-result"></output></form></details>
</div></aside><section><canvas id="model" tabindex="0" data-i18n-aria="viewer3d.canvas"></canvas>
<output id="status" role="status" aria-live="polite"></output></section></main>
<footer><span data-i18n="viewer3d.modified"></span> """ + stamp + """</footer></body></html>"""
    if not advanced:
        before, _, rest = page.partition('<div id="advanced">')
        _, _, after = rest.partition("</div></aside>")
        page = before + "</aside>" + after
        page = page.replace('<script src="/web/js/advanced3d.js" defer></script>', "")
    return page
