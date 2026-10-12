"""
Contrato de un "canal".

Un canal es una función del asistente que corre sobre su PROPIO bot de Telegram
(su propio token y su propio chat), pero dentro de la misma app/deploy. Hoy el
único canal es 'gastos'; la estructura queda para sumar otros bots sin tocar main.py.

Cada canal define:
- cómo se identifica (key, nombre visible),
- de qué env vars saca su token y su chat autorizado,
- un handler que procesa los mensajes que le llegan,
- opcionalmente, jobs programados (scheduler).

main.py levanta un Bot por cada canal ACTIVO (habilitado + con token presente) y
rutea /webhook/<key> hacia su handler. Agregar un canal nuevo = crear un módulo
en channels/<nombre>/ y registrarlo en channels/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from telegram import Bot, Update

from config import env, Settings


@dataclass
class ChannelContext:
    """Lo que un handler necesita para responder: su bot, su chat autorizado y config."""
    key: str
    bot: Bot
    chat_id: str          # chat autorizado (dueño) de este canal
    settings: Settings


@runtime_checkable
class ChannelHandler(Protocol):
    """Procesa los updates de un canal."""

    async def handle(self, update: Update, ctx: ChannelContext) -> None:
        ...

    def is_background(self, update: Update) -> bool:
        """True si el update debe procesarse en background (responder el webhook al
        toque). Necesario para álbumes de fotos: Telegram manda las fotos en serie
        por chat y si bloqueamos, la siguiente no llega hasta terminar la anterior."""
        ...


# Firma de un registrador de jobs: (scheduler, bot_del_canal, ctx) -> None
RegisterJobs = Callable[["object", Bot, ChannelContext], None]


@dataclass
class Channel:
    key: str                                  # identificador url-safe, ej: "gastos"
    display_name: str                         # nombre humano, ej: "Gastos y Tickets"
    handler: ChannelHandler
    token_env: str                            # env var del token, ej: "GASTOS_BOT_TOKEN"
    chat_id_env: str                          # env var del chat autorizado
    legacy_token_env: Optional[str] = None    # fallback de token (compat)
    legacy_chat_id_env: Optional[str] = None  # fallback de chat_id (compat)
    enabled: bool = True                      # master switch del canal
    register_jobs: Optional[RegisterJobs] = None

    @property
    def webhook_path(self) -> str:
        return f"/webhook/{self.key}"

    def resolve_token(self) -> Optional[str]:
        return env(self.token_env, self.legacy_token_env or "")

    def resolve_chat_id(self) -> Optional[str]:
        return env(self.chat_id_env, self.legacy_chat_id_env or "")

    def is_active(self) -> bool:
        """Activo = habilitado y con token disponible. Un canal scaffolded sin token
        simplemente no arranca (no rompe el resto)."""
        return self.enabled and bool(self.resolve_token())


@dataclass
class ChannelRuntime:
    """Instancia viva de un canal: el canal + su Bot inicializado + su contexto."""
    channel: Channel
    bot: Bot
    ctx: ChannelContext
