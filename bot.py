import os
import json
import time
import ccxt
import requests
from groq import Groq
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# 1. THE HEARTBEAT SERVER (Required for Koyeb)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is Awake")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# 2. THE TRADING LOGIC
# Use os.getenv and handle the error if it's missing
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("CRITICAL ERROR: GROQ_API_KEY is missing!")

client = Groq(api_key=api_key)
exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET")
})

def run_elite_cycle():
    print("Checking market...")
    try:
        # Get Price
        price = exchange.fetch_ticker('BTC/USDT')['last']
        
        # AI Decision
        prompt = f"You are GCR. BTC is {price}. News is bullish. BUY, SELL, or HOLD? JSON ONLY: {{'action': '...', 'confidence': 0-100, 'reason': '...'}}"
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        decision = json.loads(chat.choices[0].message.content)
        print(f"Decision: {decision['action']} ({decision['confidence']}%)")
        
        # (Trading logic goes here)

    except Exception as e:
        print(f"Cycle Error: {e}")

# 3. START EVERYTHING
if __name__ == "__main__":
    # Start the health check in the background
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # Start the 24/7 trading loop
    while True:
        run_elite_cycle()
        time.sleep(30) # High-speed check every 30 seconds
