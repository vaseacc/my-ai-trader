import os
import json
import time
import ccxt
import traceback
import google.generativeai as genai

# 1. SETUP: AI & Exchange
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    # Using 1.5-flash which is the most reliable free-tier model
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    exchange = ccxt.mexc({
        'apiKey': os.getenv("MEXC_API_KEY"),
        'secret': os.getenv("MEXC_SECRET"),
        'options': {'defaultType': 'spot'}
    })
except Exception as e:
    print(f"INITIALIZATION ERROR: {e}")

SYMBOL = 'BTC/USDT'
TRADE_AMOUNT_USDT = 10 

def get_ai_decision(price):
    """Ask Gemini to think like GCR with safety filters disabled"""
    print(f"Calling Google AI for price: {price}...")
    
    prompt = f"""
    You are GCR, the legendary crypto trader. The current price of Bitcoin is {price} USDT.
    Based on market psychology, would you BUY, SELL, or HOLD? 
    Respond ONLY in this JSON format:
    {{"action": "BUY", "confidence": 95, "reason": "logic here"}}
    """
    
    try:
        # Safety settings to prevent Google from blocking "Financial Advice"
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        response = model.generate_content(prompt, safety_settings=safety_settings)
        
        # DEBUG: Print the raw response to GitHub logs
        print(f"Raw AI Response: {response.text}")
        
        # Clean the AI response
        text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(text)
        
    except Exception as e:
        error_msg = f"AI Error: {str(e)}"
        print(error_msg)
        # This prints the full error "stack trace" to your GitHub logs
        traceback.print_exc() 
        return {"action": "HOLD", "confidence": 0, "reason": error_msg}

def run_bot():
    current_price = "Unknown"
    decision = {"action": "ERROR", "confidence": 0, "reason": "Bot failed to run."}
    
    try:
        # 2. GET MARKET DATA
        print("Fetching market data from MEXC...")
        ticker = exchange.fetch_ticker(SYMBOL)
        current_price = ticker['last']
        print(f"Success! Price is {current_price}")

        # 3. GET AI DECISION
        decision = get_ai_decision(current_price)
        
        # 4. EXECUTION LOGIC
        if decision['confidence'] >= 90:
            if decision['action'] == "BUY":
                print(">>> SIGNAL: BUY. (Orders are currently commented out for safety)")
                # exchange.create_market_buy_order(SYMBOL, TRADE_AMOUNT_USDT)
            elif decision['action'] == "SELL":
                print(">>> SIGNAL: SELL. (Orders are currently commented out for safety)")
                # exchange.create_market_sell_order(SYMBOL, TRADE_AMOUNT_USDT)
        else:
            print("Decision made: No trade (Confidence low).")

    except Exception as e:
        print(f"BOT CRITICAL ERROR: {e}")
        traceback.print_exc()
        decision = {"action": "HOLD", "confidence": 0, "reason": f"System Error: {str(e)}"}

    # 5. ALWAYS SAVE DATA (Even if it fails, so we can see why on the website)
    try:
        status_data = {
            "action": decision.get('action', 'HOLD'),
            "confidence": decision.get('confidence', 0),
            "reason": decision.get('reason', 'Unknown error'),
            "price": current_price,
            "timestamp": time.ctime() 
        }
        with open('data.json', 'w') as f:
            json.dump(status_data, f)
        print("Dashboard data.json updated.")
    except:
        print("Failed to write data.json")

if __name__ == "__main__":
    run_bot()
