import os, json, time, ccxt, requests, threading, math
import numpy as np
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

# --- 1. THE PHYSICS ENGINE (Deterministic & Normalized) ---

class AsymmetryEngine:
    @staticmethod
    def get_z_score(value, history):
        if len(history) < 20: return 0
        arr = np.array(history)
        return (value - np.mean(arr)) / (np.std(arr) + 1e-9)

    @staticmethod
    def get_market_physics():
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        
        # A. NORMALIZED FLOW (Z-Score)
        bids = sum([x[1] for x in ob['bids'][:20]])
        asks = sum([x[1] for x in ob['asks'][:20]])
        raw_flow = bids / asks if asks > 0 else 1.0
        
        state['flow_history'].append(raw_flow)
        if len(state['flow_history']) > CONFIG["WINDOW_SIZE"]: state['flow_history'].pop(0)
        flow_z = AsymmetryEngine.get_z_score(raw_flow, state['flow_history'])

        # B. THE PRESSURE CHAMBER (Accumulator)
        price_delta = abs(price - (state['price_history'][-1] if state['price_history'] else price))
        # If volatility is low, add pressure. If high, drain it.
        compression = 2.0 if price_delta < (price * 0.0001) else -5.0
        state['internal_pressure'] = max(0, min(100, (state['internal_pressure'] * CONFIG["PRESSURE_DECAY"]) + compression))

        state['price_history'].append(price)
        if len(state['price_history']) > CONFIG["WINDOW_SIZE"]: state['price_history'].pop(0)
        
        return price, round(flow_z, 2), round(state['internal_pressure'], 1)

# --- 2. THE AI VERIFIER (Muted Narrative) ---

def verify_hypothesis(price, flow_z, pressure):
    """AI Task: Verify mechanical invalidation. No storytelling."""
    prompt = f"""
    [CRITICAL VERIFICATION]
    Price: {price} | Flow_Z: {flow_z} | Internal_Pressure: {pressure}
    Task: Is there structural asymmetry? 
    JSON: {{"asymmetry": "UPSIDE/DOWNSIDE/NONE", "confidence": 0-100, "invalidation_point": "price level", "logic": "mechanical only"}}
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
        
        # SLOW CLOCK: Only wake AI every 10 mins OR on extreme Physics (Z > 3.0)
        extreme_physics = abs(flow_z) > CONFIG["MIN_ASYMMETRY_Z"]
        timer_hit = state['ticks'] % CONFIG["AI_CHECK_INTERVAL"] == 0
        
        should_verify = extreme_physics or timer_hit or state['is_holding']
        
        intel = {"asymmetry": "NONE", "logic": "Monitoring Physics..."}
        if should_verify:
            intel = verify_hypothesis(price, flow_z, pressure)
            state['market_regime'] = intel['asymmetry']

        # EXECUTION: Only on Physical Energy + AI Verification
        if not state['is_holding'] and pressure > 85 and extreme_physics:
            if intel['asymmetry'] != "NONE" and intel['confidence'] > 90:
                state.update({"is_holding": True, "entry_price": price, "entry_flow_z": flow_z})
                add_log(f"🚀 ASYMMETRY DETECTED ({intel['asymmetry']}): {price} | Flow Z: {flow_z}")

        # HYPOTHESIS EXIT (Wintermute Rule: Exit if the 'Reason' dies)
        elif state['is_holding']:
            # If we bought because Flow_Z was high, and now Flow_Z is negative -> EXIT
            hypothesis_broken = (state['entry_flow_z'] > 0 and flow_z < 0)
            pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2)
            
            if hypothesis_broken or pnl < -1.5 or pnl > 4.0:
                reason = "INVALIDATED" if hypothesis_broken else "SAFETY_RAIL"
                add_log(f"💰 POSITION CLOSED: {pnl}% | Reason: {reason}")
                state.update({"is_holding": False, "entry_price": 0})

        # Dashboard Activity
        msg = f"FLOW_Z: {flow_z} | PRES: {pressure}% | {state['market_regime']} | {'HYPOTHESIS_TEST' if should_verify else 'IDLE'}"
        add_activity(msg)
        save_state(state)
        
    except Exception as e: print(f"V13 Error: {e}")

def add_log(t):
    ts = datetime.now().strftime("%H:%M")
    state["logs"].insert(0, f"[{ts}] {t}"); state["logs"] = state["logs"][:20]

def add_activity(t):
    ts = datetime.now().strftime("%H:%M:%S")
    state["activity"].insert(0, f"[{ts}] {t}"); state["activity"] = state["activity"][:40]

# --- 4. THE ASYMMETRY DASHBOARD ---

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
            .pressure-bar {{ height:10px; background:#111; margin:10px 0; }}
            .pressure-fill {{ height:100%; background:#0f0; width:{state['pressure']}%; }}
            .pnl {{ font-size:3em; text-align:center; color:{'#0f0' if pnl >=0 else '#f00'}; margin:10px 0; }}
            .stream {{ font-size:0.7em; color:#444; height:150px; overflow:scroll; border-top:1px solid #222; padding-top:10px; white-space: pre-wrap; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.7em; color:#666;">
                    <span>REGIME: {state['market_regime']}</span>
                    <span>TICKS: {state['ticks']}</span>
                </div>
                <div class="pressure-bar"><div class="pressure-fill" style="width:{state['internal_pressure']}%"></div></div>
                <div class="pnl">{pnl}%</div>
                <div style="text-align:center; color:#888; font-size:0.8em; margin-bottom:10px;">
                    INTERNAL_PRESSURE: {state['internal_pressure']}%
                </div>
                <div class="stream" id="logBox">{ "\\n".join(state["logs"]) }\\n\\n--- ACTIVITY ---\\n{ "\\n".join(state["activity"]) }</div>
                <button style="width:100%; padding:10px; background:#0f0; color:#000; border:none; font-weight:bold; margin-top:10px;" onclick="copyLogs()">COPY ARCHIVE</button>
            </div>
            <script>
                function copyLogs() {{
                    navigator.clipboard.writeText(document.getElementById('logBox').innerText);
                    alert('Copied');
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
