import os, json, time, ccxt, requests, threading, math
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# --- V11 PHYSICS CONFIG ---
CONFIG = {
    "WINDOW_SIZE": 20,         # Look at last 20 price ticks
    "ENERGY_THRESHOLD": 0.15,  # Low volatility % that triggers "High Energy"
    "PHYSICS_CONFIDENCE": 85,  # Math-based confidence before AI confirms
}

# --- PERSISTENT LEDGER ---
STATE_FILE = "v11_physics_state.json"
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
        "price_history": [], # To calculate rolling Standard Deviation
        "market_phase": "STABLE",
        "logs": [],
        "history": []
    }

state = load_state()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc()

# --- 1. PHYSICS MODULES (The Deterministic Layer) ---

class MarketPhysics:
    @staticmethod
    def get_weighted_imbalance(ob):
        """Multi-tier imbalance: Closer orders matter more"""
        def get_vol(orders):
            # Tier 1 (0-5): weight 1.0 | Tier 2 (5-20): weight 0.5 | Tier 3 (20-50): weight 0.2
            v1 = sum([x[1] for x in orders[:5]]) * 1.0
            v2 = sum([x[1] for x in orders[5:20]]) * 0.5
            v3 = sum([x[1] for x in orders[20:50]]) * 0.2
            return v1 + v2 + v3
        
        bids = get_vol(ob['bids'])
        asks = get_vol(ob['asks'])
        return round(bids / asks, 2) if asks > 0 else 1.0

    @staticmethod
    def get_energy_score(prices):
        """Calculates Energy based on Rolling Volatility (Standard Deviation)"""
        if len(prices) < 10: return 0
        mean = sum(prices) / len(prices)
        variance = sum((x - mean) ** 2 for x in prices) / len(prices)
        stdev_pct = (math.sqrt(variance) / mean) * 100
        # Energy is high when stdev is low
        energy = max(0, 100 - (stdev_pct * 500)) 
        return round(min(energy, 100), 1)

# --- 2. THE COMMAND CENTER ---

def run_cycle():
    global state
    try:
        # A. PERCEPTION
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        ob = exchange.fetch_order_book('BTC/USDT', 50)
        
        # B. UPDATE PHYSICS HISTORY
        state['price_history'].append(price)
        if len(state['price_history']) > CONFIG["WINDOW_SIZE"]:
            state['price_history'].pop(0)
            
        # C. COMPUTE DETERMINISTIC PHYSICS
        imbalance = MarketPhysics.get_weighted_imbalance(ob)
        energy_score = MarketPhysics.get_energy_score(state['price_history'])
        
        trapped_side = "NONE"
        if imbalance > 2.0: trapped_side = "SHORTS"
        if imbalance < 0.5: trapped_side = "LONGS"

        # D. AI EXPLANATION ENGINE
        # We only call AI if Energy > 50 or if we are holding
        if energy_score > 50 or state['is_holding']:
            prompt = f"""
            System: Market Physics Arbitrator. 
            Facts: Price {price} | Imbalance {imbalance}x | Energy {energy_score}% | Trapped: {trapped_side}
            Task: Identify the Forced Unwind path.
            JSON: {{"phase": "...", "logic": "Explain the mechanical inevitability", "conviction": 0-100}}
            """
            chat = client.chat.completions.create(
                messages=[{"role":"user","content":prompt}],
                model="llama-3.3-70b-versatile",
                response_format={"type":"json_object"}
            )
            ai = json.loads(chat.choices[0].message.content)
            state['market_phase'] = ai['phase']
            
            # E. EXECUTION (Math + AI Alignment)
            if not state['is_holding'] and energy_score > 80 and trapped_side != "NONE":
                if ai['conviction'] >= 90:
                    state.update({"is_holding":True, "entry_price":price, "entry_time":time.time()})
                    add_log(f"🚀 FORCED {trapped_side} UNWIND at {price} | {ai['logic']}")

        elif state['is_holding']:
            pnl = round(((price - state['entry_price']) / state['entry_price']) * 100, 2)
            if pnl < -1.5 or pnl > 3.5: # Hard Physics Exit
                add_log(f"💰 RESOLVED: P/L {pnl}%")
                state.update({"is_holding":False, "entry_price":0})

        save_state(state)
        
    except Exception as e: print(f"V11 Error: {e}")

def add_log(txt):
    ts = datetime.now().strftime("%H:%M")
    state["logs"].insert(0, f"[{ts}] {txt}"); state["logs"] = state["logs"][:25]

# --- 3. PHYSICS DASHBOARD ---

class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-type", "text/html"); self.end_headers()
        ticker = exchange.fetch_ticker('BTC/USDT')
        energy = MarketPhysics.get_energy_score(state['price_history'])
        pnl = round(((ticker['last'] - state['entry_price']) / state['entry_price']) * 100, 2) if state['is_holding'] else 0
        
        html = f"""
        <html><head><title>GCR_V11_PHYSICS</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#0f0; font-family:monospace; padding:15px; }}
            .container {{ border:1px solid #0f0; padding:20px; box-shadow:0 0 15px #0f0; max-width:600px; margin:auto; }}
            .gauge-container {{ height:20px; background:#111; border:1px solid #333; margin:10px 0; }}
            .gauge-fill {{ height:100%; background:#0f0; width:{energy}%; transition: width 0.5s; }}
            .pnl {{ font-size:3.5em; text-align:center; color:{'#0f0' if pnl >=0 else '#f00'}; }}
            .log {{ font-size:0.7em; color:#555; height:180px; overflow:scroll; border-top:1px solid #222; padding-top:10px; }}
        </style></head>
        <body>
            <div class="container">
                <div style="display:flex; justify-content:space-between; font-size:0.8em;">
                    <span>PHASE: {state['market_phase']}</span>
                    <span>ENERGY: {energy}%</span>
                </div>
                <div class="gauge-container"><div class="gauge-fill"></div></div>
                
                <div class="pnl">{pnl}%</div>
                
                <div style="font-size:0.8em; color:#888; margin-bottom:10px;">
                    PATH_OF_LEAST_RESISTANCE: { "UP" if MarketPhysics.get_weighted_imbalance(exchange.fetch_order_book('BTC/USDT', 50)) > 1 else "DOWN"}
                </div>
                
                <div class="log">{"\n".join(state['logs'])}</div>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Dashboard).serve_forever(), daemon=True).start()
    while True: run_cycle(); time.sleep(15)
