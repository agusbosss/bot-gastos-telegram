import base64
import hashlib
import json
import logging
import uuid
import httpx
from anthropic import AsyncAnthropic
from config import get_settings

logger = logging.getLogger(__name__)


class ExpenseUnconfirmed(Exception):
    """No se pudo confirmar si la escritura llegó a la hoja.

    Timeout, corte de red o respuesta ilegible de Google. NO es lo mismo que un
    fracaso: Apps Script sigue ejecutando aunque el cliente corte, así que la fila
    puede haber quedado escrita igual. Nunca hay que decirle al usuario que el
    gasto no se registró, ni volver a mandarlo sin el mismo request_id.
    """


# Apps Script responde recién cuando terminó de escribir Y formatear. Con 15s
# se cortaban escrituras que sí habían entrado. El connect sigue corto porque un
# problema para conectar sí es un fallo rápido y real.
APPS_SCRIPT_TIMEOUT = httpx.Timeout(45.0, connect=10.0)

# Un solo reintento, siempre con el mismo request_id: si la primera pasada llegó
# a escribir, el Apps Script deduplica; si no llegó, esta escribe.
APPS_SCRIPT_ATTEMPTS = 2

# Fallback si no se pueden obtener del Sheet (mismas que crea setupPlanilla()).
DEFAULT_CATEGORIES = [
    {"name": "Servicios", "description": "Luz, gas, internet, teléfono, streaming, seguros"},
    {"name": "Compras", "description": "Compras de supermercado y productos varios"},
    {"name": "Salida", "description": "Salidas, restaurantes, bares, ocio, entretenimiento"},
    {"name": "Extra", "description": "Gastos extras no recurrentes"},
    {"name": "Salud", "description": "Medicamentos, consultas médicas, farmacia"},
    {"name": "Transporte", "description": "Colectivo, subte, taxi, combustible, peajes, SUBE"},
    {"name": "Educación", "description": "Facultad, cursos, útiles, fotocopias, cafés en la facu"},
    {"name": "Limpieza", "description": "Detergentes, elementos de limpieza, desinfectantes"},
    {"name": "Higiene Personal", "description": "Jabón, shampoo, dentífrico, desodorante"},
    {"name": "Tecnología", "description": "Electrónica, accesorios, software"},
    {"name": "Ropa", "description": "Cualquier prenda de ropa o accesorio de vestir"},
    {"name": "Golosinas", "description": "Dulces, chocolates, golosinas, snacks"},
    {"name": "Gimnasio", "description": "Cuota del gimnasio, suplementos, elementos deportivos"},
    {"name": "Hogar", "description": "Muebles, decoración, arreglos de la casa"},
    {"name": "Otros", "description": "Lo que no encaje en otra categoría"},
]

DEFAULT_SUBCATEGORIES = [
    {"name": "Lácteos", "parent": "Compras"},
    {"name": "Carne roja", "parent": "Compras"},
    {"name": "Carne de pollo", "parent": "Compras"},
    {"name": "Carne de cerdo", "parent": "Compras"},
    {"name": "Carne de pescado", "parent": "Compras"},
    {"name": "Fruta", "parent": "Compras"},
    {"name": "Verdura", "parent": "Compras"},
    {"name": "Huevos", "parent": "Compras"},
    {"name": "Panadería", "parent": "Compras"},
    {"name": "Alacena", "parent": "Compras", "description": "Pastas secas, enlatados, conservas, legumbres, arroz"},
    {"name": "Infusiones", "parent": "Compras", "description": "Yerba mate, café, té"},
    {"name": "Bebidas", "parent": "Compras"},
    {"name": "Alcohol", "parent": "Compras"},
    {"name": "Snacks y Golosinas", "parent": "Golosinas"},
    {"name": "Limpieza del Hogar", "parent": "Limpieza"},
    {"name": "Higiene Personal", "parent": "Higiene Personal"},
    {"name": "Otros", "parent": "Otros"},
]


async def fetch_dolar_cripto() -> float | None:
    """Obtiene el tipo de cambio del dólar cripto (venta) desde dolarapi.com."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://dolarapi.com/v1/dolares/cripto",
                timeout=5,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
            rate = float(data.get("venta") or 0)
            if rate:
                logger.info(f"Dólar cripto venta: {rate}")
            return rate or None
    except Exception as e:
        logger.warning(f"No se pudo obtener el tipo de cambio cripto: {e}")
        return None


async def fetch_categories() -> dict:
    """Obtiene categorías y subcategorías desde Google Sheets via Apps Script."""
    settings = get_settings()
    try:
        params = {"action": "get_categories", "token": settings.apps_script_secret}
        async with httpx.AsyncClient() as client:
            resp = await client.get(settings.apps_script_url, params=params, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            result = resp.json()

        if result.get("status") == "ok":
            return {
                "categories": result.get("categories", []),
                "subcategories": result.get("subcategories", []),
            }
    except Exception as e:
        logger.warning(f"No se pudieron obtener categorías del Sheet, usando defaults: {e}")

    return {
        "categories": DEFAULT_CATEGORIES,
        "subcategories": DEFAULT_SUBCATEGORIES,
    }


def _build_categories_text(cat_data: dict) -> str:
    """Formatea categorías y subcategorías para el prompt de Claude."""
    lines = []

    lines.append("**Categorías disponibles:**")
    for cat in cat_data.get("categories", []):
        desc = cat.get("description", "")
        if desc:
            lines.append(f"- Categoría: \"{cat['name']}\" — {desc}")
        else:
            lines.append(f"- Categoría: \"{cat['name']}\"")

    lines.append("")
    lines.append("**Subcategorías (para productos específicos en tickets):**")
    for sub in cat_data.get("subcategories", []):
        desc = sub.get("description", "")
        if desc:
            lines.append(f"- Subcategoría: \"{sub['name']}\" → Categoría padre: \"{sub['parent']}\" — {desc}")
        else:
            lines.append(f"- Subcategoría: \"{sub['name']}\" → Categoría padre: \"{sub['parent']}\"")

    return "\n".join(lines)


def _build_extraction_prompt(categories_text: str, ticket_rules: list[str] | None = None, multi_image: bool = False) -> str:
    rules_block = ""
    if ticket_rules:
        rules_lines = "\n".join(f"- {rule}" for rule in ticket_rules)
        rules_block = f"""

**Reglas especiales del usuario (tienen prioridad sobre las categorías estándar):**
{rules_lines}
"""

    if multi_image:
        intro = "Analizá estas fotos que son PARTES DE UN MISMO ticket de compra. Combiná toda la información en un solo resultado."
    else:
        intro = "Analizá esta foto de un ticket de compra o factura."

    return f"""{intro}
Extraé TODOS los productos con su cantidad, precio unitario y precio total.
Para cada producto, asignale una subcategoría Y su categoría padre correspondiente de esta lista:

{categories_text}
{rules_block}
**MUY IMPORTANTE — Estructura de los tickets argentinos:**

Cada producto en el ticket tiene este formato:
```
NOMBRE DEL PRODUCTO                    PRECIO
código de barras
Tarjeta Comunidad 15 [M]              -DESCUENTO
```
o también:
```
CANTIDAD x PRECIO_UNITARIO
NOMBRE DEL PRODUCTO                    PRECIO_TOTAL
código de barras
2 *25% MARCAS [A]                      -DESCUENTO
```

Las líneas que empiezan con "Tarjeta Comunidad", "*25% MARCAS", "*ALCO 25%", "*HISPANOS 25%", "*Descuento", etc. son DESCUENTOS, NO productos. NUNCA las cuentes como ítems.
El número al inicio de estas líneas (ej: "2" en "2 *25% MARCAS") indica cuántos descuentos se aplicaron, NO la cantidad comprada. IGNORALO para qty.

**Cómo extraer cada producto:**
- `qty`: SOLO si arriba del nombre hay una línea tipo "CANTIDAD x PRECIO" (ej: "2,000 x 1850,00"). Si no hay esa línea, qty = 1.
- `unit_price`: el precio en la línea del producto (a la derecha del nombre)
- `discount`: el valor absoluto del monto negativo en la línea de descuento de abajo (ej: -546,50 → 546.50). Si no tiene descuento, 0.
- `total_price`: unit_price - discount (ya que unit_price ya incluye qty×precio cuando hay multiplicación)

**Ejemplo real:**
```
PAÑUELOS CAMPANITA6X10 PAQ 60 UNI      2185,99
0000174021 07791070003482
2 *25% MARCAS    [A]                    -546,50
```
→ qty=1, unit_price=2185.99, discount=546.50, total_price=1639.49
(El "2" en "2 *25% MARCAS" NO es la cantidad. Se compró 1 paquete con $546,50 de descuento)

```
2,000 x 1850,00
FIDEO TIRABUZON Nº 28MATARAZZO PAQ 500  3700,00
Tarjeta Comunidad 15 [M]               -555,00
```
→ qty=2, unit_price=1850.00, discount=555.00, total_price=3145.00

El campo `total` debe ser el total final del ticket (después de todos los descuentos).

Respondé ÚNICAMENTE con un JSON válido con esta estructura exacta:
{{
  "store": "nombre del comercio o 'Desconocido'",
  "date": "DD/MM/YYYY o 'hoy' si no se ve",
  "currency": "ARS",
  "total": número con el total del ticket (después de descuentos),
  "items": [
    {{
      "name": "nombre del producto",
      "category": "subcategoría exacta de la lista",
      "general_category": "categoría padre exacta de la lista",
      "qty": número,
      "unit_price": número,
      "discount": número (0 si no tiene descuento),
      "total_price": número (precio final con descuento aplicado)
    }}
  ]
}}

Si no podés leer algún dato, usá null. No incluyas texto fuera del JSON."""


async def analyze_receipt(images: list[tuple[bytes, str]], ticket_rules: list[str] | None = None) -> dict:
    """Analiza una o varias imágenes de un ticket con Claude Vision.

    Args:
        images: Lista de tuplas (image_bytes, mime_type).
        ticket_rules: Reglas de tickets del usuario.
    """
    settings = get_settings()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    cat_data = await fetch_categories()
    categories_text = _build_categories_text(cat_data)
    multi = len(images) > 1
    prompt = _build_extraction_prompt(categories_text, ticket_rules, multi_image=multi)

    # Construir bloques de contenido: todas las imágenes + el prompt
    content_blocks = []
    for img_bytes, mime_type in images:
        image_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": image_b64,
            },
        })

    content_blocks.append({
        "type": "text",
        "text": prompt,
    })

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": content_blocks}],
    )

    text = message.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def _request_id(*parts) -> str:
    """ID determinístico a partir del contenido del gasto.

    Determinístico a propósito: el mismo gasto mandado dos veces (un reintento de
    red, o el modelo re-disparando el tool un par de mensajes después) genera el
    mismo ID, y el Apps Script lo descarta en vez de duplicar la fila.
    """
    raw = "|".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _normalize_date(date: str | None) -> str:
    """dd/mm/yyyy con ceros, para el ID: '1/8/2026' y '01/08/2026' son el mismo gasto."""
    if not date:
        return "hoy"
    parts = date.strip().split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{int(parts[0]):02d}/{int(parts[1]):02d}/{parts[2]}"
    return date.strip()


async def _check_request(request_id: str) -> dict | None:
    """Pregunta al Apps Script si ese request_id llegó a escribirse.

    Se usa cuando se cortó la respuesta: en vez de dejar al usuario con un
    "no sé si entró", se consulta. Devuelve la respuesta original si la
    escritura existe, None si no existe o si tampoco se pudo consultar.
    """
    settings = get_settings()
    params = {
        "action": "check_request",
        "token": settings.apps_script_secret,
        "request_id": request_id,
    }
    try:
        async with httpx.AsyncClient(timeout=APPS_SCRIPT_TIMEOUT) as client:
            resp = await client.get(settings.apps_script_url, params=params, follow_redirects=True)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        logger.warning(f"No se pudo verificar el request {request_id}: {e!r}")
        return None

    if result.get("status") == "ok" and result.get("found"):
        return result.get("result") or {"status": "ok"}
    return None


async def _post_to_apps_script(payload: dict) -> dict:
    """POST al Web App con reintento idempotente y errores bien tipados.

    Raises:
        ExpenseUnconfirmed: no hubo respuesta usable (timeout, red, 5xx, HTML).
            La fila PUEDE haber quedado escrita.
        RuntimeError: el Apps Script respondió y dijo que falló.
    """
    settings = get_settings()
    payload_bytes = json.dumps(payload).encode("utf-8")
    action = payload.get("action")
    last_error: Exception | None = None

    for attempt in range(1, APPS_SCRIPT_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=APPS_SCRIPT_TIMEOUT) as client:
                resp = await client.post(
                    settings.apps_script_url,
                    content=payload_bytes,
                    headers={"Content-Type": "application/json"},
                    follow_redirects=False,
                )
                # Apps Script contesta 302 al POST y ejecuta recién en el GET.
                if resp.status_code in (301, 302, 303):
                    resp = await client.get(resp.headers["location"], follow_redirects=True)
                resp.raise_for_status()
                result = resp.json()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
            # No sabemos si escribió: el reintento lleva el mismo request_id, así
            # que si ya había escrito el Apps Script devuelve duplicate en vez de
            # escribir otra fila.
            last_error = e
            logger.warning(
                f"Apps Script {action}: sin respuesta usable (intento {attempt}/{APPS_SCRIPT_ATTEMPTS}): {e!r}"
            )
            continue

        logger.info(f"Apps Script {action} response: {result}")
        if result.get("status") != "ok":
            error_msg = result.get("message") or result.get("error") or "Respuesta inesperada"
            raise RuntimeError(f"Apps Script: {error_msg}")

        if attempt > 1 and result.get("duplicate"):
            # El duplicado lo causó nuestro propio reintento: quiere decir que el
            # primer intento sí había escrito. Para el usuario es un éxito normal,
            # no "ya lo tenías registrado".
            logger.info(f"Apps Script {action}: el intento 1 había escrito, el reintento dedupeó.")
            result.pop("duplicate", None)
        return result

    # Se acabaron los intentos sin respuesta usable. Antes de darlo por no
    # confirmado, preguntar si igual quedó escrito (Apps Script sigue ejecutando
    # aunque nosotros cortemos, que es justo lo que pasaba con los timeouts).
    request_id = payload.get("request_id")
    if request_id:
        confirmed = await _check_request(request_id)
        if confirmed is not None:
            logger.info(f"Apps Script {action}: se cortó la respuesta pero la escritura sí entró.")
            confirmed.pop("duplicate", None)
            return confirmed

    raise ExpenseUnconfirmed(str(last_error) if last_error else "Google no respondió")


async def send_to_sheets(expense_data: dict) -> dict:
    """Envía los datos del ticket al Google Apps Script Web App.

    Returns:
        dict con `duplicate` (el ticket ya estaba registrado) y `rows_added`.
    """
    settings = get_settings()
    dolar_rate = await fetch_dolar_cripto()
    items = expense_data.get("items", [])
    payload = {
        "token": settings.apps_script_secret,
        "action": "add_expense",
        "store": expense_data.get("store", "Desconocido"),
        "date": expense_data.get("date", "hoy"),
        "currency": expense_data.get("currency", "ARS"),
        "total": expense_data.get("total"),
        "items": items,
        "dolar_rate": dolar_rate,
        "request_id": _request_id(
            "ticket",
            expense_data.get("store"),
            _normalize_date(expense_data.get("date")),
            expense_data.get("total"),
            len(items),
            items[0].get("name") if items else "",
        ),
    }
    result = await _post_to_apps_script(payload)
    return {
        "duplicate": bool(result.get("duplicate")),
        "rows_added": result.get("rows_added", 0),
        "gastos_rows_added": result.get("gastos_rows_added", 0),
    }


async def send_manual_expense(
    name: str,
    category: str,
    amount: float,
    date: str | None = None,
    subcategory: str | None = None,
    store: str | None = None,
    currency: str = "ARS",
    allow_duplicate: bool = False,
) -> dict:
    """Envía un gasto manual al Apps Script para escribir en la hoja Gastos.

    El monto puede venir en pesos (ARS) o dólares (USD). Si viene en USD, se
    convierte a pesos con el dólar cripto y se manda el monto en ARS; el Apps
    Script guarda ambas columnas (Monto $ y Monto U$D) usando el mismo tipo de
    cambio. Si se proporciona subcategory, también se escribe en la hoja Compras.

    Args:
        allow_duplicate: saltea la deduplicación. Solo cuando el usuario pide
            explícitamente registrar de nuevo un gasto idéntico y reciente.

    Returns:
        dict con amount_ars, amount_usd (si hay tipo de cambio), dolar_rate y
        `duplicate` (el gasto ya estaba registrado y no se escribió otra fila).

    Raises:
        ExpenseUnconfirmed: no se pudo confirmar la escritura (puede haber entrado).
    """
    settings = get_settings()
    dolar_rate = await fetch_dolar_cripto()

    currency = (currency or "ARS").upper()
    amount = float(amount)
    if currency == "USD":
        if not dolar_rate:
            raise RuntimeError(
                "No pude obtener el tipo de cambio del dólar para convertir el gasto a pesos."
            )
        amount_ars = round(amount * dolar_rate, 2)
        amount_usd = amount
    else:
        amount_ars = amount
        amount_usd = round(amount / dolar_rate, 2) if dolar_rate else None

    # El ID sale del contenido del gasto, no del tipo de cambio: si el dólar se
    # movió entre el intento original y el reintento, sigue siendo el mismo gasto.
    request_id = _request_id(
        "manual", name, category, amount, currency, _normalize_date(date), subcategory, store
    )
    if allow_duplicate:
        request_id = uuid.uuid4().hex  # el usuario lo pidió: que no deduplique

    payload = {
        "token": settings.apps_script_secret,
        "action": "add_manual_expense",
        "name": name,
        "category": category,
        "amount": amount_ars,
        "request_id": request_id,
    }
    if date:
        payload["date"] = date
    if subcategory:
        payload["subcategory"] = subcategory
    if store:
        payload["store"] = store
    if dolar_rate:
        payload["dolar_rate"] = dolar_rate

    logger.info(
        f"Sending manual expense to Apps Script: name={name}, category={category}, "
        f"currency={currency}, amount_ars={amount_ars}, dolar_rate={dolar_rate}, "
        f"request_id={request_id}"
    )
    result = await _post_to_apps_script(payload)
    return {
        "amount_ars": amount_ars,
        "amount_usd": amount_usd,
        "dolar_rate": dolar_rate,
        "currency": currency,
        "duplicate": bool(result.get("duplicate")),
    }


def _escape_md(text: str) -> str:
    """Escapa caracteres especiales de Markdown V1 de Telegram."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_receipt_summary(expense_data: dict, closing: str = "\n✅ Todo guardado en tu Google Sheet.") -> str:
    """Resumen del ticket. `closing` es la última línea: la decide quien guarda,
    porque solo ahí se sabe si la escritura entró, se duplicó o no se pudo confirmar."""
    store = _escape_md(str(expense_data.get("store", "Desconocido")))
    date = _escape_md(str(expense_data.get("date", "hoy")))
    items = expense_data.get("items", [])

    try:
        total = float(expense_data.get("total") or 0) or None
    except (TypeError, ValueError):
        total = None

    by_category: dict[str, float] = {}
    for item in items:
        cat = item.get("general_category", "Otros")
        try:
            total_price = float(item.get("total_price") or 0)
        except (TypeError, ValueError):
            total_price = 0.0
        by_category[cat] = by_category.get(cat, 0) + total_price

    lines = [f"🧾 *Ticket registrado* — {store} ({date})"]
    lines.append(f"📦 {len(items)} producto(s) | 💰 Total: ${total:,.0f}" if total else f"📦 {len(items)} producto(s)")
    lines.append("")
    lines.append("*Por categoría:*")
    for cat, subtotal in sorted(by_category.items(), key=lambda x: -x[1]):
        lines.append(f"  • {_escape_md(cat)}: ${subtotal:,.0f}")
    lines.append(closing)
    return "\n".join(lines)
