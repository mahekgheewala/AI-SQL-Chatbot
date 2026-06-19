import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv(override=True)
api_key = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=api_key)

try:
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content("hello")
    print("SUCCESS!")
    print("Response:", response.text)
    if hasattr(response, "usage_metadata"):
        print("Usage Metadata:", response.usage_metadata)
        print("Prompt token count:", response.usage_metadata.prompt_token_count)
        print("Candidates token count:", response.usage_metadata.candidates_token_count)
        print("Total token count:", response.usage_metadata.total_token_count)
    else:
        print("No usage_metadata attribute")
except Exception as e:
    print("ERROR:", str(e))
