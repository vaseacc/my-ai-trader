import os
import json
import ccxt
from openai import OpenAI

# 1. Setup Connections
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
exchange = ccxt.binance({
    'apiKey': os.getenv("EXCHANGE_API_KEY"),
    'secret': os.getenv("EXCHANGE_SECRET"),
})

SYMBOL = 'BTC/USDT'

def trade():
    # 2. Get Market Data
    ticker = exchange.fetch_ticker(SYMBOL)
    price = ticker['last']
    
    # 3. AI Analysis (The "GCR" Persona)
    # In a real version, we would scrape news here too
    prompt = f"You are GCR, the elite trader. BTC price is {price}. Market sentiment is neutral. Should we BUY, SELL, or HOLD? Respond in JSON: {{'action': 'BUY/SELL/HOLD', 'reason': '...'}}"
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={ "type": "json_object" }
    )
    
    decision = json.loads(response.choices[0].message.content)
    print(f"AI Decision: {decision}")

    # 4. Execute (Commented out for safety - uncomment when ready)
    # if decision['action'] == 'BUY':
    #     exchange.create_market_buy_order(SYMBOL, 10) # Buy $10 worth
    # elif decision['action'] == 'SELL':
    #     exchange.create_market_sell_order(SYMBOL, 10)

if __name__ == "__main__":
    trade()
