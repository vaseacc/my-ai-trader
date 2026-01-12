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
SESSION_START_TIME = time.time() 

# --- SETUP ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- PERSISTENT STATE MANAGEMENT ---
STATE_FILE = "trade_state.json"

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except: pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {
        "global_start": time.time(),
        "is_holding": False,
        "entry_price": 0,
        "total_trades": 0,
        "logs": []
    }

paper_trade = load_state()
latest_status = {"action": "SCANNING", "confidence": 0, "reason": "Syncing Alpha...", "price": "0", "alpha": "Checking Book...", "pnl": 0, "time": ""}

# --- 1. ALPHA SENSORS ---
def get_whale_intent():
    """MEXC Order Book Analysis (Free & Instant)"""
    try:
        limit = 50
        ob = exchange.fetch_order_book('BTC/USDT', limit)
        bids_vol = sum([bid[1] for bid in ob['bids']]) # Buyers
        asks_vol = sum([ask[1] for ask in ob['asks']]) # Sellers
        
        if bids_vol > asks_vol:
            return f"Whale Support: {round(bids_vol/asks_vol, 1)}x"
        else:
            return f"Whale Pressure: {round(asks_vol/bids_vol, 1)}x"
    except:
        return "Order Book Syncing..."

# --- 2. CORE LOGIC ---
def add_to_log(text):
    global paper_trade
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = f"[{timestamp}] {text}"
    paper_trade["logs"].insert(0, log_entry)
    paper_trade["logs"] = paper_trade["logs"][:30] # Increased to 30
    save_state(paper_trade)

def run_cycle():
    global latest_status, paper_trade
    try:
        # A. PERCEPTION
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        whale_alpha = get_whale_intent()
        
        # B. NEWS FETCH
        news_res = requests.get(f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}").json()
        news_headlines = " | ".join([n['title'] for n in news_res['Data'][:2]])
        
        # C. CALC P/L
        current_pnl = 0
        if paper_trade["is_holding"]:
            current_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2)

        # D. REASONING
        prompt = f"""
        You are GCR. Bitcoin Price: {price}. 
        Whale Intent (Order Book): {whale_alpha}. 
        Market News: {news_headlines}.
        
        TASK: Weigh the Whale Intent against the News. If News is hype but Whale Pressure is high, SELL. 
        Respond ONLY in JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}
        """
        
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)

        # E. LOGGING (Include Whale Alpha in history)
        log_msg = f"{whale_alpha} | AI ({decision['confidence']}%): {decision['reason']}"
        add_to_log(log_msg)

        # F. PAPER TRADE EXECUTION
        if decision['confidence'] >= 95:
            if decision['action'] == "BUY" and not paper_trade["is_holding"]:
                paper_trade["is_holding"] = True
                paper_trade["entry_price"] = price
                paper_trade["total_trades"] += 1
                add_to_log(f"!!! TRIGGER BUY at {price}")
            elif decision['action'] == "SELL" and paper_trade["is_holding"]:
                final_p = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2)
                add_to_log(f"!!! TRIGGER SELL at {price} | Final P/L: {final_p}%")
                paper_trade["is_holding"] = False
                paper_trade["entry_price"] = 0
        
        save_state(paper_trade)

        latest_status = {
            "action": decision['action'], 
            "confidence": decision['confidence'], 
            "reason": decision['reason'], 
            "price": price, 
            "alpha": whale_alpha,
            "pnl": current_pnl,
            "time": time.ctime()
        }
    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 3. THE DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
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
        <html><head><title>GCR_QUANT_CORE</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#00ff41; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:650px; margin:auto; }}
            .header {{ display:flex; justify-content:space-between; font-size:0.7em; color:#444; margin-bottom:10px; }}
            .main-stat {{ background:#0a0a0a; padding:15px; border:1px solid #222; text-align:center; margin-bottom:10px; }}
            .alpha-bar {{ color:#00d4ff; border-left:3px solid #00d4ff; padding-left:10px; margin:10px 0; font-size:0.9em; }}
            .log-box {{ background:#050505; color:#777; border:1px solid #222; padding:10px; height:250px; overflow-y:scroll; font-size:0.75em; white-space: pre-wrap; }}
            .btn {{ background:#00ff41; color:#000; border:none; padding:8px; width:100%; cursor:pointer; font-weight:bold; margin-top:10px; }}
        </style></head>
        <body>
            <div class="container">
                <div class="header">
                    <span>GLOBAL_AGE: {global_age}</span>
                    <span style="color:#00ff41;">SESSION: {session_uptime}</span>
                </div>
                
                <div class="main-stat">
                    <div style="font-size:3em; color:{pnl_color};">{latest_status.get('pnl', 0)}%</div>
                    <div style="letter-spacing:4px; font-size:0.8em; color:#888;">{latest_status['action']} ({latest_status['confidence']}%)</div>
                </div>

                <div class="alpha-bar">INTENT: {latest_status['alpha']}</div>
                <div style="color:#ffcc00; font-size:0.9em;">> {latest_status['reason']}</div>

                <div style="margin-top:20px;">
                    <span style="font-size:0.7em; color:#333;">RAW_ALPHA_LOG:</span>
                    <div class="log-box" id="logBox">{log_text}</div>
                    <button class="btn" onclick="copyLogs()">COPY ALL LOGS</button>
                </div>
                <div style="text-align:center; font-size:0.6em; margin-top:10px; color:#222;">PRICE: {latest_status['price']} USDT</div>
            </div>
            <script>
                function copyLogs() {{
                    const text = document.getElementById('logBox').innerText;
                    navigator.clipboard.writeText(text);
                    alert('Quant Logs Copied');
                }}
                setTimeout(()=>location.reload(), 15000);
            </script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(15)
