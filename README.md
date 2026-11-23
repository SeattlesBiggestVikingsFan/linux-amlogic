# Maintained kernel topics

## Today's isolated candidate

`integration/all-fixes-today-20261122` is a source-only refresh of the unchanged
published `integration/all-fixes` baseline. The original release and prior
source-equivalent tags remain untouched. The only new production topic is
`feature/lldv-vsif-suppression-v3-20261122`, incorporating the complete accepted
generic PR32 modern-AVI/LLDV policy and its test.

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

`workflow/catalog-today-20261122-v2` adds checksum-pinned source/build-cohort
provenance without changing the candidate source. The first catalog ref and
its independent Ethernet comparison receipt remain unchanged. The new public
receipt records only reviewed hashes and ABI facts; private source/evidence
locations, reconstructed source payloads and binaries are not imported.

The default assembler now targets the candidate tree recorded in this catalog.
Portable C2 negative controls use the public maintained pre-C2 commit whose
five relevant files match the original frozen controls byte-for-byte; no
unpublished frozen snapshot is needed to run those tests.

`upstream-dev` tracks the unmodified published Pannal development head.
New maintained feature, integration and workflow commits are obtained from
`https://github.com/SeattlesBiggestVikingsFan/linux-amlogic`, not Pannal's
repository. The catalog keeps Pannal as the upstream source and records the
owned fork separately for maintained commit retrieval.
The catalog's `upstream.commit` is the independently pinned, validated assembly
base; a newer tracked head is explicitly unvalidated and must not silently
replace it. Refreshing it requires separate source, build and acceptance review.

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

The `workflow/catalog` branch deliberately contains only public source tooling,
catalog and evidence. It is separate from the kernel source so `integration/all-fixes`
has exact production **and source-test** path, mode and content parity with the
original release. Export this branch to a separate tooling directory; do not
overlay it onto the kernel tree.

```sh
python3 assemble.py --repo "$KERNEL_REPO"
python3 assemble.py --repo "$KERNEL_REPO" --feature hdmi-c2-clock-serialization
python3 tests/test_workflow.py --repo "$KERNEL_REPO" --catalog kernel-catalog.json --output "$EVIDENCE"
python3 tests/c2/run_all.py --source "$KERNEL_REPO" --output "$EVIDENCE/c2"
python3 tests/test_ethernet_lease.py --source "$KERNEL_REPO" --output "$EVIDENCE/ethernet"
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
