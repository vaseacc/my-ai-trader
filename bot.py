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

# --- PERSISTENT STATE ---
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
        "entry_time": "", 
        "total_trades": 0, 
        "logs": [],
        "trade_history": [] # New: Stores completed trades
    }

paper_trade = load_state()
latest_status = {"action": "SCANNING", "confidence": 0, "reason": "Syncing...", "price": "0", "pnl": 0, "time": ""}

# --- 1. SIGNAL SENSORS ---

def get_whale_regime():
    try:
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        bids = sum([b[1] for b in ob['bids']])
        asks = sum([a[1] for a in ob['asks']])
        ratio = bids / asks if asks > 0 else 1
        if ratio > 1.8: return "STRONG_BUY_SUPPORT"
        if ratio < 0.55: return "STRONG_SELL_PRESSURE"
        return "NEUTRAL_NOISE"
    except: return "SENSOR_OFFLINE"

def get_filtered_news():
    keywords = ["btc", "bitcoin", "fed", "etf", "sec", "inflation", "rate", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        filtered = [n['title'] for n in res['Data'] if any(k in n['title'].lower() for k in keywords)]
        return " | ".join(filtered[:3]) if filtered else "No Macro News"
    except: return "NEWS_OFFLINE"

# --- 2. THE AGENT LOGIC ---

def add_to_log(text):
    global paper_trade
    timestamp = datetime.now().strftime("%H:%M:%S")
    paper_trade["logs"].insert(0, f"[{timestamp}] {text}")
    paper_trade["logs"] = paper_trade["logs"][:30]
    save_state(paper_trade)

def run_cycle():
    global latest_status, paper_trade
    try:
        price = exchange.fetch_ticker('BTC/USDT')['last']
        whale_state = get_whale_regime()
        btc_news = get_filtered_news()
        
        current_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2) if paper_trade["is_holding"] else 0

        prompt = f"System: GCR Arbitrator. Price: {price}. Whale: {whale_state}. News: {btc_news}. Decision? JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}"
        
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)

        # EXECUTION LOGIC
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY" and not paper_trade["is_holding"]:
                paper_trade["is_holding"] = True
                paper_trade["entry_price"] = price
                paper_trade["entry_time"] = datetime.now().strftime("%b %d, %H:%M")
                paper_trade["total_trades"] += 1
                add_to_log(f"🚀 BUY EXECUTED: {price} USDT")
            
            elif decision['action'] == "SELL" and paper_trade["is_holding"]:
                final_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2)
                trade_record = f"SOLD: {price} | P/L: {final_pnl}% | Duration: {paper_trade['entry_time']} to Now"
                paper_trade["trade_history"].insert(0, trade_record)
                add_to_log(f"💰 SELL EXECUTED: {price} (P/L: {final_pnl}%)")
                paper_trade["is_holding"] = False
                paper_trade["entry_price"] = 0
                paper_trade["entry_time"] = ""

        latest_status = {"action": decision['action'], "confidence": decision['confidence'], "reason": decision['reason'], "price": price, "alpha": whale_state, "pnl": current_pnl, "time": time.ctime()}
        save_state(paper_trade)

    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 3. THE DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        pnl_color = "#00ff41" if latest_status.get('pnl', 0) >= 0 else "#ff4444"
        history_html = "".join([f"<div style='font-size:0.7em; color:#888;'>• {t}</div>" for t in paper_trade.get("trade_history", [])[:5]])

        html = f"""
        <html><head><title>GCR_LEDGER_V6</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#00ff41; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:650px; margin:auto; }}
            .stats {{ background:#0a0a0a; border:1px solid #222; padding:20px; text-align:center; margin:15px 0; }}
            .ledger {{ background:#111; border-left:3px solid #00d4ff; padding:10px; margin:10px 0; font-size:0.85em; }}
            .log-box {{ background:#050505; color:#666; border:1px solid #222; padding:10px; height:200px; overflow-y:scroll; font-size:0.75em; white-space: pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444;">
                    <span>STATUS: {latest_status['alpha']}</span>
                    <span>SESSION: {int((time.time()-SESSION_START_TIME)/60)}m</span>
                </div>

                <div class="stats">
                    <div style="font-size:3.5em; color:{pnl_color};">{latest_status.get('pnl', 0)}%</div>
                    <div style="color:#888;">{latest_status['action']} ({latest_status['confidence']}%)</div>
                </div>

                <!-- ACTIVE POSITION LEDGER -->
                <div class="ledger">
                    <div style="color:#00d4ff; font-size:0.7em; margin-bottom:5px;">ACTIVE_POSITION_DATA:</div>
                    {"NONE" if not paper_trade['is_holding'] else f"ENTRY: {paper_trade['entry_price']} USDT<br>TIME: {paper_trade['entry_time']}"}
                </div>

                <div style="color:#ffcc00; font-size:0.9em; margin:15px 0;">> {latest_status['reason']}</div>

                <div style="font-size:0.7em; color:#333; margin-top:10px;">CLOSED_TRADES_HISTORY:</div>
                <div style="background:#0a0a0a; padding:10px; border:1px solid #222; margin-bottom:15px;">
                    {history_html if history_html else "No closed trades yet."}
                </div>

                <div class="log-box">{ "\n".join(paper_trade["logs"]) }</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(20)
