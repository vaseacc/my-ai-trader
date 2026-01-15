import os, json, time, ccxt, threading, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. CORE CONFIG ---
CONFIG = {
    "SYMBOL": "BTC/USDT",
    "TICK_INTERVAL": 15,
    "STARTING_BALANCE": 10000.0,
    "RISK_PER_TRADE": 0.002,      # 0.2% risk
    "IMBALANCE_THRESHOLD": 2.2,   
    "SLOPE_THRESHOLD": 0.0002,    
    "TIME_LIMIT": 3600,           # 1 Hour
    "MIN_HOLD": 300,              # 5 Mins
    "COOLDOWN_TIME": 600,         
    "MAX_SPREAD": 0.0006,         
    "FEE": 0.0004,                
    "SLIPPAGE_BASE": 0.0003       
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
    "market_regime": "WAITING",
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
    if vol < 0.0015: return "CHOP"
    if vol > 0.006: return "VOLATILE"
    if abs(slope) > CONFIG["SLOPE_THRESHOLD"]: return "TRENDING"
    return "NEUTRAL"

# --- 4. MASTER LOOP ---

def run_cycle():
    try:
        ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
        price = ticker['last']
        STATE["last_price"] = price 

        ob = exchange.fetch_order_book(CONFIG["SYMBOL"], 20)
        spread = (ob['asks'][0][0] - ob['bids'][0][0]) / ob['bids'][0][0]

        # Ingestion
        bids_vol = sum([x[1] for x in ob['bids'][:5]]) 
        asks_vol = sum([x[1] for x in ob['asks'][:5]]) 
        raw_imb = min(max(bids_vol / asks_vol if asks_vol > 0 else 1.0, 0.2), 5.0)
        
        STATE["imbalance_history"].append(raw_imb)
        STATE["price_history"].append(price)

        if len(STATE["price_history"]) < 30: 
            STATE["market_regime"] = "WARMING_UP"
            return
            
        if len(STATE["price_history"]) > 50: STATE["price_history"].pop(0)
        if len(STATE["imbalance_history"]) > 10: STATE["imbalance_history"].pop(0)

        # Metrics
        vol = max((max(STATE["price_history"][-15:]) - min(STATE["price_history"][-15:])) / price, 0.001)
        ma = sum(STATE["price_history"][-20:]) / 20
        ma_prev = sum(STATE["price_history"][-21:-1]) / 20
        slope = (ma - ma_prev) / ma_prev if ma_prev > 0 else 0
        
        STATE["market_regime"] = get_market_regime(vol, slope)

        # --- ENTRY ---
        if not STATE["is_holding"] and time.time() > STATE["cooldown_until"]:
            if STATE["market_regime"] not in ["TRENDING", "VOLATILE"]:
                return

            imb_smooth = sum(STATE["imbalance_history"][-5:]) / 5
            
            if (imb_smooth >= CONFIG["IMBALANCE_THRESHOLD"] or imb_smooth <= (1/CONFIG["IMBALANCE_THRESHOLD"])) and spread < CONFIG["MAX_SPREAD"]:
                
                direction = "LONG" if (imb_smooth > 1 and slope > 0) else "SHORT"
                
                # Structural check
                if (direction == "LONG" and slope < 0) or (direction == "SHORT" and slope > 0):
                    return

                stop = max(vol * 3.0, 0.005)
                size = min((STATE["balance"] * CONFIG["RISK_PER_TRADE"]) / stop, STATE["balance"] * 0.95)
                
                if size > 20:
                    STATE.update({
                        "is_holding": True, "entry_price": price, "entry_time": time.time(), 
                        "direction": direction, "current_size_usd": size, "stop_pct": stop, "best_pnl": 0
                    })
                    add_log(f"🚀 {direction} @ {price} | Target: +{round(stop*200,2)}%")

        # --- EXIT ---
        elif STATE["is_holding"]:
            pnl_pct = (price - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": pnl_pct = -pnl_pct
            
            STATE["best_pnl"] = max(STATE["best_pnl"], pnl_pct)
            time_elapsed = time.time() - STATE["entry_time"]
            stop = STATE["stop_pct"]
            
            exit_reason = None
            
            # 1. HARD STOP
            if pnl_pct <= -stop: 
                exit_reason = "STOP LOSS"
            
            # 2. IMPROVED TRAIL (The "Runner" Fix)
            # We wait for 1% profit OR 2x our risk before we even start trailing.
            trail_activation = max(stop * 2.0, 0.01) 
            if STATE["best_pnl"] > trail_activation:
                # We give it 1.5x Volatility buffer to avoid being shaken out by wiggles
                if (STATE["best_pnl"] - pnl_pct) > (vol * 1.5):
                    exit_reason = "TRAIL"
            
            # 3. TIME/TREND LIMITS
            elif time_elapsed > CONFIG["TIME_LIMIT"]: 
                exit_reason = "TIME LIMIT"
            
            if (STATE["direction"] == "LONG" and slope < -0.0004) or (STATE["direction"] == "SHORT" and slope > 0.0004):
                 if time_elapsed > CONFIG["MIN_HOLD"]: exit_reason = "TREND FLIP"

            # SAFETY GATE
            if time_elapsed < CONFIG["MIN_HOLD"]: exit_reason = None

            if exit_reason:
                final_pnl = pnl_pct - ((CONFIG["FEE"] * 2) + CONFIG["SLIPPAGE_BASE"])
                trade_usd = STATE["current_size_usd"] * final_pnl
                STATE["balance"] += trade_usd
                STATE["peak_balance"] = max(STATE["peak_balance"], STATE["balance"])
                STATE["max_drawdown"] = max(STATE["max_drawdown"], (STATE["peak_balance"] - STATE["balance"]) / STATE["peak_balance"] * 100)
                
                if final_pnl > 0: STATE["win_count"] += 1
                else: STATE["loss_count"] += 1
                
                add_log(f"💰 {exit_reason}: {round(final_pnl*100, 2)}% (${round(trade_usd, 2)})")
                STATE["is_holding"] = False
                STATE["cooldown_until"] = time.time() + CONFIG["COOLDOWN_TIME"]

    except Exception as e: print(f"Cycle Error: {e}")

# --- 5. DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        win_rate = round((STATE["win_count"] / max(1, (STATE["win_count"] + STATE["loss_count"]))) * 100, 1)
        total_pnl = round(STATE["balance"] - CONFIG["STARTING_BALANCE"], 2)
        
        html = f"""
        <html><head><title>GCR_V17</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:600px; margin:auto; }}
            .metric-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:20px; }}
            .metric {{ background:#111; padding:10px; border:1px solid #222; text-align:center; }}
            .val {{ display:block; font-size:1.2em; font-weight:bold; color:#fff; }}
            .log {{ background:#000; padding:10px; height:200px; overflow-y:scroll; font-size:0.7em; color:#888; border:1px solid #222; white-space:pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444; margin-bottom:10px;">
                    <span style="color:#0f0;">REGIME: {STATE['market_regime']}</span>
                    <span>PRICE: {STATE['last_price']}</span>
                </div>
                <div class="metric-grid">
                    <div class="metric"><span style="font-size:0.6em; color:#444;">VIRTUAL BALANCE</span><span class="val">${round(STATE['balance'],2)}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">TOTAL PNL</span><span class="val" style="color:{'#0f0' if total_pnl >=0 else '#f00'}">${total_pnl}</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">WIN RATE</span><span class="val">{win_rate}%</span></div>
                    <div class="metric"><span style="font-size:0.6em; color:#444;">MAX DRAWDOWN</span><span class="val" style="color:#f00;">{round(STATE['max_drawdown'],2)}%</span></div>
                </div>
                <div class="log">{"\\n".join(STATE['logs'])}</div>
                <div style="text-align:center; font-size:0.6em; color:#222; margin-top:10px;">SYSTEM STATUS: RUNNER_MODE_ACTIVE</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(CONFIG["TICK_INTERVAL"])
