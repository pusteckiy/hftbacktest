use std::collections::{HashMap, hash_map::Entry};

use crate::{
    backtest::{
        BacktestError,
        assettype::AssetType,
        models::{FeeModel, LatencyModel},
        order::LocalToExch,
        proc::{LocalProcessor, Processor},
        state::State,
    },
    depth::{L2MarketDepth, MarketDepth},
    types::{
        Event, LOCAL_ASK_DEPTH_CLEAR_EVENT, LOCAL_ASK_DEPTH_EVENT, LOCAL_ASK_DEPTH_SNAPSHOT_EVENT,
        LOCAL_BID_DEPTH_CLEAR_EVENT, LOCAL_BID_DEPTH_EVENT, LOCAL_BID_DEPTH_SNAPSHOT_EVENT,
        LOCAL_DEPTH_CLEAR_EVENT, LOCAL_EVENT, LOCAL_TRADE_EVENT, OrdType, Order, OrderId, Side,
        StateValues, Status, TimeInForce,
    },
};

/// The local model.
pub struct Local<AT, LM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    MD: MarketDepth,
    FM: FeeModel,
{
    orders: HashMap<OrderId, Order>,
    reported_exec_qty: HashMap<OrderId, f64>,
    total_qty_orders: HashMap<OrderId, f64>,
    pending_total_qty: HashMap<OrderId, (i64, f64, i64, f64)>,
    order_l2e: LocalToExch<LM>,
    depth: MD,
    state: State<AT, FM>,
    trades: Vec<Event>,
    execution_reports: Option<Vec<Event>>,
    last_feed_latency: Option<(i64, i64)>,
    last_order_latency: Option<(i64, i64, i64)>,
}

impl<AT, LM, MD, FM> Local<AT, LM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    MD: MarketDepth,
    FM: FeeModel,
{
    /// Constructs an instance of `Local`.
    pub fn new(
        depth: MD,
        state: State<AT, FM>,
        last_trades_cap: usize,
        order_l2e: LocalToExch<LM>,
    ) -> Self {
        Self {
            orders: Default::default(),
            reported_exec_qty: Default::default(),
            total_qty_orders: Default::default(),
            pending_total_qty: Default::default(),
            order_l2e,
            depth,
            state,
            trades: Vec::with_capacity(last_trades_cap),
            execution_reports: None,
            last_feed_latency: None,
            last_order_latency: None,
        }
    }

    pub fn process_recv_order_<const USE_HANDLER: bool, Handler>(
        &mut self,
        timestamp: i64,
        wait_resp_order_id: Option<OrderId>,
        mut handler: Handler,
    ) -> Result<bool, BacktestError>
    where
        Handler: FnMut(&Order),
    {
        let mut wait_resp_order_received = false;
        while let Some((order, total_qty_ack)) = self.order_l2e.receive_with_total_qty(timestamp) {
            // Updates the order latency only if it has a valid exchange timestamp. When the
            // order is rejected before it reaches the matching engine, it has no exchange
            // timestamp. This situation occurs in crypto exchanges.
            if order.exch_timestamp > 0 {
                self.last_order_latency =
                    Some((order.local_timestamp, order.exch_timestamp, timestamp));
            }

            if let Some(wait_resp_order_id) = wait_resp_order_id
                && order.order_id == wait_resp_order_id
            {
                wait_resp_order_received = true;
            }

            // Processes receiving order response (full and partial fills).
            if (order.status == Status::Filled || order.status == Status::PartiallyFilled)
                && order.exec_qty > 0.0
                && order.req != Status::Rejected
            {
                self.state.apply_fill(&order);
                *self.reported_exec_qty.entry(order.order_id).or_default() += order.exec_qty;
                if let Some(reports) = self.execution_reports.as_mut() {
                    reports.push(Event {
                        ev: if order.side == Side::Buy {
                            crate::types::BUY_EVENT
                        } else {
                            crate::types::SELL_EVENT
                        },
                        exch_ts: order.exch_timestamp,
                        local_ts: timestamp,
                        px: order.exec_price(),
                        qty: order.exec_qty,
                        order_id: order.order_id,
                        ival: order.time_in_force as i64,
                        fval: 0.0,
                    });
                }
            }
            // Applies the received order response to the local orders.
            let response_order_id = order.order_id;
            let matching_total_ack = total_qty_ack
                && self
                    .pending_total_qty
                    .get(&order.order_id)
                    .is_some_and(|(request_ts, _, _, _)| *request_ts == order.local_timestamp);
            let mut terminal = false;
            match self.orders.entry(order.order_id) {
                Entry::Occupied(mut entry) => {
                    let local_order = entry.get_mut();
                    let already_terminal = matches!(
                        local_order.status,
                        Status::Filled | Status::Canceled | Status::Expired
                    );
                    if matching_total_ack && already_terminal {
                        // A delayed total ACK cannot reopen a locally terminal order.
                        self.pending_total_qty.remove(&order.order_id);
                        local_order.req = Status::None;
                    } else if order.req == Status::Rejected {
                        if matching_total_ack {
                            let (_, _, original_price_tick, original_qty) =
                                self.pending_total_qty.remove(&order.order_id).unwrap();
                            local_order.price_tick = original_price_tick;
                            local_order.qty = original_qty;
                        }
                        if order.local_timestamp == local_order.local_timestamp {
                            if local_order.req == Status::New {
                                local_order.req = Status::None;
                                local_order.status = Status::Expired;
                            } else {
                                local_order.req = Status::None;
                            }
                        }
                    } else {
                        let pending_req_timestamp = local_order.local_timestamp;
                        let pending_modify = local_order.req == Status::Replaced
                            && order.local_timestamp < local_order.local_timestamp;
                        local_order.update(&order);
                        if matching_total_ack {
                            let (_, requested_total, _, _) =
                                self.pending_total_qty.remove(&order.order_id).unwrap();
                            // The exchange snapshot freezes executions through exchange arrival.
                            // Local fill reports may additionally include later executions that
                            // arrived before this ACK, so take the larger known watermark.
                            let exchange_cum = (order.qty - order.leaves_qty).max(0.0);
                            let reported_cum = self
                                .reported_exec_qty
                                .get(&order.order_id)
                                .copied()
                                .unwrap_or(0.0);
                            let cumulative = exchange_cum.max(reported_cum);
                            let leaves = (requested_total - cumulative).max(0.0);
                            let leaves_lots = (leaves / self.depth.lot_size()).round();
                            local_order.qty = requested_total.max(cumulative);
                            local_order.leaves_qty = if leaves_lots > 0.0 {
                                leaves_lots * self.depth.lot_size()
                            } else {
                                0.0
                            };
                            local_order.exec_qty = 0.0;
                            local_order.status = if local_order.leaves_qty == 0.0 {
                                if cumulative > 0.0 {
                                    Status::Filled
                                } else {
                                    Status::Canceled
                                }
                            } else if cumulative > 0.0 {
                                Status::PartiallyFilled
                            } else {
                                Status::New
                            };
                            self.total_qty_orders
                                .insert(order.order_id, requested_total);
                        }
                        // A fill for the old quote is not the outstanding amend's ACK.
                        if pending_modify {
                            local_order.req = Status::Replaced;
                            local_order.local_timestamp = pending_req_timestamp;
                        }
                    }
                    terminal = matches!(
                        local_order.status,
                        Status::Filled | Status::Canceled | Status::Expired
                    );
                    if USE_HANDLER {
                        handler(&order);
                    }
                }
                Entry::Vacant(entry) => {
                    if matching_total_ack {
                        self.pending_total_qty.remove(&order.order_id);
                        self.reported_exec_qty.remove(&order.order_id);
                        self.total_qty_orders.remove(&order.order_id);
                    }
                    // A tagged total-quantity ACK belongs to an existing local order. If the
                    // slot was cleared after a terminal fill, never recreate it from this ACK.
                    if !total_qty_ack && order.req != Status::Rejected {
                        let order_ = entry.insert(order);
                        terminal = matches!(
                            order_.status,
                            Status::Filled | Status::Canceled | Status::Expired
                        );
                        if USE_HANDLER {
                            handler(order_);
                        }
                    }
                }
            }
            if terminal && !self.pending_total_qty.contains_key(&response_order_id) {
                self.reported_exec_qty.remove(&response_order_id);
                self.total_qty_orders.remove(&response_order_id);
            }
        }
        Ok(wait_resp_order_received)
    }
}

impl<AT, LM, MD, FM> LocalProcessor<MD> for Local<AT, LM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    MD: MarketDepth + L2MarketDepth,
    FM: FeeModel,
{
    fn submit_order(
        &mut self,
        order_id: OrderId,
        side: Side,
        price: f64,
        qty: f64,
        order_type: OrdType,
        time_in_force: TimeInForce,
        current_timestamp: i64,
    ) -> Result<(), BacktestError> {
        if self.orders.contains_key(&order_id) {
            return Err(BacktestError::OrderIdExist);
        }

        let price_tick = (price / self.depth.tick_size()).round() as i64;
        let mut order = Order::new(
            order_id,
            price_tick,
            self.depth.tick_size(),
            qty,
            side,
            order_type,
            time_in_force,
        );
        order.req = Status::New;
        order.local_timestamp = current_timestamp;
        self.reported_exec_qty.insert(order.order_id, 0.0);
        self.orders.insert(order.order_id, order.clone());

        self.order_l2e.request(order, |order| {
            order.req = Status::Rejected;
        });

        Ok(())
    }

    fn modify(
        &mut self,
        order_id: OrderId,
        price: f64,
        qty: f64,
        current_timestamp: i64,
    ) -> Result<(), BacktestError> {
        if self.total_qty_orders.contains_key(&order_id) {
            return Err(BacktestError::InvalidOrderRequest);
        }
        let order = self
            .orders
            .get_mut(&order_id)
            .ok_or(BacktestError::OrderNotFound)?;

        if order.req != Status::None {
            return Err(BacktestError::OrderRequestInProcess);
        }

        let orig_price_tick = order.price_tick;
        let orig_qty = order.qty;

        let price_tick = (price / self.depth.tick_size()).round() as i64;
        order.price_tick = price_tick;
        order.qty = qty;

        order.req = Status::Replaced;
        order.local_timestamp = current_timestamp;

        self.order_l2e.request(order.clone(), |order| {
            order.req = Status::Rejected;
            order.price_tick = orig_price_tick;
            order.qty = orig_qty;
        });

        Ok(())
    }

    fn modify_total(
        &mut self,
        order_id: OrderId,
        price: f64,
        total_qty: f64,
        current_timestamp: i64,
    ) -> Result<(), BacktestError> {
        if !total_qty.is_finite() || total_qty < 0.0 {
            return Err(BacktestError::InvalidOrderRequest);
        }

        let order = self
            .orders
            .get_mut(&order_id)
            .ok_or(BacktestError::OrderNotFound)?;

        if order.req != Status::None {
            return Err(BacktestError::OrderRequestInProcess);
        }
        if order.status != Status::New && order.status != Status::PartiallyFilled {
            return Err(BacktestError::InvalidOrderStatus);
        }

        let orig_price_tick = order.price_tick;
        let orig_qty = order.qty;

        order.price_tick = (price / self.depth.tick_size()).round() as i64;
        order.qty = total_qty;
        order.req = Status::Replaced;
        order.local_timestamp = current_timestamp;
        self.pending_total_qty.insert(
            order_id,
            (current_timestamp, total_qty, orig_price_tick, orig_qty),
        );

        self.order_l2e.request_total_qty(order.clone(), |order| {
            order.req = Status::Rejected;
            order.price_tick = orig_price_tick;
            order.qty = orig_qty;
        });

        Ok(())
    }

    fn cancel(&mut self, order_id: OrderId, current_timestamp: i64) -> Result<(), BacktestError> {
        let order = self
            .orders
            .get_mut(&order_id)
            .ok_or(BacktestError::OrderNotFound)?;

        if order.req != Status::None {
            return Err(BacktestError::OrderRequestInProcess);
        }

        order.req = Status::Canceled;
        order.local_timestamp = current_timestamp;

        self.order_l2e.request(order.clone(), |order| {
            order.req = Status::Rejected;
        });

        Ok(())
    }

    fn clear_inactive_orders(&mut self) {
        self.orders.retain(|_, order| {
            order.status != Status::Expired
                && order.status != Status::Filled
                && order.status != Status::Canceled
        });
        let orders = &self.orders;
        let pending_total_qty = &self.pending_total_qty;
        self.reported_exec_qty.retain(|order_id, _| {
            orders.contains_key(order_id) || pending_total_qty.contains_key(order_id)
        });
        self.total_qty_orders.retain(|order_id, _| {
            orders.contains_key(order_id) || pending_total_qty.contains_key(order_id)
        });
    }

    fn position(&self) -> f64 {
        self.state.values().position
    }

    fn state_values(&self) -> &StateValues {
        self.state.values()
    }

    fn depth(&self) -> &MD {
        &self.depth
    }

    fn orders(&self) -> &HashMap<u64, Order> {
        &self.orders
    }

    fn execution_reports(&self) -> &[Event] {
        self.execution_reports.as_deref().unwrap_or(&[])
    }

    fn clear_execution_reports(&mut self) {
        self.execution_reports.get_or_insert_with(Vec::new).clear();
    }

    fn last_trades(&self) -> &[Event] {
        self.trades.as_slice()
    }

    fn clear_last_trades(&mut self) {
        self.trades.clear();
    }

    fn feed_latency(&self) -> Option<(i64, i64)> {
        self.last_feed_latency
    }

    fn order_latency(&self) -> Option<(i64, i64, i64)> {
        self.last_order_latency
    }
}

impl<AT, LM, MD, FM> Processor for Local<AT, LM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    MD: MarketDepth + L2MarketDepth,
    FM: FeeModel,
{
    fn event_seen_timestamp(&self, event: &Event) -> Option<i64> {
        event.is(LOCAL_EVENT).then_some(event.local_ts)
    }

    fn process(&mut self, ev: &Event) -> Result<(), BacktestError> {
        // Processes a depth event
        if ev.is(LOCAL_BID_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::Buy, ev.px);
        } else if ev.is(LOCAL_ASK_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::Sell, ev.px);
        } else if ev.is(LOCAL_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::None, 0.0);
        } else if ev.is(LOCAL_BID_DEPTH_EVENT) || ev.is(LOCAL_BID_DEPTH_SNAPSHOT_EVENT) {
            self.depth.update_bid_depth(ev.px, ev.qty, ev.local_ts);
        } else if ev.is(LOCAL_ASK_DEPTH_EVENT) || ev.is(LOCAL_ASK_DEPTH_SNAPSHOT_EVENT) {
            self.depth.update_ask_depth(ev.px, ev.qty, ev.local_ts);
        }
        // Processes a trade event
        else if ev.is(LOCAL_TRADE_EVENT) && self.trades.capacity() > 0 {
            self.trades.push(ev.clone());
        }

        // Stores the current feed latency
        self.last_feed_latency = Some((ev.exch_ts, ev.local_ts));

        Ok(())
    }

    fn process_recv_order(
        &mut self,
        timestamp: i64,
        wait_resp_order_id: Option<OrderId>,
    ) -> Result<bool, BacktestError> {
        self.process_recv_order_::<false, _>(timestamp, wait_resp_order_id, |_| {})
    }

    fn earliest_recv_order_timestamp(&self) -> i64 {
        self.order_l2e
            .earliest_recv_order_timestamp()
            .unwrap_or(i64::MAX)
    }

    fn earliest_send_order_timestamp(&self) -> i64 {
        self.order_l2e
            .earliest_send_order_timestamp()
            .unwrap_or(i64::MAX)
    }
}

#[cfg(test)]
mod amend_total_ack_tests {
    use super::*;
    use crate::{
        backtest::{
            assettype::LinearAsset,
            models::{CommonFees, ConstantLatency, TradingValueFeeModel},
            order::order_bus,
            state::State,
        },
        depth::HashMapMarketDepth,
    };

    #[test]
    fn late_successful_total_ack_keeps_local_terminal_fill_terminal() {
        for clear_before_ack in [false, true] {
            let (mut exchange_to_local, local_to_exchange) = order_bus(ConstantLatency::new(0, 0));
            let mut local = Local::new(
                HashMapMarketDepth::new(1.0, 0.1),
                State::new(
                    LinearAsset::new(1.0),
                    TradingValueFeeModel::new(CommonFees::new(0.0, 0.0)),
                ),
                8,
                local_to_exchange,
            );

            let order_id = 7;
            let request_timestamp = 10;
            let mut pending = Order::new(
                order_id,
                98,
                1.0,
                10.0,
                Side::Buy,
                OrdType::Limit,
                TimeInForce::GTX,
            );
            pending.status = Status::New;
            pending.req = Status::Replaced;
            pending.local_timestamp = request_timestamp;
            local.orders.insert(order_id, pending);
            local
                .pending_total_qty
                .insert(order_id, (request_timestamp, 10.0, 99, 10.0));
            local.total_qty_orders.insert(order_id, 10.0);
            local.reported_exec_qty.insert(order_id, 0.0);

            // A full fill reaches Local before the successful amend response.
            let mut fill = Order::new(
                order_id,
                99,
                1.0,
                10.0,
                Side::Buy,
                OrdType::Limit,
                TimeInForce::GTX,
            );
            fill.status = Status::Filled;
            fill.leaves_qty = 0.0;
            fill.exec_qty = 10.0;
            fill.exec_price_tick = 99;
            fill.exch_timestamp = 15;
            fill.local_timestamp = 5;
            exchange_to_local.respond(fill);
            local
                .process_recv_order_::<false, _>(15, None, |_| {})
                .unwrap();

            assert_eq!(local.orders[&order_id].status, Status::Filled);
            assert_eq!(local.reported_exec_qty[&order_id], 10.0);
            assert_eq!(local.state.values().position, 10.0);
            assert_eq!(local.state.values().num_trades, 1);

            if clear_before_ack {
                local.clear_inactive_orders();
                assert!(!local.orders.contains_key(&order_id));
                assert_eq!(local.reported_exec_qty[&order_id], 10.0);
                assert!(local.pending_total_qty.contains_key(&order_id));
            }

            let mut ack = Order::new(
                order_id,
                98,
                1.0,
                10.0,
                Side::Buy,
                OrdType::Limit,
                TimeInForce::GTX,
            );
            ack.status = Status::New;
            ack.req = Status::Replaced;
            ack.local_timestamp = request_timestamp;
            ack.exch_timestamp = 20;
            exchange_to_local.respond_total_qty(ack);
            assert!(
                local
                    .process_recv_order_::<false, _>(20, Some(order_id), |_| {})
                    .unwrap()
            );

            assert_eq!(local.state.values().position, 10.0);
            assert_eq!(local.state.values().num_trades, 1);
            assert!(!local.pending_total_qty.contains_key(&order_id));
            assert!(!local.reported_exec_qty.contains_key(&order_id));
            assert!(!local.total_qty_orders.contains_key(&order_id));
            if clear_before_ack {
                assert!(!local.orders.contains_key(&order_id));
            } else {
                let order = &local.orders[&order_id];
                assert_eq!(order.status, Status::Filled);
                assert_eq!(order.req, Status::None);
                assert_eq!(order.leaves_qty, 0.0);
            }
        }
    }
}
