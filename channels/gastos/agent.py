"""
Agente del canal 'gastos': conversación de texto con Claude para registrar
gastos y tickets. Usa el loop genérico de channels/llm.py con un set de tools
acotado a gastos, memoria y recordatorios (sin clima ni noticias).

Para mensajes con imágenes (tickets) se usa el flujo de tools/expenses.py directo
desde el handler.
"""
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic, APIStatusError
from sqlalchemy.orm import Session

from config import get_settings
from channels.llm import run_agent, friendly_api_error
from db.models import ConversationMessage, Reminder, BotMemory
from tools.expenses import (
    ExpenseUnconfirmed,
    send_manual_expense,
    fetch_categories,
    _build_categories_text,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un asistente personal que ayuda a llevar el registro de gastos y compras.
Hablás en español argentino informal. Tenés herramientas para registrar gastos y recordar cosas.

**Herramientas disponibles:**
- save_reminder: guardar recordatorio con fecha/hora
- add_expense: registrar gasto manual en la hoja de Gastos
- save_memory: guardar información general que el usuario quiere que recuerdes
- save_ticket_rule: guardar regla para análisis de tickets/compras
- list_memories: listar lo que tenés guardado en memoria
- delete_memory: borrar una memoria por ID

**Sobre gastos (add_expense) — REGLA ESTRICTA:**
- SIEMPRE que el usuario mencione un gasto NUEVO, DEBÉS llamar al tool add_expense ANTES de responder. Sin excepción.
- NUNCA respondas "registré", "guardé" o similar sin haber ejecutado add_expense. Si no llamás el tool, el gasto NO se registra.
- Extraé: nombre del gasto, monto, fecha (si la menciona, sino hoy), y `store` si menciona un comercio.
- Lo ÚNICO que justifica no registrar un gasto nuevo es que falte el MONTO. Todo lo demás (categoría, subcategoría, comercio, fecha) lo resolvés vos con lo que haya y registrás igual.

**Qué NO volver a registrar — REGLA CRÍTICA:**
- Cada mensaje del usuario en el historial empieza con `[dd/mm/aaaa hh:mm]`: es la fecha y hora en que lo mandó, la agrega el sistema. No la escribas en tus respuestas. El historial abarca varios días: lo que no tenga la fecha de hoy NO es de hoy.
- Actuá SOLO sobre el último mensaje del usuario. Los gastos de mensajes anteriores YA se procesaron: los tool_use y sus resultados están en el historial. NUNCA vuelvas a llamar add_expense para los gastos de esos mensajes anteriores.
- Un gasto que falló antes se reintenta SOLO si el usuario lo pide explícitamente en su último mensaje ("sí, reintentá", "registrá los huevos"). Si te manda un gasto nuevo, registrá SOLO ese y no toques los anteriores.
- Todo gasto que venga en el último mensaje es un gasto NUEVO y se registra, aunque en el historial haya uno idéntico (mismo concepto, monto y lugar): la gente compra lo mismo seguido (el almuerzo de la facu, las empanadas, el café). NUNCA decidas vos que "ya estaba registrado" ni preguntes si es el mismo: llamá add_expense. El sistema detecta solo los reenvíos reales y te lo avisa con `"duplicado": true`; esa es la única fuente de verdad sobre duplicados.
- La única excepción es que el último mensaje diga explícitamente que se refiere a un gasto ya registrado ("el de recién", "te lo repito", "ese que te pasé"): ahí no lo registres de nuevo.

**Cómo leer el resultado de add_expense:**
- `"ok": true` → registrado. Confirmalo.
- `"ok": true` con `"duplicado": true` → ese gasto YA estaba registrado y NO se escribió de nuevo. Decíselo tal cual: "ese gasto ya estaba registrado, no lo dupliqué". No lo reintentes.
- `"estado": "sin_confirmar"` → Google no respondió a tiempo. El gasto PUEDE haber quedado registrado igual. NUNCA digas que falló ni lo vuelvas a registrar: decile que no pudiste confirmarlo y que lo revise en la hoja.
- `"estado": "error"` → ahí sí falló. Informá: "No pude registrar el gasto: [motivo]". No lo reintentes por tu cuenta.
- **Moneda:** el monto puede estar en pesos o dólares. Detectá la moneda y pasá `currency`: si el usuario dice "dólares", "usd", "US$", "verdes" o similar, usá `currency="USD"`; si no aclara, `currency="ARS"`. NUNCA le pidas el tipo de cambio ni le preguntes a cuánto está el dólar: el sistema convierte solo con el dólar cripto y guarda el monto en pesos y en dólares. Confirmá mostrando ambas monedas como venga en el resultado del tool.

**Categorización — REGLA CRÍTICA (leé con atención):**
- Usá EXCLUSIVAMENTE los nombres EXACTOS que aparecen en la sección "Categorías y subcategorías disponibles" de más abajo. NUNCA inventes una categoría ni una subcategoría que no esté en esa lista.
- Cada categoría y subcategoría trae una descripción que indica CUÁNDO se aplica. Leé las descripciones y elegí la que mejor encaje. No te guíes solo por el nombre.
- Las descripciones mandan sobre tu intuición. Ejemplo: si el usuario menciona "facultad", "universidad" o algo de sus estudios, fijate qué categoría cubre eso según su descripción y usala (no lo mandes a "Salida" por default).
- Si el usuario nombra explícitamente una categoría que existe en la lista (ej: "extra", "salida", "transporte"), respetala TAL CUAL. No la reinterpretes ni pidas aclaración.
- `category` debe ser SIEMPRE una de las **Categorías** de la lista. `subcategory` (cuando aplique) debe ser una de las **Subcategorías**, y como `category` usá la categoría padre que figura para esa subcategoría en la lista.
- Si de verdad NINGUNA categoría encaja, usá "Otros". JAMÁS inventes un nombre nuevo.
- **NUNCA pidas aclaración para elegir categoría o subcategoría.** Un gasto como "empanadas 5000" puede encajar en varias (Compras, Salida, Educación) y NO tenés que preguntar cuál: elegí la más probable, REGISTRALO, y avisá en la confirmación cuál usaste (ej: "lo puse en Compras") para que el usuario la corrija si quiere. Quedarte esperando una respuesta es el peor resultado posible: el gasto se pierde.
- Varias categorías tienen la descripción vacía. Si está vacía, guiate por el nombre y por el contexto. Que falte la descripción NO es motivo para preguntar.

**Gastos de consumo (alimentos, bebidas, limpieza, higiene):**
- Para productos de consumo, incluí SIEMPRE `subcategory` (de la lista) además de `category` (su categoría padre), y `store` si se menciona el comercio.

**Sobre la memoria:**
- Cuando el usuario diga "recordá que...", "anotá que..." → usá save_memory
- Cuando dé una regla para categorización de tickets → usá save_ticket_rule
- Si pregunta qué recordás → usá list_memories
- Si pide borrar algo → usá delete_memory

Siempre respondé de forma concisa y útil. Usá emojis con moderación."""

# Cuenta filas de la conversación, y ahora cada turno con tools ocupa varias
# (el tool_use y su tool_result), así que necesita más margen que antes.
MAX_HISTORY = 40
MODEL = "claude-sonnet-4-6"
AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

TOOLS = [
    {
        "name": "save_reminder",
        "description": "Guarda un recordatorio para enviarlo al usuario en una fecha/hora específica.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "El texto del recordatorio a enviar.",
                },
                "remind_at": {
                    "type": "string",
                    "description": "Fecha y hora en formato ISO 8601, ej: 2026-03-15T18:00:00",
                },
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "add_expense",
        "description": "Registra un gasto en la hoja de Gastos. Para productos de consumo (alimentos, bebidas, limpieza, higiene personal), SIEMPRE incluir subcategory (ej: 'Verduras y Frutas', 'Lácteos', 'Limpieza del Hogar') para que también quede registrado en la hoja de Compras. Para otros gastos (servicios, salidas, transporte) no es necesario subcategory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nombre del gasto (ej: 'Fotocopias', 'Luz', 'Maple de huevos').",
                },
                "category": {
                    "type": "string",
                    "description": "Categoría del gasto. Usá una existente o creá una nueva si ninguna encaja (ej: Servicios, Compras, Salida, Transporte).",
                },
                "amount": {
                    "type": "number",
                    "description": "Monto del gasto en la moneda indicada en `currency` (por defecto pesos argentinos).",
                },
                "currency": {
                    "type": "string",
                    "enum": ["ARS", "USD"],
                    "description": "Moneda del monto: 'ARS' (pesos, default) o 'USD' (dólares). Si el usuario dice 'dólares', 'usd', 'US$', 'verdes' o similar, usá 'USD'. El sistema convierte y guarda ambas monedas automáticamente.",
                },
                "date": {
                    "type": "string",
                    "description": "Fecha del gasto en formato DD/MM/YYYY. Si no se indica, se usa hoy.",
                },
                "subcategory": {
                    "type": "string",
                    "description": "Subcategoría para productos de consumo (ej: 'Verduras y Frutas', 'Lácteos', 'Limpieza del Hogar'). Si se indica, el ítem también se registra en la hoja de Compras.",
                },
                "store": {
                    "type": "string",
                    "description": "Comercio donde se realizó la compra (ej: 'Verdulería', 'Carrefour'). Se usa en la hoja de Compras.",
                },
                "allow_duplicate": {
                    "type": "boolean",
                    "description": "Solo true si el usuario pide explícitamente registrar de nuevo un gasto idéntico y reciente que el sistema marcó como duplicado (ej: 'sí, cargalo igual, compré dos veces lo mismo'). Por defecto false.",
                },
            },
            "required": ["name", "category", "amount"],
        },
    },
    {
        "name": "save_memory",
        "description": "Guarda información general del usuario en memoria persistente (preferencias, datos personales, reglas generales). Se inyecta en el contexto del chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "El texto a recordar (ej: 'Prefiere respuestas cortas', 'Vive en Buenos Aires').",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "save_ticket_rule",
        "description": "Guarda una regla específica para el análisis de tickets y fotos de compras. Se inyecta solo en el prompt de análisis de tickets, no en el chat general.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "La regla para tickets (ej: 'Los tickets de Farmacity van a categoría Salud', 'Compras en el kiosco van a Snacks').",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "list_memories",
        "description": "Lista las memorias guardadas del usuario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pool": {
                    "type": "string",
                    "description": "Qué memorias listar: 'general', 'tickets', o 'all'. Default: 'all'.",
                    "enum": ["general", "tickets", "all"],
                },
            },
            "required": [],
        },
    },
    {
        "name": "delete_memory",
        "description": "Borra una memoria guardada por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "ID de la memoria a borrar (se obtiene con list_memories).",
                },
            },
            "required": ["memory_id"],
        },
    },
]


def _load_memories(db: Session, chat_id: str, pool: str = "general") -> list:
    """Carga memorias de un pool específico."""
    return (
        db.query(BotMemory)
        .filter(BotMemory.chat_id == chat_id, BotMemory.pool == pool)
        .order_by(BotMemory.created_at.asc())
        .all()
    )


def _build_system_prompt(db: Session, chat_id: str, categories_text: str = "") -> str:
    """Construye el system prompt con fecha actual, memorias generales y categorías vivas."""
    today = datetime.now(AR_TZ)
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_nombre = dias[today.weekday()]
    date_block = f"\n**Fecha y hora actual:** {dia_nombre} {today.strftime('%d/%m/%Y %H:%M')} (Argentina)"

    prompt = SYSTEM_PROMPT + date_block

    memories = _load_memories(db, chat_id, "general")
    if memories:
        memory_lines = [f"- {m.content}" for m in memories]
        memory_block = "\n".join(memory_lines)
        prompt += f"\n\n**Memorias del usuario:**\n{memory_block}"

    if categories_text:
        prompt += (
            "\n\n**Categorías y subcategorías disponibles (usá SOLO estas, respetando los nombres exactos y sus descripciones):**\n"
            + categories_text
        )

    return prompt


async def _execute_tool(tool_name: str, tool_input: dict, db: Session, chat_id: str) -> str:
    if tool_name == "save_reminder":
        try:
            remind_at = datetime.fromisoformat(tool_input["remind_at"])
            reminder = Reminder(
                chat_id=chat_id,
                message=tool_input["message"],
                remind_at=remind_at,
            )
            db.add(reminder)
            db.commit()
            return json.dumps({
                "ok": True,
                "mensaje": tool_input["message"],
                "programado_para": remind_at.strftime("%d/%m/%Y a las %H:%M"),
            }, ensure_ascii=False)
        except Exception as e:
            db.rollback()
            return json.dumps({"ok": False, "error": str(e)})

    elif tool_name == "add_expense":
        name = tool_input["name"]
        category = tool_input["category"]
        amount = tool_input["amount"]
        date = tool_input.get("date")
        subcategory = tool_input.get("subcategory")
        store = tool_input.get("store")
        currency = (tool_input.get("currency") or "ARS").upper()

        # Solo la llamada a la hoja va dentro del try. Antes el armado del
        # mensaje estaba adentro también, así que un error de formato se
        # reportaba como "el gasto no se registró" aunque la fila ya estuviera.
        try:
            info = await send_manual_expense(
                name, category, amount, date,
                subcategory=subcategory, store=store, currency=currency,
                allow_duplicate=bool(tool_input.get("allow_duplicate")),
            )
        except ExpenseUnconfirmed as e:
            logger.warning(f"add_expense sin confirmar ({name}): {e}")
            return json.dumps({
                "ok": False,
                "estado": "sin_confirmar",
                "error": (
                    "Google no respondió a tiempo. El gasto PUEDE haber quedado registrado igual. "
                    "Avisale al usuario que no pudiste confirmarlo y que lo revise en la hoja. "
                    "NO lo registres de nuevo."
                ),
            }, ensure_ascii=False)
        except Exception as e:
            logger.error(f"add_expense falló ({name}): {e}", exc_info=True)
            return json.dumps({
                "ok": False,
                "estado": "error",
                "error": str(e),
            }, ensure_ascii=False)

        ars = info["amount_ars"]
        usd = info.get("amount_usd")
        if currency == "USD":
            monto = f"US$ {float(amount):,.2f}" + (f" (${ars:,.0f})" if ars else "")
        else:
            monto = f"${ars:,.0f}" + (f" (US$ {usd:,.2f})" if usd else "")

        if info.get("duplicate"):
            return json.dumps({
                "ok": True,
                "duplicado": True,
                "mensaje": (
                    f"'{name}' de {monto} ya estaba registrado hace un rato: NO se escribió otra fila. "
                    "Decíselo al usuario tal cual."
                ),
            }, ensure_ascii=False)

        extra = f" (también en Compras como '{subcategory}')" if subcategory else ""
        return json.dumps({
            "ok": True,
            "mensaje": f"Gasto '{name}' de {monto} en {category} registrado{extra}.",
        }, ensure_ascii=False)

    elif tool_name == "save_memory":
        try:
            memory = BotMemory(
                chat_id=chat_id,
                pool="general",
                content=tool_input["content"],
            )
            db.add(memory)
            db.commit()
            return json.dumps({
                "ok": True,
                "id": memory.id,
                "mensaje": "Memoria guardada.",
            }, ensure_ascii=False)
        except Exception as e:
            db.rollback()
            return json.dumps({"ok": False, "error": str(e)})

    elif tool_name == "save_ticket_rule":
        try:
            memory = BotMemory(
                chat_id=chat_id,
                pool="tickets",
                content=tool_input["content"],
            )
            db.add(memory)
            db.commit()
            return json.dumps({
                "ok": True,
                "id": memory.id,
                "mensaje": "Regla de tickets guardada.",
            }, ensure_ascii=False)
        except Exception as e:
            db.rollback()
            return json.dumps({"ok": False, "error": str(e)})

    elif tool_name == "list_memories":
        try:
            pool = tool_input.get("pool", "all")
            result = {}

            if pool in ("general", "all"):
                general = _load_memories(db, chat_id, "general")
                result["general"] = [
                    {"id": m.id, "contenido": m.content} for m in general
                ]

            if pool in ("tickets", "all"):
                tickets = _load_memories(db, chat_id, "tickets")
                result["tickets"] = [
                    {"id": m.id, "contenido": m.content} for m in tickets
                ]

            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    elif tool_name == "delete_memory":
        try:
            memory_id = tool_input["memory_id"]
            memory = (
                db.query(BotMemory)
                .filter(BotMemory.id == memory_id, BotMemory.chat_id == chat_id)
                .first()
            )
            if memory:
                db.delete(memory)
                db.commit()
                return json.dumps({"ok": True, "mensaje": f"Memoria #{memory_id} borrada."}, ensure_ascii=False)
            else:
                return json.dumps({"ok": False, "error": f"No encontré memoria con ID {memory_id}."})
        except Exception as e:
            db.rollback()
            return json.dumps({"ok": False, "error": str(e)})

    return json.dumps({"error": f"Tool desconocida: {tool_name}"})


def _has_tool_result(content) -> bool:
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _as_blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def _drop_orphan_tool_results(messages: list) -> list:
    """Descarta el arranque del historial hasta el primer mensaje real del usuario.

    El recorte por MAX_HISTORY puede cortar justo en el medio de un turno y dejar
    un tool_result sin el tool_use que lo originó: la API rechaza eso. También
    garantiza que el historial arranque con un mensaje `user`, como exige la API.
    """
    start = 0
    while start < len(messages):
        msg = messages[start]
        if msg["role"] == "user" and not _has_tool_result(msg["content"]):
            break
        start += 1
    return messages[start:]


def _merge_same_role(messages: list) -> list:
    """Junta mensajes consecutivos del mismo rol.

    Pasa cuando un turno se cortó a la mitad (error de API) y el último mensaje
    guardado fue un tool_result: el mensaje nuevo del usuario quedaría pegado a
    otro `user`. Los tool_result quedan primero, que es como los pide la API.
    """
    merged: list = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = _as_blocks(merged[-1]["content"]) + _as_blocks(msg["content"])
        else:
            merged.append({"role": msg["role"], "content": msg["content"]})
    return merged


def _timestamp(created_at: datetime | None) -> str:
    """Prefijo `[dd/mm/aaaa hh:mm] ` con la hora argentina de un mensaje del usuario.

    Sin esto el historial no dice de qué día es cada mensaje, y como abarca
    varios días el modelo tomaba un add_expense de hace semanas por uno de hoy:
    "las empanadas de $2.200 ya figuran en tu historial de hoy" y no registraba.
    SQLite guarda `created_at` en UTC sin zona horaria.
    """
    if not created_at:
        return ""
    local = created_at.replace(tzinfo=timezone.utc).astimezone(AR_TZ)
    return f"[{local:%d/%m/%Y %H:%M}] "


def _load_history(db: Session, chat_id: str) -> list:
    """Reconstruye el historial en formato de la API, incluyendo los turnos de tools."""
    rows = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.chat_id == chat_id)
        # Desempate por id: created_at en SQLite tiene precisión de segundo, así
        # que dos mensajes del mismo segundo salían en orden arbitrario.
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(MAX_HISTORY)
        .all()
    )

    messages = []
    for row in reversed(rows):
        if row.content_json:
            try:
                content = json.loads(row.content_json)
            except json.JSONDecodeError:
                logger.warning(f"content_json inválido en el mensaje {row.id}, se ignora")
                continue
        else:
            content = row.content
            if content and row.role == "user":
                content = _timestamp(row.created_at) + content
        if not content:
            continue
        messages.append({"role": row.role, "content": content})

    return _merge_same_role(_drop_orphan_tool_results(messages))


def _persist_turn(db: Session, chat_id: str, turn_messages: list) -> None:
    """Guarda los turnos de tool_use/tool_result que generó este mensaje.

    Es lo que permite que el próximo mensaje sepa qué se ejecutó de verdad. Sin
    esto el modelo solo ve su propio texto y, ante la duda, vuelve a registrar
    los gastos del mensaje anterior.
    """
    for msg in turn_messages:
        db.add(ConversationMessage(
            chat_id=chat_id,
            role=msg["role"],
            content="",
            content_json=json.dumps(msg["content"], ensure_ascii=False),
        ))


async def chat(user_message: str, chat_id: str, db: Session) -> str:
    """Procesa un mensaje de texto del usuario y retorna la respuesta del agente."""
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Guardar mensaje del usuario
    db.add(ConversationMessage(chat_id=chat_id, role="user", content=user_message))
    db.flush()

    messages = _load_history(db, chat_id)
    history_len = len(messages)

    # Cargar categorías vivas desde la hoja Categorías (con descripciones) para el system prompt
    try:
        cat_data = await fetch_categories()
        categories_text = _build_categories_text(cat_data)
    except Exception as e:
        logger.warning(f"No se pudieron cargar categorías para el chat: {e}")
        categories_text = ""

    system_prompt = _build_system_prompt(db, chat_id, categories_text)

    async def _exec(name: str, tool_input: dict) -> str:
        return await _execute_tool(name, tool_input, db, chat_id)

    text = None
    error = None
    try:
        text = await run_agent(
            client=client,
            model=MODEL,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
            execute_tool=_exec,
        )
    except APIStatusError as e:
        error = friendly_api_error(e)
    except Exception as e:
        # Se atrapa acá y no en el handler a propósito: si la excepción sube, la
        # sesión hace rollback y se pierde el registro de los tools que YA se
        # ejecutaron, que es exactamente lo que hace que se repitan después.
        logger.error(f"Error en el loop del agente: {e}", exc_info=True)
        error = "Ocurrió un error procesando tu mensaje. Intentá de nuevo."

    # Se persiste SIEMPRE, incluso si el loop se cortó a la mitad: los tools ya
    # se ejecutaron y el próximo turno tiene que verlos para no repetirlos.
    _persist_turn(db, chat_id, messages[history_len:])

    if error:
        return error

    if text:
        db.add(ConversationMessage(chat_id=chat_id, role="assistant", content=text))
        return text

    return "No pude generar una respuesta. Intentá de nuevo."
