import os
import json
import asyncio
import ccxt.pro as ccxt  # Note the '.pro' - this is for WebSockets
from groq import Groq
from datetime import datetime

# 1. SETUP
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET"),
})

SYMBOL = 'BTC/USDT'

async def gcr_brain(price, news_headline):
    """The Logic of the Elites: Immediate reaction to news"""
    print(f"[{datetime.now()}] ANALYZING NEWS: {news_headline}")
    
    prompt = f"""
    You are GCR. You trade instant news. 
    Price: {price}
    News: {news_headline}
    
    TASK: If this news is a 'Market Mover' (like an ETF approval, a hack, or a billionaire tweet), trade immediately.
    If it is noise, HOLD.
    
    Respond ONLY in JSON:
    {{"action": "BUY" | "SELL" | "HOLD", "confidence": 0-100, "reason": "..."}}
    """
    
    try:
        # We use the 8b model here because it's nearly INSTANT (sub-500ms)
        chat = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192", 
            response_format={"type": "json_object"}
        )
        return json.loads(chat.choices[0].message.content)
    except:
        return {"action": "HOLD"}

async def price_loop():
    """STREAMS live prices with zero delay"""
    while True:
        ticker = await exchange.watch_ticker(SYMBOL)
        # This updates every time the price moves 1 cent
        yield ticker['last']

async def news_loop():
    """STREAMS live news (Mocking a high-speed feed)"""
    # In a real setup, you'd connect to a WebSocket news service here
    # Pro traders use 'Tree News' or 'The Block Pro'
    while True:
        await asyncio.sleep(10) # Checking for new headlines every few seconds
        yield "ELON MUSK TWEETS ABOUT BITCOIN PAYMENT" # Example trigger

async def main():
    print("Agent Active. Listening for Market Alpha...")
    
    # We run the price and news streams simultaneously
    async for price in price_loop():
        # This is where the magic happens. 
        # When a news event is detected, the AI is called instantly.
        # For this demo, we check a news condition:
        headline = "FED RAISES INTEREST RATES" # This would come from your news_loop
        
        decision = await gcr_brain(price, headline)
        
        if decision['confidence'] > 95:
            print(f"!!! GCR SIGNAL DETECTED: {decision['action']} !!!")
            # exchange.create_market_order(...) 
            break # Exit loop after a high-conviction trade

if __name__ == "__main__":
    asyncio.run(main())
