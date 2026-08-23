import os
import google.generativeai as genai
from ai.prompts import GEMINI_SYSTEM_PROMPT
from agent.prompts import AGENT_SYSTEM_PROMPT
from utils.config_loader import init_env

# Ensure environment variables are loaded
init_env()

# Global model references
SQL_GENERATOR_MODEL = None
PLANNER_MODEL = None
FORMATTER_MODEL = None

_models_initialized = False


def _resolve_api_key() -> tuple[str, bool]:
    """Return (api_key, using_fallback). Raises RuntimeError if neither the
    primary nor fallback Gemini API key is configured."""
    primary_key = os.getenv("GEMINI_API_KEY")
    fallback_key = os.getenv("GEMINI_FALLBACK_API_KEY")

    api_key_to_use = primary_key
    using_fallback = False

    if not api_key_to_use or api_key_to_use == "YOUR_GEMINI_API_KEY_HERE":
        api_key_to_use = fallback_key
        using_fallback = True

    if not api_key_to_use or api_key_to_use == "YOUR_GEMINI_API_KEY_HERE":
        raise RuntimeError(
            "Gemini API configuration missing.\n\n"
            "Neither GEMINI_API_KEY nor GEMINI_FALLBACK_API_KEY is configured.\n\n"
            "Please update backend/.env."
        )
    return api_key_to_use, using_fallback


def _build_models(api_key: str) -> str:
    """Configure the genai SDK with *api_key* and (re)build every model
    instance. Returns the model name used, so callers can log it — the one
    place this happens, instead of each call site hardcoding a model-name
    string for logging (see gemini_metrics.py / agent_coordinator.py, which
    read CURRENT_MODEL_NAME below rather than a literal)."""
    global SQL_GENERATOR_MODEL, PLANNER_MODEL, FORMATTER_MODEL

    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    SQL_GENERATOR_MODEL = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=GEMINI_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json"
        )
    )

    PLANNER_MODEL = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=AGENT_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    FORMATTER_MODEL = genai.GenerativeModel(
        model_name=model_name,
        generation_config=genai.types.GenerationConfig(
            temperature=0.2,
            response_mime_type="text/plain",
        ),
    )

    return model_name


def initialize_models():
    """Initializes all Gemini models exactly once."""
    global _models_initialized

    if _models_initialized:
        return

    api_key, using_fallback = _resolve_api_key()
    model_name = _build_models(api_key)
    _models_initialized = True

    print("\n========== GEMINI STARTUP ==========")
    if using_fallback:
        print("API KEY SOURCE: Fallback Key")
        print("API KEY PREFIX (FALLBACK):", api_key[:10] + "...")
    else:
        print("API KEY SOURCE: Primary Key")
        print("API KEY PREFIX (PRIMARY):", api_key[:10] + "...")
    print(f"MODEL: {model_name}")
    print("Gemini models initialized successfully.")
    print("====================================\n")


def configure_api_key(api_key: str):
    """
    Configures a new API key and re-initializes all model instances.
    Used for testing or dynamic key switching.
    """
    global _models_initialized
    _build_models(api_key)
    _models_initialized = True


def current_model_name() -> str:
    """The actually-configured Gemini model name — read this for logging
    instead of hardcoding a model-name string (several call sites used to
    hardcode "gemini-2.5-flash" regardless of GEMINI_MODEL, mislabeling
    cost/model analytics whenever the env var differed)."""
    return os.getenv("GEMINI_MODEL", "gemini-flash-latest")


# Run initial initialization immediately upon import
try:
    initialize_models()
except Exception as e:
    # Print warnings for potential missing credentials on startup (e.g. during test loading)
    print(f"Warning: Gemini models could not be initialized on import: {e}")
