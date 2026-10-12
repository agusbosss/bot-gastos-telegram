from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.sql import func
from db.database import Base


class ConversationMessage(Base):
    """Historial de conversación por chat_id para mantener contexto.

    `content` guarda texto plano (mensajes normales). `content_json` guarda los
    content blocks de la API tal cual (tool_use / tool_result) para los turnos
    donde el agente ejecutó herramientas: sin eso, en el turno siguiente el
    modelo no tiene forma de saber qué se ejecutó de verdad y vuelve a
    registrar gastos que ya estaban registrados.
    """
    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)       # "user" | "assistant"
    content = Column(Text, nullable=False, default="")
    content_json = Column(Text, nullable=True)  # JSON con los content blocks
    created_at = Column(DateTime, server_default=func.now())


class BotMemory(Base):
    """Memorias persistentes del bot, separadas en pools (general / tickets)."""
    __tablename__ = "bot_memories"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True, nullable=False)
    pool = Column(String, nullable=False, default="general")  # "general" | "tickets"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Reminder(Base):
    """Recordatorios programados para enviar al usuario."""
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    remind_at = Column(DateTime, nullable=False)
    sent = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

