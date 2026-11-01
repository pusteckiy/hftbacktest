"""Synthetic public-API controls for the isolated modify_total build; no historical data."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("NUMBA_OPT", "0")
os.environ.setdefault("NUMBA_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np

from hftbacktest import (
    BacktestAsset,
    HashMapMarketDepthBacktest,
    ROIVectorMarketDepthBacktest,
    event_dtype,
    EXCH_EVENT,
    LOCAL_EVENT,
    DEPTH_SNAPSHOT_EVENT,
    TRADE_EVENT,
    BUY_EVENT,
    SELL_EVENT,
)
from hftbacktest.order import (
    NONE,
    NEW,
    FILLED,
    PARTIALLY_FILLED,
    GTC,
    GTX,
    LIMIT,
)

BASE = 1_000_000_000
LATENCY = 10_000_000
BID = 99.0
ASK = 101.0


def _row(ev: int, exch: int, local: int, px: float = 0.0, qty: float = 0.0):
    row = np.zeros(1, dtype=event_dtype)
    row["ev"] = ev
    row["exch_ts"] = exch
    row["local_ts"] = local
    row["px"] = px
    row["qty"] = qty
    return row[0]


def _backtest(trades=(), lot_size=1.0, queue_ahead=1.0, partial=True, roi=False):
    rows = []
    for side, price, qty in ((BUY_EVENT, BID, queue_ahead), (SELL_EVENT, ASK, 100.0)):
        rows.append(_row(EXCH_EVENT | DEPTH_SNAPSHOT_EVENT | side, BASE, BASE + LATENCY, price, qty))
        rows.append(_row(LOCAL_EVENT | DEPTH_SNAPSHOT_EVENT | side, BASE, BASE + LATENCY, price, qty))
    for ts, qty, price in trades:
        rows.append(_row(EXCH_EVENT | TRADE_EVENT | SELL_EVENT, ts, ts, price, qty))
    rows.append(_row(EXCH_EVENT | LOCAL_EVENT, BASE + 250_000_000, BASE + 250_000_000))
    data = np.asarray(rows, dtype=event_dtype)
    data = data[np.argsort(data["local_ts"], kind="stable")]
    asset = (
        BacktestAsset()
        .linear_asset(1.0)
        .data(data)
        .constant_order_latency(LATENCY, LATENCY)
        .risk_adverse_queue_model()
        .tick_size(1.0)
        .lot_size(lot_size)
        .last_trades_capacity(32)
    )
    asset = asset.partial_fill_exchange() if partial else asset.no_partial_fill_exchange()
    if roi:
        asset.roi_lb(BID - 20.0).roi_ub(ASK + 20.0)
        hbt = ROIVectorMarketDepthBacktest([asset])
    else:
        hbt = HashMapMarketDepthBacktest([asset])
    assert hbt.elapse(LATENCY) == 0
    assert hbt.depth(0).best_bid == BID
    assert hbt.depth(0).best_ask == ASK
    return hbt


def _place(hbt, oid=1, qty=10.0, tif=GTX):
    assert hbt.submit_buy_order(0, oid, BID, qty, tif, LIMIT, True) == 0
    assert hbt.current_timestamp == BASE + 3 * LATENCY
    return hbt.orders(0).get(oid)


def _advance(hbt, duration):
    assert hbt.elapse(duration) == 0


def _active(hbt, oid=1):
    order = hbt.orders(0).get(oid)
    assert order is not None
    assert order.req == NONE
    return order


class ModifyTotalControls(unittest.TestCase):
    def test_total_subtracts_fill_before_send_whose_receipt_is_later(self):
        hbt = _backtest([(BASE + 25_000_000, 4.0, BID)])
        try:
            _place(hbt)
            # The exchange filled 3 before this send; its fill report arrives at +35ms.
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 3.0)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 7.0)
            self.assertEqual(order.status, PARTIALLY_FILLED)
            self.assertAlmostEqual(hbt.state_values(0).trading_volume, 3.0)
        finally:
            hbt.close()

    def test_total_subtracts_fill_during_request(self):
        hbt = _backtest([(BASE + 35_000_000, 4.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 3.0)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 7.0)
            self.assertEqual(order.status, PARTIALLY_FILLED)
        finally:
            hbt.close()

    def test_response_flight_fill_reconciles_without_exchange_future_peek(self):
        hbt = _backtest([(BASE + 45_000_000, 4.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            # Exchange amend arrives at +40ms, fill is at +45ms, fill receipt
            # and the delayed amend ACK are serialized at +55ms.
            _advance(hbt, 25_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 3.0)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 7.0)
            self.assertEqual(order.status, PARTIALLY_FILLED)
        finally:
            hbt.close()

    def test_fractional_aero_total_205_exec_143_leaves_62(self):
        hbt = _backtest([(BASE + 25_000_000, 14.4, BID)], lot_size=0.1, queue_ahead=0.1)
        try:
            _place(hbt, qty=20.5)
            assert hbt.modify_total(0, 1, BID, 20.5, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 14.3, places=8)
            self.assertAlmostEqual(order.qty, 20.5, places=8)
            self.assertAlmostEqual(order.leaves_qty, 6.2, places=8)
            self.assertEqual(order.status, PARTIALLY_FILLED)
        finally:
            hbt.close()

    def test_repeated_total_price_amends_keep_lifetime_cumulative(self):
        hbt = _backtest([
            (BASE + 25_000_000, 4.0, BID),       # 3 filled before amend 1
            (BASE + 65_000_000, 2.0, BID),       # 2 more during amend 2
        ])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            first = _active(hbt)
            self.assertAlmostEqual(first.leaves_qty, 7.0)

            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID - 1.0, first.qty, False) == 0
            _advance(hbt, 20_000_000)
            second = _active(hbt)
            self.assertAlmostEqual(second.qty, 10.0)
            self.assertAlmostEqual(second.leaves_qty, 5.0)

            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, second.qty, False) == 0
            _advance(hbt, 20_000_000)
            third = _active(hbt)
            self.assertAlmostEqual(third.qty, 10.0)
            self.assertAlmostEqual(third.leaves_qty, 5.0)
            self.assertAlmostEqual(hbt.position(0), 5.0)
            self.assertEqual(hbt.state_values(0).num_trades, 2)
        finally:
            hbt.close()

    def test_resize_up_then_down_uses_total_not_leaves(self):
        hbt = _backtest([(BASE + 25_000_000, 4.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            self.assertAlmostEqual(_active(hbt).leaves_qty, 7.0)

            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, 13.0, False) == 0
            _advance(hbt, 20_000_000)
            grown = _active(hbt)
            self.assertAlmostEqual(grown.qty, 13.0)
            self.assertAlmostEqual(grown.leaves_qty, 10.0)

            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, 8.0, False) == 0
            _advance(hbt, 20_000_000)
            shrunk = _active(hbt)
            self.assertAlmostEqual(shrunk.qty, 8.0)
            self.assertAlmostEqual(shrunk.leaves_qty, 5.0)
            self.assertAlmostEqual(hbt.position(0), 3.0)
        finally:
            hbt.close()

    def test_legacy_modify_still_means_absolute_remaining_quantity(self):
        hbt = _backtest([(BASE + 25_000_000, 4.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify(0, 1, BID - 1.0, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 3.0)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 10.0)
        finally:
            hbt.close()

    def test_postonly_rejection_keeps_shape_and_clears_request(self):
        hbt = _backtest()
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, ASK, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertEqual(order.price_tick, int(BID))
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 10.0)
            self.assertEqual(order.status, NEW)
            self.assertEqual(order.req, NONE)
        finally:
            hbt.close()

    def test_full_fill_before_amend_ack_is_not_reinserted(self):
        hbt = _backtest([(BASE + 25_000_000, 11.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 5_000_000)
            filled = hbt.orders(0).get(1)
            self.assertIsNotNone(filled)
            self.assertEqual(filled.status, FILLED)
            self.assertAlmostEqual(hbt.position(0), 10.0)
            before = hbt.state_values(0)
            before_trades = before.num_trades
            before_volume = before.trading_volume

            hbt.clear_inactive_orders(0)
            self.assertIsNone(hbt.orders(0).get(1))
            _advance(hbt, 15_000_000)
            self.assertIsNone(hbt.orders(0).get(1))
            after = hbt.state_values(0)
            self.assertAlmostEqual(hbt.position(0), 10.0)
            self.assertEqual(after.num_trades, before_trades)
            self.assertAlmostEqual(after.trading_volume, before_volume)
        finally:
            hbt.close()

    def test_full_fill_after_amend_arrival_before_response_does_not_reopen_order(self):
        hbt = _backtest([(BASE + 45_000_000, 11.0, BID)])
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            # Total amend reaches the exchange at +40ms and ACKs at +50ms.
            # The full fill at +45ms is not reported locally until +55ms, so
            # the accepted ACK must still expose the working order at +50ms.
            _advance(hbt, 20_000_000)
            accepted = _active(hbt)
            self.assertEqual(accepted.status, NEW)
            self.assertAlmostEqual(accepted.qty, 10.0)
            self.assertAlmostEqual(accepted.leaves_qty, 10.0)
            self.assertAlmostEqual(hbt.position(0), 0.0)

            # Only the local fill receipt at +55ms makes it terminal.
            _advance(hbt, 5_000_000)
            filled = hbt.orders(0).get(1)
            self.assertIsNotNone(filled)
            self.assertEqual(filled.status, FILLED)
            self.assertEqual(filled.req, NONE)
            self.assertAlmostEqual(hbt.position(0), 10.0)
            before = hbt.state_values(0)
            trades, volume = before.num_trades, before.trading_volume

            hbt.clear_inactive_orders(0)
            self.assertIsNone(hbt.orders(0).get(1))
            _advance(hbt, 10_000_000)
            self.assertIsNone(hbt.orders(0).get(1))
            after = hbt.state_values(0)
            self.assertEqual(after.num_trades, trades)
            self.assertAlmostEqual(after.trading_volume, volume)
        finally:
            hbt.close()

    def test_same_price_noop_preserves_queue_priority(self):
        hbt = _backtest(
            [(BASE + 35_000_000, 3.0, BID), (BASE + 65_000_000, 2.0, BID), (BASE + 66_000_000, 1.0, BID)],
            queue_ahead=5.0,
        )
        try:
            _place(hbt)
            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, 10.0, False) == 0
            _advance(hbt, 40_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 1.0)
            self.assertAlmostEqual(order.leaves_qty, 9.0)
        finally:
            hbt.close()

    def test_same_price_total_decrease_preserves_queue_priority(self):
        hbt = _backtest(
            [(BASE + 35_000_000, 3.0, BID), (BASE + 65_000_000, 2.0, BID), (BASE + 66_000_000, 1.0, BID)],
            queue_ahead=5.0,
        )
        try:
            _place(hbt)
            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, 9.0, False) == 0
            _advance(hbt, 40_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 1.0)
            self.assertAlmostEqual(order.qty, 9.0)
            self.assertAlmostEqual(order.leaves_qty, 8.0)
        finally:
            hbt.close()

    def test_same_price_total_increase_resets_queue_priority(self):
        hbt = _backtest(
            [(BASE + 35_000_000, 3.0, BID), (BASE + 65_000_000, 3.0, BID), (BASE + 66_000_000, 3.0, BID)],
            queue_ahead=5.0,
        )
        try:
            _place(hbt)
            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID, 11.0, False) == 0
            _advance(hbt, 30_000_000)
            self.assertAlmostEqual(hbt.position(0), 0.0)
            _advance(hbt, 10_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 1.0)
            self.assertAlmostEqual(order.qty, 11.0)
            self.assertAlmostEqual(order.leaves_qty, 10.0)
        finally:
            hbt.close()

    def test_price_change_resets_queue_priority(self):
        hbt = _backtest(
            [(BASE + 35_000_000, 3.0, BID), (BASE + 65_000_000, 1.0, BID + 1.0)],
            queue_ahead=5.0,
        )
        try:
            _place(hbt)
            _advance(hbt, 10_000_000)
            assert hbt.modify_total(0, 1, BID + 1.0, 10.0, False) == 0
            _advance(hbt, 40_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(hbt.position(0), 1.0)
            self.assertEqual(order.price_tick, int(BID + 1.0))
            self.assertAlmostEqual(order.leaves_qty, 9.0)
        finally:
            hbt.close()

    def test_roi_vector_public_wrapper_exposes_total_mode(self):
        hbt = _backtest(partial=False, roi=True)
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID - 1.0, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 10.0)
        finally:
            hbt.close()

    def test_no_partial_exchange_accepts_total_quantity_mode(self):
        hbt = _backtest(partial=False)
        try:
            _place(hbt)
            assert hbt.modify_total(0, 1, BID - 1.0, 10.0, False) == 0
            _advance(hbt, 20_000_000)
            order = _active(hbt)
            self.assertAlmostEqual(order.qty, 10.0)
            self.assertAlmostEqual(order.leaves_qty, 10.0)
        finally:
            hbt.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

