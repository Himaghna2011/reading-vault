# test_cohere.py
import os
from dotenv import load_dotenv
import cohere
import json
import re

load_dotenv()

api_key = os.getenv('COHERE_API_KEY')
print(f"🔑 Cohere API Key: {api_key[:15]}..." if api_key else "❌ No API key found!")

if not api_key:
    print("❌ Please add COHERE_API_KEY to your .env file")
    exit()

try:
    # Initialize client
    client = cohere.Client(api_key)
    print("✅ Cohere client initialized")
    
    print("\n" + "="*50)
    print("🔄 Testing Cohere Chat API...")
    print("="*50)
    
    # ✅ USE THE CORRECT MODEL
    # Try these in order:
    # 1. command-r-08-2024 (latest)
    # 2. command-r-plus-08-2024 (if available)
    # 3. command-r7b-12-2024 (fast)
    
    model_to_try = "command-r-08-2024"  # ✅ CORRECT MODEL
    
    print(f"📦 Using model: {model_to_try}")
    
    prompt = """You are a vocabulary expert. Analyze the word 'stoic' in this sentence:
    
Sentence: "His stoic demeanor hid a deeply emotional nature."
Target Word: "stoic"
Student's Guess: "I think it means being calm"

Return ONLY valid JSON with these fields:
- detected_pos
- contextual_meaning
- overallScore (0-100)
- synonyms (list of 3)
- root
- origin

JSON:"""

    response = client.chat(
        model=model_to_try,
        message=prompt,
        temperature=0.3,
        max_tokens=1500
    )
    
    print(f"✅ Cohere Chat API responded!")
    print(f"📝 Response: {response.text[:300]}...")
    
    # Try to extract JSON
    json_match = re.search(r'\{[\s\S]*\}', response.text)
    if json_match:
        try:
            result = json.loads(json_match.group())
            print("\n✅ JSON parsed successfully!")
            print(f"📊 Word: {result.get('detected_pos', 'N/A')}")
            print(f"📊 Score: {result.get('overallScore', 'N/A')}")
            print(f"📊 Root: {result.get('root', 'N/A')}")
            print(f"📊 Origin: {result.get('origin', 'N/A')}")
            print(f"📊 Synonyms: {result.get('synonyms', [])}")
        except json.JSONDecodeError as e:
            print(f"❌ JSON parsing error: {e}")
            print(f"Raw: {response.text[:200]}...")
    else:
        print("❌ No JSON found in response")
        
except Exception as e:
    print(f"❌ Cohere error: {e}")
    import traceback
    traceback.print_exc()