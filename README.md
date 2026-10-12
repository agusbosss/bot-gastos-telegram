# Bot de gastos para Telegram

Un bot de Telegram que registra tus gastos en una planilla de Google Sheets. Le
escribís, le mandás un audio o le sacás una foto al ticket, y Claude lo entiende,
lo categoriza y lo carga solo.

```
"Gasté 5000 en el súper"        ─┐
🎙️ nota de voz                  ─┼─▶  bot (Claude)  ─▶  Google Sheets
📸 foto del ticket (o varias)    ─┘
```

## Qué hace

- **Gastos por texto o por voz.** "Almorcé en la facu 8500", "pagué la luz 32 mil",
  "20 dólares de Spotify". Detecta monto, categoría, fecha y comercio.
- **Tickets por foto.** Lee cada producto del ticket (con descuentos incluidos), lo
  carga en la hoja **Compras** con su subcategoría, y en **Gastos** deja una fila por
  categoría con el subtotal. Si el ticket es largo, mandá varias fotos juntas como
  álbum y las toma como un solo ticket.
- **Pesos y dólares.** Cada gasto queda en las dos monedas, con el dólar cripto del
  momento ([dolarapi.com](https://dolarapi.com)). Si le decís "dólares", convierte
  solo; nunca te pregunta el tipo de cambio.
- **Categorías que manejás vos.** El bot usa las categorías de la hoja
  **Categorías** y lee sus descripciones para decidir. Agregás o cambiás una
  categoría en la planilla y el bot la usa desde el próximo mensaje.
- **Memoria.** "Recordá que los tickets de Farmacity van a Salud" queda como regla
  para siempre.
- **Recordatorios.** "Recordame pagar la tarjeta el viernes a las 10".
- **No duplica.** Si un mensaje llega dos veces o se corta la conexión con Google,
  el gasto no se carga dos veces.

La planilla además:

- ordena todo por fecha (lo más reciente arriba) y colorea cada fila según su
  categoría;
- tiene, al costado de **Gastos**, un resumen por categoría entre dos fechas que
  elegís vos;
- tiene un **Dashboard** con un gráfico de compras por subcategoría;
- si cargás un gasto a mano en pesos, completa sola la columna en dólares (y al
  revés).

> Está pensado para Argentina: fechas dd/mm/aaaa, pesos, dólar cripto y la forma
> en que vienen los tickets de los supermercados de acá.

> ¿Sin Railway, en otro hosting, en tu compu o 100% gratis? Todas las opciones,
> con lo que implica cada una, están en
> [`docs/formas-de-desplegarlo.md`](docs/formas-de-desplegarlo.md).

---

## Cuánto cuesta

| Servicio | Para qué | Costo |
|---|---|---|
| [Anthropic (Claude)](https://console.anthropic.com) | Entender mensajes y leer tickets | Pago por uso. Con uso personal son unos pocos dólares por mes. |
| [Railway](https://railway.com) | Tener el bot prendido 24/7 | Plan Hobby, USD 5 por mes. |
| [Groq](https://console.groq.com) | Transcribir notas de voz | Gratis. Opcional. |
| Google Sheets + Apps Script | La planilla | Gratis. |
| Telegram | El bot | Gratis. |

---

## Instalación

Vas a necesitar: una cuenta de Google, Telegram, una cuenta de
[GitHub](https://github.com) y unos 30 minutos. No hace falta saber programar.

Durante la instalación vas a juntar estos datos. Anotalos a medida que aparezcan:

| Dato | Dónde sale | Paso |
|---|---|---|
| `APPS_SCRIPT_URL` | Al publicar el Apps Script | 1 |
| `APPS_SCRIPT_SECRET` | Lo genera `setupPlanilla()` | 1 |
| `GASTOS_BOT_TOKEN` | @BotFather | 2 |
| `GASTOS_CHAT_ID` | @userinfobot | 2 |
| `ANTHROPIC_API_KEY` | console.anthropic.com | 3 |
| `GROQ_API_KEY` | console.groq.com (opcional) | 3 |
| `WEBHOOK_SECRET` | Lo inventás vos | 4 |

### 1. La planilla y el Apps Script

1. Creá una planilla nueva y vacía en [sheets.new](https://sheets.new). Ponele el
   nombre que quieras (por ejemplo "Gastos").
2. En la planilla: **Extensiones → Apps Script**. Se abre el editor en otra pestaña.
3. Cargá los tres archivos de la carpeta [`appscript/`](appscript/) de este repo:
   - En el archivo `Código.gs` que ya viene creado, borrá todo y pegá el contenido de
     [`Code.gs`](appscript/Code.gs).
   - Con el **+** al lado de "Archivos" → **Secuencia de comandos**, creá
     `Formatting` y pegá [`Formatting.gs`](appscript/Formatting.gs).
   - De la misma forma, creá `Setup` y pegá [`Setup.gs`](appscript/Setup.gs).
   - Guardá con el ícono del disquete (o Cmd/Ctrl + S).
4. Arriba, en el desplegable de funciones, elegí **`setupPlanilla`** y tocá
   **Ejecutar**.
   - La primera vez Google pide permisos: **Revisar permisos** → elegí tu cuenta →
     si aparece "Google no verificó esta app", tocá **Configuración avanzada** →
     **Ir a (nombre del proyecto)** → **Permitir**. Es tu propio script, corriendo con
     tu cuenta.
5. Abajo se abre el **Registro de ejecución**. Ahí aparece
   `Token secreto (va en APPS_SCRIPT_SECRET del bot): ...`. Copiá ese token: es tu
   **`APPS_SCRIPT_SECRET`**.
   Si lo perdés, está en **Configuración del proyecto** (el engranaje) →
   **Propiedades del script** → `SECRET_TOKEN`.
6. Volvé a la planilla: ya tiene las hojas Gastos, Compras, Dashboard y Categorías.
   Revisá también que en **Configuración del proyecto** (el engranaje del editor) la
   zona horaria sea la tuya (por ejemplo "(GMT-03:00) Buenos Aires"): es la que usa
   el script para la fecha de "hoy". La de referencia está en
   [`appsscript.json`](appscript/appsscript.json).
7. Publicá el script como aplicación web. En el editor: **Implementar → Nueva
   implementación** → engranaje → **Aplicación web**.
   - Descripción: lo que quieras (por ejemplo "bot").
   - Ejecutar como: **Yo**.
   - Quién tiene acceso: **Cualquier persona**.
   - **Implementar**, y copiá la **URL de la aplicación web** (termina en `/exec`).
     Esa es tu **`APPS_SCRIPT_URL`**.

> "Cualquier persona" es necesario para que el bot pueda escribir, pero nadie puede
> hacer nada sin el token secreto: sin él, el script responde `Unauthorized`.

**Probalo** (opcional, desde una terminal; reemplazá la URL y el token):

```bash
curl -L "TU_APPS_SCRIPT_URL?action=get_categories&token=TU_TOKEN"
```

Si devuelve `{"status":"ok","categories":[...]}`, está todo bien.

### 2. El bot de Telegram

1. Abrí [@BotFather](https://t.me/BotFather) en Telegram y mandale `/newbot`.
2. Elegí un nombre (por ejemplo "Mis gastos") y un usuario que termine en `bot`.
3. BotFather te responde con un token del estilo `123456789:AA...`. Ese es tu
   **`GASTOS_BOT_TOKEN`**.
4. Para saber tu ID de Telegram, escribile a [@userinfobot](https://t.me/userinfobot):
   te responde con tu `Id` (un número). Ese es tu **`GASTOS_CHAT_ID`**. El bot solo
   le va a contestar a ese chat; si otra persona lo encuentra, no le responde.

### 3. Las API keys

- **Claude:** entrá a [console.anthropic.com](https://console.anthropic.com), cargá
  crédito en **Billing** (con USD 5 alcanza para empezar) y en **API Keys** creá una
  key. Esa es tu **`ANTHROPIC_API_KEY`**.
- **Groq (para las notas de voz, opcional):** entrá a
  [console.groq.com](https://console.groq.com) → **API Keys** → **Create API Key**.
  Esa es tu **`GROQ_API_KEY`**. Sin ella el bot funciona igual, solo que no entiende
  audios.

### 4. Subir el bot a Railway

1. Hacé un **fork** de este repo en tu cuenta de GitHub (botón **Fork**, arriba a la
   derecha).
2. Entrá a [railway.com](https://railway.com), creá una cuenta y un proyecto nuevo:
   **New Project → Deploy from GitHub repo** → elegí tu fork.
3. En el servicio que se creó, pestaña **Variables** → **Raw Editor**, pegá esto con
   tus valores:

   ```
   GASTOS_BOT_TOKEN=...
   GASTOS_CHAT_ID=...
   ANTHROPIC_API_KEY=...
   GROQ_API_KEY=...
   APPS_SCRIPT_URL=...
   APPS_SCRIPT_SECRET=...
   WEBHOOK_SECRET=...
   DATABASE_URL=sqlite:////data/agent.db
   ```

   Para `WEBHOOK_SECRET` inventá un texto largo con letras, números, `_` o `-` (por
   ejemplo, apretá el teclado un rato). Sirve para que el bot solo acepte mensajes
   que vengan de Telegram.
4. Agregá un **volumen** para que el bot no pierda su memoria en cada actualización:
   clic derecho en el servicio → **Attach volume** → Mount path: `/data`.
5. En **Settings → Networking**, tocá **Generate Domain**. Te da una dirección del
   estilo `https://tu-bot-production.up.railway.app`.
6. Esperá a que el deploy quede en verde. Si abrís esa dirección en el navegador,
   tiene que mostrar `"status": "ok"` y `"gastos"` entre los canales activos.

### 5. Conectar Telegram con el bot

Abrí esta dirección en el navegador, reemplazando los tres valores:

```
https://api.telegram.org/botTU_GASTOS_BOT_TOKEN/setWebhook?url=https://TU_DOMINIO_DE_RAILWAY/webhook/gastos&secret_token=TU_WEBHOOK_SECRET
```

Tiene que responder `"ok":true` y `"Webhook was set"`.

### 6. Probar

Escribile a tu bot "gasté 1500 en un café". Tiene que contestar confirmando y la
fila tiene que aparecer en la hoja **Gastos**. Después probá con una foto de un
ticket y con un audio.

---

## Uso

| Querés… | Mandale… |
|---|---|
| Registrar un gasto | "Gasté 5000 en el súper", "almuerzo 8500", "ayer 12 mil de nafta" |
| Un gasto en dólares | "20 dólares de Spotify" |
| Un ticket | Una foto del ticket, o varias juntas como álbum |
| Un gasto por voz | Una nota de voz diciendo el gasto |
| Que recuerde algo | "Recordá que Farmacity va a Salud" |
| Ver lo que recuerda | "¿Qué tenés guardado?" |
| Un recordatorio | "Recordame pagar la luz mañana a las 18" |

Si el bot elige una categoría que no era, te dice cuál usó: la cambiás en la
planilla (es un desplegable) o le decís cómo querés que lo categorice la próxima.

## La planilla

| Hoja | Qué tiene |
|---|---|
| **Gastos** | Una fila por gasto: Gasto, Categoría, Monto (AR$), Monto (U$D), Fecha. Al costado, en G:J, el resumen por categoría entre las fechas de **Desde** y **Hasta** (arrancan en el mes actual; pisalas con las que quieras). |
| **Compras** | El detalle de cada producto de los tickets: fecha, comercio, producto, subcategoría, categoría, cantidad, precio unitario y total. Los gastos manuales de la categoría Compras también aparecen acá. |
| **Dashboard** | Total de compras por subcategoría y un gráfico de barras que se actualiza solo. |
| **Categorías** | A:C las categorías (nombre, descripción, color); D:F las subcategorías (nombre, categoría padre, descripción). |

**Cómo personalizarla:**

- **Categorías:** agregá, borrá o renombrá filas en la hoja Categorías. La
  **descripción** es lo que más usa el bot para decidir: cuanto más clara, mejor
  categoriza (por ejemplo "Cualquier gasto en la facultad: cafés, útiles,
  fotocopias").
- **Colores:** pintá el fondo de la celda de la columna C de una categoría y las
  hojas Gastos y Compras se recolorean solas.
- **Cargar gastos a mano:** escribilos directo en Gastos. Si ponés solo el monto en
  pesos, el dólar se completa solo (y al revés). Si la categoría es "Compras", se
  copia también a la hoja Compras.
- **Menú "🤖 Bot de gastos":** aparece arriba en la planilla. Tiene opciones para
  reordenar por fecha y para completar los montos en dólares que falten.
- La planilla se reordena sola cada vez que la abrís.

## Si algo no funciona

**El bot no contesta nada.**
- Revisá en Railway (**Deployments → View logs**) que el deploy esté corriendo y sin
  errores.
- Revisá el webhook: abrí
  `https://api.telegram.org/botTU_TOKEN/getWebhookInfo`. La `url` tiene que terminar
  en `/webhook/gastos` y no tiene que haber `last_error_message`. Si el error dice
  `403`, el `secret_token` del paso 5 no coincide con `WEBHOOK_SECRET`.
- Si contesta "No estoy autorizado para responder en este chat", el
  `GASTOS_CHAT_ID` está mal.

**Contesta, pero dice que no pudo registrar el gasto.**
- `Unauthorized`: `APPS_SCRIPT_SECRET` no coincide con el `SECRET_TOKEN` del script.
- Un error raro con HTML o "página no encontrada": la URL está mal, o la
  implementación no tiene acceso para **Cualquier persona**.

**Mandé un mensaje y no pasó nada, sin ningún error.** Si justo se estaba
actualizando el bot en Railway, se reinicia durante unos 30-60 segundos y los
mensajes de ese momento se pierden. Mandalo de nuevo.

**Los audios dicen que falta configurar la transcripción.** Falta `GROQ_API_KEY` en
las variables de Railway.

**El bot se olvidó de todo después de una actualización.** Falta el volumen en
`/data` o la variable `DATABASE_URL=sqlite:////data/agent.db` (paso 4).

## Cambiar el código del Apps Script

Si modificás algo en los `.gs`, guardar no alcanza: el bot sigue usando la versión
publicada. Para publicar la nueva **sin cambiar la URL**:

**Implementar → Gestionar implementaciones** → lápiz de tu implementación →
Versión: **Nueva versión** → **Implementar**.

No crees una implementación nueva: esa tiene otra URL y el bot sigue apuntando a
la vieja.

Un detalle si tocás el código: Apps Script comparte un solo espacio de nombres
entre todos los `.gs`, así que puede haber una sola función `onEdit` y una sola
`onOpen` (las dos viven en `Code.gs`).

## Correrlo en tu compu (opcional, para desarrollar)

Con Python 3.11:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # completalo con tus valores
python main.py
```

Telegram necesita una URL pública para mandarle los mensajes. Para probar en local,
exponé el puerto 8000 con algo como [ngrok](https://ngrok.com) y registrá el webhook
(paso 5) con esa URL. Ojo: un bot tiene un solo webhook, así que mientras apunte a
tu compu no va a recibir nada en Railway.

## Cómo está armado

```
├── main.py              # FastAPI: recibe los mensajes de Telegram en /webhook/gastos
├── scheduler.py         # manda los recordatorios
├── config.py            # variables de entorno
├── channels/
│   ├── base.py          # estructura de un "canal" (un bot de Telegram)
│   ├── llm.py           # loop de Claude con herramientas, reusable
│   └── gastos/
│       ├── agent.py     # prompt y herramientas del bot de gastos
│       └── handler.py   # texto, voz y fotos (incluye álbumes)
├── tools/
│   ├── expenses.py      # lectura de tickets, dólar cripto y envío a la planilla
│   └── transcription.py # notas de voz → texto (Whisper vía Groq)
├── db/                  # SQLite: historial, memorias y recordatorios
├── appscript/           # el código de la planilla (Code, Formatting, Setup)
├── Dockerfile
└── railway.toml
```

- **Claude Sonnet** interpreta los mensajes y lee las fotos de los tickets.
- La planilla recibe los gastos a través del **Apps Script publicado como aplicación
  web**: el bot le manda cada gasto con el token secreto y un `request_id` armado a
  partir del contenido del gasto. El script descarta durante 15 minutos cualquier
  `request_id` repetido; así un reintento nunca duplica una fila.
- El bot guarda el historial de la conversación, incluidas las herramientas que usó,
  para saber qué gastos ya cargó.

## Licencia

[MIT](LICENSE).
