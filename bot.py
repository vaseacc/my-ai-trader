import os
import json
import ccxt
import google.generativeai as genai

# 1. SETUP: AI & Exchange
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# Connect to MEXC
exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET"),
    'options': {'defaultType': 'spot'}
})

SYMBOL = 'BTC/USDT'
TRADE_AMOUNT_USDT = 10  # How much USDT to use per trade

def get_ai_decision(price):
    """Ask Gemini to think like GCR"""
    prompt = f"""
    You are GCR, the legendary crypto trader. You are known for elite market psychology.
    The current price of Bitcoin (BTC) is {price} USDT.
    
    Based on this price, would you BUY, SELL, or HOLD? 
    Your goal is to be highly selective. Only trade if you are very confident.
    
    Respond ONLY in this JSON format:
    {{
        "action": "BUY", 
        "confidence": 95, 
        "reason": "Explain your logic in 1 sentence"
    }}
    """
    
    response = model.generate_content(prompt)
    
    # Clean the AI response (removes markdown backticks if Gemini adds them)
    text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(text)

def run_bot():
    try:
        print(f"--- Bot starting. Checking {SYMBOL} ---")
        
        # 2. GET MARKET DATA
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = ticker['last']
        print(f"Current Price: {current_price}")

        # 3. GET AI DECISION
        decision = get_ai_decision(current_price)
        print(f"AI Decision: {decision['action']} (Confidence: {decision['confidence']}%)")
        print(f"Reasoning: {decision['reason']}")

        # 4. EXECUTION (High confidence only)
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY":
                print(">>> Placing BUY Order...")
                # To actually trade, remove the '#' from the line below:
                # exchange.create_market_buy_order(SYMBOL, TRADE_AMOUNT_USDT)
                
            elif decision['action'] == "SELL":
                print(">>> Placing SELL Order...")
                # To actually trade, remove the '#' from the line below:
                # exchange.create_market_sell_order(SYMBOL, TRADE_AMOUNT_USDT)
        else:
            print("Confidence too low. No trade today.")

    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    run_bot()
