# Maintained kernel topics

## Published upstream refresh

`integration/all-fixes-upstream-20261123` assembles all 26 accepted topics on
published Pannal `605cf2407df57a850fc7db29e514c2de41b5ebc7`. The complete public
history contains four commits beyond the previously validated `58c06bb0` pin:
HDR10+ metadata validation, SCDC channel-2 status, HDR/DV/OOTF bounds, and PQ
sysfs/descriptor handling. The prior source remains `d4989289`, tree `a2921b26`.
Every old branch, release/source tag and archived PR reference remains unchanged.

Twenty-one topic deltas apply without adaptation and retain their stable patch
IDs. Five already-upstream topics remain checksum-reviewed, with no replay
commits. The default assembly has exactly 59,132 paths. Applying the complete
public upstream patch to the prior candidate produces this exact tree; reversing
it reproduces the complete prior tree. Only the ten published upstream paths
change, including the new `vframe_aux.h`. No private parser/VFM experiments,
UX12, pre-roll or pending HDR10+ visibility changes are added.

The new kernel parameter `hdmi_tx_hw.lldv_vsif_suppress` defaults **Off**.
Enabling it retains source-led LLDV processing while withholding only its Dolby
VSIF and using advertised modern AVI timing. The modern-AVI declaration policy
is also opt-in. Leave it Off for displays requiring Dolby signaling. The
independent `seamless_mode_refresh` compiled default remains **On**.
The generic default-Off path, generic commit with suppression On, refreshed
integration, and Dolby-signaled display behavior have not been hardware-tested.
Existing acceptance applies only to the custom default-On projector-chain
image; no automatic boot/profile override is added to this generic candidate.

Native Ethernet review8a ABI source is unchanged in all three relevant files.
HTTPS10/UX11 are runtime/UI updates, not additional kernel patches. Their older
accepted kernel and the newer combined baseline are different binaries/config
and export cohorts: do not substitute or relabel them, or claim binary-wide ABI
equivalence. New firmware builds and module audits remain separate parent gates.

`workflow/catalog-upstream-20261123` contains the refreshed catalog, exact
upstream-equivalence review, source-tree proof, current host checks and build
contract. Older evidence remains explicitly historical. Public receipts contain
reviewed hashes and ABI facts, not private source/evidence locations, source
payloads, credentials or binaries.

The default assembler now targets the candidate tree recorded in this catalog.
Portable C2 negative controls use the public maintained pre-C2 commit whose
five relevant files match the original frozen controls byte-for-byte; no
unpublished frozen snapshot is needed to run those tests.

`upstream-dev-20261123` pins the fetched, unmodified public development head.
New maintained feature, integration and workflow commits are obtained from
`https://github.com/SeattlesBiggestVikingsFan/linux-amlogic`, not Pannal's
repository. The catalog keeps Pannal as the upstream source and records the
owned fork separately for maintained commit retrieval.
The catalog's `upstream.commit` is now the exact live `605cf240` source assembly
base, not the old `58c06bb0` plus a refresh annotation. Offline source validation
does not imply a target build, module-ABI or hardware acceptance. A future moving
head must not silently replace this pin.

Each `fix/*` or `feature/*` ref contains one focused, cherry-pickable delta on
that pin plus its explicitly declared dependencies. Select features by catalog
ID, not by cherry-picking an entire branch range. Dependency-base archive
commits are not feature payloads. Fourteen distribution patches are separate
topics and are already present in the reconstructed source: do not apply them
again during packaging.

Keep published PR refs and `source/*` tags unchanged. Maintain new versions
using new refs and catalogs. Upstream-integrated topics retain their original
published commit and patch-ID provenance but have no replay commits. They are
not substitutes for AFBC deferred scanout retention, which remains included as
its own production fix and regressions.

The workflow branch deliberately contains only public tooling, catalog and
evidence. Export it to a separate directory; do not overlay it onto the kernel
source. Exact path/mode/content parity now means the new locked default assembly,
not the earlier release whose upstream source differs.

```sh
python3 assemble.py --repo "$KERNEL_REPO"
python3 assemble.py --repo "$KERNEL_REPO" --feature hdmi-c2-clock-serialization
python3 tests/test_workflow.py --repo "$KERNEL_REPO" --catalog kernel-catalog.json --output "$EVIDENCE"
python3 tests/c2/run_all.py --source "$KERNEL_REPO" --output "$EVIDENCE/c2"
python3 tests/test_ethernet_lease.py --source "$KERNEL_REPO" --output "$EVIDENCE/ethernet"
python3 tests/test_upstream_metadata.py --source "$KERNEL_REPO" --output "$EVIDENCE/metadata"
python3 tests/test_upstream_controls.py --source "$KERNEL_REPO" --output "$EVIDENCE/controls"
```

The assembler uses an isolated temporary index and returns a tree; it never
checks out files, moves branches, pushes, builds firmware or touches hardware.
Default selection recreates the locked source tree. Selecting a topic closes
its dependencies automatically; invalid/missing/cyclic dependencies and
unreviewed upstream skips fail explicitly.

`source/release-original-20261122` identifies original release source.
`source/reconstructed-equivalent-20261122` identifies different reconstructed
commits with the same tree. Neither relabels a historical binary as rebuilt
from these new commit IDs. Original release build/module evidence remains
original evidence; this workflow adds offline source and host-model checks,
not a new firmware build or hardware certification.

`source/candidate-upstream-20261123` identifies this distinct, unbuilt source
candidate. Rebuild coherently with the unchanged framework target/config contract;
do not reuse old kernel objects, `Module.symvers`, images or module certification.
The new parser return type and internal HDR/OOTF structs require matching callers.
There are no new mandatory Kconfig settings or compiler dependencies. Checked-in
config hashes are not the resolved build `.config` hash. Generate fresh config,
symbol, module/provider and proprietary-adapter audits in the parent-owned SDK;
the inherited DOVI exceptions below are not waived by passing host checks.

The selected compiled seamless gate is **on**. The accepted later Kodi coupling
keeps manual Off authoritative at startup/change; this requires the companion
Kodi integration and does not force DV mode/profile changes.

Native Ethernet 8a ABI1 is retained; manual timing 9 is excluded. Ethernet
host models do not perform calibration or prove PHY/driver ABI compatibility.
The native DOVI/module policy is unchanged. Original module audit claims
(12 legacy provider CRCs with two inherited exceptions, exact 18 DOVI5 and
28 shim matches) are inherited provenance, not a new generic vendor ABI
certification. Physical legacy declaration limits and proprietary-module
exceptions remain unresolved; no proprietary binary is committed.
