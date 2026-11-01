"""Public native API probes: real amend, partial accounting and timestamp-batch behavior."""
import json,time
import numpy as np
from hftbacktest import BacktestAsset,HashMapMarketDepthBacktest,event_dtype,EXCH_EVENT,LOCAL_EVENT,DEPTH_SNAPSHOT_EVENT,DEPTH_EVENT,DEPTH_CLEAR_EVENT,TRADE_EVENT,BUY_EVENT,SELL_EVENT,GTX,LIMIT
S=1_000_000_000;LAT=10_000_000
def asset(events):
 a=np.zeros(len(events)+1,event_dtype)
 for i,(t,kind,p,q) in enumerate(events):a[i]["ev"]=EXCH_EVENT|LOCAL_EVENT|kind;a[i]["exch_ts"]=a[i]["local_ts"]=S+t;a[i]["px"]=p;a[i]["qty"]=q
 a[-1]["ev"]=EXCH_EVENT|LOCAL_EVENT;a[-1]["exch_ts"]=a[-1]["local_ts"]=S+10_000_000_000
 return BacktestAsset().data([a]).linear_asset(1.).tick_size(1.).lot_size(.1).constant_order_latency(LAT,LAT).partial_fill_exchange().risk_adverse_queue_model().trading_value_fee_model(.0002,.0005).last_trades_capacity(1000)
def initial():
 return [(0,DEPTH_SNAPSHOT_EVENT|BUY_EVENT,99.,.1),(0,DEPTH_SNAPSHOT_EVENT|BUY_EVENT,98.,.1),(0,DEPTH_SNAPSHOT_EVENT|BUY_EVENT,97.,.1),(0,DEPTH_SNAPSHOT_EVENT|SELL_EVENT,101.,10.)]
def state(h,label):
 o=h.orders(0).get(1);v=h.state_values(0)
 return {"label":label,"time_ns":int(h.current_timestamp),"position":float(h.position(0)),"cash":float(v.balance),"fees":float(v.fee),"num_trades":int(v.num_trades),"order":None if o is None else {"id":int(o.order_id),"status":int(o.status),"req":int(o.req),"price":float(o.price),"qty":float(o.qty),"leaves":float(o.leaves_qty),"exec_qty":float(o.exec_qty),"exec_price_tick":int(o.exec_price_tick),"exch_ns":int(o.exch_timestamp),"cancellable":bool(o.cancellable)}}
def until(h,offset):assert h.elapse(S+offset-h.current_timestamp)==0
