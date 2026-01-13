import os, json, time, ccxt, requests, threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- V9 PRO CONFIG ---
CONFIG = {
    "MAX_DAILY_LOSS_PCT": -5.0,
    "MAX_TRADES_PER_DAY": 3,
    "MIN_CONFIDENCE": 85,
    "POSITION_BASE_USDT": 10, 
    "STATE_STABILITY_THRESHOLD": 3
}

# --- PERSISTENT LEDGER ---
STATE_FILE = "v9_wintermute_state.json"
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
        "entry_time": 0,
        "trades_today": 0,
        "last_date": "",
        "current_regime": "NEUTRAL",
        "regime_counter": 0,
        "logs": [],
        "history": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. SPECIALIZED AGENTS ---

class MarketAgent:
    @staticmethod
    def get_context():
        ticker = exchange.fetch_ticker('BTC/USDT')
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        bids = sum([x[1] for x in ob['bids']]); asks = sum([x[1] for x in ob['asks']])
        ratio = bids / asks if asks > 0 else 1
        
        volatility = abs(ticker['percentage'] or 0)
        whale_state = "BULLISH_SUPPORT" if ratio > 1.8 else ("BEARISH_PRESSURE" if ratio < 0.55 else "NEUTRAL")
        
        return {
            "price": ticker['last'],
            "volatility": "HIGH" if volatility > 1.5 else "LOW",
            "whale_ratio": round(ratio, 2),
            "whale_state": whale_state
        }

class NewsAgent:
    @staticmethod
    def get_macro():
        keywords = ["btc", "bitcoin", "fed", "etf", "sec", "inflation", "rate", "hack", "bitwise"]
        try:
            url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
            res = requests.get(url).json()
            filtered = [n['title'] for n in res['Data'] if any(k in n['title'].lower() for k in keywords)]
            return " | ".join(filtered[:3]) if filtered else "NO_MACRO_CATALYST"
        except: return "NEWS_OFFLINE"

class RiskAgent:
    @staticmethod
    def calc_size(confidence, vol_state):
        # Wintermute Rule: Smaller sizes during high volatility
        multiplier = 0.5 if vol_state == "HIGH" else 1.0
        return round(CONFIG["POSITION_BASE_USDT"] * (confidence / 100) * multiplier, 2)

# --- 2. PRO DECISION ENGINE (The Brain) ---

def pro_decision_engine(m_data, news):
    """Wintermute-Level Heuristics"""
    prompt = f"""
    System: Elite Quant Arbitrator (Wintermute/GCR Style).
    Data: Price {m_data['price']} | Whale: {m_data['whale_state']} ({m_data['whale_ratio']}x) | News: {news}
    
    Heuristics:
    - Never buy into high Whale Pressure, regardless of news.
    - Treat news hype without Whale Support as 'Exit Liquidity' (SELL).
    - Accumulation requires Whale Support + Compressed Volatility.

    JSON Output:
    {{
      "regime": "ACCUMULATION / DISTRIBUTION / NOISE",
      "bias": "BULLISH / BEARISH / NEUTRAL",
      "confidence": 0-100,
      "logic": "1-sentence pro reasoning"
    }}
    """
    chat = client.chat.completions.create(
        messages=[{"role":"user","content":prompt}],
        model="llama-3.3-70b-versatile",
        response_format={"type":"json_object"}
    )
    return json.loads(chat.choices[0].message.content)

# --- 3. MASTER CONTROL LOOP ---

def run_cycle():
    global state
    try:
        # A. AGENT PERCEPTION
        market = MarketAgent.get_context()
        news = NewsAgent.get_macro()
        
        # B. AI CLASSIFICATION
        intel = pro_decision_engine(market, news)
        
        # C. REGIME TRACKING (Stability Check)
        if intel['regime'] == state["current_regime"]:
            state["regime_counter"] += 1
        else:
            state["current_regime"] = intel['regime']
            state["regime_counter"] = 1

        # D. DETERMINISTIC EXECUTION (Wintermute Rules)
        trade_allowed = (
            state["regime_counter"] >= CONFIG["STATE_STABILITY_THRESHOLD"] and 
            state["trades_today"] < CONFIG["MAX_TRADES_PER_DAY"]
        )

        # Logic Gate
        if not state["is_holding"] and trade_allowed:
            if intel['regime'] == "ACCUMULATION" and market['whale_state'] == "BULLISH_SUPPORT":
                size = RiskAgent.calc_size(intel['confidence'], market['volatility'])
                state.update({"is_holding":True, "entry_price":market['price'], "entry_time":time.time(), "trades_today":state['trades_today']+1})
                add_log(f"🚀 BUY: {size} USDT at {market['price']} | REASON: {intel['logic']}")

        elif state["is_holding"]:
            pnl = round(((market['price'] - state['entry_price']) / state['entry_price']) * 100, 2)
            if intel['regime'] == "DISTRIBUTION" or pnl < -2.0 or pnl > 4.0:
                add_log(f"💰 EXIT: P/L {pnl}% | Reason: {intel['regime'] if pnl > -2 else 'StopLoss'}")
                state["history"].insert(0, {"pnl": pnl, "date": datetime.now().strftime("%d %b")})
                state.update({"is_holding":False, "entry_price":0})

        save_state(state)
    except Exception as e: print(f"V9 Error: {e}")

def add_log(txt):
    ts = datetime.now().strftime("%H:%M")
    state["logs"].insert(0, f"[{ts}] {txt}"); state["logs"] = state["logs"][:25]

# --- 4. TWO-TIER DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        m = MarketAgent.get_context()
        pnl = round(((m['price'] - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_WINTERMUTE_V9</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#050505; color:#00ff41; font-family:monospace; padding:15px; }}
            .card {{ border:1px solid #222; padding:15px; background:#0a0a0a; margin-bottom:15px; }}
            .pro-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:10px; font-size:0.7em; color:#444; }}
            .big-pnl {{ font-size:3.5em; text-align:center; color:{'#00ff41' if pnl >=0 else '#ff4444'}; }}
            .log-box {{ height:200px; overflow-y:scroll; font-size:0.7em; color:#666; background:#000; padding:10px; border:1px solid #222; }}
        </style></head>
        <body>
            <div style="max-width:600px; margin:auto;">
                <div class="pro-grid">
                    <div>WHALE: {m['whale_ratio']}x ({m['whale_state']})</div>
                    <div style="text-align:right;">VOLATILITY: {m['volatility']}</div>
                </div>
                
                <div class="card">
                    <div class="big-pnl">{pnl}%</div>
                    <div style="text-align:center; color:#888;">REGIME: {state['current_regime']} ({state['regime_counter']}x)</div>
                </div>

                <div class="card" style="border-left:3px solid #00d4ff;">
                    <b style="font-size:0.7em; color:#00d4ff;">BEGINNER_VIEW:</b><br>
                    <span style="font-size:0.9em; color:#eee;">
                        {"The market is quiet, bot is waiting." if state['current_regime']=="NEUTRAL" else "AI is tracking smart money movement."}
                    </span>
                </div>

                <div class="log-box">{"\n".join(state['logs'])}</div>
            </div>
            <script>setTimeout(()=>location.reload(), 20000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(20)
