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
    return {"global_start": time.time(), "is_holding": False, "entry_price": 0, "total_trades": 0, "logs": []}

paper_trade = load_state()
latest_status = {"action": "SCANNING", "confidence": 0, "reason": "Calibrating Sensors...", "price": "0", "pnl": 0, "time": ""}

# --- 1. DETERMINISTIC SIGNAL LAYER (The Sensors) ---

def get_whale_regime():
    """Calculates whale intent with a strict 1.8x noise filter"""
    try:
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        bids = sum([b[1] for b in ob['bids']])
        asks = sum([a[1] for a in ob['asks']])
        
        ratio = bids / asks if asks > 0 else 1
        
        if ratio > 1.8: return "STRONG_BUY_SUPPORT"
        if ratio < 0.55: return "STRONG_SELL_PRESSURE" # (1/1.8 = 0.55)
        return "NEUTRAL_NOISE"
    except:
        return "SENSOR_OFFLINE"

def get_filtered_news():
    """Filters news specifically for BTC and Macro Impact"""
    keywords = ["btc", "bitcoin", "fed", "etf", "sec", "inflation", "rate", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        
        # Only keep headlines that mention our keywords
        filtered = [
            n['title'] for n in res['Data'] 
            if any(k in n['title'].lower() for k in keywords)
        ]
        return " | ".join(filtered[:3]) if filtered else "No BTC-Relevant News"
    except:
        return "NEWS_OFFLINE"

# --- 2. THE ARBITRATOR (The AI Judge) ---

def run_cycle():
    global latest_status, paper_trade
    try:
        # A. Gather Clean Data
        price = exchange.fetch_ticker('BTC/USDT')['last']
        whale_state = get_whale_regime()
        btc_news = get_filtered_news()
        
        # B. Calculate P/L
        current_pnl = round(((price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100, 2) if paper_trade["is_holding"] else 0

        # C. The Judge Prompt
        prompt = f"""
        System: Act as GCR (Elite Arbitrator).
        Facts:
        - BTC Price: {price} USDT
        - Whale Intent: {whale_state}
        - Relevant News: {btc_news}

        Decision Guidelines:
        - ACTION: BUY, SELL, or HOLD.
        - CONFIDENCE: 0-100. (Use 90+ ONLY if Whale Intent and News are perfectly aligned).
        - REASON: Focus on Divergence (e.g., News is hype but Whale is STRONG_SELL_PRESSURE).

        Respond ONLY in valid JSON:
        {{
          "action": "BUY",
          "confidence": 85,
          "reason": "..."
        }}
        """
        
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)

        # D. Logging
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = f"[{timestamp}] [{whale_state}] AI ({decision['confidence']}%): {decision['reason']}"
        paper_trade["logs"].insert(0, log_msg)
        paper_trade["logs"] = paper_trade["logs"][:30]
        
        # E. Logic Execution (Threshold lowered to 90% per advice)
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY" and not paper_trade["is_holding"]:
                paper_trade["is_holding"] = True
                paper_trade["entry_price"] = price
                paper_trade["total_trades"] += 1
            elif decision['action'] == "SELL" and paper_trade["is_holding"]:
                paper_trade["is_holding"] = False
                paper_trade["entry_price"] = 0
        
        save_state(paper_trade)
        latest_status = {"action": decision['action'], "confidence": decision['confidence'], "reason": decision['reason'], "price": price, "alpha": whale_state, "pnl": current_pnl, "time": time.ctime()}

    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 3. THE DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        pnl_color = "#00ff41" if latest_status.get('pnl', 0) >= 0 else "#ff4444"
        log_text = "\n".join(paper_trade["logs"])
        
        uptime = f"{int((time.time() - SESSION_START_TIME)/60)}m"

        html = f"""
        <html><head><title>GCR_REGIME_V5</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#00ff41; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:650px; margin:auto; }}
            .header {{ display:flex; justify-content:space-between; font-size:0.75em; color:#444; }}
            .stats {{ background:#0a0a0a; border:1px solid #222; padding:20px; text-align:center; margin:15px 0; }}
            .log-box {{ background:#050505; color:#777; border:1px solid #222; padding:10px; height:250px; overflow-y:scroll; font-size:0.75em; white-space: pre-wrap; }}
            .btn {{ background:#00ff41; color:#000; border:none; padding:10px; width:100%; cursor:pointer; font-weight:bold; margin-top:10px; }}
        </style></head>
        <body>
            <div class="container">
                <div class="header">
                    <span>REGIME: {latest_status['alpha']}</span>
                    <span>UPTIME: {uptime}</span>
                </div>
                <div class="stats">
                    <div style="font-size:3.5em; color:{pnl_color};">{latest_status.get('pnl', 0)}%</div>
                    <div style="color:#888;">{latest_status['action']} ({latest_status['confidence']}%)</div>
                </div>
                <div style="color:#ffcc00; font-size:0.9em; margin-bottom:15px;">> {latest_status['reason']}</div>
                <div class="log-box" id="logBox">{log_text}</div>
                <button class="btn" onclick="copyLogs()">COPY ARCHIVE</button>
            </div>
            <script>
                function copyLogs() {{
                    const text = document.getElementById('logBox').innerText;
                    navigator.clipboard.writeText(text);
                    alert('Archive Copied');
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
