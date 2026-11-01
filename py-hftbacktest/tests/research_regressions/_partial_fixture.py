"""Public API probe of native partial-fill observations; tiny synthetic fixture only."""
import json,numpy as np
from hftbacktest import BacktestAsset,HashMapMarketDepthBacktest,EXCH_EVENT,LOCAL_EVENT,BUY_EVENT,SELL_EVENT,DEPTH_EVENT,TRADE_EVENT,GTX,LIMIT
from hftbacktest.order import IOC
DT=np.dtype([('ev','u8'),('exch_ts','i8'),('local_ts','i8'),('px','f8'),('qty','f8'),('order_id','u8'),('ival','i8'),('fval','f8')])
LAT=137_000_000;BASE=1_000_000_000
def row(ns,ev,px=0.,qty=0.):return (int(EXCH_EVENT|LOCAL_EVENT|ev),ns,ns+1_000_000,px,qty,0,0,0.)
def asset(a,partial):
 base=BacktestAsset().data([a]).linear_asset(1.).constant_order_latency(LAT,LAT).risk_adverse_queue_model()
 base=base.partial_fill_exchange() if partial else base.no_partial_fill_exchange()
 return base.trading_value_fee_model(.0002,.0005).tick_size(1.).lot_size(.1).last_trades_capacity(100)

def run(kind,partial=True,quantity=1.):
 if kind=='ioc':
  rows=[row(BASE,DEPTH_EVENT|BUY_EVENT,100.,.4),row(BASE,DEPTH_EVENT|BUY_EVENT,99.,1.),row(BASE,DEPTH_EVENT|SELL_EVENT,101.,.3),row(BASE,DEPTH_EVENT|SELL_EVENT,102.,.5)]
 else:
  rows=[row(BASE,DEPTH_EVENT|BUY_EVENT,100.,100.),row(BASE,DEPTH_EVENT|SELL_EVENT,101.,100.),row(1_300_000_000,TRADE_EVENT|SELL_EVENT,100.,100.4)]
  rows += [] if kind=='single' else [row(1_450_000_000,TRADE_EVENT|SELL_EVENT,100.,.2),row(1_600_000_000,TRADE_EVENT|SELL_EVENT,100.,.1)] if kind=='cancel' else ([] if kind=='single' else [row(1_500_000_000,TRADE_EVENT|SELL_EVENT,100.,.3),row(1_500_000_000,TRADE_EVENT|SELL_EVENT,100.,.3)])
 rows.append(row(2_000_000_000,0))
 h=HashMapMarketDepthBacktest([asset(np.array(rows,DT),partial)])
 out=[]
 def snap(ret,label):
  it=h.orders(0).values();orders=[]
  while it.has_next():
   o=it.get();orders.append({k:int(getattr(o,k)) if k in ['status','req','order_id','exch_timestamp','local_timestamp','time_in_force','exec_price_tick'] else float(getattr(o,k)) for k in ['order_id','status','req','qty','leaves_qty','exec_qty','exec_price_tick','exch_timestamp','local_timestamp','time_in_force']})
  v=h.state_values(0);out.append({'label':label,'ret':ret,'now':int(h.current_timestamp),'position':float(h.position(0)),'balance':float(v.balance),'fee':float(v.fee),'trading_value':float(v.trading_value),'native_trades':int(v.num_trades),'orders':orders})
 try:
  h.elapse(0);h.elapse(1_100_000_000-h.current_timestamp)
  tif=IOC if kind=='ioc' else GTX;price=102. if kind=='ioc' else 100.
  assert h.submit_buy_order(0,1,price,quantity,tif,LIMIT,False)==0;snap(-1,'submitted')
  next_timer=1_200_000_000;cancelled=False;last=(-1,-1,-1)
  while h.current_timestamp<1_900_000_000:
   deadline=min(next_timer,1_490_000_000) if kind=='cancel' and not cancelled else next_timer
   ret=h.wait_next_feed(True,max(0,deadline-h.current_timestamp));now=h.current_timestamp
   if ret==1:break
   if ret==0 and now==next_timer:next_timer+=100_000_000
   v=h.state_values(0);state=(v.num_trades,h.position(0),ret)
   if ret==3 or state[:2]!=last[:2]:snap(int(ret),'event')
   last=state
   if kind=='cancel' and not cancelled and now>=1_490_000_000:
    assert h.cancel(0,1,False)==0;cancelled=True;snap(-1,'cancel_sent')
  snap(-1,'final')
 finally:h.close()
 return out
