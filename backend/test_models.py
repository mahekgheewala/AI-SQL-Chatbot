import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv(override=True)
api_key = os.getenv("GEMINI_API_KEY")

if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
    print("API Key is missing or invalid.")
    exit(1)

genai.configure(api_key=api_key)

print("=== ALL AVAILABLE MODELS ===")
for m in genai.list_models():
    print(f"Name: {m.name} | GenerateContent Support: {'generateContent' in m.supported_generation_methods}")
