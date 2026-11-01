# Research fork specification

The user requested a personal hftbacktest fork containing every correction already used in research, so future backtests can install a reproducible fixed dependency without patching new temporary copies.

- Base the compatibility branch on upstream py-v2.4.4, commit a244a14250b42d97fc305569c93c4117cd5e1dff, matching the original PyPI 2.4.4 Rust sources.
- Preserve the verified partial-fill, IOC/cancel, lot-rounding, execution-receipt, remaining-quantity amend and opt-in total-quantity amend corrections, including the late ACK terminal-state fix.
- Preserve existing modify semantics; expose modify_total on HashMap and ROI-vector wrappers.
- Retain the three-crate build workspace and resolved dependency lock used by the approved binary; distinguish the Python distribution as 2.4.4+replay.1.
- Include portable synthetic regression controls and installation instructions. No market datasets, credentials, private bot strategies or binary build directories belong in Git.
- Do not modify Replay, the live bot, historical sources/results or active installed packages. No CI is requested.
- Document known limitations, upstream attribution and the historical source/binary provenance. Do not promise byte-identical binaries or venue-perfect execution.
