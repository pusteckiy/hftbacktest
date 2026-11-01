"""Public-API edge regressions for the private PartialFill dependency build."""
from pathlib import Path
import sys, json, traceback
import numpy as np
from _partial_fixture import asset, row, BASE, LAT
from hftbacktest import HashMapMarketDepthBacktest, DEPTH_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT, GTX, LIMIT
from hftbacktest.order import IOC, FILLED, EXPIRED, CANCELED, NONE

def scenario(kind, side):
    buy = side == 1
    if kind.startswith('ioc'):
        amounts = [0.3, 0.5, 0.7] if kind == 'ioc_full' else [0.3, 0.5]
        rows = [row(BASE, DEPTH_EVENT | (SELL_EVENT if buy else BUY_EVENT), 101.0 + j if buy else 100.0 - j, q) for j, q in enumerate(amounts)]
        rows.append(row(BASE, DEPTH_EVENT | (BUY_EVENT if buy else SELL_EVENT), 100.0 if buy else 101.0, 10.0))
        wanted = 1.0
        price = 103.0 if buy else 98.0
        tif = IOC
        expected_q = side * (1.0 if kind == 'ioc_full' else 0.8)
        value = 101.9 if buy and kind == 'ioc_full' else 99.1 if kind == 'ioc_full' else 81.3 if buy else 79.5
        expected_count = 3 if kind == 'ioc_full' else 2
    else:
        rows = [row(BASE, DEPTH_EVENT | BUY_EVENT, 100.0, 0.7), row(BASE, DEPTH_EVENT | SELL_EVENT, 101.0, 0.7)]
        px = 100.0 if buy else 101.0
        rows += [row(1300000000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, 0.9), row(1500000000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, 0.3), row(1500000000 if kind == 'same_timestamp' else 1700000000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, 0.2 if kind == 'cancel_race' else 0.5), row(1900000000, TRADE_EVENT | (SELL_EVENT if buy else BUY_EVENT), px, 0.1)]
        wanted = 1.0
        price = px
        tif = GTX
        expected_q = side * (0.7 if kind == 'cancel_race' else 1.0)
        value = abs(expected_q) * px
        expected_count = 3
    rows.append(row(2500000000, 0))
    rows.sort(key=lambda r: r[1])
    h = HashMapMarketDepthBacktest([asset(np.array(rows, dtype=rows_dtype), True)])
    observed = []
    canceled = False
    journal = []
    try:
        h.elapse(0)
        h.elapse(1100000000 - h.current_timestamp)
        code = h.submit_buy_order(0, 1, price, wanted, tif, LIMIT, False) if buy else h.submit_sell_order(0, 1, price, wanted, tif, LIMIT, False)
        assert code == 0
        while h.current_timestamp < 2200000000:
            deadline = 1600000000 if kind == 'cancel_race' and (not canceled) else 2200000000
            ret = h.wait_next_feed(True, max(0, deadline - h.current_timestamp))
            assert ret in [0, 2, 3], ('native_error', ret)
            now = h.current_timestamp
            if ret == 3:
                it = h.orders(0).values()
                while it.has_next():
                    o = it.get()
                    if o.exch_timestamp > 0:
                        assert now - o.exch_timestamp == LAT
                v = h.state_values(0)
                observed.append({'now': int(now), 'position': float(h.position(0)), 'count': int(v.num_trades)})
            if kind == 'cancel_race' and now >= 1600000000 and (not canceled):
                assert h.cancel(0, 1, False) == 0
                canceled = True
        v = h.state_values(0)
        actual = {'position': float(h.position(0)), 'balance': float(v.balance), 'fee': float(v.fee), 'trading_value': float(v.trading_value), 'num_trades': int(v.num_trades)}
        expected = {'position': expected_q, 'balance': -side * value, 'fee': value * (0.0005 if tif == IOC else 0.0002), 'trading_value': value, 'num_trades': expected_count}
        checks = {k: abs(actual[k] - v) < 1e-08 for k, v in expected.items()}
        it = h.orders(0).values()
        count_orders = 0
        while it.has_next():
            o = it.get()
            count_orders += 1
            expected_status = EXPIRED if kind == 'ioc_partial' else CANCELED if kind == 'cancel_race' else FILLED
            checks['final_status'] = o.status == expected_status
            checks['final_request_clear'] = o.req == NONE
            checks['final_leaves_quantity'] = abs(o.leaves_qty - (1.0 - abs(expected_q))) < 1e-08
        checks['one_order_state'] = count_orders == 1
        return {'status': 'passed' if all(checks.values()) else 'failed', 'actual': actual, 'expected': expected, 'checks': checks, 'observed': observed}
    finally:
        h.close()
rows_dtype = np.dtype([('ev', 'u8'), ('exch_ts', 'i8'), ('local_ts', 'i8'), ('px', 'f8'), ('qty', 'f8'), ('order_id', 'u8'), ('ival', 'i8'), ('fval', 'f8')])
