import os
from dotenv import find_dotenv, dotenv_values

print("=== DIAGNOSTIC START ===")
print("CWD:", os.getcwd())
print("OS ENV KEY:", os.environ.get("GEMINI_API_KEY"))
print("DOTENV PATH:", find_dotenv())
print("DOTENV VALUES:", dotenv_values(find_dotenv()).get("GEMINI_API_KEY"))
print("=== DIAGNOSTIC END ===")
