import os
import json
import time
import ccxt
from groq import Groq

# 1. SETUP: AI & Exchange
# Groq is much more reliable than Gemini for JSON responses
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET"),
    'options': {'defaultType': 'spot'}
})

SYMBOL = 'BTC/USDT'
TRADE_AMOUNT_USDT = 10 

def get_ai_decision(price):
    """Ask Groq Llama-3 to think like GCR"""
    prompt = f"""
    You are GCR, the legendary crypto trader. Bitcoin is currently {price} USDT.
    Based on market psychology, would you BUY, SELL, or HOLD? 
    Respond ONLY in this JSON format:
    {{"action": "BUY", "confidence": 95, "reason": "logic here"}}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-70b-8192", # This is their most powerful model
            response_format={"type": "json_object"} # Forces a JSON response
        )
        return json.loads(chat_completion.choices[0].message.content)
    except Exception as e:
        return {"action": "HOLD", "confidence": 0, "reason": f"Groq Error: {str(e)}"}

def run_bot():
    try:
        # 2. GET PRICE
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = ticker['last']
        
        # 3. GET AI DECISION
        decision = get_ai_decision(current_price)
        print(f"Decision: {decision['action']} ({decision['confidence']}%)")

        # 4. SAVE TO DASHBOARD (data.json)
        status = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": current_price,
            "timestamp": time.ctime()
        }
        with open('data.json', 'w') as f:
            json.dump(status, f)

        # 5. TRADE LOGIC
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY":
                print("Executing BUY...")
                # exchange.create_market_buy_order(SYMBOL, TRADE_AMOUNT_USDT)
            elif decision['action'] == "SELL":
                print("Executing SELL...")
                # exchange.create_market_sell_order(SYMBOL, TRADE_AMOUNT_USDT)

    except Exception as e:
        print(f"Bot error: {e}")

if __name__ == "__main__":
    run_bot()
