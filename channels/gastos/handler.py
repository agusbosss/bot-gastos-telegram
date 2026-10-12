"""
Handler del canal 'gastos'. Rutea los mensajes según su tipo:
- foto(s) → ticket de compra (Claude Vision → Google Sheets)
- nota de voz/audio → se transcribe y se procesa como texto
- texto → agente Claude (registro de gastos, memoria, recordatorios)

Soporta álbumes de fotos (media groups) tratándolos como un solo ticket.
"""
import asyncio
import logging

import httpx
from telegram import Update

from channels.base import ChannelContext
from channels.gastos import agent
from db.database import get_db
from db.models import BotMemory
from tools.expenses import (
    ExpenseUnconfirmed,
    analyze_receipt,
    send_to_sheets,
    format_receipt_summary,
)
from tools.transcription import transcribe_audio, TranscriptionNotConfigured

logger = logging.getLogger(__name__)

MEDIA_GROUP_DELAY = 3  # segundos para esperar más fotos del álbum


class GastosHandler:
    """Procesa mensajes del canal de gastos y tickets."""

    def __init__(self) -> None:
        # Buffer para media groups (álbumes de fotos), por media_group_id.
        self._media_group_buffers: dict[str, dict] = {}

    def is_background(self, update: Update) -> bool:
        m = update.message
        return bool(m and m.photo and m.media_group_id)

    async def handle(self, update: Update, ctx: ChannelContext) -> None:
        message = update.message
        if not message:
            return

        if message.photo:
            if message.media_group_id:
                await self._handle_media_group_photo(message, ctx)
            else:
                await self._handle_single_photo(message, ctx)
            return

        if message.voice or message.audio:
            await self._handle_voice(message, ctx)
            return

        if message.text:
            await self._handle_text(message, ctx)
            return

    # --- Descargas ---

    async def _download_telegram_file(self, ctx: ChannelContext, file_id: str) -> bytes:
        file = await ctx.bot.get_file(file_id)
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path, timeout=30)
            resp.raise_for_status()
            return resp.content

    async def _download_photo(self, ctx: ChannelContext, message) -> bytes:
        return await self._download_telegram_file(ctx, message.photo[-1].file_id)

    # --- Texto ---

    async def _handle_text(self, message, ctx: ChannelContext) -> None:
        user_text = message.text.strip()
        if not user_text:
            return

        await message.chat.send_action("typing")
        try:
            with get_db() as db:
                response = await agent.chat(user_text, ctx.chat_id, db)
            await message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error en _handle_text: {e}", exc_info=True)
            await message.reply_text("Ocurrió un error procesando tu mensaje. Intentá de nuevo.")

    # --- Voz / audio ---

    async def _handle_voice(self, message, ctx: ChannelContext) -> None:
        voice = message.voice or message.audio
        await message.chat.send_action("typing")

        try:
            audio_bytes = await self._download_telegram_file(ctx, voice.file_id)
        except Exception as e:
            logger.error(f"Error descargando audio: {e}", exc_info=True)
            await message.reply_text("No pude descargar el audio. Intentá de nuevo.")
            return

        # Extensión según el mime type para que Whisper detecte bien el formato.
        filename = "audio.ogg"
        mime = getattr(voice, "mime_type", None)
        if mime and "/" in mime:
            ext = mime.split("/")[-1].split(";")[0]
            if ext:
                filename = f"audio.{ext}"

        try:
            text = await transcribe_audio(audio_bytes, filename=filename)
        except TranscriptionNotConfigured as e:
            logger.warning(f"Transcripción no configurada: {e}")
            await message.reply_text(
                "🎙️ Recibí tu audio, pero todavía no tengo configurada la transcripción.\n"
                "Necesito una API key gratuita de Groq (console.groq.com) en la variable "
                "GROQ_API_KEY de Railway."
            )
            return
        except Exception as e:
            logger.error(f"Error transcribiendo audio: {e}", exc_info=True)
            await message.reply_text("No pude transcribir el audio. Intentá de nuevo o escribilo.")
            return

        if not text:
            await message.reply_text(
                "No entendí lo que dijiste en el audio. Probá de nuevo hablando más claro."
            )
            return

        # Mostrar lo transcripto (sin Markdown para evitar romper con caracteres especiales).
        await message.reply_text(f'🎙️ Escuché: "{text}"')
        await message.chat.send_action("typing")

        try:
            with get_db() as db:
                response = await agent.chat(text, ctx.chat_id, db)
            await message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error procesando audio transcrito: {e}", exc_info=True)
            await message.reply_text("Ocurrió un error procesando tu audio. Intentá de nuevo.")

    # --- Fotos / tickets ---

    async def _handle_single_photo(self, message, ctx: ChannelContext) -> None:
        await message.reply_text("📸 Analizando el ticket, un momento...")
        await message.chat.send_action("typing")

        try:
            image_bytes = await self._download_photo(ctx, message)
        except Exception as e:
            logger.error(f"Error descargando foto: {e}", exc_info=True)
            await message.reply_text("No pude descargar la foto. Intentá de nuevo.")
            return

        await self._process_ticket([(image_bytes, "image/jpeg")], ctx, message)

    async def _handle_media_group_photo(self, message, ctx: ChannelContext) -> None:
        group_id = message.media_group_id

        try:
            image_bytes = await self._download_photo(ctx, message)
        except Exception as e:
            logger.error(f"Error descargando foto del álbum: {e}", exc_info=True)
            return

        if group_id not in self._media_group_buffers:
            self._media_group_buffers[group_id] = {
                "photos": [(image_bytes, "image/jpeg")],
                "message": message,
                "processing": False,
            }
            logger.info(f"Media group {group_id}: primera foto recibida")
        else:
            self._media_group_buffers[group_id]["photos"].append((image_bytes, "image/jpeg"))
            logger.info(
                f"Media group {group_id}: {len(self._media_group_buffers[group_id]['photos'])} fotos"
            )

        # Esperar un poco: cada foto que llega llama a esto, pero solo la última procesa.
        await asyncio.sleep(MEDIA_GROUP_DELAY)

        if group_id not in self._media_group_buffers:
            return  # Ya fue procesado por otra coroutine

        buf = self._media_group_buffers[group_id]
        if buf["processing"]:
            return

        buf["processing"] = True
        photos = buf["photos"]
        msg = buf["message"]
        del self._media_group_buffers[group_id]

        n = len(photos)
        logger.info(f"Media group {group_id}: procesando {n} fotos como un solo ticket")

        await msg.reply_text(f"📸 Procesando {n} fotos como un solo ticket...")
        await msg.chat.send_action("typing")

        await self._process_ticket(photos, ctx, msg)

    async def _process_ticket(self, images: list[tuple[bytes, str]], ctx: ChannelContext, message) -> None:
        """Analiza imágenes de ticket con Claude Vision y guarda en Sheets."""
        with get_db() as db:
            ticket_memories = (
                db.query(BotMemory)
                .filter(BotMemory.chat_id == ctx.chat_id, BotMemory.pool == "tickets")
                .all()
            )
            ticket_rules = [m.content for m in ticket_memories] if ticket_memories else None

        try:
            expense_data = await analyze_receipt(images, ticket_rules=ticket_rules)
        except Exception as e:
            logger.error(f"Error analizando ticket con Claude: {e}", exc_info=True)
            await message.reply_text("No pude analizar el ticket. Intentá de nuevo o escribí el gasto manualmente.")
            return

        try:
            result = await send_to_sheets(expense_data)
            closing = (
                "\n♻️ Este ticket ya estaba registrado, no lo dupliqué."
                if result["duplicate"]
                else "\n✅ Todo guardado en tu Google Sheet."
            )
        except ExpenseUnconfirmed as e:
            # Puede haber quedado guardado igual: Apps Script sigue ejecutando
            # aunque nosotros cortemos. Nunca afirmar que falló ni reintentar solo.
            logger.warning(f"Ticket sin confirmar: {e}")
            closing = (
                "\n⚠️ Google no respondió a tiempo y no pude confirmar si quedó guardado.\n"
                "Fijate en la hoja antes de volver a mandarlo."
            )
        except Exception as e:
            logger.error(f"Error guardando ticket en Sheets: {e}", exc_info=True)
            closing = f"\n❌ No se guardó en Google Sheets: {e}"

        summary = format_receipt_summary(expense_data, closing)

        try:
            await message.reply_text(summary, parse_mode="Markdown")
        except Exception:
            try:
                await message.reply_text(summary)
            except Exception as e:
                logger.error(f"Error enviando resumen del ticket: {e}", exc_info=True)
