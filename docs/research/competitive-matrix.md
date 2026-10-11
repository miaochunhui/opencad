# Official CAD Competitive Matrix

Status: **PARTIAL, industrial parity NOT_ASSESSED**. Research date: 2026-10-11.
Only facts with a readable official body in `official-sources.json` are included.
The JSON is a source ledger, not a test result. Bodies and fetch logs are outside
Git under `/hf3fs-jd/data/tmp/opencad-research-gpu-20261011/`.
Continuation readback: 2026-10-12 00:12:56 +08:00. New bodies/logs are under
`/hf3fs-jd/data/tmp/opencad-official-continuation-20261011-v1/`; this follows the
2026-10-11 task across local midnight, not a recapture time inferred from mtime.

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
| CATIA family / V5 boundary | `catia-experience-continuation` role/discipline catalogue; family root capture not started due to budget lock | Family is not one runtime; V5 deployment NOT_VERIFIED | NOT_VERIFIED | R2026x is advertised in the captured 3DEXPERIENCE body, not independently release-tested. Original failed family capture remains unchanged. |
| 3DEXPERIENCE CATIA mechanical offering | `catia-experience-continuation`, `catia-mechanical-continuation` | Cloud-platform offering; mechanical client runtime NOT_VERIFIED | NOT_VERIFIED | Assembly/kinematics, surfacing, sheet-metal unfolding and 3D tolerances/annotations are official descriptions, not industrial workload or all-role entitlement evidence. |
| CATIA 3D Shape Designer role | `catia-experience-continuation` visible role card | Explicit browser applications for 3D design, freeform shaping and pattern design | NOT_VERIFIED | Role-specific statement only; do not classify all CATIA roles as browser-based. |
| SOLIDWORKS Design | `solidworks-computers-continuation`, `solidworks-individual-continuation`, xDesign store FAQ | Installed client; 2026 Help describes Installation Manager and Windows prerequisites; FAQ says cloud-deployed | NOT_VERIFIED | Internet entitlement and embedded Chromium/platform content do not turn installed CAD into native browser CAD. Modules depend on purchased products. |
| SOLIDWORKS Design with Cloud Services | `solidworks-xdesign-store-continuation` visible FAQ/comparison | License-key desktop CAD, locally installed | NOT_VERIFIED | Separate from xDesign; exact included services and subscription tiers not certified. |
| SOLIDWORKS Connected | Connected-specific store candidate refused before HTTP; family/browser references do not resolve role mapping | NOT_VERIFIED for this named offering | NOT_VERIFIED | Do not assume legacy Connected name equals the currently described Design offer. Requires named installation/role source. |
| SOLIDWORKS xDesign | `solidworks-xdesign-store-continuation` introduction, feature lists and FAQ | Explicit browser execution/no installation | NOT_VERIFIED | Parametric/freeform, part/assembly and sheet-metal/structure descriptions accepted. Separate xStudio photorealistic jobs/credits do not establish interactive server frames. Original product-page timeouts retained. |
| SOLIDWORKS family | `solidworks-family-continuation` | Mix of offerings; not one runtime | NOT_VERIFIED | Design, manufacturing, simulation and data management descriptions do not establish every module in a seat. |
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
| Product roles, editions, licensing | PARTIAL; selected CATIA roles and Design/xDesign deployment captured; Connected mapping/full entitlement NOT_VERIFIED | Official body per product/role; separate entitlement from family marketing. |
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

The earlier capture tool reported a zero-length HEAD as intermediate
"already complete"; missing body and failed final pull stay NOT_CAPTURED.
Onshape guessed help paths returning a generic page stay CAPTURED_OTHER_PAGE
with no facts for the requested subject. URLs, redirects and rc=0 alone never
populate the matrix.

## Continuation Evidence

Six new readable bodies were accepted after local SHA256/size checks and exact
fetch-log readback, each with original caller rc=0 and wget rc=0. The ledger
records body-level anchors and their semantic locations. SOLIDWORKS Help topics
are JSON-encoded under `__NEXT_DATA__.props.pageProps.helpContentData.helpText`;
the decoded inner article, not its generic outer title or navigation, supports
installation facts. `jq` readback confirmed both exact topic titles and no
`errorDetails`. No browser interaction was tested.

Five rejected attempts remain separate: two product-page timeouts (caller rc=1,
wget rc=124, zero bytes), one HTTP-success Help error body (caller rc=0 but the
requested topic did not exist), and two shared-budget refusals (caller rc=2,
no HTTP/wget launched). The error body's bytes/SHA and all logs are retained.
Accurate Help links were discovered in captured navigation; xDesign's 3ds.com
store was a new candidate, not a claimed search result. Both continuation
`web.run` searches were empty; interim claims of returned search evidence were
withdrawn and do not populate the ledger.

Continuation transfers were serial at 1500k, tries=1, with unique capture
queries isolating old URL caches. The current read-only fetch dependency uses
actual GET byte evidence rather than the old zero-length HEAD gate; this does
not retroactively certify old failures. Measured accepted-body byte windows
were below 8 MB/s, not proof of packet-level peak compliance. No installation,
credential/configuration change, lock theft, retry or remote cleanup occurred.

The two research documents passed executed credential-shape scans before
staging. The first PCRE2 scan could not execute in this environment and is not
a clean result. Seven raw public HTML captures contain an API_key-shaped
attribute; only coordinates are noted in the JSON, never its values. External
raw evidence is not certified secret-free and must not be imported into Git.
The source-verification stage uses `--inventory-only --verify-official`; its
source verdict does not certify GPU, browser, product or industrial behavior.

Still unclosed: V5/full CATIA deployment, Connected-specific role/install
mapping, complete license inclusion, competitor server-frame topology and
industrial comparison. These are evidence gaps, not assertions of absence.
