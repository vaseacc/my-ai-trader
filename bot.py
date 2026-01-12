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
exchange = ccxt.mexc() # No API keys needed for public price data!

# --- VIRTUAL (PAPER) PORTFOLIO ---
paper_trade = {
    "is_holding": False,
    "entry_price": 0,
    "current_pnl": 0,
    "total_trades": 0,
    "history": [] # Stores past trades
}

latest_status = {"action": "SCANNING", "confidence": 0, "reason": "Initializing...", "price": "0", "alpha": "", "time": ""}

# --- 1. THE TRADING ENGINE (PAPER MODE) ---
def execute_paper_trade(action, current_price):
    global paper_trade
    if action == "BUY" and not paper_trade["is_holding"]:
        paper_trade["is_holding"] = True
        paper_trade["entry_price"] = current_price
        paper_trade["total_trades"] += 1
        print(f"PAPER BUY executed at {current_price}")
        
    elif action == "SELL" and paper_trade["is_holding"]:
        # Record final P/L before clearing
        final_pnl = ((current_price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100
        paper_trade["history"].append(final_pnl)
        paper_trade["is_holding"] = False
        paper_trade["entry_price"] = 0
        print(f"PAPER SELL executed at {current_price}. Final P/L: {round(final_pnl, 2)}%")

def update_live_pnl(current_price):
    global paper_trade
    if paper_trade["is_holding"]:
        pnl = ((current_price - paper_trade["entry_price"]) / paper_trade["entry_price"]) * 100
        paper_trade["current_pnl"] = round(pnl, 2)
    else:
        paper_trade["current_pnl"] = 0

# --- 2. ALPHA PERCEPTION (Order Flow + News) ---
def get_order_flow():
    try:
        ob = exchange.fetch_order_book('BTC/USDT', 20)
        bids = sum([b[1] for b in ob['bids']])
        asks = sum([a[1] for a in ob['asks']])
        if bids > asks: return f"Whale Support: {round(bids/asks, 1)}x"
        return f"Whale Pressure: {round(asks/bids, 1)}x"
    except: return "Flow Syncing..."

def run_cycle():
    global latest_status
    try:
        # A. Live Market Data
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
        
        # B. Update Paper Stats
        update_live_pnl(price)
        flow = get_order_flow()
        
        # C. Fast News (CryptoCompare)
        news_res = requests.get(f"https://min-api.cryptocompare.com/data/v2/news/?lang=EN&api_key={os.getenv('CRYPTOCOMPARE_KEY')}").json()
        news = " | ".join([n['title'] for n in news_res['Data'][:2]])

        # D. GCR Logic
        prompt = f"""
        You are GCR. Live Price: {price}. Whale Flow: {flow}. News: {news}.
        Decide: BUY, SELL, or HOLD. 
        Respond ONLY in JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}
        """
        
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)

        # E. Logic Execution (Paper Mode)
        if decision['confidence'] >= 95:
            execute_paper_trade(decision['action'], price)

        latest_status = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": price,
            "alpha": flow,
            "time": time.ctime()
        }

    except Exception as e:
        print(f"Cycle Error: {e}")

# --- 3. THE LIVE DASHBOARD (Showing Paper Profits) ---
class Dashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        pnl_color = "#00ff41" if paper_trade["current_pnl"] >= 0 else "#ff4444"
        holding_status = "HOLDING BTC" if paper_trade["is_holding"] else "WAITING FOR ENTRY"
        
        html = f"""
        <html><body style="background:#000;color:#00ff41;font-family:monospace;padding:20px;line-height:1.5;">
            <div style="border:1px solid #00ff41;padding:25px;box-shadow:0 0 20px #00ff41;max-width:600px;margin:auto;">
                <h2 style="text-align:center;letter-spacing:2px;">GCR_PAPER_AGENT_V3</h2>
                <div style="background:#111;padding:20px;border:1px solid #333;margin:20px 0;">
                    <p style="margin:0;color:#888;font-size:0.8em;">LIVE PAPER PERFORMANCE</p>
                    <p style="font-size:2.5em;margin:10px 0;color:{pnl_color};">{paper_trade['current_pnl']}%</p>
                    <p style="margin:0;font-size:1em;color:#00d4ff;">{holding_status}</p>
                    <hr style="border:0.1px solid #222;">
                    <p style="margin:5px 0;font-size:0.8em;">Entry Price: {paper_trade['entry_price']} USDT</p>
                    <p style="margin:5px 0;font-size:0.8em;">Current Price: {latest_status['price']} USDT</p>
                    <p style="margin:5px 0;font-size:0.8em;">Total Trades: {paper_trade['total_trades']}</p>
                </div>
                <p><span style="color:#888;">AI_DECISION:</span> {latest_status['action']} ({latest_status['confidence']}%)</p>
                <p><span style="color:#888;">WHALE_INTENT:</span> {latest_status['alpha']}</p>
                <p style="color:#ffcc00;border-left:3px solid #ffcc00;padding-left:10px;">"{latest_status['reason']}"</p>
                <small style="color:#444;">LAST_UPDATE: {latest_status['time']}</small>
            </div>
            <script>setTimeout(()=>location.reload(), 15000);</script>
        </body></html>
        """
        self.wfile.write(html.encode())

if __name__ == "__main__":
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), Dashboard).serve_forever(), daemon=True).start()
    while True:
        run_cycle()
        time.sleep(15)
