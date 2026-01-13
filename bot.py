import os, json, time, ccxt, requests, threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

# --- V7 GLOBALS ---
SESSION_START = time.time()
MAX_POSITION_MINUTES = 240
MAX_TRADES_PER_DAY = 1

# --- PERSISTENT STATE ---
STATE_FILE = "trade_state_v7.json"

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
        "entry_time": 0,
        "daily_trade_count": 0,
        "last_trade_date": "",
        "prev_market_state": "NEUTRAL_NOISE",
        "logs": [],
        "history": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. THE DETERMINISTIC SENSORS ---

def get_vol_regime(ticker):
    pct = abs(ticker['percentage'] or 0)
    if pct < 0.2: return "COMPRESSION"
    if pct > 1.5: return "EXPANSION"
    return "NORMAL"

def get_whale_regime():
    try:
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        b = sum([x[1] for x in ob['bids']]); a = sum([x[1] for x in ob['asks']])
        ratio = b / a if a > 0 else 1
        if ratio > 1.8: return "STRONG_BUY_SUPPORT"
        if ratio < 0.55: return "STRONG_SELL_PRESSURE"
        return "NEUTRAL_NOISE"
    except: return "NEUTRAL_NOISE"

def get_macro_news():
    keywords = ["btc", "bitcoin", "fed", "rate", "etf", "sec", "inflation", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        filtered = [n['title'] for n in res['Data'] if any(k in n['title'].lower() for k in keywords)]
        return " | ".join(filtered[:3]) if filtered else "NO_MACRO_CATALYST"
    except: return "NEWS_OFFLINE"

# --- 2. GOVERNANCE ENGINE ---

def add_to_log(text):
    global state
    ts = datetime.now().strftime("%H:%M:%S")
    state["logs"].insert(0, f"[{ts}] {text}")
    state["logs"] = state["logs"][:30]
    save_state(state)

def run_cycle():
    global state
    try:
        # A. SENSORS
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        vol_state = get_vol_regime(ticker)
        whale_state = get_whale_regime()
        news_data = get_macro_news()

        # B. AI CLASSIFICATION (The Judge)
        prompt = f"""
        System: GCR Market Classifier.
        Price: {price} | Vol: {vol_state} | Whale: {whale_state} | News: {news_data}
        Task: Classify Market State.
        JSON format:
        {{
            "market_state": "ACCUMULATION / DISTRIBUTION / NEUTRAL_NOISE / BREAKOUT_RISK",
            "news_bias": "RISK_ON / RISK_OFF / IGNORE",
            "conviction": "LOW / MEDIUM / HIGH",
            "reason": "..."
        }}
        """
        chat = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
        ai = json.loads(chat.choices[0].message.content)

        # C. DETERMINISTIC RULES (The Commander)
        current_state = ai['market_state']
        news_bias = ai['news_bias']
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Reset daily trade counter
        if state["last_trade_date"] != today:
            state["daily_trade_count"] = 0
            state["last_trade_date"] = today

        # REGIME CHECK: Only trade State Transitions or High Conviction
        should_buy = (current_state == "ACCUMULATION" and whale_state == "STRONG_BUY_SUPPORT" and news_bias != "RISK_OFF")
        should_sell = (current_state == "DISTRIBUTION" and whale_state == "STRONG_SELL_PRESSURE") or state["is_holding"]

        # D. EXECUTION LOGIC
        if not state["is_holding"] and should_buy:
            if state["daily_trade_count"] < MAX_TRADES_PER_DAY:
                if ai['conviction'] == "HIGH":
                    state.update({"is_holding":True, "entry_price":price, "entry_time":time.time(), "daily_trade_count":state["daily_trade_count"]+1})
                    add_to_log(f"🚀 BUY: {current_price} | REASON: {ai['reason']}")
                else: add_to_log(f"SKIP: Conviction {ai['conviction']} too low.")
            else: add_to_log("SKIP: Max daily trades reached.")
        
        # EXIT LOGIC (TP/SL or Time-based)
        elif state["is_holding"]:
            # Forced Time Exit (240 mins)
            mins_open = (time.time() - state["entry_time"]) / 60
            pnl = ((price - state["entry_price"]) / state["entry_price"]) * 100
            
            if mins_open > MAX_POSITION_MINUTES or current_state == "DISTRIBUTION" or pnl < -2.0:
                add_to_log(f"💰 SELL: {price} | P/L: {round(pnl,2)}% | Reason: Time/Signal Exit")
                state["history"].insert(0, f"P/L: {round(pnl,2)}% | Entry: {state['entry_price']}")
                state.update({"is_holding":False, "entry_price":0, "entry_time":0})
        
        else:
            # NEGATIVE LOGGING: Why didn't we trade?
            if current_state == "NEUTRAL_NOISE": add_to_log("IDLE: Neutral Noise.")
            elif whale_state == "NEUTRAL_NOISE": add_to_log(f"IDLE: Whale state {whale_state} is weak.")

        state["prev_market_state"] = current_state
        save_state(state)

    except Exception as e: print(f"Cycle Error: {e}")

# --- 3. V7 DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        uptime = f"{int((time.time()-SESSION_START)/60)}m"
        age = f"{int((time.time()-state['global_start'])/86400)}d"
        pnl = round(((float(exchange.fetch_ticker('BTC/USDT')['last']) - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_V7_GOVERNANCE</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#00ff41; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:650px; margin:auto; }}
            .header {{ display:flex; justify-content:space-between; font-size:0.75em; color:#444; border-bottom:1px solid #222; padding-bottom:5px; }}
            .stats {{ background:#0a0a0a; padding:20px; text-align:center; margin:15px 0; border:1px solid #111; }}
            .log-box {{ background:#000; color:#666; border:1px solid #222; padding:10px; height:250px; overflow-y:scroll; font-size:0.75em; white-space: pre-wrap; }}
            .tag {{ font-size:0.7em; background:#222; padding:3px 6px; border-radius:3px; }}
        </style></head>
        <body>
            <div class="container">
                <div class="header">
                    <span>UPTIME: {uptime} | AGE: {age}</span>
                    <span style="color:#00d4ff;">TRADES_TODAY: {state['daily_trade_count']}</span>
                </div>
                <div class="stats">
                    <div style="font-size:3.5em; color:{'#00ff41' if pnl >=0 else '#ff4444'};">{pnl}%</div>
                    <div style="margin-top:5px; font-size:0.9em; letter-spacing:2px;">{state['prev_market_state']}</div>
                    { f"<div class='tag'>ENTRY: {state['entry_price']}</div>" if state['is_holding'] else ""}
                </div>
                <div class="log-box">{ "\n".join(state["logs"]) }</div>
                <div style="font-size:0.6em; color:#222; margin-top:10px; text-align:center;">GCR GOVERNANCE PROTOCOL V7.0</div>
            </div>
            <script>setTimeout(()=>location.reload(), 20000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(20)
