import os, json, time, ccxt, requests, threading, math
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- CONFIG & TIMERS ---
SESSION_START_TIME = time.time()
CONFIG = {
    "WINDOW_SIZE": 20,         
    "PHYSICS_CONFIDENCE": 90,  # LOWERED: Now trades more aggressively
    "NEWS_COOLDOWN": 600       
}

# --- PERSISTENT STATE ---
STATE_FILE = "trade_state_v11_aggressive.json"

def save_state(s):
    try:
        with open(STATE_FILE, 'w') as f: json.dump(s, f)
    except: pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f: return json.load(f)
        except: pass
    return {
        "global_start": time.time(),
        "is_holding": False,
        "entry_price": 0,
        "entry_time": "",
        "price_history": [],
        "market_phase": "STABLE",
        "cached_news": "No News Cached",
        "last_news_time": 0,
        "important_logs": [],
        "activity_stream": [],
        "history": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. SENSORS ---

class MarketPhysics:
    @staticmethod
    def get_weighted_imbalance(ob):
        v1 = sum([x[1] for x in ob['bids'][:5]]) * 1.0
        v2 = sum([x[1] for x in ob['asks'][:5]]) * 1.0
        return round(v1 / v2, 2) if v2 > 0 else 1.0

    @staticmethod
    def get_energy_score(prices):
        if len(prices) < 10: return 0
        mean = sum(prices) / len(prices)
        variance = sum((x - mean) ** 2 for x in prices) / len(prices)
        stdev_pct = (math.sqrt(variance) / mean) * 100
        energy = max(0, 100 - (stdev_pct * 500)) 
        return round(min(energy, 100), 1)

def get_macro_news():
    global state
    if time.time() - state["last_news_time"] < CONFIG["NEWS_COOLDOWN"]:
        return state["cached_news"]
    keywords = ["btc", "bitcoin", "fed", "rate", "etf", "sec", "inflation", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        filtered = [n['title'] for n in res['Data'] if any(k in n['title'].lower() for k in keywords)]
        state["cached_news"] = " | ".join(filtered[:3]) if filtered else "NO_MACRO_CATALYST"
        state["last_news_time"] = time.time()
        return state["cached_news"]
    except: return state["cached_news"]

# --- 2. COMMAND CENTER ---

def add_to_important_log(text):
    global state
    ts = datetime.now().strftime("%H:%M:%S")
    state["important_logs"].insert(0, f"[{ts}] {text}")
    state["important_logs"] = state["important_logs"][:20]
    save_state(state)

def add_to_activity_stream(text):
    global state
    ts = datetime.now().strftime("%H:%M:%S")
    state["activity_stream"].insert(0, f"[{ts}] {text}")
    state["activity_stream"] = state["activity_stream"][:60]
    save_state(state)

def run_cycle():
    global state
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        
        state['price_history'].append(price)
        if len(state['price_history']) > CONFIG["WINDOW_SIZE"]: state['price_history'].pop(0)
            
        imbalance = MarketPhysics.get_weighted_imbalance(ob)
        energy = MarketPhysics.get_energy_score(state['price_history'])
        news = get_macro_news()
        pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0

        # AI Reasoning
        prompt = f"GCR Arbitrator. Price: {price} | Flow: {imbalance}x | Energy: {energy}% | News: {news}\nOutput JSON: {{'phase': '...', 'conviction': 0-100, 'trapped_side': 'LONGS/SHORTS/NONE', 'logic': '...'}}"
        chat = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
        ai = json.loads(chat.choices[0].message.content)
        state['market_phase'] = ai['phase']

        # LOG CYCLE
        activity_msg = f"FLOW: {imbalance}x | ENERGY: {energy}% | {ai['phase']} | {ai['logic'][:60]}"
        add_to_activity_stream(activity_msg)

        # EXECUTION (Triggered at 90% confidence)
        if not state['is_holding'] and energy > 80 and ai['conviction'] >= CONFIG["PHYSICS_CONFIDENCE"]:
            if ai['trapped_side'] != "NONE":
                state.update({"is_holding": True, "entry_price": price, "entry_time": datetime.now().strftime("%b %d, %H:%M")})
                add_to_important_log(f"🚀 BUY: {price} | {ai['logic']}")

        if state['is_holding'] and (pnl < -1.5 or pnl > 3.5):
            add_to_important_log(f"💰 EXIT: {price} | P/L: {pnl}%")
            state.update({"is_holding": False, "entry_price": 0})

        save_state(state)
    except Exception as e: print(f"Cycle Error: {e}")

# --- 3. THE DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        energy = MarketPhysics.get_energy_score(state['price_history'])
        ticker = exchange.fetch_ticker('BTC/USDT')
        pnl = round(((ticker['last'] - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_V11_AGGRESSIVE</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#0f0; font-family:monospace; padding:15px; line-height:1.4; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:700px; margin:auto; }}
            .pnl {{ font-size:3em; text-align:center; color:{'#0f0' if pnl >=0 else '#f00'}; }}
            .scroll-box {{ font-size:0.7em; color:#666; height:180px; overflow:scroll; background:#000; padding:10px; border:1px solid #111; margin-top:5px; white-space: pre-wrap; }}
            .btn {{ background:#0f0; color:#000; border:none; padding:10px; width:100%; cursor:pointer; font-weight:bold; margin-top:10px; }}
            .label {{ font-size:0.7em; color:#00d4ff; margin-top:20px; text-transform:uppercase; border-bottom:1px solid #222; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444;">
                    <span>AGE: {int((time.time()-state['global_start'])/86400)}d</span>
                    <span>CONFIDENCE: {CONFIG['PHYSICS_CONFIDENCE']}%</span>
                </div>
                
                <div class="pnl">{pnl}%</div>
                <div style="text-align:center; color:#555; font-size:0.8em;">{ 'HOLDING POSITION' if state['is_holding'] else 'SCANNING FOR BREAKOUT' }</div>

                <button class="btn" onclick="copyFullLog()">📋 COPY FULL SYSTEM LOG</button>

                <div class="label">Trade Events</div>
                <div class="scroll-box" id="importantBox">{ "\n".join(state["important_logs"]) }</div>

                <div class="label">Activity Stream</div>
                <div class="scroll-box" id="activityBox">{ "\n".join(state["activity_stream"]) }</div>
            </div>

            <script>
                function copyFullLog() {{
                    const important = document.getElementById('importantBox').innerText;
                    const activity = document.getElementById('activityBox').innerText;
                    const fullLog = "--- TRADE EVENTS ---\\n" + important + "\\n\\n--- ACTIVITY STREAM ---\\n" + activity;
                    
                    const el = document.createElement('textarea');
                    el.value = fullLog;
                    document.body.appendChild(el);
                    el.select();
                    document.execCommand('copy');
                    document.body.removeChild(el);
                    alert('Full system logs copied to clipboard!');
                }}
                setTimeout(()=>location.reload(), 20000);
            </script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(15)
