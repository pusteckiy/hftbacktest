"""Public-API reproduction of decimal-lot terminal-order retention."""
from pathlib import Path
import json,time
import numpy as np
from _partial_fixture import row,asset,DT,BASE
from hftbacktest import HashMapMarketDepthBacktest,DEPTH_EVENT,TRADE_EVENT,BUY_EVENT,SELL_EVENT,GTX,LIMIT
def one(side,lot,total,first):
    buy=side==1;px=100. if buy else 101.
    rows=[row(BASE,DEPTH_EVENT|BUY_EVENT,100.,1.),row(BASE,DEPTH_EVENT|SELL_EVENT,101.,1.),
          row(1_300_000_000,TRADE_EVENT|(SELL_EVENT if buy else BUY_EVENT),px,1.+first*lot),
          row(1_500_000_000,TRADE_EVENT|(SELL_EVENT if buy else BUY_EVENT),px,(total-first)*lot),
          row(1_700_000_000,TRADE_EVENT|(SELL_EVENT if buy else BUY_EVENT),px,lot),
          row(2_500_000_000,0)]
    h=HashMapMarketDepthBacktest([asset(np.array(rows,DT),True).lot_size(lot)])
    observed=[];error=0
    try:
        h.elapse(0);h.elapse(1_100_000_000-h.current_timestamp)
        submit=h.submit_buy_order if buy else h.submit_sell_order
        assert submit(0,1,px,total*lot,GTX,LIMIT,False)==0
        while h.current_timestamp<2_200_000_000:
            ret=h.wait_next_feed(True,max(0,2_200_000_000-h.current_timestamp))
            if ret not in [0,2,3]:error=int(ret);break
            if ret==3:
                v=h.state_values(0)
                observed.append({'time_ns':int(h.current_timestamp),'position':float(h.position(0)),'trades':int(v.num_trades)})
        return {'side':side,'lot':lot,'total_lots':total,'first_lots':first,'native_error':error,'error_time_ns':int(h.current_timestamp) if error else None,'observed':observed,'terminal_position':float(h.position(0)),
                'float_remainder':total*lot-first*lot,'nominal_remaining':(total-first)*lot}
    finally:h.close()
