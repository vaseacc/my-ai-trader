import os
import google.generativeai as genai

def list_my_models():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not found in environment variables.")
        return

    genai.configure(api_key=api_key)

    print("--- FETCHING AVAILABLE MODELS ---")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(f"Model Name: {m.name}")
                print(f"Display Name: {m.display_name}")
                print("-" * 30)
    except Exception as e:
        print(f"FAILED TO LIST MODELS: {e}")

if __name__ == "__main__":
    list_my_models()
