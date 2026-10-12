"""
Transcripción de audios (notas de voz de Telegram) a texto.

La API de Claude no acepta audio directamente, así que se transcribe primero
con Whisper vía Groq (endpoint compatible con OpenAI, gratis y rápido). El texto
resultante se procesa después con el agente igual que un mensaje escrito.

Las notas de voz de Telegram llegan en OGG/Opus, formato que Whisper acepta sin
necesidad de convertir el audio.
"""
import logging

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

# Groq expone la API de transcripción de Whisper compatible con OpenAI.
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class TranscriptionNotConfigured(Exception):
    """Se lanza cuando falta la API key de transcripción."""


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Transcribe un audio a texto usando Whisper (Groq).

    Args:
        audio_bytes: Contenido del archivo de audio.
        filename: Nombre con extensión correcta (Whisper la usa para detectar el formato).

    Returns:
        El texto reconocido (puede ser cadena vacía si no se entendió nada).

    Raises:
        TranscriptionNotConfigured: si no hay GROQ_API_KEY configurada.
    """
    settings = get_settings()
    api_key = settings.groq_api_key
    if not api_key:
        raise TranscriptionNotConfigured(
            "Falta GROQ_API_KEY. Conseguí una gratis en console.groq.com."
        )

    files = {"file": (filename, audio_bytes)}
    data = {
        "model": settings.transcription_model,
        "language": "es",
        "response_format": "json",
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GROQ_TRANSCRIPTION_URL,
            headers=headers,
            data=data,
            files=files,
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()

    text = (result.get("text") or "").strip()
    logger.info(f"Audio transcrito ({len(audio_bytes)} bytes): {text!r}")
    return text
