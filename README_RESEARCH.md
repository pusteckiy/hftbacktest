# Verified research fork

This compatibility branch preserves the hftbacktest corrections used by our research backend. It is based on upstream **py-v2.4.4 / a244a14250b42d97fc305569c93c4117cd5e1dff**, not a silent upgrade to a newer engine. Python package version: **2.4.4+replay.1**.

## Installation

Use Python 3.11+ and a Rust toolchain compatible with the upstream crate (Rust 1.91.1+). Install the tagged fork rather than the unmodified PyPI package:

    python -m pip install "hftbacktest @ git+https://github.com/pusteckiy/hftbacktest.git@v2.4.4-replay.1#subdirectory=py-hftbacktest"

For a research run, pin the resolved 40-character Git commit in its dependency/provenance manifest. The tag identifies this reviewed patch set. Existing environments are not changed automatically.

## Included corrections

- Accumulate each partial execution once; retain complete same-timestamp maker/IOC accounting.
- Correct exact-quantity terminal cleanup and decimal-lot rounding in the partial-fill queue.
- Prevent cancel/reject/ACK responses from replaying earlier executions.
- Reconcile legacy absolute-remaining amendments, including fills during request/response latency and queue-priority changes.
- Add opt-in execution receipt journaling (HashMap C exports) so multiple executions are not lost when order snapshots are replaced.
- Add **modify_total(asset_no, order_id, price, total_qty, wait)** to HashMap and ROI-vector backtests. Total quantity includes lifetime executions. Preserve **modify** as absolute remaining quantity.
- Keep a late successful total-amend ACK from resurrecting an already terminal order.

Never mix a total-mode amend chain with legacy remaining-quantity amendments. Unsupported processors fail closed. Receipt journaling is enabled by the first clear call; copy reports before advancing, clearing, or closing the backtest. The portable test helper demonstrates the existing C receipt API.

## Build and controls

The root workspace deliberately contains the same three backtest crates as the approved source distribution. The upstream collector/live-connector source remains in the repository but is outside this compatibility workspace. Cargo.lock records the resolved dependency set.

    cargo test -p hftbacktest --no-default-features --features backtest --release --locked --lib
    cd py-hftbacktest
    maturin build --release --locked
    python -m pip install ../target/wheels/hftbacktest-2.4.4+replay.1-*.whl
    cd ..
    NUMBA_OPT=0 python -m unittest discover -s py-hftbacktest/tests/research_regressions -v

The synthetic suite requires NumPy/Numba from the package dependencies and uses no exchange credentials, network, market data or private bot code. Release native Rust optimizations remain enabled; NUMBA_OPT=0 matches the validated research strategy environment.

## Provenance and limits

RESEARCH_FIXES_PROVENANCE.json records source hashes and the historical approved extension hash. Partial-fill accounting includes the correction reviewed in [upstream PR #323](https://github.com/nkaz001/hftbacktest/pull/323); upstream MIT attribution is retained. The additional corrections were validated in isolated research copies before consolidation here.

This is a backtest dependency, not a deployment of a live strategy. It does not add market impact, liquidation, funding cashflows or historical rule guarantees. ROI has total-amend bindings but does not gain the custom HashMap receipt exports. Same-timestamp report ordering and last-bit floating accumulation can still differ; the prior strict daily volume-reporting gate was not relaxed or fixed by this consolidation. Binaries can differ by compiler/platform even when source and financial behavior match.
