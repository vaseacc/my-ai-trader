import os
import json
import time
import ccxt
import requests
import threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- SETUP ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({'apiKey': os.getenv("MEXC_API_KEY"), 'secret': os.getenv("MEXC_SECRET")})

latest_status = {"action": "WATCHING", "confidence": 0, "reason": "Syncing Flow...", "price": "0", "alpha": "Scan Start", "time": ""}

# --- 1. FREE ALPHA: THE ORDER BOOK WHALE DETECTOR ---
def get_order_flow_alpha():
    """Detects 'Whale Walls' on the order book for FREE"""
    try:
        # We look at the top 50 orders on the book
        limit = 50
        orderbook = exchange.fetch_order_book('BTC/USDT', limit)
        
        # Calculate the total 'weight' of buy vs sell orders
        # If one side is 3x larger than the other, a Whale is present
        bids_vol = sum([bid[1] for bid in orderbook['bids']]) # Buy orders
        asks_vol = sum([ask[1] for ask in orderbook['asks']]) # Sell orders
        
        if bids_vol > asks_vol * 1.5:
            return f"WHALE SUPPORT: Buy Walls are {round(bids_vol/asks_vol, 1)}x stronger than Sell Walls."
        elif asks_vol > bids_vol * 1.5:
            return f"WHALE PRESSURE: Sell Walls are {round(asks_vol/bids_vol, 1)}x stronger than Buy Walls."
        else:
            return "Order flow is balanced. No major whale walls."
    except:
        return "Order Book Error"

def get_instant_news():
    """High-speed free news feed (Seconds delay only)"""
    cc_key = os.getenv("CRYPTOCOMPARE_KEY")
    if not cc_key: return "News Key Missing"
    try:
        url = f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={cc_key}"
        res = requests.get(url).json()
        return " | ".join([n['title'] for n in res['Data'][:3]])
    except:
        return "News Feed Offline"

# --- 2. THE GCR REASONING ENGINE ---
def run_cycle():
    global latest_status
    try:
        # A. Perception
        price = exchange.fetch_ticker('BTC/USDT')['last']
        whale_intent = get_order_flow_alpha()
        fast_news = get_instant_news()

        # B. Reasoning
        prompt = f"""
        You are GCR. Bitcoin Price: {price} USDT.
        ORDER FLOW ALPHA (Real-time Intent): {whale_intent}
        LATEST NEWS HEADLINES: {fast_news}
        
        STRATEGY: 
        1. If News is BULLISH but WHALE PRESSURE (Sell Walls) is high, it is a TRAP. SELL.
        2. If News is BEARISH but WHALE SUPPORT (Buy Walls) is high, the bottom is in. BUY.
        3. Only trade if Order Flow Divergence is clear.
        
        Respond ONLY in JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}
        """
        
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(completion.choices[0].message.content)

        latest_status = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": price,
            "alpha": whale_intent,
            "time": time.ctime()
        }
        
        # C. Trade Execution
        if decision['confidence'] >= 95:
            print(f"!!! GCR ALPHA TRADE: {decision['action']} !!!")
            # exchange.create_market_order(...)

    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 3. LIVE DASHBOARD ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        html = f"""
        <html><body style="background:#050505;color:#00ff41;font-family:monospace;padding:40px;">
            <div style="border:1px solid #00ff41;padding:30px;box-shadow:0 0 30px #00ff41;max-width:700px;margin:auto;">
                <h1 style="text-align:center;">GCR_AGENT_ORDER_FLOW_V3</h1>
                <hr style="border:0.5px solid #00ff41;">
                <p style="font-size:1.5em;">ACTION: <span style="background:#003300;padding:5px;">{latest_status['action']}</span></p>
                <p>CONFIDENCE: {latest_status['confidence']}%</p>
                <p>BTC_PRICE: {latest_status['price']} USDT</p>
                <p style="color:#00d4ff;">INTENT: {latest_status['alpha']}</p>
                <p style="color:#ffcc00;border-left:4px solid #ffcc00;padding-left:15px;line-height:1.6;">{latest_status['reason']}</p>
                <hr style="border:0.5px solid #00ff41;">
                <small>LAST_TICK: {latest_status['time']}</small>
            </div>
            <script>setTimeout(()=>location.reload(), 10000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(10) # 10-second check for ultra-fast reaction
