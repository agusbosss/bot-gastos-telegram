"""
Registro de canales del asistente.

Cada canal corre sobre su propio bot de Telegram pero dentro de la misma app.
Hoy hay uno solo, 'gastos'. Para agregar otro: creá channels/<nombre>/ (mirá
'gastos' de ejemplo), importá su Channel acá y sumalo a ALL_CHANNELS.
"""
from channels.base import Channel, ChannelContext, ChannelRuntime
from channels.gastos import GASTOS_CHANNEL

# Todos los canales conocidos (activos o no).
ALL_CHANNELS: list[Channel] = [
    GASTOS_CHANNEL,
]

# Canal usado como default para cosas globales (ej: enviar recordatorios).
DEFAULT_CHANNEL_KEY = "gastos"


def active_channels() -> list[Channel]:
    """Canales habilitados y con token disponible (los que realmente arrancan)."""
    return [c for c in ALL_CHANNELS if c.is_active()]


def get_channel(key: str) -> Channel | None:
    return next((c for c in ALL_CHANNELS if c.key == key), None)


__all__ = [
    "Channel",
    "ChannelContext",
    "ChannelRuntime",
    "ALL_CHANNELS",
    "DEFAULT_CHANNEL_KEY",
    "active_channels",
    "get_channel",
]
