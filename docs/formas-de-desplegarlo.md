# Formas de desplegar el bot

Este documento lista todas las formas de poner el bot a funcionar, con lo que
cuesta cada una, lo que hay que cambiar en el código y los riesgos. Está escrito
para que una persona o una IA que agarre el repo pueda elegir un camino y
ejecutarlo sin tener que redescubrir nada.

**Estado de cada opción:**

| | Opción | Estado en el repo | Costo mensual |
|---|---|---|---|
| A | Python en Railway | **Implementada** (es lo que describe el README) | USD 5 + Claude |
| B | Python en otro hosting de contenedores | Funciona con cambios de configuración | De $0 a USD 5 + modelo |
| C | Python en una compu propia o Raspberry Pi | Requiere agregar el modo *polling* | $0 + modelo |
| D | Todo en Apps Script, sin servidor | **No implementada**: requiere reescribir el bot en JavaScript | $0 + modelo |
| E | Cambiar Claude por otro modelo | **No implementada**: se combina con A, B, C o D | Puede ser $0 |

El camino 100% gratis es **D + E con un modelo de capa gratuita** (por ejemplo
Gemini).

---

## Cómo funciona hoy (lo que no cambia en ninguna opción)

```
Telegram ──webhook──▶ bot (Python) ──▶ Claude (texto, fotos)
                          │        ──▶ Groq / Whisper (audios)
                          │        ──▶ dolarapi.com (dólar cripto)
                          │
                          └──POST──▶ Apps Script (Web App) ──▶ Google Sheets
```

Las piezas y qué hace cada una:

| Pieza | Archivo | Responsabilidad |
|---|---|---|
| Entrada de Telegram | `main.py` | Recibe updates en `/webhook/gastos`, valida `WEBHOOK_SECRET` y el chat autorizado (`GASTOS_CHAT_ID`). |
| Ruteo por tipo de mensaje | `channels/gastos/handler.py` | Texto → agente. Voz → transcribe y pasa al agente. Foto → lectura de ticket. Álbum → junta las fotos 3 s y las procesa como un solo ticket. |
| Agente | `channels/gastos/agent.py` + `channels/llm.py` | Prompt, herramientas (`add_expense`, `save_memory`, `save_ticket_rule`, `list_memories`, `delete_memory`, `save_reminder`) y loop de tool use. Guarda el historial **incluyendo los tool_use y tool_result**. |
| Tickets | `tools/expenses.py` → `analyze_receipt` | Manda las fotos a Claude con un prompt específico para tickets argentinos (descuentos, cantidades) y recibe JSON. |
| Envío a la planilla | `tools/expenses.py` → `_post_to_apps_script` | POST con token, `request_id` determinístico, un reintento y verificación con `check_request` si se cortó la respuesta. |
| Audios | `tools/transcription.py` | Whisper vía Groq, forzado a español. |
| Persistencia | `db/` (SQLite) | Historial, memorias (pools `general` y `tickets`) y recordatorios. |
| Recordatorios | `scheduler.py` | Revisa cada minuto los vencidos y los manda. |
| Planilla | `appscript/Code.gs`, `Formatting.gs`, `Setup.gs` | `doPost` (escribir gastos), `doGet` (categorías y `check_request`), `onEdit`, `onOpen`, formato, `setupPlanilla()`. |

La parte de Apps Script es la misma en **todas** las opciones. En la opción D
además absorbe el resto del bot.

---

## Reglas que cualquier versión tiene que respetar

Salieron de errores reales en producción. Cada una costó gastos perdidos o
duplicados.

1. **Idempotencia por `request_id`.** Cada gasto lleva un ID derivado de su
   contenido (`_request_id` en `tools/expenses.py`) y el Apps Script lo descarta
   durante 15 minutos si se repite (`_withWriteLock` en `Code.gs`, dentro de un
   `LockService`). Es lo único que decide si algo es duplicado.
2. **El modelo nunca decide que un gasto "ya estaba registrado".** Todo gasto
   del último mensaje es nuevo, aunque en el historial haya uno idéntico (la
   gente compra lo mismo seguido). Si el modelo tiene criterio propio sobre
   duplicados, deja de registrar gastos legítimos.
3. **El modelo nunca pide aclaración de categoría.** Elige la más probable,
   registra y avisa cuál usó. Lo único que frena un registro es que falte el
   monto. Un modelo que pregunta y se queda esperando pierde el gasto.
4. **Cada mensaje del usuario en el historial lleva fecha y hora**
   (`[dd/mm/aaaa hh:mm]`, ver `_timestamp` en `agent.py`). Sin eso, el modelo
   confunde gastos de días distintos.
5. **El historial guarda los tool_use y tool_result**, no solo el texto. Sin
   eso, el modelo no sabe qué ejecutó y vuelve a registrar gastos anteriores.
6. **Nunca afirmar "registrado" sin haber llamado a la herramienta.** Un modelo
   chico (Haiku) lo hacía: contestaba "Registrado ✅" sin escribir nada. Al
   cambiar de modelo, esto es lo primero que hay que probar.
7. **Un timeout no es un fracaso.** Apps Script sigue ejecutando aunque el
   cliente corte. Si no hay respuesta, se pregunta con `check_request`; si
   tampoco se puede confirmar, se le dice al usuario que lo revise. Nunca se
   reintenta con otro `request_id`.
8. **Lo que pasa después de escribir la fila es cosmético** (`_bestEffort`): un
   error ordenando o coloreando no puede convertirse en "no se pudo registrar".
9. **Una sola función `onEdit` y una sola `onOpen`** en todo el proyecto de Apps
   Script: todos los `.gs` comparten un mismo espacio de nombres y una pisa a la
   otra. No agregar un trigger instalable de `onEdit` además del simple: corre
   dos veces.
10. **La planilla usa locale `es_AR`**: el bot manda las fechas como
    `dd/MM/yyyy` y Sheets tiene que interpretarlas como día/mes.
11. **La columna F de Gastos queda vacía.** El orden por fecha solo mueve las
    columnas con encabezado contiguas desde la A (`_tableWidth`); el resumen de
    G:J se queda quieto.
12. **Un cambio en los `.gs` no se publica solo.** Hay que ir a Gestionar
    implementaciones → editar la existente → Nueva versión. Una implementación
    nueva cambia la URL.

---

## A. Python en Railway (implementada)

Es la instalación del [README](../README.md). El repo ya trae `Dockerfile` y
`railway.toml`.

- **Costo:** plan Hobby de Railway (USD 5 por mes) + Claude por uso. Railway no
  tiene plan gratis permanente, solo un crédito de prueba inicial.
- **Persistencia:** sin volumen, cada deploy borra la base SQLite (historial,
  memorias, recordatorios). Montar un volumen en `/data` y usar
  `DATABASE_URL=sqlite:////data/agent.db`.
- **Riesgo conocido:** cada deploy reinicia el servicio durante 30 a 60
  segundos. Los mensajes que llegan en ese momento se pierden sin dejar error.

## B. Python en otro hosting de contenedores

El `Dockerfile` corre en cualquier lado que acepte contenedores y exponga HTTPS.
Hay que cambiar solo la configuración, no el código:

1. Cargar las mismas variables de entorno que en Railway (ver `.env.example`).
2. Exponer el puerto de la variable `PORT` (el `Dockerfile` ya lo usa).
3. Resolver dónde vive la base SQLite (ver la tabla).
4. Registrar el webhook con la URL nueva (paso 5 del README).

| Hosting | Costo | Cuidado con |
|---|---|---|
| Render | Plan gratis | Se duerme tras un rato sin tráfico: el primer mensaje tarda decenas de segundos. El disco del plan gratis no persiste. |
| Google Cloud Run | Capa gratuita amplia para uso personal | Pide tarjeta. Escala a cero: el disco no persiste, así que la base tiene que ir a otro lado. Pasar `--min-instances=0`. El scheduler de recordatorios no corre si no hay instancia viva: reemplazarlo por Cloud Scheduler llamando a un endpoint. |
| Fly.io, Koyeb y similares | Varía; revisar el plan vigente | Persistencia del disco y si el plan duerme la app. |
| VPS (Hetzner, DigitalOcean, Oracle Cloud Always Free) | De $0 (Oracle) a pocos dólares | Hay que mantener el servidor: Docker, HTTPS (Caddy o Nginx + Let's Encrypt) y reinicio automático. |

**Si el hosting no tiene disco persistente**, hay dos salidas:

- Una base externa: `DATABASE_URL` acepta cualquier URL de SQLAlchemy (por
  ejemplo, Postgres gratis en Neon o Supabase). Hay que agregar el driver a
  `requirements.txt` (`psycopg[binary]`). `db/database.py` pasa
  `check_same_thread=False`, que es solo para SQLite: hay que condicionarlo.
- Aceptar perder historial y memorias en cada reinicio. El bot sigue
  registrando gastos; lo que se pierde es contexto y recordatorios pendientes.

**Si el hosting duerme la app**, el scheduler de recordatorios no corre mientras
duerme. Los recordatorios salen tarde o hay que dispararlos desde afuera.

## C. Python en una compu propia o Raspberry Pi

Gratis y sin cambios de lógica, pero el bot responde solo mientras la máquina
está prendida y con internet.

Hay dos formas de recibir los mensajes:

1. **Webhook + túnel** (sin tocar código): exponer el puerto 8000 con
   [ngrok](https://ngrok.com) o Cloudflare Tunnel y registrar el webhook con esa
   URL. La URL gratuita de ngrok cambia en cada reinicio; Cloudflare Tunnel con
   dominio propio no cambia.
2. **Polling** (requiere código, no está implementado): el bot le pregunta a
   Telegram si hay mensajes nuevos, sin necesidad de URL pública. Para
   agregarlo:
   - Llamar a `deleteWebhook` (un bot no puede tener webhook y polling a la vez).
   - Agregar en `main.py` un loop en el `lifespan` que haga
     `await bot.get_updates(offset=..., timeout=30)` y pase cada update a
     `_dispatch(rt, update)`, avanzando `offset` a `update_id + 1`.
   - Respetar `is_background()` para los álbumes, igual que `_process_webhook`.
   - Hacerlo opcional con una variable (por ejemplo `TELEGRAM_MODE=polling`)
     para no romper el modo webhook.

Para que arranque solo: `systemd`, `docker run --restart unless-stopped` o
`docker compose`.

## D. Todo en Apps Script, sin servidor (no implementada)

El bot entero vive en el proyecto de Apps Script de la planilla. No hay hosting:
el costo es solo el del modelo. La instalación se reduce a pegar archivos,
cargar keys en Propiedades del script, publicar el Web App y registrar el
webhook.

### Arquitectura

```
Telegram ──webhook──▶ doPost (Apps Script) ──▶ modelo (UrlFetchApp)
                           │                ──▶ dolarapi.com
                           └──▶ escribe directo en la planilla (sin HTTP intermedio)
```

Cómo se traduce cada pieza:

| Python | Apps Script |
|---|---|
| `main.py` (webhook) | `doPost(e)`. Hay que distinguir un update de Telegram (tiene `update_id`) de los requests actuales del bot (tienen `action`), o separarlos en dos proyectos. |
| `WEBHOOK_SECRET` | **No sirve igual:** `doPost` no ve los headers HTTP, así que no puede leer `X-Telegram-Bot-Api-Secret-Token`. Alternativa: poner un secreto en la URL del webhook (`.../exec?k=SECRETO`) y validarlo con `e.parameter.k`. |
| Chat autorizado | Comparar `update.message.chat.id` con una propiedad del script. |
| `channels/llm.py` (loop de tool use) | Un `while` con `UrlFetchApp.fetch` a la API del modelo, ejecutando cada herramienta como función JS. |
| `send_manual_expense` / `send_to_sheets` | Llamada directa a `_handleAddManualExpense(payload)` / `_handleAddExpense(payload)` dentro de `_withWriteLock`. Ya no hay HTTP en el medio, así que desaparecen los timeouts del punto 7 de las reglas. |
| `fetch_dolar_cripto` | `UrlFetchApp.fetch("https://dolarapi.com/v1/dolares/cripto")`. |
| Descarga de fotos y audios | `getFile` de la Bot API → `https://api.telegram.org/file/bot<TOKEN>/<file_path>` → `getBlob()` → base64 con `Utilities.base64Encode`. |
| `transcription.py` (Groq) | `UrlFetchApp` con `payload: { file: blob, model: ..., language: "es" }`: Apps Script arma el multipart solo. Con Gemini no hace falta, porque acepta el audio directo. |
| SQLite: historial | Hoja oculta `_historial` (chat_id, rol, JSON del contenido, fecha). Leer las últimas N filas. |
| SQLite: memorias | Hoja oculta `_memorias` (id, pool, contenido). |
| SQLite: recordatorios | Hoja oculta `_recordatorios` + trigger por tiempo cada 1 minuto (`ScriptApp.newTrigger(...).timeBased().everyMinutes(1)`) que manda los vencidos con `sendMessage`. |
| Respuestas | `UrlFetchApp` a `sendMessage` de la Bot API. |

### Problemas propios de esta opción y cómo resolverlos

1. **Telegram reintenta los updates.** Un Web App de Apps Script responde con
   una redirección 302 y Telegram puede tomarlo como fallo y reenviar el mismo
   update. Por eso, lo primero en `doPost` tiene que ser deduplicar por
   `update_id` con `CacheService` dentro de `LockService` (mismo patrón que
   `_withWriteLock`). Si no, cada mensaje puede procesarse dos veces. Si
   igualmente hay demasiados reintentos, la alternativa es **polling**: un
   trigger por tiempo cada 1 minuto que llama a `getUpdates` (con el webhook
   borrado). Es más robusto, pero el bot tarda hasta un minuto en contestar.
2. **No se puede contestar primero y procesar después.** `doPost` responde
   recién cuando termina. Un ticket con fotos puede tardar decenas de segundos
   y, si es demasiado, Telegram reintenta (ver 1). La deduplicación por
   `update_id` es obligatoria, no opcional.
3. **Límite de 6 minutos por ejecución** y cuotas diarias de `UrlFetchApp` y de
   triggers. Para uso personal sobra; conviene revisar las cuotas vigentes en la
   documentación de Google.
4. **Álbumes de fotos.** Cada foto del álbum llega como un update separado, con
   el mismo `media_group_id`. Opción simple: aceptar una foto por ticket en la
   primera versión. Opción completa: guardar cada foto en `CacheService` por
   `media_group_id` y procesar el grupo desde un trigger de un solo uso
   (`ScriptApp.newTrigger(...).timeBased().after(5000)`), borrando el trigger al
   terminar.
5. **Concurrencia.** Todas las escrituras (gastos, historial, memorias) pasan
   por `LockService`, porque dos mensajes seguidos corren en paralelo.
6. **Secretos.** Todas las keys van en `PropertiesService.getScriptProperties()`,
   nunca en el código, igual que `SECRET_TOKEN` hoy.

### Orden sugerido para implementarla

1. `doPost` que reconozca updates de Telegram, deduplique por `update_id`,
   valide el chat y conteste un eco con `sendMessage`. Registrar el webhook y
   probar.
2. Loop de tool use con el modelo elegido y la herramienta `add_expense`
   llamando a `_handleAddManualExpense`. Copiar el prompt de
   `channels/gastos/agent.py` **sin quitar las reglas** de la sección anterior.
3. Historial en hoja oculta, con fecha en cada mensaje y con los turnos de
   herramientas.
4. Fotos de tickets: copiar el prompt de `_build_extraction_prompt` de
   `tools/expenses.py` tal cual (tiene las reglas de descuentos de los tickets
   argentinos).
5. Audios.
6. Memorias y recordatorios.
7. Álbumes.

Cada paso se prueba solo antes de seguir. En el paso 2, probar varias veces que
el modelo **llame de verdad** a `add_expense` y no conteste "registrado" sin
hacerlo.

## E. Cambiar el modelo

Lo específico de Claude está en tres lugares:

| Lugar | Qué hace | Qué cambiar |
|---|---|---|
| `channels/llm.py` | Loop de tool use con el SDK de Anthropic | Adaptar el formato de herramientas, mensajes y `stop_reason` del proveedor nuevo. |
| `channels/gastos/agent.py` | `MODEL`, `TOOLS` en formato Anthropic, historial con bloques `tool_use` / `tool_result` | Traducir el esquema de las herramientas y el formato de los turnos guardados. El historial viejo en la base queda en formato Anthropic: borrarlo o migrarlo. |
| `tools/expenses.py` → `analyze_receipt` | Lectura de tickets con visión | Cambiar la llamada; el prompt y el JSON de salida se mantienen. |

Opciones:

| Modelo | Costo | Ventajas | Cuidado con |
|---|---|---|---|
| Claude Sonnet (actual) | Pago por uso | Muy confiable llamando herramientas y leyendo tickets. | Es lo único pago fuera del hosting. |
| Claude Haiku | Más barato | Rápido. | Ya se probó y a veces contestaba "registrado" sin llamar a la herramienta (regla 6). |
| Gemini (API de Google) | Tiene capa gratuita | Visión, audio directo (reemplaza a Groq) y function calling. La key sale de Google AI Studio con la misma cuenta de la planilla. Encaja natural con la opción D. | En la capa gratuita, Google puede usar los datos enviados para mejorar sus modelos: avisarle al usuario (son gastos y fotos de tickets). Los límites gratuitos cambian; verificarlos al implementar. |
| Modelos abiertos en Groq | Tiene capa gratuita | Rápidos; ya se usa Groq para los audios. | Suelen ser menos confiables con herramientas y con tickets largos. Probar bien antes. |
| OpenAI | Pago por uso | API muy parecida a la de Groq para audios. | Mismo trabajo de traducción que cualquier proveedor. |

Lo mejor es hacer el proveedor configurable (por ejemplo `LLM_PROVIDER` y una
interfaz común para `run_agent` y `analyze_receipt`), en vez de reemplazar uno
por otro.

**Prueba mínima antes de dar por bueno un modelo nuevo:**

1. "gasté 1500 en un café" → aparece la fila en Gastos.
2. El mismo mensaje otra vez, en un mensaje aparte → se registra de nuevo
   (regla 2).
3. "empanadas 5000" → registra sin preguntar la categoría (regla 3).
4. "20 dólares de Spotify" → la fila tiene las dos monedas.
5. Una foto de un ticket de supermercado con descuentos → los totales coinciden
   con el ticket.
6. "recordá que Farmacity va a Salud" y después un ticket de Farmacity → usa
   Salud.

---

## Cómo elegir

- **Lo más simple que ya funciona:** A.
- **Gratis, sin programar, aceptando que los datos los vea Google:** D + Gemini
  (hay que implementarlo).
- **Gratis, con el mismo código y una máquina siempre prendida:** C.
- **Gratis o casi, con el mismo código y sin máquina propia:** B en Cloud Run u
  Oracle Cloud, resolviendo la persistencia.
- **Máxima precisión:** cualquier opción con Claude Sonnet.
