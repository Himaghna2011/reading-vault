# test_openai.py
import os
from dotenv import load_dotenv
from openai import OpenAI
import json
import re

load_dotenv()

api_key = os.getenv('OPENAI_API_KEY')

if not api_key:
    print("❌ OPENAI_API_KEY not found! Add it to .env")
    exit()

print(f"🔑 OpenAI Key: {api_key[:15]}...")
print("✅ API key found!")

try:
    client = OpenAI(api_key=api_key)
    print("✅ OpenAI client initialized")
    
    print("\n" + "="*50)
    print("🔄 Testing OpenAI API...")
    print("="*50)
    
    prompt = """You are a vocabulary expert. Analyze 'stoic' in:
"His stoic demeanor hid a deeply emotional nature."

Return ONLY valid JSON with:
- detected_pos
- contextual_meaning
- overallScore (0-100)
- synonyms (list)
- root
- origin

JSON:"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1500
    )
    
    text = response.choices[0].message.content
    print(f"📝 Response: {text[:300]}...")
    
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        result = json.loads(json_match.group())
        print("\n✅ JSON parsed successfully!")
        print(f"📊 Part of Speech: {result.get('detected_pos', 'N/A')}")
        print(f"📊 Score: {result.get('overallScore', 'N/A')}")
        print(f"📊 Root: {result.get('root', 'N/A')}")
        print(f"📊 Origin: {result.get('origin', 'N/A')}")
        print(f"📊 Synonyms: {result.get('synonyms', [])}")
    else:
        print("❌ No JSON found")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()