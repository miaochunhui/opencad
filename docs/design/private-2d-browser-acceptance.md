# Private 2D Browser Acceptance

`tools/opencad_private_2d_browser.py` is a local, isolated acceptance for the
private 2D page. It builds the shipped `secure_web` application **in-process**
with temporary `AuthController`/`ProjectStore` roots and serves it on loopback.
A headless Chromium then drives the actual 2D UI. It is not a production,
CLI-process, 3D, industrial-parity, GPU or all-stage acceptance.

## What It Exercises

For both 1440×900 and 390×844:

1. Real private-page login and project creation through stable
   `data-panel-i18n-*` controls.
2. A real `#cmd-line` command (`line 0,0 40,20`) that commits a durable project
   revision, changes the rendered canvas, and updates the entity count.
3. A real language switch through `#lang-select`; the unsaved command remains
   in the same focused input, the project/revision/SHA/document/entities do not
   change, and no additional project POST occurs.
4. A real browser-offline read that shields the workspace while retaining the
   command draft in the original node, followed by network restoration and the
   project-session refresh control on the same page. Draft, node identity and
   project identity survive; no page errors are accepted.
5. Full-page reload/reopen: the authenticated project selector is used again
   and the durable document/history/content are recovered; the selected locale
   is reloaded.
6. Explicit logout, a second account's project picker, creation of an empty
   separate project, and return to the first account/project with the original
   document/SHA/revision recovered.

## Invariants

- Source must be a **clean committed tree**; tracked file bytes/modes, HEAD and
  status are captured before and after. Any drift fails the run.
- Evidence and runtime are fresh external roots. Existing directories are
  refused. Browser profiles, temporary SQLite roots and account passwords stay
  local to the run; passwords are never written to evidence.
- The source binding stores the full tracked manifest. The run refuses
  production or GPU claims.

## Reproduce

```bash
cd /data/dev/wt-opencad-locale-integration-20261012
sudo -n -u infra env PYTHONDONTWRITEBYTECODE=1 \
  python3 -B tools/opencad_private_2d_browser.py \
  --source-root /data/dev/wt-opencad-locale-integration-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-private-2d-browser-NEW \
  --runtime-root /data/tmp/oc-private-2d-browser-NEW \
  --browser /home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome
```

`NEW` must be a new version; do not reuse an evidence root. Read
`result.json`/`completion.log` and check the actual parent exit code. The
screenshots are under the two width directories. The whole-repository
`tools/acceptance.py --stage all` run is a separate gate and is not replaced by
this local 2D workflow.

## Scope Limits

This does not certify 3D viewer or industrial panels, all languages/panels,
production TLS/proxy/HA, session revocation (covered separately by
`tools/browser_identity_acceptance.py` and the all-stage suite), or any GPU/RTX
claim. In-process server construction is intentional; use the identity/bridge
evidence tools where CLI process identity is required.
