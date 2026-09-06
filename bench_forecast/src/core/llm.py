from typing import Any


class LLMFactory:
    """Wrapper factory for Groq and Ollama inference clients."""

    @staticmethod
    def get_chat_model(provider: str = "groq", model_name: str = "llama3-70b-8192") -> Any:
        ...

