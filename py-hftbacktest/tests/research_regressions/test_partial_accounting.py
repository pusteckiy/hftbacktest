"""Regression controls for the verified partial-fill, IOC, cancel and amend fixes."""
import unittest
import numpy as np
from hftbacktest import HashMapMarketDepthBacktest, DEPTH_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT, GTX, LIMIT
from _partial_fixture import asset, row, run, DT
from _partial_edges import scenario
from _partial_decimal import one
import _legacy_amend as legacy
import _amend_fixture as amend

class PartialAccountingControls(unittest.TestCase):
    def test_same_timestamp_partial_fills_accumulate_once(self):
        rows = run("same_timestamp")
        first = rows[3]
        self.assertAlmostEqual(first["position"], .4)
        self.assertAlmostEqual(first["balance"], -40.)
        self.assertAlmostEqual(first["fee"], .008)
        final = rows[-1]
        self.assertAlmostEqual(final["position"], 1.)
        self.assertAlmostEqual(final["balance"], -100.)
        self.assertAlmostEqual(final["fee"], .02)
        self.assertEqual(final["native_trades"], 3)

    def test_partial_cancel_and_multilevel_ioc_cash(self):
        for kind, position, balance, fee, count in [
            ("cancel", .7, -70., .014, 3),
            ("ioc", .8, -81.3, .04065, 2),
        ]:
            with self.subTest(kind=kind):
                final = run(kind)[-1]
                for key, expected in [("position", position), ("balance", balance), ("fee", fee)]:
                    self.assertAlmostEqual(final[key], expected)
                self.assertEqual(final["native_trades"], count)

    def test_both_sides_exact_fill_cleanup_ioc_and_cancel_races(self):
        for kind in ["exact_then_more", "same_timestamp", "cancel_race", "ioc_full", "ioc_partial"]:
            for side in [1, -1]:
                with self.subTest(kind=kind, side=side):
                    result = scenario(kind, side)
                    self.assertEqual(result["status"], "passed", result)

    def test_decimal_lot_terminal_orders_are_removed(self):
        for lot, total, first in [(.001, 10, 7), (.1, 10, 7), (.001, 10, 4), (.001, 100, 73)]:
            for side in [1, -1]:
                with self.subTest(lot=lot, total=total, side=side):
                    result = one(side, lot, total, first)
                    self.assertEqual(result["native_error"], 0, result)
                    self.assertAlmostEqual(result["terminal_position"], side * total * lot)

    def test_rejected_cancel_does_not_replay_a_previous_execution(self):
        for side in [1, -1]:
            with self.subTest(side=side):
                buy = side == 1
                px = 100. if buy else 101.
                data = np.array([
                    row(1_000_000_000, DEPTH_EVENT | BUY_EVENT, 100., 100.),
                    row(1_000_000_000, DEPTH_EVENT | SELL_EVENT, 101., 100.),
                    row(1_300_000_000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, 100.4),
                    row(1_500_000_000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, .6),
                    row(2_500_000_000, 0),
                ], DT)
                hbt = HashMapMarketDepthBacktest([asset(data, True)])
                sent = False
                try:
                    hbt.elapse(0)
                    hbt.elapse(1_100_000_000 - hbt.current_timestamp)
                    submit = hbt.submit_buy_order if buy else hbt.submit_sell_order
                    self.assertEqual(submit(0, 1, px, 1., GTX, LIMIT, False), 0)
                    while hbt.current_timestamp < 2_200_000_000:
                        deadline = 1_450_000_000 if not sent else 2_200_000_000
                        self.assertIn(hbt.wait_next_feed(True, max(0, deadline - hbt.current_timestamp)), (0, 2, 3))
                        if hbt.current_timestamp >= 1_450_000_000 and not sent:
                            self.assertEqual(hbt.cancel(0, 1, False), 0)
                            sent = True
                        hbt.clear_inactive_orders(0)
                    values = hbt.state_values(0)
                    self.assertAlmostEqual(hbt.position(0), side)
                    self.assertAlmostEqual(values.balance, -side * px)
                    self.assertAlmostEqual(values.fee, px * .0002)
                    self.assertEqual(values.num_trades, 2)
                finally:
                    hbt.close()

    def test_no_partial_control_is_unchanged(self):
        final = run("single", partial=False, quantity=.4)[-1]
        self.assertAlmostEqual(final["position"], .4)
        self.assertAlmostEqual(final["balance"], -40.)
        self.assertAlmostEqual(final["fee"], .008)

class LegacyAmendControls(unittest.TestCase):
    def test_absolute_remaining_amend_scenarios(self):
        for case in legacy.cases:
            with self.subTest(name=case[0]):
                self.assertTrue(legacy.run(*case)["passed"])
        for name, qty, trade, expected in [
            ("queue_reduction_keeps_priority", .5, .5, .1),
            ("queue_increase_resets_priority", 2., 1.1, .1),
        ]:
            with self.subTest(name=name):
                result = legacy.run(name, [(50_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 99., .6),
                    (100_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 99., trade)],
                    [(60_000_000, "amend", 99., qty), (120_000_000, "final", 0, 0)],
                    {"position": expected, "cash": -99. * expected}, ahead=1.)
                self.assertTrue(result["passed"])
        for name, qty in [("no_partial_increase", 2.), ("no_partial_reduce", .5)]:
            with self.subTest(name=name):
                result = legacy.run(name, [(100_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 98., 3.1)],
                    [(30_000_000, "amend", 98., qty), (120_000_000, "final", 0, 0)],
                    {"position": qty, "cash": -98. * qty}, partial=False)
                self.assertTrue(result["passed"])

    def test_receipts_survive_fill_and_ack_timestamp_collisions(self):
        from _receipts import reports, clear_reports
        for events in [
            [(40_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 99., .3),
             (40_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 98., .4)],
            [(35_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 99., .3),
             (35_000_000, amend.TRADE_EVENT | amend.SELL_EVENT, 99., .4)],
        ]:
            with self.subTest(events=events):
                hbt = HashMapMarketDepthBacktest([amend.asset(amend.initial() + events)])
                try:
                    clear_reports(hbt, 0)
                    self.assertEqual(hbt.elapse(0), 0)
                    self.assertEqual(hbt.submit_buy_order(0, 1, 99., 1., GTX, LIMIT, False), 0)
                    amend.until(hbt, 30_000_000)
                    self.assertEqual(hbt.modify(0, 1, 98., .6, False), 0)
                    amend.until(hbt, 60_000_000)
                    fills = reports(hbt, 0).copy()
                    self.assertGreater(len(fills), 0)
                    signed = [1 if int(r["ev"]) & BUY_EVENT else -1 for r in fills]
                    self.assertAlmostEqual(sum(s * r["qty"] for s, r in zip(signed, fills)), hbt.position(0))
                    self.assertAlmostEqual(sum(-s * r["qty"] * r["px"] for s, r in zip(signed, fills)), hbt.state_values(0).balance)
                    self.assertEqual(len(fills), hbt.state_values(0).num_trades)
                    self.assertTrue(all(int(r["order_id"]) == 1 for r in fills))
                    clear_reports(hbt, 0)
                    self.assertEqual(len(reports(hbt, 0)), 0)
                finally:
                    hbt.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
