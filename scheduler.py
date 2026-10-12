"""
Scheduler de jobs programados.

Es agnóstico a los canales: solo corre el chequeo global de recordatorios y le
pide a cada canal activo que registre SUS jobs (vía channel.register_jobs). El
canal de gastos no tiene jobs propios: lo único que corre por horario son los
recordatorios.

Si un canal futuro necesita jobs por horario, los puede leer de
config/schedules.json con load_schedules() + add_scheduled_job().
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot

from db.database import get_db
from db.models import Reminder

logger = logging.getLogger(__name__)

SCHEDULES_PATH = Path(__file__).parent / "config" / "schedules.json"

DAY_MAP = {
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
    "fri": "fri", "sat": "sat", "sun": "sun",
}


def load_schedules() -> dict:
    """Carga la configuración de schedules desde schedules.json."""
    try:
        with open(SCHEDULES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("schedules.json no encontrado, usando defaults.")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando schedules.json: {e}")
        return {}


def add_scheduled_job(
    scheduler: AsyncIOScheduler,
    job_config: dict,
    job_callable: Callable,
    job_id: str,
    job_name: str,
    timezone_name: str,
) -> None:
    """Agrega un job cron al scheduler según la config de schedules.json.

    `job_callable` debe ser una corrutina sin argumentos (usá functools.partial
    para fijar el bot/chat_id) para que APScheduler la awaitee correctamente.
    """
    if not job_config.get("enabled", False):
        logger.info(f"{job_name} desactivado en schedules.json.")
        return

    hour = job_config.get("hour", 7)
    minute = job_config.get("minute", 0)
    days = job_config.get("days", [])

    if not days:
        logger.info(f"{job_name} sin días configurados, no se programa.")
        return

    day_of_week = ",".join(DAY_MAP[d] for d in days if d in DAY_MAP)
    if not day_of_week:
        logger.warning(f"{job_name}: días inválidos en config: {days}")
        return

    scheduler.add_job(
        job_callable,
        trigger=CronTrigger(
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
            timezone=timezone_name,
        ),
        id=job_id,
        name=job_name,
        replace_existing=True,
    )
    logger.info(f"{job_name} programado: {hour:02d}:{minute:02d} los {day_of_week}")


async def check_reminders(bot: Bot) -> None:
    """Envía recordatorios que ya vencieron y los marca como enviados."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with get_db() as db:
        pending = (
            db.query(Reminder)
            .filter(Reminder.sent == False, Reminder.remind_at <= now)  # noqa: E712
            .all()
        )
        for reminder in pending:
            try:
                await bot.send_message(
                    chat_id=reminder.chat_id,
                    text=f"⏰ *Recordatorio:* {reminder.message}",
                    parse_mode="Markdown",
                )
                reminder.sent = True
                logger.info(f"Recordatorio enviado: {reminder.id}")
            except Exception as e:
                logger.error(f"Error enviando recordatorio {reminder.id}: {e}")


def create_scheduler(runtimes: Iterable, reminder_bot: Bot) -> AsyncIOScheduler:
    """Arma el scheduler: jobs de cada canal + chequeo global de recordatorios.

    Args:
        runtimes: iterable de ChannelRuntime (canal + bot + ctx) de los canales activos.
        reminder_bot: bot con el que se envían los recordatorios (el del canal default).
    """
    scheduler = AsyncIOScheduler()

    for rt in runtimes:
        if rt.channel.register_jobs:
            try:
                rt.channel.register_jobs(scheduler, rt.bot, rt.ctx)
            except Exception as e:
                logger.error(f"Error registrando jobs del canal '{rt.channel.key}': {e}")

    # Verificar recordatorios cada minuto (infra global).
    scheduler.add_job(
        check_reminders,
        trigger=IntervalTrigger(minutes=1),
        args=[reminder_bot],
        id="check_reminders",
        name="Check Reminders",
        replace_existing=True,
    )

    return scheduler
