import os
import json
import time
import ccxt
import requests
from groq import Groq

# Setup
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({'apiKey': os.getenv("MEXC_API_KEY"), 'secret': os.getenv("MEXC_SECRET")})

def run_elite_cycle():
    # 1. Get News (High speed)
    news = requests.get(f"https://cryptopanic.com/api/v1/posts/?auth_token={os.getenv('CP_API_KEY')}&public=true").json()
    headlines = [p['title'] for p in news['results'][:5]]
    
    # 2. Get Price
    price = exchange.fetch_ticker('BTC/USDT')['last']
    
    # 3. AI Reasoning (Groq is sub-second speed)
    prompt = f"You are GCR. Price: {price}. News: {headlines}. BUY, SELL, or HOLD? JSON ONLY: {{'action': '...', 'confidence': 0-100, 'reason': '...'}}"
    
    completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
        response_format={"type": "json_object"}
    )
    decision = json.loads(completion.choices[0].message.content)
    
    print(f"[{time.ctime()}] Decision: {decision['action']} ({decision['confidence']}%)")
    # ... Execution Logic Here ...

# This is the 24/7 loop
if __name__ == "__main__":
    while True:
        try:
            run_elite_cycle()
        except Exception as e:
            print(f"Error: {e}")
        
        # Check every 30 seconds for "Near Real-Time" performance
        time.sleep(30)
