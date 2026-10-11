# OpenCAD Repository Rules

Global Codex rules remain applicable. Use Chinese for task communication.
Run repository commands as `sudo -n -u infra`; do not change credentials,
permissions, identity chains, services or production without explicit scope.

- Check Git status and current ownership before editing. Other people's files
  and uncommitted changes are protected. Use independent branches/worktrees
  after the initial bootstrap commit; never overwrite another seat's work.
- Manual edits use `apply_patch`. Source migration uses the reviewed repository
  Python tool. No inline code, shell redirection edits or bulk add.
- Keep runtime workspaces, logs, screenshots and test evidence outside this
  tree under `/hf3fs-jd/data/tmp/<task>/`. Disable Python bytecode writes.
- Bootstrap never replaces existing migration targets. Its provenance manifest
  is immutable origin evidence, not the current development source fingerprint.
- Use `tools/acceptance.py` for independent acceptance. Report test counts,
  skips, failures, source drift and untested stages. Missing infra tools are
  migration debt, not grounds to silently skip or claim full green.
- Product delivery is Web-only. Desktop release gates are not prerequisites.
  Do not claim industrial parity, production multi-tenancy or GPU speed from
  unit tests, a small model or process existence.
- Commit only named owned paths, inspect the staged diff and use DCO sign-off.
  No push without the chair's reviewed authorization. No automatic cleanup.
- This bootstrap seat does not spawn agents or enable the shared Flash pool.
