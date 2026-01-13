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

# --- ALLOWED AI STATES (CRITICAL FIX) ---
ALLOWED_STATES = {
    "ACCUMULATION",
    "DISTRIBUTION",
    "NEUTRAL_NOISE",
    "BREAKOUT_RISK"
}

# --- PERSISTENT STATE ---
STATE_FILE = "trade_state_v7_final.json"

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except:
        pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "global_start": time.time(),
        "is_holding": False,
        "entry_price": 0,
        "entry_time": 0,
        "daily_trade_count": 0,
        "last_trade_date": "",
        "prev_market_state": "NEUTRAL_NOISE",
        "state_counter": 0,
        "cached_ai": {
            "market_state": "NEUTRAL_NOISE",
            "news_bias": "IGNORE",
            "reason": "Initializing..."
        },
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
        bids = sum(x[1] for x in ob['bids'])
        asks = sum(x[1] for x in ob['asks'])
        ratio = bids / asks if asks > 0 else 1
        if ratio > 1.8:
            return "STRONG_BUY_SUPPORT"
        if ratio < 0.55:
            return "STRONG_SELL_PRESSURE"
        return "NEUTRAL_NOISE"
    except:
        return "NEUTRAL_NOISE"

def update_macro_news():
    if time.time() - state["last_news_time"] < NEWS_COOLDOWN:
        return state["cached_news"]

    keywords = ["btc", "bitcoin", "fed", "rate", "etf", "sec", "inflation", "powell"]
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}"
        res = requests.get(url).json()
        filtered = [
            n['title'] for n in res['Data']
            if any(k in n['title'].lower() for k in keywords)
        ]
        state["cached_news"] = " | ".join(filtered[:3]) if filtered else "No major news."
        state["last_news_time"] = time.time()
        return state["cached_news"]
    except:
        return state["cached_news"]

# --- 2. COMMAND CENTER ---

def add_to_log(text):
    ts = datetime.now().strftime("%H:%M:%S")
    state["logs"].insert(0, f"[{ts}] {text}")
    state["logs"] = state["logs"][:30]
    save_state(state)

def run_cycle():
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        whale_state = get_whale_regime()
        news_data = update_macro_news()

        # --- AI CLASSIFICATION ---
        if time.time() - state["last_ai_time"] > AI_COOLDOWN:
            prompt = f"""
You are a strict market state classifier.

You MUST output valid JSON.
You MUST choose market_state from ONLY this list:
ACCUMULATION, DISTRIBUTION, NEUTRAL_NOISE, BREAKOUT_RISK

No other words are allowed.

Inputs:
Price: {price}
Whale Activity: {whale_state}
News: {news_data}

Output format:
{{"market_state":"...", "news_bias":"RISK_ON | RISK_OFF | IGNORE", "reason":"short explanation"}}
"""

            chat = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile",
                response_format={"type": "json_object"}
            )

            ai_resp = json.loads(chat.choices[0].message.content)
            ai_state = ai_resp.get("market_state", "NEUTRAL_NOISE")

            # --- HARD SAFETY ---
            if ai_state == "NEUTRAL":
                ai_state = "NEUTRAL_NOISE"

            if ai_state not in ALLOWED_STATES:
                ai_state = "NEUTRAL_NOISE"
                ai_resp["reason"] = "Invalid AI label → forced NEUTRAL_NOISE"

            ai_resp["market_state"] = ai_state

            if ai_state == state["prev_market_state"]:
                state["state_counter"] += 1
            else:
                add_to_log(f"TRANSITION: {state['prev_market_state']} -> {ai_state}")
                state["state_counter"] = 1
                state["prev_market_state"] = ai_state

            state["cached_ai"] = ai_resp
            state["last_ai_time"] = time.time()

        m_state = state["cached_ai"]["market_state"]
        stable = state["state_counter"] >= MIN_STATE_STABILITY

        # --- DAILY RESET ---
        today = datetime.now().strftime("%Y-%m-%d")
        if state["last_trade_date"] != today:
            state["daily_trade_count"] = 0
            state["last_trade_date"] = today

        # --- ENTRY LOGIC ---
        should_buy = (
            not state["is_holding"]
            and m_state == "ACCUMULATION"
            and stable
            and whale_state == "STRONG_BUY_SUPPORT"
            and state["cached_ai"]["news_bias"] != "RISK_OFF"
        )

        if should_buy and state["daily_trade_count"] < MAX_TRADES_PER_DAY:
            state.update({
                "is_holding": True,
                "entry_price": price,
                "entry_time": time.time(),
                "daily_trade_count": state["daily_trade_count"] + 1
            })
            add_to_log(f"🚀 BUY: {price} | {state['cached_ai']['reason']}")

        # --- EXIT LOGIC ---
        elif state["is_holding"]:
            mins_open = (time.time() - state["entry_time"]) / 60
            pnl = round(((price - state["entry_price"]) / state["entry_price"]) * 100, 2)
            exit_signal = (
                m_state == "DISTRIBUTION"
                or whale_state == "STRONG_SELL_PRESSURE"
                or pnl < -2.0
                or mins_open > MAX_POSITION_MINUTES
            )

            if exit_signal:
                reason = "SIGNAL" if m_state == "DISTRIBUTION" else "TIME/SL"
                add_to_log(f"💰 EXIT: {reason} | P/L: {pnl}%")
                state["history"].insert(0, {
                    "entry": state["entry_price"],
                    "exit": price,
                    "pnl": pnl,
                    "reason": reason,
                    "date": datetime.now().strftime("%d %b")
                })
                state.update({
                    "is_holding": False,
                    "entry_price": 0,
                    "entry_time": 0
                })

        save_state(state)

    except Exception as e:
        print("Cycle Error:", e)

# --- 3. DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0

        whale_map = {
            "STRONG_BUY_SUPPORT": "Whales are supporting price (Bullish)",
            "STRONG_SELL_PRESSURE": "Whales are selling (Bearish)",
            "NEUTRAL_NOISE": "Quiet"
        }

        market_map = {
            "ACCUMULATION": "Smart money accumulating",
            "DISTRIBUTION": "Selling pressure rising",
            "BREAKOUT_RISK": "High volatility risk",
            "NEUTRAL_NOISE": "Messy and unpredictable"
        }

        html = f"""
        <html><body style="background:#000;color:#00ff41;font-family:monospace;padding:20px;">
        <h2>GCR_V7_FINAL</h2>
        <h1>{pnl}%</h1>
        <p>{'HOLDING BTC' if state['is_holding'] else 'WAITING FOR A+ SETUP'}</p>
        <hr>
        <b>Whales:</b> {whale_map[get_whale_regime()]}<br>
        <b>Market:</b> {market_map[state['prev_market_state']]}<br>
        <b>News:</b> {state['cached_news'][:100]}<br>
        <hr>
        <pre>{"\n".join(state["logs"])}</pre>
        </body></html>
        """
        self.wfile.write(html.encode())

# --- MAIN ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(),
        daemon=True
    ).start()

    while True:
        run_cycle()
        time.sleep(20)
