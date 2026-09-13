import os
from dotenv import load_dotenv
import cohere

load_dotenv()

api_key = os.getenv('COHERE_API_KEY')
print(f"Cohere Key: {api_key[:15]}...")

try:
    client = cohere.Client(api_key)
    
    # ✅ Use the new Chat API
    response = client.chat(
        model="command-r-plus",
        message="Say 'Hello from Cohere Chat API!'",
        temperature=0.3,
        max_tokens=20
    )
    
    print("✅ Cohere Chat API is working!")
    print(f"Response: {response.text}")
    
except Exception as e:
    print(f"❌ Cohere error: {e}")