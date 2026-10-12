"""
Servidor principal: FastAPI + webhooks de Telegram, multi-canal.

Levanta un Bot de Telegram por cada canal ACTIVO (habilitado + con token) y
rutea /webhook/<canal> hacia el handler de ese canal. Hoy el único canal es
'gastos', así que el webhook de Telegram va a /webhook/gastos.
"""
import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application

from config import get_settings
from db.database import init_db
from scheduler import create_scheduler
from channels import (
    active_channels,
    ChannelContext,
    ChannelRuntime,
    DEFAULT_CHANNEL_KEY,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_scheduler = None
_runtimes: dict[str, ChannelRuntime] = {}
_apps: dict[str, Application] = {}
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    settings = get_settings()
    logger.info("Configuración cargada correctamente.")

    init_db()
    logger.info("Base de datos inicializada.")

    # Un Bot/Application por canal activo.
    channels = active_channels()
    if not channels:
        logger.error("No hay canales activos. Revisá los tokens (ej: GASTOS_BOT_TOKEN).")

    for channel in channels:
        application = Application.builder().token(channel.resolve_token()).build()
        await application.initialize()
        _apps[channel.key] = application
        ctx = ChannelContext(
            key=channel.key,
            bot=application.bot,
            chat_id=channel.resolve_chat_id(),
            settings=settings,
        )
        _runtimes[channel.key] = ChannelRuntime(channel=channel, bot=application.bot, ctx=ctx)
        logger.info(f"Canal '{channel.key}' ({channel.display_name}) activo → {channel.webhook_path}")

    # Scheduler: jobs de cada canal + recordatorios globales (enviados por el canal default).
    reminder_rt = _runtimes.get(DEFAULT_CHANNEL_KEY) or next(iter(_runtimes.values()), None)
    if reminder_rt:
        _scheduler = create_scheduler(_runtimes.values(), reminder_rt.bot)
        _scheduler.start()
        logger.info("Scheduler iniciado.")
    else:
        logger.warning("Scheduler no iniciado: no hay ningún canal activo.")

    yield

    if _scheduler:
        _scheduler.shutdown(wait=False)
    for application in _apps.values():
        try:
            await application.shutdown()
        except Exception as e:
            logger.error(f"Error apagando bot: {e}")
    logger.info("Servidor apagado.")


app = FastAPI(title="Bot de gastos", lifespan=lifespan)


@app.get("/")
async def health():
    channels = {key: rt.channel.display_name for key, rt in _runtimes.items()}
    return {"status": "ok", "agent": "Bot de gastos", "canales_activos": channels}


async def _dispatch(rt: ChannelRuntime, update: Update) -> None:
    """Valida el chat autorizado del canal y despacha al handler."""
    message = update.message
    if not message:
        return

    chat_id = str(message.chat_id)
    if chat_id != rt.ctx.chat_id:
        logger.warning(f"[{rt.channel.key}] Mensaje de chat_id no autorizado: {chat_id}")
        try:
            await message.reply_text("No estoy autorizado para responder en este chat.")
        except Exception:
            pass
        return

    await rt.channel.handler.handle(update, rt.ctx)


def _check_webhook_secret(request: Request) -> None:
    """Rechaza requests que no vienen de Telegram.

    Si WEBHOOK_SECRET está seteada, Telegram la manda en cada update en el header
    X-Telegram-Bot-Api-Secret-Token (se configura con el parámetro secret_token
    de setWebhook). Sin ella, cualquiera que conozca la URL podría mandar updates
    falsos haciéndose pasar por tu chat.
    """
    expected = os.environ.get("WEBHOOK_SECRET")
    if not expected:
        return
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _process_webhook(rt: ChannelRuntime, request: Request) -> dict:
    _check_webhook_secret(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    update = Update.de_json(data, rt.bot)

    # Algunos updates (álbumes de fotos) deben procesarse en background para
    # responder el webhook al toque y no bloquear las fotos siguientes.
    if update.message and rt.channel.handler.is_background(update):
        task = asyncio.create_task(_dispatch(rt, update))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        await _dispatch(rt, update)

    return {"ok": True}


@app.post("/webhook/{channel_key}")
async def telegram_webhook(channel_key: str, request: Request):
    """Recibe updates de Telegram para un canal específico."""
    rt = _runtimes.get(channel_key)
    if not rt:
        raise HTTPException(status_code=404, detail=f"Canal '{channel_key}' no activo")
    return await _process_webhook(rt, request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=get_settings().port, reload=False)
