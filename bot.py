import os, json, time, ccxt, threading, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. CORE CONFIG ---
CONFIG = {
    "SYMBOL": "BTC/USDT",
    "TICK_INTERVAL": 15,
    "STARTING_BALANCE": 10000.0,
    "RISK_PER_TRADE": 0.005,
    "IMBALANCE_THRESHOLD": 1.8,
    "MA_WINDOW": 20,
    "MOMENTUM_WINDOW": 4,
    "TIME_LIMIT": 900,
    "COOLDOWN_TIME": 60,
    "MAX_SPREAD": 0.0008,
    "MIN_VOLATILITY": 0.0012,
    "SLIPPAGE": 0.0003          # FIX 5: 0.03% realistic fill penalty
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
    "best_pnl": 0,             # FIX 4: Tracks peak profit for trailing
    "price_history": [],
    "imbalance_history": [],
    "last_price": 0,
    "cooldown_until": 0,
    "logs": [],
    "win_count": 0,
    "loss_count": 0
}

exchange = ccxt.mexc()

# --- 3. REFINED QUANT HELPERS ---

def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts}] {msg}")
    STATE["logs"] = STATE["logs"][:30]

def get_recent_volatility():
    prices = STATE["price_history"][-10:]
    if len(prices) < 5: return 0.0012
    return max((max(prices) - min(prices)) / min(prices), 0.001)

def get_smoothed_imbalance():
    """FIX 2: Averages last 3 ticks to filter spoofing"""
    if len(STATE["imbalance_history"]) < 3:
        return STATE["imbalance_history"][-1] if STATE["imbalance_history"] else 1.0
    return sum(STATE["imbalance_history"][-3:]) / 3

def get_ma_slope():
    """FIX 3: Normalized slope (percentage change of MA)"""
    if len(STATE["price_history"]) < CONFIG["MA_WINDOW"] + 1: return 0
    ma_now = sum(STATE["price_history"][-CONFIG["MA_WINDOW"]:]) / CONFIG["MA_WINDOW"]
    ma_prev = sum(STATE["price_history"][-(CONFIG["MA_WINDOW"]+1):-1]) / CONFIG["MA_WINDOW"]
    return (ma_now - ma_prev) / ma_prev

def calculate_position_size(stop_loss_pct):
    risk_amount = STATE["balance"] * CONFIG["RISK_PER_TRADE"]
    return round(min(risk_amount / stop_loss_pct, STATE["balance"] * 2), 2)

# --- 4. MASTER LOOP ---

def run_cycle():
    try:
        ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
        current_price = ticker['last']
        STATE["last_price"] = current_price 

        ob = exchange.fetch_order_book(CONFIG["SYMBOL"], 20)
        best_bid, best_ask = ob['bids'][0][0], ob['asks'][0][0]
        spread = (best_ask - best_bid) / best_bid

        # --- FIX 2: SMOOTHED IMBALANCE ---
        bids_vol = sum([x[1] for x in ob['bids'][:5]]) 
        asks_vol = sum([x[1] for x in ob['asks'][:5]]) 
        raw_imb = min(max(bids_vol / asks_vol if asks_vol > 0 else 1.0, 0.2), 5.0)
        STATE["imbalance_history"].append(raw_imb)
        if len(STATE["imbalance_history"]) > 10: STATE["imbalance_history"].pop(0)
        
        imbalance = get_smoothed_imbalance()

        STATE["price_history"].append(current_price)
        if len(STATE["price_history"]) > 30: STATE["price_history"].pop(0)

        vol = get_recent_volatility()
        slope = get_ma_slope()

        # ENTRY
        if not STATE["is_holding"] and time.time() > STATE["cooldown_until"]:
            if spread < CONFIG["MAX_SPREAD"] and vol > CONFIG["MIN_VOLATILITY"]:
                
                # LONG: Smoothed Whale + Normalized Slope + Momentum
                if imbalance >= CONFIG["IMBALANCE_THRESHOLD"] and slope > 0:
                    stop = max(vol * 3.0, 0.004)
                    size = calculate_position_size(stop)
                    STATE.update({"is_holding": True, "entry_price": current_price, "entry_time": time.time(), "direction": "LONG", "current_size_usd": size, "best_pnl": 0})
                    add_log(f"🚀 BUY {size} USD | Slope: {round(slope*100,4)}%")

                # SHORT: Smoothed Whale + Normalized Slope + Momentum
                elif imbalance <= (1 / CONFIG["IMBALANCE_THRESHOLD"]) and slope < 0:
                    stop = max(vol * 3.0, 0.004)
                    size = calculate_position_size(stop)
                    STATE.update({"is_holding": True, "entry_price": current_price, "entry_time": time.time(), "direction": "SHORT", "current_size_usd": size, "best_pnl": 0})
                    add_log(f"🔻 SELL {size} USD | Slope: {round(slope*100,4)}%")

        # EXIT
        elif STATE["is_holding"]:
            pnl_pct = (current_price - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": pnl_pct = -pnl_pct
            
            # --- FIX 4: TRAILING PROFIT LOGIC ---
            STATE["best_pnl"] = max(STATE["best_pnl"], pnl_pct)
            
            stop, take = max(vol * 3.0, 0.004), max(vol * 4.0, 0.006)
            time_elapsed = time.time() - STATE["entry_time"]
            
            exit_reason = None
            if pnl_pct <= -stop: 
                exit_reason = "STOP LOSS"
            elif pnl_pct >= take:
                # Trail by 1 volatility unit from the peak
                if (STATE["best_pnl"] - pnl_pct) > vol:
                    exit_reason = "TRAIL EXIT"
            elif time_elapsed > CONFIG["TIME_LIMIT"] and abs(pnl_pct) < vol * 0.5:
                exit_reason = "STALL"

            if exit_reason:
                # --- FIX 5: APPLY SLIPPAGE ---
                final_pnl = pnl_pct - CONFIG["SLIPPAGE"]
                
                res_usd = STATE["current_size_usd"] * final_pnl
                STATE["balance"] += res_usd
                STATE["peak_balance"] = max(STATE["peak_balance"], STATE["balance"])
                STATE["max_drawdown"] = max(STATE["max_drawdown"], (STATE["peak_balance"] - STATE["balance"]) / STATE["peak_balance"] * 100)
                
                if final_pnl > 0: STATE["win_count"] += 1
                else: STATE["loss_count"] += 1
                
                add_log(f"💰 {exit_reason}: {round(final_pnl*100, 2)}% (${round(res_usd, 2)})")
                STATE["is_holding"] = False
                STATE["cooldown_until"] = time.time() + CONFIG["COOLDOWN_TIME"]

    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 5. DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        win_rate = round((STATE["win_count"] / max(1, (STATE["win_count"] + STATE["loss_count"]))) * 100, 1)
        total_pnl = round(STATE["balance"] - CONFIG["STARTING_BALANCE"], 2)
        
        html = f"""
        <html><head><title>GCR_V11</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:600px; margin:auto; }}
            .metric-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:20px; }}
            .metric {{ background:#111; padding:10px; border:1px solid #222; text-align:center; }}
            .val {{ display:block; font-size:1.2em; font-weight:bold; color:#fff; }}
            .log {{ background:#000; padding:10px; height:200px; overflow-y:scroll; font-size:0.7em; color:#888; border:1px solid #222; white-space:pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <h3 style="text-align:center; color:#0f0; margin-top:0;">GCR_V11_REALIST</h3>
                <div class="metric-grid">
                    <div class="metric"><span style="font-size:0.6em; color:#444;">VIRTUAL BALANCE</span><span class="val">${round(STATE['balance'],2)}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">WIN RATE</span><span class="val">{win_rate}%</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">TOTAL PNL (W/ SLIPPAGE)</span><span class="val" style="color:{'#0f0' if total_pnl >=0 else '#f00'}">${total_pnl}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">MAX DRAWDOWN</span><span class="val" style="color:#f00;">{round(STATE['max_drawdown'],2)}%</span></div>
                </div>
                <div class="log">{"\\n".join(STATE['logs'])}</div>
                <div style="text-align:center; font-size:0.6em; color:#222; margin-top:10px;">SLIPPAGE: 0.03% APPLIED PER TRADE</div>
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
