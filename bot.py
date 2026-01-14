import os, json, time, ccxt, threading, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. CORE CONFIG ---
CONFIG = {
    "SYMBOL": "BTC/USDT",
    "TICK_INTERVAL": 15,         
    "IMBALANCE_THRESHOLD": 1.8,    
    "HISTORY_WINDOW": 5,          
    "TIME_LIMIT": 300,            
    "COOLDOWN_TIME": 60,
    "MAX_SPREAD": 0.0008,         
    "MIN_VOLATILITY": 0.002       
}

# --- 2. PERSISTENT STATE ---
STATE = {
    "is_holding": False,
    "entry_price": 0,
    "entry_time": 0,
    "direction": None,
    "imbalance_history": [],
    "price_history": [],
    "last_price": 0,           
    "cooldown_until": 0,
    "logs": [],               # Important Events (Trades)
    "activity_stream": [],    # Deep Debug Logs (Every Cycle)
    "pnl_history": []             
}

exchange = ccxt.mexc()

# --- 3. UTILS ---

def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts}] {msg}")
    STATE["logs"] = STATE["logs"][:30]

def add_debug(msg):
    """Logs every single calculation for debugging"""
    ts = datetime.now().strftime("%H:%M:%S")
    STATE["activity_stream"].insert(0, f"[{ts}] {msg}")
    STATE["activity_stream"] = STATE["activity_stream"][:50]

def get_recent_volatility():
    prices = STATE["price_history"][-10:]
    if len(prices) < 5: return 0.002 
    return max((max(prices) - min(prices)) / min(prices), 0.001)

def persistent_imbalance(direction):
    window = STATE["imbalance_history"][-CONFIG["HISTORY_WINDOW"]:]
    if len(window) < CONFIG["HISTORY_WINDOW"]: return False
    if direction == "LONG":
        return sum(x >= CONFIG["IMBALANCE_THRESHOLD"] for x in window) >= CONFIG["HISTORY_WINDOW"] - 1
    if direction == "SHORT":
        return sum(x <= (1 / CONFIG["IMBALANCE_THRESHOLD"]) for x in window) >= CONFIG["HISTORY_WINDOW"] - 1
    return False

def price_confirms(direction):
    if len(STATE["price_history"]) < CONFIG["HISTORY_WINDOW"]: return False
    move = (STATE["price_history"][-1] - STATE["price_history"][0]) / STATE["price_history"][0]
    vol_buffer = get_recent_volatility() * 0.3
    if direction == "LONG": return move > vol_buffer
    if direction == "SHORT": return move < -vol_buffer
    return False

# --- 4. MASTER LOOP ---

def run_cycle():
    try:
        ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
        current_price = ticker['last']
        STATE["last_price"] = current_price 

        ob = exchange.fetch_order_book(CONFIG["SYMBOL"], 20)
        best_bid, best_ask = ob['bids'][0][0], ob['asks'][0][0]
        spread = (best_ask - best_bid) / best_bid

        bids = sum([x[1] for x in ob['bids'][:5]]) 
        asks = sum([x[1] for x in ob['asks'][:5]]) 
        imbalance = bids / asks if asks > 0 else 1.0

        STATE["price_history"].append(current_price)
        STATE["imbalance_history"].append(imbalance)
        if len(STATE["price_history"]) > 20: STATE["price_history"].pop(0)
        if len(STATE["imbalance_history"]) > 10: STATE["imbalance_history"].pop(0)

        # --- DEBUG LOGGING ---
        vol = get_recent_volatility()
        status = "SCANNING"
        if spread > CONFIG["MAX_SPREAD"]: status = "WIDE_SPREAD"
        elif vol < CONFIG["MIN_VOLATILITY"]: status = "LOW_VOL"
        elif time.time() < STATE["cooldown_until"]: status = "COOLDOWN"
        elif STATE["is_holding"]: status = "HOLDING"

        add_debug(f"P:{current_price} | I:{round(imbalance,2)}x | S:{round(spread*100,3)}% | V:{round(vol*100,2)}% | {status}")

        # LOGIC GATES
        if spread > CONFIG["MAX_SPREAD"] or vol < CONFIG["MIN_VOLATILITY"] or time.time() < STATE["cooldown_until"]:
            return

        if not STATE["is_holding"]:
            if persistent_imbalance("LONG") and price_confirms("LONG"):
                STATE.update({"is_holding": True, "entry_price": current_price, "entry_time": time.time(), "direction": "LONG"})
                add_log(f"🚀 ENTER LONG: {current_price}")
            elif persistent_imbalance("SHORT") and price_confirms("SHORT"):
                STATE.update({"is_holding": True, "entry_price": current_price, "entry_time": time.time(), "direction": "SHORT"})
                add_log(f"🔻 ENTER SHORT: {current_price}")

        elif STATE["is_holding"]:
            pnl = (current_price - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": pnl = -pnl
            
            stop_loss, take_profit = vol * 1.5, vol * 3.0
            time_elapsed = time.time() - STATE["entry_time"]
            
            exit_reason = None
            if pnl <= -stop_loss: exit_reason = "STOP LOSS"
            elif pnl >= take_profit: exit_reason = "TAKE PROFIT"
            elif time_elapsed > CONFIG["TIME_LIMIT"]: exit_reason = "TIME EXIT"

            if exit_reason:
                p_pct = round(pnl * 100, 2)
                add_log(f"💰 {exit_reason}: {p_pct}% at {current_price}")
                STATE["pnl_history"].append(p_pct); STATE["is_holding"] = False
                STATE["cooldown_until"] = time.time() + CONFIG["COOLDOWN_TIME"]

    except Exception as e: print(f"Error: {e}")

# --- 5. DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        current_pnl = 0
        if STATE["is_holding"]:
            current_pnl = round(((STATE["last_price"] - STATE["entry_price"]) / STATE["entry_price"]) * (1 if STATE["direction"]=="LONG" else -1) * 100, 2)

        html = f"""
        <html><head><title>GCR_V6</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:15px; max-width:600px; margin:auto; box-shadow:0 0 15px #0f0; }}
            .pnl {{ font-size:3em; text-align:center; color:{'#0f0' if current_pnl >= 0 else '#f00'}; }}
            .box {{ background:#111; padding:10px; height:150px; overflow-y:scroll; font-size:0.7em; color:#888; white-space:pre-wrap; margin-top:10px; border:1px solid #222; }}
            .debug {{ color:#555; height:200px; }}
            .btn {{ width:100%; padding:10px; background:#0f0; border:none; font-weight:bold; cursor:pointer; margin-top:10px; }}
            .label {{ font-size:0.6em; color:#444; margin-top:10px; text-transform:uppercase; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em;">
                    <span>BTC: {STATE['last_price']}</span>
                    <span>TRADES: {len(STATE['pnl_history'])}</span>
                </div>
                <div class="pnl">{current_pnl}%</div>
                
                <button class="btn" onclick="copyFullLog()">📋 COPY FULL SYSTEM LOG</button>

                <div class="label">Trade Events</div>
                <div class="box" id="tradeBox">{"\\n".join(STATE['logs'])}</div>

                <div class="label">Raw Activity Stream</div>
                <div class="box debug" id="debugBox">{"\\n".join(STATE['activity_stream'])}</div>
            </div>
            <script>
                function copyFullLog() {{
                    const logs = "--- TRADE EVENTS ---\\n" + document.getElementById('tradeBox').innerText + "\\n\\n--- RAW ACTIVITY ---\\n" + document.getElementById('debugBox').innerText;
                    navigator.clipboard.writeText(logs);
                    alert('Log copied!');
                }}
                setTimeout(()=>location.reload(), 15000);
            </script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(CONFIG["TICK_INTERVAL"])
