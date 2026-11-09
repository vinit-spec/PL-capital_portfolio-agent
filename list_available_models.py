"""
list_available_models.py
------------------------
Utility script to list models accessible with your GEMINI_API_KEY.
Usage:
    python list_available_models.py
"""

import os
import sys
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY is not set.")
    print("Create a .env file containing:")
    print("GEMINI_API_KEY=your_key_here")
    sys.exit(1)

genai.configure(api_key=api_key)

print("🔍 Querying available Gemini models for your API key...")
try:
    models = genai.list_models()
    flash_models = []
    other_models = []
    for m in models:
        # Check if model supports generateContent
        if "generateContent" in m.supported_generation_methods:
            name = m.name.replace("models/", "")
            if "flash" in name.lower():
                flash_models.append(name)
            else:
                other_models.append(name)

    print("\n✅ Available Flash Models (recommended for GEMINI_MODEL):")
    for f in sorted(flash_models):
        print(f"  • {f}")

    print("\n📋 Other Generation Models:")
    for o in sorted(other_models)[:10]:
        print(f"  • {o}")

except Exception as e:
    print(f"❌ Error querying models: {e}")
    sys.exit(1)
