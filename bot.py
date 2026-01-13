import os, json, time, ccxt, requests, threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- V7 FINAL GLOBALS ---
SESSION_START = time.time()
MAX_POSITION_MINUTES = 240
MAX_TRADES_PER_DAY = 1
MIN_STATE_STABILITY = 3
AI_COOLDOWN = 120
NEWS_COOLDOWN = 600

# --- PERSISTENT STATE ---
STATE_FILE = "trade_state_v7_final.json"

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
        "state_counter": 0,
        "cached_ai": {"market_state": "NEUTRAL_NOISE", "news_bias": "IGNORE", "reason": "Initializing..."},
        "last_ai_time": 0,
        "last_news_time": 0,
        "cached_news": "No news yet.",
        "logs": [],
        "history": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. DECOUPLED SENSORS ---

def get_whale_regime():
    try:
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        b = sum([x[1] for x in ob['bids']]); a = sum([x[1] for x in ob['asks']])
        ratio = b / a if a > 0 else 1
        if ratio > 1.8: return "STRONG_BUY_SUPPORT"
        if ratio < 0.55: return "STRONG_SELL_PRESSURE"
        return "NEUTRAL_NOISE"
    except: return "NEUTRAL_NOISE"

def update_macro_news():
    global state
    if time.time() - state["last_news_time"] < NEWS_COOLDOWN:
        return state["cached_news"]
    keywords = ["btc", "bitcoin", "fed", "rate", "etf", "sec", "inflation", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        filtered = [n['title'] for n in res['Data'] if any(k in n['title'].lower() for k in keywords)]
        state["cached_news"] = " | ".join(filtered[:3]) if filtered else "No major news."
        state["last_news_time"] = time.time()
        return state["cached_news"]
    except: return state["cached_news"]

# --- 2. THE COMMAND CENTER ---

def add_to_log(text):
    global state
    ts = datetime.now().strftime("%H:%M:%S")
    state["logs"].insert(0, f"[{ts}] {text}")
    state["logs"] = state["logs"][:30]
    save_state(state)

def run_cycle():
    global state
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        whale_state = get_whale_regime()
        news_data = update_macro_news()

        if time.time() - state["last_ai_time"] > AI_COOLDOWN:
            prompt = f"System: Classifier. Price: {price} | Whale: {whale_state} | News: {news_data}\nOutput JSON: {{\"market_state\": \"...\", \"news_bias\": \"...\", \"reason\": \"...\"}}"
            chat = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
            ai_resp = json.loads(chat.choices[0].message.content)
            
            if ai_resp['market_state'] == state["prev_market_state"]:
                state["state_counter"] += 1
            else:
                add_to_log(f"TRANSITION: {state['prev_market_state']} -> {ai_resp['market_state']}")
                state["state_counter"] = 1
                state["prev_market_state"] = ai_resp['market_state']
            
            state["cached_ai"] = ai_resp
            state["last_ai_time"] = time.time()

        m_state = state["cached_ai"]['market_state']
        stable = state["state_counter"] >= MIN_STATE_STABILITY
        
        if state["last_trade_date"] != datetime.now().strftime("%Y-%m-%d"):
            state.update({"daily_trade_count": 0, "last_trade_date": datetime.now().strftime("%Y-%m-%d")})

        should_buy = False
        if not state["is_holding"]:
            if m_state == "BREAKOUT_RISK":
                pass 
            elif m_state == "ACCUMULATION" and stable and whale_state == "STRONG_BUY_SUPPORT" and state["cached_ai"]['news_bias'] != "RISK_OFF":
                should_buy = True

        if should_buy and state["daily_trade_count"] < MAX_TRADES_PER_DAY:
            state.update({"is_holding": True, "entry_price": price, "entry_time": time.time(), "daily_trade_count": state["daily_trade_count"]+1})
            add_to_log(f"🚀 BUY: {price} | REASON: {state['cached_ai']['reason']}")

        elif state["is_holding"]:
            mins_open = (time.time() - state["entry_time"]) / 60
            pnl = round(((price - state["entry_price"]) / state["entry_price"]) * 100, 2)
            exit_sig = (m_state == "DISTRIBUTION" or whale_state == "STRONG_SELL_PRESSURE")
            
            if mins_open > MAX_POSITION_MINUTES or exit_sig or pnl < -2.0:
                reason = "TIME" if mins_open > MAX_POSITION_MINUTES else ("SIGNAL" if exit_sig else "SL")
                add_to_log(f"💰 EXIT: {reason} | P/L: {pnl}%")
                state["history"].insert(0, {"entry": state["entry_price"], "exit": price, "pnl": pnl, "reason": reason, "date": datetime.now().strftime("%d %b")})
                state.update({"is_holding": False, "entry_price": 0, "entry_time": 0})

        save_state(state)
    except Exception as e: print(f"Cycle Error: {e}")

# --- 3. THE DASHBOARD (WITH BEGINNER VIEW) ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        # --- BEGINNER-FRIENDLY TRANSLATIONS ---
        whale_human = "Quiet"
        ws = get_whale_regime()
        if ws == "STRONG_BUY_SUPPORT": whale_human = "Whales are protecting the price (Bullish)"
        elif ws == "STRONG_SELL_PRESSURE": whale_human = "Whales are pushing the price down (Bearish)"
        
        state_human = "The market is messy and unpredictable"
        ms = state['prev_market_state']
        if ms == "ACCUMULATION": state_human = "Smart money is buying slowly (Good Sign)"
        elif ms == "DISTRIBUTION": state_human = "Investors are selling their bags (Caution)"
        elif ms == "BREAKOUT_RISK": state_human = "Price might jump or crash suddenly (High Risk)"

        history_html = "".join([f"<div style='font-size:0.7em;color:#555;'>• {t['date']}: {t['pnl']}%</div>" for t in state["history"][:3]])
        
        html = f"""
        <html><head><title>GCR_V7_FINAL</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#00ff41; font-family:monospace; padding:15px; line-height:1.4; }}
            .container {{ border:1px solid #00ff41; padding:20px; box-shadow:0 0 15px #00ff41; max-width:650px; margin:auto; }}
            .stats {{ background:#0a0a0a; border:1px solid #222; padding:20px; text-align:center; margin:15px 0; }}
            .beginner-box {{ background:#111; border-left:4px solid #00d4ff; padding:15px; margin:15px 0; font-size:0.9em; color:#eee; }}
            .log-box {{ background:#050505; color:#555; border:1px solid #222; padding:10px; height:180px; overflow-y:scroll; font-size:0.7em; white-space: pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444;">
                    <span>GCR_V7_FINAL</span>
                    <span>TRADES: {state['daily_trade_count']}</span>
                </div>

                <div class="stats">
                    <div style="font-size:3.5em; color:{'#00ff41' if pnl >=0 else '#ff4444'};">{pnl}%</div>
                    <div style="font-size:0.8em; color:#00d4ff;">{ 'HOLDING BTC' if state['is_holding'] else 'WAITING FOR A+ SETUP' }</div>
                </div>

                <!-- BEGINNER FRIENDLY SUMMARY -->
                <div class="beginner-box">
                    <b style="color:#00d4ff; font-size:0.75em; text-transform:uppercase;">Simple Summary for Beginners:</b><br>
                    • <b>Whale Activity:</b> {whale_human}<br>
                    • <b>Market Mood:</b> {state_human}<br>
                    • <b>Latest News:</b> <span style="color:#aaa;">{state['cached_news'][:80]}...</span>
                </div>

                <div style="color:#ffcc00; font-size:0.85em; margin-bottom:15px;">> {state['cached_ai']['reason']}</div>

                <div style="font-size:0.7em; color:#333;">HISTORY | LOGS:</div>
                <div class="log-box">{ "\n".join(state["logs"]) }</div>
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
