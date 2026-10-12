"""
Loop agéntico de Claude, genérico y reusable por cualquier canal.

Un canal aporta: system prompt, lista de tools, y una función que ejecuta una
tool por nombre. Este módulo maneja el bucle tool_use y la traducción de errores
de la API a mensajes amigables. No sabe nada de gastos, noticias, etc.
"""
import json
import logging
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic, APIStatusError

logger = logging.getLogger(__name__)

# Ejecutor de tools: (nombre, input) -> string JSON con el resultado.
ToolExecutor = Callable[[str, dict], Awaitable[str]]


def _block_to_dict(block) -> dict:
    """Content block de la respuesta → dict JSON-serializable.

    Los turnos de tool_use tienen que poder guardarse en la base tal cual para
    que el próximo mensaje del usuario los vea. Se descartan las claves en None
    (ej: `citations`) para no ensuciar el payload que vuelve a la API.
    """
    data = block.model_dump()
    return {k: v for k, v in data.items() if v is not None}


async def run_agent(
    *,
    client: AsyncAnthropic,
    model: str,
    system: str,
    tools: list,
    messages: list,
    execute_tool: ToolExecutor,
    max_tokens: int = 4096,
) -> str | None:
    """Corre el bucle tool_use hasta que Claude devuelve texto final.

    `messages` se muta in-place (se le agregan los turnos de tool_use/result) con
    dicts serializables, así el caller puede persistir el turno completo.
    Devuelve el texto final o None.
    """
    while True:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        logger.info(f"stop_reason={response.stop_reason}, blocks={len(response.content)}")

        if response.stop_reason == "tool_use":
            messages.append({
                "role": "assistant",
                "content": [_block_to_dict(b) for b in response.content],
            })

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Ejecutando tool: {block.name} con {block.input}")
                    result = await execute_tool(block.name, block.input)
                    tool_result = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                    try:
                        if json.loads(result).get("ok") is False:
                            tool_result["is_error"] = True
                    except Exception:
                        pass
                    tool_results.append(tool_result)

            messages.append({"role": "user", "content": tool_results})
            continue

        # end_turn / max_tokens / otro: extraer texto
        text = next(
            (block.text for block in response.content if hasattr(block, "text")),
            None,
        )
        if text:
            logger.info(f"Respuesta final sin tool_use: {text[:120]!r}")
            return text

        logger.warning(
            f"Respuesta sin texto. stop_reason={response.stop_reason}, content={response.content}"
        )
        return None


def friendly_api_error(e: APIStatusError) -> str:
    """Traduce un error de la API de Claude a un mensaje para el usuario."""
    logger.error(f"Error de API de Claude: {e.status_code} - {e.message}")
    if e.status_code in (401, 403) or "credit" in e.message.lower() or "billing" in e.message.lower():
        return "⚠️ Se agotaron los créditos de Claude o hay un problema de facturación. Revisá tu cuenta en console.anthropic.com."
    if e.status_code == 429:
        return "⚠️ Se superó el límite de requests de la API de Claude. Esperá un momento e intentá de nuevo."
    return f"⚠️ Error de la API de Claude (HTTP {e.status_code}). Intentá de nuevo más tarde."
