import os, json, time, ccxt, requests, threading, math
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- V12 ENTROPY CONFIG ---
CONFIG = {
    "PRESSURE_ACCUMULATION_RATE": 5,  # How fast energy builds in compression
    "PRESSURE_LEAK_RATE": 10,        # How fast energy drops in volatility
    "VOLATILITY_THRESHOLD": 0.05,    # % change considered "Stable"
    "STABILITY_REQUIRED": 4,         # Must be unstable for 4 cycles to trade
    "PHYSICS_CONFIDENCE": 92         # High bar for entry
}

# --- PERSISTENT STATE ---
STATE_FILE = "trade_state_v12_entropy.json"

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
        "pressure": 0,       # Slow-moving accumulator
        "last_price": 0,
        "last_imbalance": 1.0,
        "last_ai_trigger_pressure": 0,
        "market_state": "DELTA_NEUTRAL",
        "logs": [],
        "activity_stream": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. THE PHYSICS OF ENTROPY ---

class EntropyEngine:
    @staticmethod
    def get_market_physics():
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        
        # A. MULTI-TIER IMBALANCE
        bids = sum([x[1] for x in ob['bids'][:10]]) * 1.2 + sum([x[1] for x in ob['bids'][10:30]]) * 0.5
        asks = sum([x[1] for x in ob['asks'][:10]]) * 1.2 + sum([x[1] for x in ob['asks'][10:30]]) * 0.5
        imbalance = bids / asks if asks > 0 else 1.0

        # B. PRESSURE ACCUMULATOR (Slow moving)
        price_delta = abs(price - state['last_price']) / (state['last_price'] or price) * 100
        if price_delta < CONFIG["VOLATILITY_THRESHOLD"]:
            state['pressure'] = min(100, state['pressure'] + CONFIG["PRESSURE_ACCUMULATION_RATE"])
        else:
            state['pressure'] = max(0, state['pressure'] - CONFIG["PRESSURE_LEAK_RATE"])

        # C. ENTROPY DETECTOR (Detect Surprise)
        # Did the order book move way more than the price?
        imbalance_delta = abs(imbalance - state['last_imbalance'])
        entropy_spike = imbalance_delta > 1.5 and price_delta < 0.02 # Big wall, no price move
        
        return price, round(imbalance, 2), state['pressure'], entropy_spike

# --- 2. THE COMMAND CENTER ---

def run_cycle():
    global state
    try:
        price, imbalance, pressure, entropy_spike = EntropyEngine.get_market_physics()
        
        # --- AI GATING (Only speak if necessary) ---
        whale_flipped = (imbalance > 1.8 and state['last_imbalance'] < 1.8) or (imbalance < 0.5 and state['last_imbalance'] > 0.5)
        pressure_jump = abs(pressure - state['last_ai_trigger_pressure']) >= 15
        
        should_call_ai = pressure_jump or whale_flipped or entropy_spike or state['is_holding']
        
        ai_decision = {"action": "HOLD", "conviction": 0, "reason": "System Muted - No Structural Change"}

        if should_call_ai:
            prompt = f"""
            System: Market Physics Arbitrator. 
            Facts: Price {price} | Imbalance {imbalance}x | Pressure {pressure}% | Entropy {entropy_spike}
            Task: Identify TRAPPED participants and FORCED UNWIND path.
            JSON: {{"action": "BUY/SELL/HOLD", "phase": "LIQUIDITY_PINNED/LEVERAGE_SKEW/UNWIND", "trapped": "LONGS/SHORTS/NONE", "conviction": 0-100, "logic": "..."}}
            """
            chat = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", response_format={"type":"json_object"})
            ai_decision = json.loads(chat.choices[0].message.content)
            state['last_ai_trigger_pressure'] = pressure
            state['market_state'] = ai_decision['phase']

        # --- EXECUTION (HARD GATES) ---
        if not state['is_holding'] and pressure > 80 and ai_decision['conviction'] >= CONFIG["PHYSICS_CONFIDENCE"]:
            if ai_decision['trapped'] != "NONE":
                state.update({"is_holding": True, "entry_price": price})
                add_to_log(f"🚀 FORCED {ai_decision['trapped']} UNWIND: {price} | {ai_decision['logic']}")

        # --- LOGGING ---
        activity = f"IMB: {imbalance}x | PRE: {pressure}% | {state['market_state']} | {'AI_WOKE' if should_call_ai else 'AI_MUTED'}"
        add_to_activity(activity)

        state['last_price'] = price
        state['last_imbalance'] = imbalance
        save_state(state)
        
    except Exception as e: print(f"V12 Error: {e}")

def add_to_log(t):
    ts = datetime.now().strftime("%H:%M:%S")
    state["logs"].insert(0, f"[{ts}] {t}"); state["logs"] = state["logs"][:20]; save_state(state)

def add_to_activity(t):
    ts = datetime.now().strftime("%H:%M:%S")
    state["activity_stream"].insert(0, f"[{ts}] {t}"); state["activity_stream"] = state["activity_stream"][:50]; save_state(state)

# --- 3. THE DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        uptime = f"{int((time.time() - SESSION_START_TIME)/60)}m"
        pnl = round(((state['last_price'] - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_V12_ENTROPY</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:650px; margin:auto; }}
            .pressure-bar {{ height:15px; background:#111; border:1px solid #333; margin:10px 0; overflow:hidden; }}
            .pressure-fill {{ height:100%; background:#0f0; width:{state['pressure']}%; transition: width 1s; }}
            .log {{ font-size:0.75em; color:#555; background:#050505; border:1px solid #111; padding:10px; height:200px; overflow:scroll; margin-top:10px; white-space: pre-wrap; }}
            .pnl {{ font-size:3em; text-align:center; color:{'#0f0' if pnl >=0 else '#f00'}; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444;">
                    <span>STATE: {state['market_state']}</span>
                    <span>SESSION: {uptime}</span>
                </div>
                <div class="pressure-bar"><div class="pressure-fill"></div></div>
                <div class="pnl">{pnl}%</div>
                <div style="text-align:center; font-size:0.7em; color:#888;">PRICE: {state['last_price']} | IMBALANCE: {state['last_imbalance']}x</div>
                <button style="width:100%; padding:10px; background:#0f0; border:none; font-weight:bold; margin-top:10px;" onclick="copyLogs()">COPY ALL LOGS</button>
                <div class="log" id="logBox">{ "\\n".join(state["logs"]) }\\n\\n--- ACTIVITY STREAM ---\\n{ "\\n".join(state["activity_stream"]) }</div>
            </div>
            <script>
                function copyLogs() {{
                    const t = document.getElementById('logBox').innerText;
                    navigator.clipboard.writeText(t);
                    alert('Copied.');
                }}
                setTimeout(()=>location.reload(), 15000);
            </script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(15)
