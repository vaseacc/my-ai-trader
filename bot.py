import os
import json
import time
import ccxt
import google.generativeai as genai

# 1. SETUP: AI & Exchange
# These keys are pulled from your GitHub Secrets
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash')

# Connect to MEXC Exchange
exchange = ccxt.mexc({
    'apiKey': os.getenv("MEXC_API_KEY"),
    'secret': os.getenv("MEXC_SECRET"),
    'options': {'defaultType': 'spot'}
})

SYMBOL = 'BTC/USDT'
TRADE_AMOUNT_USDT = 10  # Amount to spend per trade

def get_ai_decision(price):
    """Ask Gemini to think like GCR"""
    prompt = f"""
    You are GCR, the legendary crypto trader. You are known for elite market psychology.
    The current price of Bitcoin (BTC) is {price} USDT.
    
    Analyze the market. Based on this price, would you BUY, SELL, or HOLD? 
    Your goal is to be highly selective. Only trade if you are 90% confident.
    
    Respond ONLY in this JSON format:
    {{
        "action": "BUY", 
        "confidence": 95, 
        "reason": "Explain your logic in 1 sentence"
    }}
    """
    
    try:
        response = model.generate_content(prompt)
        # Clean the AI response (removes markdown backticks if Gemini adds them)
        text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"action": "HOLD", "confidence": 0, "reason": "AI failed to respond."}

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

        # 4. EXECUTION (Only if Confidence is 90% or higher)
        # Note: These are commented out for your safety. 
        # Remove the '#' when you are ready to trade real money.
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY":
                print(">>> AI signaled BUY. Executing...")
                # exchange.create_market_buy_order(SYMBOL, TRADE_AMOUNT_USDT)
                
            elif decision['action'] == "SELL":
                print(">>> AI signaled SELL. Executing...")
                # exchange.create_market_sell_order(SYMBOL, TRADE_AMOUNT_USDT)
        else:
            print("Confidence too low. No trade executed.")

        # 5. SAVE DATA FOR THE WEBSITE DASHBOARD
        status_data = {
            "action": decision['action'],
            "confidence": decision['confidence'],
            "reason": decision['reason'],
            "price": current_price,
            "timestamp": time.ctime() 
        }
        
        with open('data.json', 'w') as f:
            json.dump(status_data, f)
        print("Dashboard data updated.")

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")

if __name__ == "__main__":
    run_bot()
