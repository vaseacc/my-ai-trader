import os
import json
import time
import ccxt
import requests
from groq import Groq

# 1. SETUP
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET"),
})

SYMBOL = 'BTC/USDT'
TRADE_AMOUNT = 10

def get_market_alpha():
    """PERCEPTION: Get news and check for Whale movements"""
    # 1. Get News from CryptoPanic
    news_url = f"https://cryptopanic.com/api/v1/posts/?auth_token={os.getenv('CP_API_KEY')}&public=true"
    news_data = requests.get(news_url).json()
    headlines = [p['title'] for p in news_data['results'][:5]]
    
    # 2. Get Price Action
    ticker = exchange.fetch_ticker(SYMBOL)
    
    return {
        "price": ticker['last'],
        "headlines": " | ".join(headlines),
        "change_24h": ticker['percentage']
    }

def gcr_brain(data):
    """REASONING: The Elite Strategy"""
    prompt = f"""
    You are GCR. Bitcoin is {data['price']} USDT.
    Recent News: {data['headlines']}
    
    TASK: Look for Sentiment Arbitrage. 
    If news is bad but price is stable, BUY. 
    If news is hype but price is flat, SELL.
    Respond ONLY in JSON: {{"action": "BUY/SELL/HOLD", "confidence": 0-100, "reason": "..."}}
    """
    try:
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        return json.loads(chat.choices[0].message.content)
    except:
        return {"action": "HOLD", "confidence": 0}

def main_loop():
    print("Bot is now running 24/7 on Render...")
    while True:
        try:
            # 1. Sense the market
            alpha = get_market_alpha()
            
            # 2. Think like an elite
            decision = gcr_brain(alpha)
            
            # 3. Execute
            print(f"[{time.ctime()}] Price: {alpha['price']} | Decision: {decision['action']} ({decision['confidence']}%)")
            
            if decision['confidence'] >= 95:
                # Actual trading code goes here
                print(f"!!! TRADING: {decision['action']} !!!")
            
            # 4. Update Dashboard (Saves to a file)
            # Note: Render's disk is temporary, so for a permanent dashboard 
            # you'd ideally use a database, but this works for now.
            with open('data.json', 'w') as f:
                json.dump({**decision, "price": alpha['price'], "timestamp": time.ctime()}, f)

            # 5. WAIT: Only wait 30 seconds (Real-time feel)
            time.sleep(30) 
            
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main_loop()
