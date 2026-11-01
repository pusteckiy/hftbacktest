# Verification of the research compatibility patch set

Date: 2026-11-01. Base: upstream py-v2.4.4, a244a14250b42d97fc305569c93c4117cd5e1dff.

## Runtime checks

- Offline, locked native release-wheel build passed for CPython3.12 on Linux x86-64.
- All 24 portable Python regression test methods passed on the historical approved native build.
- The same 24 methods passed on the newly built fork wheel. Subcases cover both sides, IOC depth walking, decimal lots, cancel races, legacy amendments, total amendments and receipt/ACK collisions.
- All 23 Rust library unit tests passed, including the late-total-ACK terminal-order regression.
- A negative control on unmodified hftbacktest 2.4.4 failed the partial-fill accounting assertion: position 0 instead of 0.4. The test therefore detects a real historical defect.
- Initial test discovery inside the unbuilt Python source package shadowed the installed extension; it failed import before execution. The documented command runs from repository root.

## Source preservation

All 43 historical native Rust source files and the canonical Python binding match the approved source byte-for-byte. The three-crate workspace and Cargo.lock match that build. RESEARCH_FIXES_PROVENANCE.json lists each patched source hash. Python distribution metadata and fork documentation are intentionally distinct.

## Standards review

No blocking documented-standard findings. A pending-amend four-field tuple is a possible Data Clumps smell; a named record is deferred to preserve the approved implementation. Existing unsafe C exports follow the upstream binding convention. Borrowed receipt arrays must be copied before simulator mutation or shutdown.

## Specification review

No missing, partial or unrequested implementation found within the consolidation scope. Legacy modify, opt-in modify_total, the quantity/accounting fixes, dependency lock, portable controls and provenance are present. No private market data, strategy or build binary was added.

## Limits

This verifies consolidation of the existing fixes, not an engine redesign or new strategy profitability. No multi-day financial replay, live deployment or byte-identical binary claim is made. The known last-bit volume-reporting strict-gate failure and ROI receipt limitations remain documented.
