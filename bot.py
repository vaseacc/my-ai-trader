import os
import json
import time
import ccxt
import requests
import threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- GLOBAL DATA STORAGE ---
latest_status = {
    "action": "Starting...",
    "confidence": 0,
    "reason": "Initial cycle...",
    "price": "Loading...",
    "news": "News key missing (CP_API_KEY)",
    "time": ""
}

# --- 1. THE DASHBOARD SERVER ---
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        # Color coding for the action
        color = "#00ff00" # Green for Buy/Hold
        if latest_status['action'] == "SELL": color = "#ff4444"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>GCR AI AGENT</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: 'Courier New', monospace; background: #0a0a0a; color: white; padding: 20px; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: auto; border: 1px solid {color}; padding: 20px; box-shadow: 0 0 15px {color}; }}
                h1 {{ color: {color}; text-align: center; border-bottom: 1px solid {color}; padding-bottom: 10px; }}
                .stat {{ margin: 15px 0; font-size: 1.1em; }}
                .label {{ color: #888; font-size: 0.8em; text-transform: uppercase; display: block; }}
                .val {{ font-size: 1.3em; font-weight: bold; }}
                .reason {{ color: #00d4ff; font-style: italic; background: #1a1a1a; padding: 15px; border-radius: 5px; margin-top: 20px; }}
                small {{ color: #555; }}
            </style>
            <script>setTimeout(() => location.reload(), 30000);</script>
        </head>
        <body>
            <div class="container">
                <h1>GCR_AI_PRO_V2</h1>
                <div class="stat"><span class="label">Current Action</span><span class="val" style="color:{color}">{latest_status['action']}</span></div>
                <div class="stat"><span class="label">AI Confidence</span><span class="val">{latest_status['confidence']}%</span></div>
                <div class="stat"><span class="label">Live BTC Price</span><span class="val">{latest_status['price']} USDT</span></div>
                <div class="stat"><span class="label">Market Narrative</span><br><small>{latest_status['news']}</small></div>
                <div class="reason">“{latest_status['reason']}”</div>
                <p style="text-align:center"><small>Last Update: {latest_status['time']}</small></p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

def run_dashboard():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    server.serve_forever()

# --- 2. THE IMPROVED TRADING LOGIC ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({'apiKey': os.getenv("MEXC_API_KEY"), 'secret': os.getenv("MEXC_SECRET")})

def get_market_alpha():
    """Separated Price and News so one failure doesn't break both"""
    price = "Error"
    headlines = "No news available (Add CP_API_KEY)"

    # Try to get Price
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        price = ticker['last']
    except Exception as e:
        print(f"Price Fetch Error: {e}")

    # Try to get News
    cp_key = os.getenv('CP_API_KEY')
    if cp_key:
        try:
            news_url = f"https://cryptopanic.com/api/v1/posts/?auth_token={cp_key}&public=true"
            res = requests.get(news_url).json()
            headlines = " | ".join([p['title'] for p in res['results'][:3]])
        except:
            headlines = "News API error"
    
    return price, headlines

def run_cycle():
    global latest_status
    price, news = get_market_alpha()
    
    prompt = f"You are GCR. Bitcoin is {price}. Market News: {news}. Decision? JSON: {{'action': 'BUY/SELL/HOLD', 'confidence': 0-100, 'reason': '...'}}"
    
    try:
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)
        
        latest_status = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": price,
            "news": news,
            "time": time.ctime()
        }
    except Exception as e:
        print(f"Logic Error: {e}")

# --- 3. EXECUTION ---
if __name__ == "__main__":
    threading.Thread(target=run_dashboard, daemon=True).start()
    while True:
        run_cycle()
        time.sleep(30) # High-speed refresh
