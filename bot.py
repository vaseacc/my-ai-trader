import os, json, time, ccxt, threading, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. CORE CONFIG ---
CONFIG = {
    "SYMBOL": "BTC/USDT",
    "TICK_INTERVAL": 15,
    "STARTING_BALANCE": 10000.0,
    "RISK_PER_TRADE": 0.005,      # Risk 0.5% of balance
    "IMBALANCE_THRESHOLD": 1.8,
    "MA_WINDOW": 20,
    "MOMENTUM_WINDOW": 4,
    "TIME_LIMIT": 900,
    "MIN_HOLD": 60,
    "COOLDOWN_TIME": 60,
    "MAX_SPREAD": 0.0008,
    "FEE": 0.0004,                # FIX 1: 0.04% Fee per trade (MEXC standard)
    "SLIPPAGE_BASE": 0.0003       # FIX 1: 0.03% Base Slippage
}

# --- 2. PERSISTENT STATE ---
STATE = {
    "balance": CONFIG["STARTING_BALANCE"],
    "peak_balance": CONFIG["STARTING_BALANCE"],
    "max_drawdown": 0,
    "is_holding": False,
    "entry_price": 0,
    "entry_time": 0,
    "direction": None,
    "current_size_usd": 0,
    "best_pnl": 0,
    "stop_pct": 0,
    "price_history": [],
    "imbalance_history": [],
    "last_price": 0,
    "market_regime": "NEUTRAL",   # FIX 4: Tracking market state
    "cooldown_until": 0,
    "logs": [],
    "win_count": 0,
    "loss_count": 0
}

exchange = ccxt.mexc()

# --- 3. CORE HELPERS ---

def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts}] {msg}")
    STATE["logs"] = STATE["logs"][:30]

def get_market_regime(vol, slope):
    """FIX 4: Detects Trend, Chop, or Volatile regimes"""
    if vol < 0.001: return "CHOP"
    if vol > 0.005: return "VOLATILE"
    if abs(slope) > 0.0001: return "TRENDING"
    return "NEUTRAL"

def get_liquidity_depth(ob, side):
    """FIX 2: Sums volume in top 5 levels (in USDT)"""
    levels = ob['bids'][:5] if side == 'buy' else ob['asks'][:5]
    total_usdt = sum([float(x[0]) * float(x[1]) for x in levels])
    return total_usdt

def get_smoothed_imbalance():
    """FIX 3: 5-tick smoothed order book ratio"""
    if len(STATE["imbalance_history"]) < 5:
        return STATE["imbalance_history"][-1] if STATE["imbalance_history"] else 1.0
    return sum(STATE["imbalance_history"][-5:]) / 5

# --- 4. MASTER LOOP ---

def run_cycle():
    try:
        ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
        current_price = ticker['last']
        STATE["last_price"] = current_price 

        ob = exchange.fetch_order_book(CONFIG["SYMBOL"], 20)
        best_bid, best_ask = ob['bids'][0][0], ob['asks'][0][0]
        spread = (best_ask - best_bid) / best_bid

        # Data Ingestion
        bids_vol = sum([x[1] for x in ob['bids'][:5]]) 
        asks_vol = sum([x[1] for x in ob['asks'][:5]]) 
        raw_imb = min(max(bids_vol / asks_vol if asks_vol > 0 else 1.0, 0.2), 5.0)
        STATE["imbalance_history"].append(raw_imb)
        if len(STATE["imbalance_history"]) > 10: STATE["imbalance_history"].pop(0)
        
        STATE["price_history"].append(current_price)
        if len(STATE["price_history"]) > 30: STATE["price_history"].pop(0)

        # Physics
        imbalance = get_smoothed_imbalance()
        vol = max((max(STATE["price_history"][-10:]) - min(STATE["price_history"][-10:])) / current_price, 0.001)
        
        # Trend Calc
        ma = sum(STATE["price_history"][-20:]) / min(len(STATE["price_history"]), 20)
        ma_prev = sum(STATE["price_history"][-21:-1]) / min(len(STATE["price_history"]), 20)
        slope = (ma - ma_prev) / ma_prev if ma_prev > 0 else 0
        
        # Determine Regime (FIX 4)
        STATE["market_regime"] = get_market_regime(vol, slope)

        # --- ENTRY LOGIC ---
        if not STATE["is_holding"] and time.time() > STATE["cooldown_until"]:
            if spread < CONFIG["MAX_SPREAD"] and STATE["market_regime"] != "CHOP":
                
                # Signal logic
                is_long = imbalance >= CONFIG["IMBALANCE_THRESHOLD"] and slope > 0
                is_short = imbalance <= (1 / CONFIG["IMBALANCE_THRESHOLD"]) and slope < 0

                if is_long or is_short:
                    stop = max(vol * 3.0, 0.004)
                    risk_amt = STATE["balance"] * CONFIG["RISK_PER_TRADE"]
                    raw_size = risk_amt / stop
                    
                    # FIX 2: Check Liquidity for realistic fill
                    side = 'buy' if is_long else 'sell'
                    available_liquidity = get_liquidity_depth(ob, side)
                    
                    # If our size is > 50% of top-book liquidity, we get partial fill/extra slippage
                    fill_efficiency = min(available_liquidity / (raw_size * 2), 1.0)
                    executed_size = raw_size * fill_efficiency
                    
                    if executed_size > 10: # Minimum order size
                        STATE.update({
                            "is_holding": True, "entry_price": current_price, "entry_time": time.time(), 
                            "direction": "LONG" if is_long else "SHORT", 
                            "current_size_usd": executed_size, "stop_pct": stop, "best_pnl": 0
                        })
                        add_log(f"🚀 ENTER {STATE['direction']} | Size: ${round(executed_size, 2)} | Regime: {STATE['market_regime']}")

        # --- EXIT LOGIC ---
        elif STATE["is_holding"]:
            pnl_pct = (current_price - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": pnl_pct = -pnl_pct
            
            STATE["best_pnl"] = max(STATE["best_pnl"], pnl_pct)
            time_elapsed = time.time() - STATE["entry_time"]
            stop = STATE["stop_pct"]
            
            exit_reason = None
            # Thesis Check
            if time_elapsed > CONFIG["MIN_HOLD"]:
                if STATE["direction"] == "LONG" and imbalance < 1.0: exit_reason = "FLOW REV"
                elif STATE["direction"] == "SHORT" and imbalance > 1.0: exit_reason = "FLOW REV"
            
            # Technical Check
            if pnl_pct <= -stop: exit_reason = "STOP LOSS"
            elif STATE["best_pnl"] > stop * 1.5 and (STATE["best_pnl"] - pnl_pct) > vol: exit_reason = "TRAIL"
            elif time_elapsed > CONFIG["TIME_LIMIT"] and abs(pnl_pct) < vol * 0.5: exit_reason = "STALL"

            if exit_reason:
                # FIX 1: Realistic PnL (Entry Fee + Exit Fee + Slippage)
                total_costs = (CONFIG["FEE"] * 2) + CONFIG["SLIPPAGE_BASE"]
                final_pnl_pct = pnl_pct - total_costs
                
                trade_usd = STATE["current_size_usd"] * final_pnl_pct
                STATE["balance"] += trade_usd
                STATE["peak_balance"] = max(STATE["peak_balance"], STATE["balance"])
                STATE["max_drawdown"] = max(STATE["max_drawdown"], (STATE["peak_balance"] - STATE["balance"]) / STATE["peak_balance"] * 100)
                
                if final_pnl_pct > 0: STATE["win_count"] += 1
                else: STATE["loss_count"] += 1
                
                add_log(f"💰 {exit_reason}: {round(final_pnl_pct*100, 2)}% (${round(trade_usd, 2)})")
                STATE["is_holding"] = False
                STATE["cooldown_until"] = time.time() + CONFIG["COOLDOWN_TIME"]

    except Exception as e:
        print(f"Loop Error: {e}")

# --- 5. DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        win_rate = round((STATE["win_count"] / max(1, (STATE["win_count"] + STATE["loss_count"]))) * 100, 1)
        total_pnl = round(STATE["balance"] - CONFIG["STARTING_BALANCE"], 2)
        
        html = f"""
        <html><head><title>GCR_V14</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; max-width:600px; margin:auto; box-shadow:0 0 15px #0f0; }}
            .metric-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:20px; }}
            .metric {{ background:#111; padding:10px; border:1px solid #222; text-align:center; }}
            .val {{ display:block; font-size:1.2em; font-weight:bold; color:#fff; }}
            .log {{ background:#000; padding:10px; height:200px; overflow-y:scroll; font-size:0.7em; color:#888; border:1px solid #222; white-space:pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444; margin-bottom:10px;">
                    <span>REGIME: {STATE['market_regime']}</span>
                    <span>PRICE: {STATE['last_price']}</span>
                </div>
                <div class="metric-grid">
                    <div class="metric"><span style="font-size:0.6em; color:#444;">VIRTUAL BALANCE</span><span class="val">${round(STATE['balance'],2)}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">PNL (NET OF FEES)</span><span class="val" style="color:{'#0f0' if total_pnl >=0 else '#f00'}">${total_pnl}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">WIN RATE</span><span class="val">{win_rate}%</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">MAX DRAWDOWN</span><span class="val" style="color:#f00;">{round(STATE['max_drawdown'],2)}%</span></div>
                </div>
                <div class="log">{"\\n".join(STATE['logs'])}</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(CONFIG["TICK_INTERVAL"])
