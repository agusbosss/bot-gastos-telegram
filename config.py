import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Cargamos el .env a os.environ así el token y el chat del bot (GASTOS_BOT_TOKEN,
# GASTOS_CHAT_ID) se leen igual en local y en Railway, sin tener que declararlos
# como campos de Settings. En Railway las env vars ya viven en os.environ, así
# que load_dotenv() no pisa nada (no override por defecto).
load_dotenv()


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str

    # Groq (transcripción de audios con Whisper). Opcional: si falta, los audios
    # avisan que hay que configurarla en vez de fallar.
    groq_api_key: str = ""
    transcription_model: str = "whisper-large-v3-turbo"

    # Google Apps Script
    apps_script_url: str
    apps_script_secret: str

    # Timezone
    timezone: str = "America/Argentina/Buenos_Aires"

    # App
    port: int = 8000
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # tolerar variables de entorno extra sin romper el arranque


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def env(*names: str) -> str | None:
    """Devuelve el primer valor no vacío entre las env vars dadas (por prioridad)."""
    for name in names:
        if not name:
            continue
        value = os.environ.get(name)
        if value:
            return value
    return None
