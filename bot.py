import os, json, time, ccxt, threading, math
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- 1. CORE STRATEGY CONFIG ---
CONFIG = {
    "SYMBOL": "BTC/USDT",
    "TICK_INTERVAL": 15,      # Check market every 15s
    "IMBALANCE_THRESHOLD": 1.8, # 1.8x more buyers than sellers to go LONG
    "STOP_LOSS": 0.015,       # 1.5% Stop Loss
    "TAKE_PROFIT": 0.03,      # 3.0% Take Profit
}

# --- 2. PERSISTENT STATE ---
STATE = {
    "is_holding": False,
    "entry_price": 0,
    "entry_time": None,
    "direction": None,
    "pnl_history": [],
    "logs": []
}

exchange = ccxt.mexc()

def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    STATE["logs"].insert(0, f"[{ts}] {msg}")
    STATE["logs"] = STATE["logs"][:25]
    print(f"[{ts}] {msg}")

# --- 3. QUANTITATIVE BRAIN ---

def get_order_book_imbalance():
    """Returns the ratio of Buyers vs Sellers (The Whale Sensor)"""
    try:
        ob = exchange.fetch_order_book(CONFIG["SYMBOL"], 20)
        bids = sum([x[1] for x in ob['bids']]) # Total Buy Volume
        asks = sum([x[1] for x in ob['asks']]) # Total Sell Volume
        return round(bids / asks, 2) if asks > 0 else 1.0
    except:
        return 1.0

def run_cycle():
    try:
        # A. Gather Data
        ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
        current_price = ticker['last']
        imbalance = get_order_book_imbalance()

        # B. Trading Logic (Paper Mode)
        if not STATE["is_holding"]:
            # Rule: If buyers are 1.8x stronger than sellers, ENTER LONG
            if imbalance >= CONFIG["IMBALANCE_THRESHOLD"]:
                STATE.update({
                    "is_holding": True,
                    "entry_price": current_price,
                    "entry_time": datetime.now().strftime("%H:%M"),
                    "direction": "LONG"
                })
                add_log(f"🚀 PAPER BUY: {current_price} | Imbalance: {imbalance}x")
            
            # Rule: If sellers are 1.8x stronger than buyers, ENTER SHORT
            elif imbalance <= (1 / CONFIG["IMBALANCE_THRESHOLD"]):
                STATE.update({
                    "is_holding": True,
                    "entry_price": current_price,
                    "entry_time": datetime.now().strftime("%H:%M"),
                    "direction": "SHORT"
                })
                add_log(f"🔻 PAPER SHORT: {current_price} | Imbalance: {imbalance}x")

        # C. Exit Logic (Take Profit / Stop Loss)
        elif STATE["is_holding"]:
            # Calculate PnL
            pnl = (current_price - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": pnl = -pnl
            
            # Check Exit Conditions
            if pnl >= CONFIG["TAKE_PROFIT"] or pnl <= -CONFIG["STOP_LOSS"]:
                result = "WIN" if pnl > 0 else "LOSS"
                add_log(f"💰 EXIT {result}: {round(pnl*100, 2)}% at {current_price}")
                STATE["pnl_history"].append(round(pnl*100, 2))
                STATE.update({"is_holding": False, "entry_price": 0})

    except Exception as e:
        add_log(f"Error: {str(e)}")

# --- 4. TERMINAL DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        
        # Calculate current PnL for display
        current_pnl = 0
        if STATE["is_holding"]:
            ticker = exchange.fetch_ticker(CONFIG["SYMBOL"])
            current_pnl = (ticker['last'] - STATE["entry_price"]) / STATE["entry_price"]
            if STATE["direction"] == "SHORT": current_pnl = -current_pnl
            current_pnl = round(current_pnl * 100, 2)

        html = f"""
        <html><head><title>GCR_CORE_V1</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#0f0; font-family:monospace; padding:20px; }}
            .container {{ border:1px solid #0f0; padding:20px; max-width:500px; margin:auto; box-shadow: 0 0 15px #0f0; }}
            .pnl {{ font-size:4em; text-align:center; color:{'#0f0' if current_pnl >= 0 else '#f00'}; }}
            .status {{ text-align:center; color:#888; letter-spacing:2px; margin-bottom:20px; }}
            .log {{ background:#000; border:1px solid #111; padding:10px; height:200px; overflow:scroll; font-size:0.8em; color:#555; white-space:pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div class="status">{STATE['direction'] if STATE['is_holding'] else 'WAITING FOR WHALE SIGNAL'}</div>
                <div class="pnl">{current_pnl}%</div>
                <div style="font-size:0.7em; color:#333; margin-bottom:5px;">SYSTEM_LOGS:</div>
                <div class="log">{"\\n".join(STATE['logs'])}</div>
                <div style="margin-top:15px; font-size:0.7em; text-align:center;">{CONFIG['SYMBOL']} | WIN_RATE: {len([x for x in STATE['pnl_history'] if x > 0])}/{max(1, len(STATE['pnl_history']))}</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

# --- 5. START ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(CONFIG["TICK_INTERVAL"])
