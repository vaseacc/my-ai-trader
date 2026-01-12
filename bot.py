import os
import json
import time
import ccxt
import requests
import threading
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- GLOBAL DATA STORAGE ---
# This holds the latest bot info so the website can show it
latest_status = {
    "action": "Starting...",
    "confidence": 0,
    "reason": "Wait for first cycle...",
    "price": 0,
    "news": "Loading...",
    "time": ""
}

# --- 1. THE DASHBOARD SERVER ---
class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        # This HTML creates a professional dark-mode dashboard
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>GCR AI AGENT</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: 'Courier New', monospace; background: #0a0a0a; color: #00ff00; padding: 20px; }}
                .container {{ max-width: 600px; margin: auto; border: 1px solid #00ff00; padding: 20px; box-shadow: 0 0 15px #00ff00; }}
                .stat {{ margin: 15px 0; font-size: 1.2em; }}
                .label {{ color: #888; font-size: 0.8em; text-transform: uppercase; }}
                .reason {{ color: #00d4ff; font-style: italic; border-top: 1px solid #333; padding-top: 10px; }}
                .buy {{ background: #004400; padding: 5px; }}
                .sell {{ background: #440000; padding: 5px; }}
            </style>
            <script>setTimeout(() => location.reload(), 30000);</script>
        </head>
        <body>
            <div class="container">
                <h1>GCR_AI_AGENT_V2</h1>
                <div class="stat"><span class="label">Status:</span> <span class="{latest_status['action'].lower()}">{latest_status['action']}</span></div>
                <div class="stat"><span class="label">Confidence:</span> {latest_status['confidence']}%</div>
                <div class="stat"><span class="label">BTC Price:</span> {latest_status['price']} USDT</div>
                <div class="stat"><span class="label">Latest News:</span><br><small>{latest_status['news']}</small></div>
                <div class="reason">{latest_status['reason']}</div>
                <p><small>Last Update: {latest_status['time']}</small></p>
            </div>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

def run_dashboard():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DashboardHandler)
    server.serve_forever()

# --- 2. THE ELITE TRADING LOGIC ---
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({'apiKey': os.getenv("MEXC_API_KEY"), 'secret': os.getenv("MEXC_SECRET")})

def get_market_alpha():
    """Get real news and price"""
    try:
        # Get News from CryptoPanic
        news_url = f"https://cryptopanic.com/api/v1/posts/?auth_token={os.getenv('CP_API_KEY')}&public=true"
        news_res = requests.get(news_url).json()
        headlines = " | ".join([p['title'] for p in news_res['results'][:3]])
        
        price = exchange.fetch_ticker('BTC/USDT')['last']
        return price, headlines
    except:
        return 0, "News feed error"

def run_cycle():
    global latest_status
    price, news = get_market_alpha()
    
    prompt = f"""
    You are GCR. Bitcoin is {price}. Recent News: {news}.
    Analyze like a whale. Respond ONLY in JSON: 
    {{"action": "BUY/SELL/HOLD", "confidence": 0-100, "reason": "..."}}
    """
    
    try:
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)
        
        # Update the Global Status for the Website
        latest_status = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": price,
            "news": news,
            "time": time.ctime()
        }
        
        # REAL TRADE EXECUTION (Optional)
        if decision['confidence'] >= 95:
            print(f"TRADING: {decision['action']}")
            # exchange.create_market_order(...)

    except Exception as e:
        print(f"Error: {e}")

# --- 3. EXECUTION ---
if __name__ == "__main__":
    # Start Dashboard in background
    threading.Thread(target=run_dashboard, daemon=True).start()
    
    # Run the 24/7 Agent
    while True:
        run_cycle()
        time.sleep(60) # Run once every minute to stay within free API limits
