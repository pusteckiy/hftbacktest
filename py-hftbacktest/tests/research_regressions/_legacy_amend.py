"""Native amend financial regressions. Quantity means new remaining order size."""
import json,math,os,time
import _amend_fixture as p
def close(a,b):assert math.isclose(a,b,abs_tol=1e-9),f"{a} != {b}"
def book(ahead=.1):
 return [(0,p.DEPTH_SNAPSHOT_EVENT|p.BUY_EVENT,99.,ahead),(0,p.DEPTH_SNAPSHOT_EVENT|p.BUY_EVENT,98.,ahead),(0,p.DEPTH_SNAPSHOT_EVENT|p.SELL_EVENT,101.,10.)]
def run(name,events,steps,expect,partial=True,ahead=.1):
 a=p.asset(book(ahead)+events)
 if not partial:a.no_partial_fill_exchange()
 h=p.HashMapMarketDepthBacktest([a]);states=[]
 try:
  assert h.elapse(0)==0
  assert h.submit_buy_order(0,1,99.,1.,p.GTX,p.LIMIT,False)==0
  for t,action,price,qty in steps:
   p.until(h,t)
   if action=="amend":assert h.modify(0,1,price,qty,False)==0
   s=p.state(h,action);states.append(s)
   if action=="check_ack":close(s["position"],expect.get("position_before_fill",0.))
   if action=="check_pending":assert s["order"]["req"]==7, s
  end=states[-1]
  close(end["position"],expect["position"]);close(end["cash"],expect["cash"]);close(end["fees"],-expect["cash"]*.0002)
  if "status" in expect:assert end["order"]["status"]==expect["status"]
  return {"name":name,"passed":True,"final":end,"states":states}
 finally:h.close()
cases=[
 ("increase",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,98.,3.1)],[(30_000_000,"amend",98.,2.),(50_000_000,"check_ack",0,0),(120_000_000,"final",0,0)],{"position":2.,"cash":-196.}),
 ("reduce",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,98.,3.1)],[(30_000_000,"amend",98.,.5),(50_000_000,"check_ack",0,0),(120_000_000,"final",0,0)],{"position":.5,"cash":-49.}),
 ("price_only",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,98.,3.1)],[(30_000_000,"amend",98.,1.),(50_000_000,"check_ack",0,0),(120_000_000,"final",0,0)],{"position":1.,"cash":-98.}),
 ("partial_price_change",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,.5),(200_000_000,p.TRADE_EVENT|p.SELL_EVENT,98.,.7)],[(120_000_000,"partial",0,0),(130_000_000,"amend",98.,.6),(150_000_000,"check_ack",0,0),(220_000_000,"final",0,0)],{"position_before_fill":.4,"position":1.,"cash":-98.4}),
 ("partial_same_price_reduce",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,.5),(200_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,.2)],[(120_000_000,"partial",0,0),(130_000_000,"amend",99.,.2),(150_000_000,"check_ack",0,0),(220_000_000,"final",0,0)],{"position_before_fill":.4,"position":.6,"cash":-59.4}),
 ("partial_same_price_increase",[(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,.5),(200_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,1.3)],[(120_000_000,"partial",0,0),(130_000_000,"amend",99.,1.2),(150_000_000,"check_ack",0,0),(220_000_000,"final",0,0)],{"position_before_fill":.4,"position":1.6,"cash":-158.4}),
 ("fill_during_amend",[(35_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,.5),(100_000_000,p.TRADE_EVENT|p.SELL_EVENT,98.,.7)],[(30_000_000,"amend",98.,.6),(45_000_000,"check_pending",0,0),(50_000_000,"check_ack",0,0),(120_000_000,"final",0,0)],{"position_before_fill":.4,"position":1.,"cash":-98.4}),
 ("full_fill_during_amend",[(35_000_000,p.TRADE_EVENT|p.SELL_EVENT,99.,1.1)],[(30_000_000,"amend",98.,1.),(60_000_000,"final",0,0)],{"position":1.,"cash":-99.,"status":3}),
 ("post_only_cross",[],[(30_000_000,"amend",102.,1.),(60_000_000,"final",0,0)],{"position":0.,"cash":0.,"status":2}),
]
