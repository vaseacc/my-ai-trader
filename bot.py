import os
import json
import time
import ccxt
import requests
import threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- TIMERS ---
# This resets every time the bot/server restarts
SESSION_START_TIME = time.time() 

# --- SETUP ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- PERSISTENT STATE MANAGEMENT ---
STATE_FILE = "trade_state.json"

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except: pass
    
    return {
        "global_start": time.time(), # The very first time the bot ever ran
        "is_holding": False,
        "entry_price": 0,
        "total_trades": 0,
        "logs": []
    }

paper_trade = load_state()
latest_status = {"action": "SCANNING", "confidence": 0, "reason": "System Booting...", "price": "0", "pnl": 0, "time": ""}

# --- 1. CORE LOGIC ---
def add_to_log(text):
    global paper_trade
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {text}"
    paper_trade["logs"].insert(0, log_entry)
    paper_trade["logs"] = paper_trade["logs"][:25] # Keep last 25
    save_state(paper_trade)

def run_cycle():
    global latest_status, paper_trade
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        
        # Calculate P/L
        current_pnl = 0
        if paper_trade["is_holding"]:
            current_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2)

        # Get Fast News
        news_res = requests.get(f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}").json()
        news = " | ".join([n['title'] for n in news_res['Data'][:2]])
        
        # GCR Reasoning
        prompt = f"You are GCR. Price: {price}. News: {news}. Decision? JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}"
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)

        # Log AI reasoning
        add_to_log(f"AI ({decision['confidence']}%): {decision['reason']}")

        # Paper Trade Logic
        if decision['confidence'] >= 95:
            if decision['action'] == "BUY" and not paper_trade["is_holding"]:
                paper_trade["is_holding"] = True
                paper_trade["entry_price"] = price
                paper_trade["total_trades"] += 1
                add_to_log(f"!!! PAPER BUY: {price} USDT")
            elif decision['action'] == "SELL" and paper_trade["is_holding"]:
                final_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2)
                add_to_log(f"!!! PAPER SELL: {price} USDT (P/L: {final_pnl}%)")
                paper_trade["is_holding"] = False
                paper_trade["entry_price"] = 0
        
        save_state(paper_trade)

        latest_status = {
            "action": decision['action'], 
            "confidence": decision['confidence'], 
            "reason": decision['reason'], 
            "price": price, 
            "pnl": current_pnl,
            "time": time.ctime()
        }
    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 2. THE DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        # --- CALCULATE TIMERS ---
        def format_time(seconds):
            d, rem = divmod(int(seconds), 86400)
            h, rem = divmod(rem, 3600)
            m, _ = divmod(rem, 60)
            return f"{d}d {h}h {m}m"

        session_uptime = format_time(time.time() - SESSION_START_TIME)
        global_age = format_time(time.time() - paper_trade["global_start"])
        
        pnl_color = "#00ff41" if latest_status.get('pnl', 0) >= 0 else "#ff4444"
        log_text = "\n".join(paper_trade["logs"])

        html = f"""
        <html><head><title>GCR_TRADING_CORE</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#00ff41; font-family:monospace; padding:20px; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:700px; margin:auto; }}
            .header-info {{ display:flex; justify-content:space-between; font-size:0.7em; color:#555; margin-bottom:10px; }}
            .stats {{ background:#111; padding:20px; border:1px solid #333; margin:15px 0; text-align:center; }}
            .log-box {{ background:#000; color:#888; border:1px solid #333; padding:10px; height:250px; overflow-y:scroll; font-size:0.8em; white-space: pre-wrap; margin-top:10px; }}
            .btn {{ background:#00ff41; color:#000; border:none; padding:8px 15px; cursor:pointer; font-weight:bold; width:100%; margin-top:10px; }}
            .alert {{ color:#ffcc00; }}
        </style></head>
        <body>
            <div class="container">
                <div class="header-info">
                    <span>PROJECT_AGE: {global_age}</span>
                    <span style="color:#00ff41;">SESSION_UPTIME: {session_uptime}</span>
                </div>
                <h2 style="text-align:center; border-bottom:1px solid #333; padding-bottom:10px;">GCR_AGENT_TERMINAL_V4</h2>
                
                <div class="stats">
                    <div style="font-size:3em; color:{pnl_color};">{latest_status.get('pnl', 0)}%</div>
                    <div style="color:#00d4ff; letter-spacing:3px;">{'HOLDING_POSITION' if paper_trade['is_holding'] else 'SCANNING_MARKET'}</div>
                    <div style="font-size:0.9em; margin-top:10px; color:#888;">PRICE: {latest_status['price']} USDT | TRADES: {paper_trade['total_trades']}</div>
                </div>

                <div class="alert">> {latest_status['reason']}</div>
                
                <div style="margin-top:25px;">
                    <div style="font-size:0.7em; color:#555;">REASONING_HISTORY:</div>
                    <div class="log-box" id="logBox">{log_text}</div>
                    <button class="btn" onclick="copyLogs()">COPY ARCHIVE</button>
                </div>
            </div>

            <script>
                function copyLogs() {{
                    const text = document.getElementById('logBox').innerText;
                    navigator.clipboard.writeText(text);
                    alert('Log Archive Copied');
                }}
                setTimeout(()=>location.reload(), 20000);
            </script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(20)
