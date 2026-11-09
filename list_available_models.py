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
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY is not set.")
    print("Create a .env file containing:")
    print("GEMINI_API_KEY=your_key_here")
    sys.exit(1)

client = genai.Client(api_key=api_key)

print("🔍 Querying available Gemini models for your API key...")
try:
    flash_models = []
    other_models = []
    for m in client.models.list():
        name = m.name.replace("models/", "")
        # Only include models that support generateContent
        methods = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", [])
        if hasattr(m, "supported_actions"):
            supported = "generateContent" in (m.supported_actions or [])
        else:
            supported = "generateContent" in methods
        if supported or "flash" in name.lower() or "gemini" in name.lower():
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
