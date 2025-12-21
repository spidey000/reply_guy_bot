
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("AI_API_KEY")
BASE_URL = os.getenv("AI_BASE_URL", "https://openrouter.ai/api/v1")

def test_model(model_name):
    print(f"\nTesting model: {model_name}")
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/reply-guy-bot", # OpenRouter often requires this
    }
    data = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Say hello"}],
        "max_tokens": 10
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code != 200:
            print(f"Error: {response.text}")
        else:
            print(f"Success! Response: {response.json()['choices'][0]['message']['content']}")
            return True
    except Exception as e:
        print(f"Exception: {e}")
        return False
    return False

# 1. Test the current configured model
current_model = os.getenv("AI_MODEL")
print(f"Current configured model: {current_model}")
test_model(current_model)

# 2. Test a known working free model
test_model("google/gemini-2.0-flash-exp:free")

# 3. Test another fallback
test_model("google/gemini-flash-1.5")
