# Official CAD Competitive Matrix

Status: **PARTIAL, industrial parity NOT_ASSESSED**. Research date: 2026-10-11.
Only facts with a readable official body in `official-sources.json` are included.
The JSON is a source ledger, not a test result. Bodies and fetch logs are outside
Git under `/hf3fs-jd/data/tmp/opencad-research-gpu-20261011/`.

## Runtime Categories

- Native/browser-based application: official body explicitly identifies browser
  execution; this does not locate geometry computation or viewport rasterization.
- Connected desktop: an installed client using cloud services; networking alone
  does not make an application native Web.
- Remote rendered: server-generated frames delivered to a client; cloud/SaaS
  branding, remote access and a separate photorealistic renderer are insufficient.
- SDK/kernel: components used to build applications, not a complete CAD product.
- NOT_VERIFIED: missing or insufficient source, not a claim that capability is absent.

## Product and Role Separation

| Product / Specific Offering | Captured Official Evidence | Browser / Desktop Classification | Server Frame Topology | Boundary / Remaining Evidence |
| --- | --- | --- | --- | --- |
| CATIA family | `catia-family`: NOT_CAPTURED | NOT_VERIFIED | NOT_VERIFIED | No discipline, role, release or licensing facts accepted. |
| 3DEXPERIENCE CATIA | `catia-experience`, `catia-mechanical`: NOT_CAPTURED | NOT_VERIFIED | NOT_VERIFIED | R2026x and mechanical role candidates have no body. Cloud is not browser proof. |
| SOLIDWORKS desktop | `solidworks-desktop-connected`: NOT_CAPTURED | NOT_VERIFIED | NOT_VERIFIED | Do not borrow xDesign positioning or desktop tests. |
| SOLIDWORKS Connected | Same candidate lacked body | NOT_VERIFIED | NOT_VERIFIED | Must capture installed-client requirements independently of platform connectivity. |
| SOLIDWORKS xDesign | `solidworks-xdesign`: redirected, then empty timeout | NOT_VERIFIED | NOT_VERIFIED | Browser/no-install claim remains a hypothesis pending readable official body. |
| NX/UG / current Designcenter family | `nx-cad-family`, `nx-current-tiers` | Product family spans on-premises and cloud-SaaS; not one runtime | NOT_VERIFIED | Official page says NX naming retired in June 2026; preserve old naming only as historical identifier. |
| Designcenter X Essentials | `nx-current-tiers` explicitly browser-based | Browser application, essential CAD/CAM/CAE | NOT_VERIFIED | Entry tier; not full Standard/Advanced/Premium industrial scope. |
| Designcenter X Standard/Advanced/Premium | `nx-current-tiers` SaaS family and tier descriptions | Cloud-connected product family; installed-client details not captured | NOT_VERIFIED | Token modules require an eligible core seat; don't imply every add-on is included. |
| Designcenter on-premises Standard/Advanced/Premium | `nx-current-tiers` on-premises descriptions | On-premises offering; precise client install requirements not captured | NOT_VERIFIED | Different deployment from X Essentials. |
| AutoCAD Web | `autocad-web-guide` explicitly web-based 2D commands | Browser-based 2D guide | NOT_VERIFIED | Lines/circles, modify, precision, layers, annotate/dimension, layout/PDF. Not full desktop AutoCAD or 3D industrial parity. |
| Onshape | `onshape-root`, `onshape-platform` cloud-native/no-install CAD and built-in PDM | Cloud-native/no-install; explicit browser topology NOT_VERIFIED in captured bodies | NOT_VERIFIED | Render Studio listing is not interactive server rasterization proof; candidate help/graphics pages resolved to generic Help home. |
| FreeCAD | `freecad-features`, `freecad-downloads` | Desktop distributions: Windows/Mac/Linux | NOT_VERIFIED | Parametric OCCT-based modeling and constraints sketching; upstream capability is not OpenCAD Web coverage. |
| Open CASCADE Technology | `occt-overview` | C++ SDK/kernel, not an end-user CAD app | NOT_VERIFIED | Surface/solid modeling, exchange and OpenGL/OpenGL ES graphics; no claim of hosted browser CAD. |

## Industrial Gap Register

The renderer adds bounded mesh-to-PNG output, not any new industrial CAD module.
Existing bootstrap or upstream features are not an acceptance substitute.

| Capability Family | Research / OpenCAD Acceptance Status | Evidence Required to Close |
| --- | --- | --- |
| Product roles, editions, licensing | PARTIAL sources; CATIA/SOLIDWORKS NOT_CAPTURED | Official body per product/role; separate entitlement from family marketing. |
| Solid/parametric modeling | Upstream FreeCAD/OCCT descriptions captured; this seat's renderer is not a modeling kernel | Exact OpenCAD workflow/numeric regression and feature scope owned by modeling seats. |
| Advanced surfaces / continuity | NOT_ASSESSED | Industrial surfaces, continuity/tolerance and round-trip cases. |
| Assemblies / mate solver / large-model behavior | NOT_ASSESSED | Multi-part numeric solve, conflicts, saved revision and representative scale. |
| Drawings / PMI / GD&T | AutoCAD Web 2D guide only; industrial parity NOT_ASSESSED | Standard-specific downstream drawings and semantics, not just visible labels. |
| Sheet metal / weldments / tooling / CAM | NOT_ASSESSED | Representative feature chains, manufacturing outputs and validated postprocessor. |
| CAE / simulation | NOT_ASSESSED | Validated physical case, solver provenance and units; cloud tier name insufficient. |
| Enterprise PDM/PLM / multi-tenancy | Onshape platform claims captured; OpenCAD production NOT_ASSESSED here | Auth/project tests, immutable DB snapshots, concurrent edits and recovery. |
| Server GPU / rendering speed | Independent EGL component tested separately; competitor topology NOT_VERIFIED | Hardware frame proof and controlled representative HTTP A/B; no browser WebGL proxy. |

## Capture and Re-run

`web.run` was called for current official pages and searches, but returned empty
without source IDs in this seat. A previously reported chair search capture was
withdrawn. These failures do not predict future availability. Only the subsequent
Astrill body readbacks are accepted, with byte counts, SHA256, started capture time
and logs in the JSON. File mtime may be the remote Last-Modified time and is not
used as capture time.

Re-run the JSON `fetch_recipe.argv_template` with one candidate URL and a new
3FS output name. At most three requests at 1500k each give this seat a configured
aggregate 4,608,000 B/s, below 8 MB/s. Existing node/git must pass read-only
preflight; no dependency installation is authorized. Preserve failures/caches.
Small buffered transfers cannot establish peak rate compliance; the ledger
records that measurement limitation instead of treating `0.000` as real zero.

The existing fetch tool sometimes reports a zero-length HEAD as intermediate
"already complete"; missing body and failed final pull stay NOT_CAPTURED.
Onshape guessed help paths returning a generic page stay CAPTURED_OTHER_PAGE
with no facts for the requested subject. URLs, redirects and rc=0 alone never
populate the matrix.
