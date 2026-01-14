import os, json, time, ccxt, requests, threading, math
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- V13 ASYMMETRY CONFIG ---
CONFIG = {
    "WINDOW_SIZE": 60,         # 15 mins of data at 15s intervals
    "AI_CHECK_INTERVAL": 40,   # Only call AI every 40 ticks (~10 mins)
    "PRESSURE_DECAY": 0.95,    # Pressure naturally leaks over time
    "MIN_ASYMMETRY_Z": 2.5,    # Must be 2.5 standard deviations from normal
}

# --- PERSISTENT STATE ---
STATE_FILE = "v13_asymmetry_state.json"
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
        "entry_flow_z": 0,
        "internal_pressure": 0,
        "flow_history": [],
        "price_history": [],
        "ticks": 0,
        "market_regime": "EQUILIBRIUM",
        "logs": [],
        "activity": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. THE PHYSICS ENGINE (Pure Python Math) ---

class AsymmetryEngine:
    @staticmethod
    def calculate_z_score(value, history):
        """Calculates Z-Score without needing Numpy"""
        if len(history) < 10: return 0
        n = len(history)
        mean = sum(history) / n
        variance = sum((x - mean) ** 2 for x in history) / n
        stdev = math.sqrt(variance)
        if stdev == 0: return 0
        return (value - mean) / stdev

    @staticmethod
    def get_market_physics():
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        
        # A. NORMALIZED FLOW
        bids = sum([x[1] for x in ob['bids'][:20]])
        asks = sum([x[1] for x in ob['asks'][:20]])
        raw_flow = bids / asks if asks > 0 else 1.0
        
        flow_z = AsymmetryEngine.calculate_z_score(raw_flow, state['flow_history'])
        state['flow_history'].append(raw_flow)
        if len(state['flow_history']) > CONFIG["WINDOW_SIZE"]: state['flow_history'].pop(0)

        # B. THE PRESSURE CHAMBER
        # If price stays within 0.01% of last price, add pressure
        last_p = state['price_history'][-1] if state['price_history'] else price
        price_delta = abs(price - last_p) / last_p
        
        compression = 2.0 if price_delta < 0.0001 else -5.0
        state['internal_pressure'] = max(0, min(100, (state['internal_pressure'] * CONFIG["PRESSURE_DECAY"]) + compression))

        state['price_history'].append(price)
        if len(state['price_history']) > CONFIG["WINDOW_SIZE"]: state['price_history'].pop(0)
        
        return price, round(flow_z, 2), round(state['internal_pressure'], 1)

# --- 2. THE AI VERIFIER ---

def verify_hypothesis(price, flow_z, pressure):
    prompt = f"""
    [CRITICAL VERIFICATION]
    Price: {price} | Flow_Z: {flow_z} | Internal_Pressure: {pressure}
    Task: Is there structural asymmetry? Respond ONLY in valid JSON.
    {{
      "asymmetry": "UPSIDE/DOWNSIDE/NONE", 
      "confidence": 0-100, 
      "logic": "mechanical only"
    }}
    """
    try:
        chat = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type":"json_object"}
        )
        return json.loads(chat.choices[0].message.content)
    except: return {"asymmetry": "NONE", "confidence": 0}

# --- 3. MASTER CONTROL LOOP ---

def run_cycle():
    global state
    state['ticks'] += 1
    try:
        price, flow_z, pressure = AsymmetryEngine.get_market_physics()
        
        # WAKE GATES
        extreme_physics = abs(flow_z) > CONFIG["MIN_ASYMMETRY_Z"]
        timer_hit = state['ticks'] % CONFIG["AI_CHECK_INTERVAL"] == 0
        should_verify = extreme_physics or timer_hit or state['is_holding']
        
        intel = {"asymmetry": "NONE", "logic": "Monitoring Physics..."}
        if should_verify:
            intel = verify_hypothesis(price, flow_z, pressure)
            state['market_regime'] = intel['asymmetry']

        # EXECUTION ENGINE
        if not state['is_holding'] and pressure > 85 and extreme_physics:
            if intel['asymmetry'] != "NONE" and intel.get('confidence', 0) > 90:
                state.update({"is_holding": True, "entry_price": price, "entry_flow_z": flow_z})
                add_log(f"🚀 ASYMMETRY: {intel['asymmetry']} at {price} (Z: {flow_z})")

        # HYPOTHESIS VALIDATION (The Wintermute Exit)
        elif state['is_holding']:
            # Exit if the Whale Wall (Flow Z) flips direction
            hypothesis_dead = (state['entry_flow_z'] > 0 and flow_z < 0) or (state['entry_flow_z'] < 0 and flow_z > 0)
            pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2)
            
            if hypothesis_dead or pnl < -1.5 or pnl > 4.0:
                reason = "INVALIDATED" if hypothesis_dead else "LIMIT"
                add_log(f"💰 EXIT: {pnl}% | {reason}")
                state.update({"is_holding": False, "entry_price": 0})

        add_activity(f"Z:{flow_z} | P:{pressure}% | {state['market_regime']} | {'AI_WOKE' if should_verify else 'IDLE'}")
        save_state(state)
        
    except Exception as e: print(f"V13 Error: {e}")

def add_log(t):
    ts = datetime.now().strftime("%H:%M")
    state["logs"].insert(0, f"[{ts}] {t}"); state["logs"] = state["logs"][:20]

def add_activity(t):
    ts = datetime.now().strftime("%H:%M:%S")
    state["activity"].insert(0, f"[{ts}] {t}"); state["activity"] = state["activity"][:40]

# --- 4. THE DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        pnl = round(((state['price_history'][-1] - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_V13_ASYMMETRY</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:600px; margin:auto; }}
            .pressure-bar {{ height:10px; background:#111; margin:10px 0; border:1px solid #333; }}
            .pressure-fill {{ height:100%; background:#0f0; width:{state['internal_pressure']}%; transition: width 0.5s; }}
            .pnl {{ font-size:3.5em; text-align:center; color:{'#0f0' if pnl >=0 else '#f00'}; margin:10px 0; }}
            .stream {{ font-size:0.7em; color:#444; height:200px; overflow:scroll; border-top:1px solid #222; padding-top:10px; white-space: pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#444;">
                    <span>REGIME: {state['market_regime']}</span>
                    <span>TICKS: {state['ticks']}</span>
                </div>
                <div class="pressure-bar"><div class="pressure-fill"></div></div>
                <div class="pnl">{pnl}%</div>
                <div style="text-align:center; color:#888; font-size:0.8em; margin-bottom:10px;">
                    INTERNAL_PRESSURE: {state['internal_pressure']}%
                </div>
                <div class="stream" id="logBox">{ "\\n".join(state["logs"]) }\\n\\n--- ACTIVITY ---\\n{ "\\n".join(state["activity"]) }</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(15)
