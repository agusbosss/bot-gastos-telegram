/**
 * Bot de gastos — Google Apps Script
 *
 * Maneja gastos de compras (tickets) y gastos manuales.
 * Hojas: Gastos, Compras, Dashboard, Categorías (con dos tablas)
 *
 * Primera vez: correr setupPlanilla() (Setup.gs) desde el editor.
 *
 * Publicar como Web App:
 *   - Ejecutar como: "Yo" (tu cuenta de Google)
 *   - Quien puede acceder: "Cualquier persona"
 */

const SPREADSHEET_ID = ""; // Vacío = spreadsheet activo

/**
 * Token compartido con el bot (APPS_SCRIPT_SECRET). Vive en las Propiedades del
 * script, no en el código, para que el código se pueda compartir sin exponerlo.
 * Lo genera setupPlanilla(); se ve en Configuración del proyecto → Propiedades
 * del script → SECRET_TOKEN.
 */
function _secretToken() {
  return PropertiesService.getScriptProperties().getProperty("SECRET_TOKEN") || "";
}

/** Sin token configurado no se acepta ningún request (ni siquiera uno con token vacío). */
function _isAuthorized(token) {
  var expected = _secretToken();
  return !!expected && token === expected;
}

// Ventana en la que un mismo request_id se considera duplicado y NO se vuelve a
// escribir. Cubre tanto un reintento de red del bot como que el modelo re-dispare
// el mismo gasto un par de mensajes después.
const DEDUPE_TTL_SECONDS = 900; // 15 min

// Cuánto espera una escritura a que se libere el lock de otra en curso.
const LOCK_WAIT_MS = 45000;

// ─── Constantes ──────────────────────────────────────────────

const SHEETS = {
  COMPRAS: "Compras",
  GASTOS: "Gastos",
  CATEGORIAS: "Categorías",
  RESUMEN: "Resumen",
};

const COMPRAS_HEADERS = [
  "Fecha", "Comercio", "Producto", "Subcategoría", "Categoría",
  "Cantidad", "Precio Unit. ($)", "Total ($)", "Moneda"
];

const GASTOS_HEADERS = [
  "Gasto", "Categoría", "Monto ($)", "Monto (U$D)", "Fecha"
];

const CATEGORIAS_HEADERS = ["Categoría", "Descripción", "Color"];

const SUBCATEGORIAS_HEADERS = ["Subcategoría", "Categoría Padre", "Descripción"];

const DEFAULT_CATEGORIES = [
  ["Servicios", "Luz, gas, internet, teléfono, streaming, seguros"],
  ["Compras", "Compras de supermercado y productos varios"],
  ["Salida", "Salidas, restaurantes, bares, ocio, entretenimiento"],
  ["Extra", "Gastos extras no recurrentes"],
  ["Salud", "Medicamentos, consultas médicas, farmacia"],
  ["Transporte", "Colectivo, subte, taxi, combustible, peajes, SUBE"],
  ["Educación", "Facultad, cursos, útiles, fotocopias, cafés en la facu"],
  ["Limpieza", "Detergentes, elementos de limpieza, desinfectantes"],
  ["Higiene Personal", "Jabón, shampoo, dentífrico, desodorante"],
  ["Tecnología", "Electrónica, accesorios, software"],
  ["Ropa", "Cualquier prenda de ropa o accesorio de vestir"],
  ["Golosinas", "Dulces, chocolates, golosinas, snacks"],
  ["Gimnasio", "Cuota del gimnasio, suplementos, elementos deportivos"],
  ["Hogar", "Muebles, decoración, arreglos de la casa"],
  ["Otros", "Lo que no encaje en otra categoría"],
];

// [Subcategoría, Categoría padre, Descripción (opcional)]
const DEFAULT_SUBCATEGORIES = [
  ["Lácteos", "Compras"],
  ["Carne roja", "Compras"],
  ["Carne de pollo", "Compras"],
  ["Carne de cerdo", "Compras"],
  ["Carne de pescado", "Compras", "Merluza, filet, mariscos"],
  ["Fruta", "Compras"],
  ["Verdura", "Compras"],
  ["Huevos", "Compras"],
  ["Panadería", "Compras"],
  ["Alacena", "Compras", "Pastas secas, enlatados, conservas, legumbres, arroz"],
  ["Aderezos", "Compras"],
  ["Condimentos", "Compras"],
  ["Congelados", "Compras"],
  ["Infusiones", "Compras", "Yerba mate, café, té"],
  ["Bebidas", "Compras"],
  ["Alcohol", "Compras"],
  ["Snacks y Golosinas", "Golosinas"],
  ["Limpieza del Hogar", "Limpieza"],
  ["Higiene Personal", "Higiene Personal"],
  ["Electrónica y Tecnología", "Tecnología"],
  ["Ropa y Calzado", "Ropa"],
  ["Otros", "Otros", "Lo que no encaje en otra categoría"],
];


// ─── Endpoints ──────────────────────────────────────────────

function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);

    if (!_isAuthorized(payload.token)) {
      return _jsonResponse({ status: "error", message: "Unauthorized" });
    }

    // Cachear tipo de cambio para uso en onEdit
    if (payload.dolar_rate) {
      PropertiesService.getScriptProperties()
        .setProperty("last_dolar_rate", String(payload.dolar_rate));
    }

    var action = payload.action || "add_expense";

    if (action === "add_expense") {
      return _jsonResponse(_withWriteLock(payload.request_id, function () {
        return _handleAddExpense(payload);
      }));
    }
    if (action === "add_manual_expense") {
      return _jsonResponse(_withWriteLock(payload.request_id, function () {
        return _handleAddManualExpense(payload);
      }));
    }

    return _jsonResponse({ status: "error", message: "Acción desconocida: " + action });

  } catch (err) {
    Logger.log("Error en doPost: " + err.toString());
    return _jsonResponse({ status: "error", message: err.toString() });
  }
}


/**
 * Serializa las escrituras y las hace idempotentes.
 *
 * - LockService: sin esto, dos ejecuciones simultáneas (dos gastos seguidos, un
 *   reintento del bot, un onEdit manual) leían y reescribían el rango completo
 *   de la hoja, así que una podía pisar la fila de la otra.
 * - CacheService: si `requestId` ya se procesó, se devuelve la respuesta
 *   original marcada con `duplicate: true` en vez de escribir la fila otra vez.
 *   El chequeo pasa DENTRO del lock, así un reintento que llega mientras la
 *   primera ejecución sigue corriendo espera y después ve la marca (si el
 *   chequeo fuera afuera, las dos escribirían).
 */
function _withWriteLock(requestId, fn) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(LOCK_WAIT_MS);
  } catch (err) {
    Logger.log("No se pudo tomar el lock: " + err.toString());
    return {
      status: "error",
      message: "El script está ocupado escribiendo otro gasto. Reintentá en unos segundos.",
    };
  }

  try {
    var cache = CacheService.getScriptCache();
    var key = requestId ? "req_" + requestId : null;

    if (key) {
      var cached = cache.get(key);
      if (cached) {
        Logger.log("request_id repetido (" + requestId + "): no se escribe de nuevo.");
        var previous = JSON.parse(cached);
        previous.duplicate = true;
        return previous;
      }
    }

    var result = fn();

    if (key && result && result.status === "ok") {
      cache.put(key, JSON.stringify(result), DEDUPE_TTL_SECONDS);
    }
    return result;

  } finally {
    lock.releaseLock();
  }
}


/**
 * Corre trabajo accesorio sin dejar que su error se propague. Devuelve si salió.
 *
 * Todo lo que pasa DESPUÉS de escribir la fila del gasto (dropdowns, espejo en
 * Compras, ordenar, colorear) entra acá: el gasto ya quedó registrado, así que
 * nada de eso puede convertir una escritura exitosa en un error para el bot
 * (que era justo lo que hacía que reportara ❌ gastos que sí habían entrado).
 */
function _bestEffort(label, fn) {
  try {
    fn();
    return true;
  } catch (err) {
    Logger.log("Paso accesorio '" + label + "' omitido (la fila ya quedó escrita): " + err.toString());
    return false;
  }
}


/**
 * Igual que _bestEffort pero además pide el lock: se usa desde onEdit, donde el
 * reformateo es un read-modify-write de la hoja entera que podría pisarle una
 * fila al bot. Si el lock está tomado, se saltea (es cosmético).
 */
function _bestEffortWithLock(waitMs, label, fn) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(waitMs);
  } catch (err) {
    Logger.log("Lock ocupado: se omite '" + label + "'.");
    return false;
  }
  try {
    return _bestEffort(label, fn);
  } finally {
    lock.releaseLock();
  }
}


function doGet(e) {
  try {
    var action = (e && e.parameter && e.parameter.action) ? e.parameter.action : "";
    var token = (e && e.parameter && e.parameter.token) ? e.parameter.token : "";

    if (!_isAuthorized(token)) {
      return _jsonResponse({ status: "error", message: "Unauthorized" });
    }

    if (action === "get_categories") {
      return _jsonResponse(_handleGetCategories());
    }
    if (action === "check_request") {
      return _jsonResponse(_handleCheckRequest(e.parameter.request_id));
    }

    return _jsonResponse({ status: "error", message: "Acción desconocida" });

  } catch (err) {
    Logger.log("Error en doGet: " + err.toString());
    return _jsonResponse({ status: "error", message: err.toString() });
  }
}


// ─── Handlers ───────────────────────────────────────────────

function _handleAddExpense(payload) {
  var ss = _getSpreadsheet();
  var comprasSheet = _getOrCreateSheet(ss, SHEETS.COMPRAS, COMPRAS_HEADERS);
  var gastosSheet = _getOrCreateSheet(ss, SHEETS.GASTOS, GASTOS_HEADERS);

  _ensureCategoriesSheet(ss);

  var store = payload.store || "Desconocido";
  var date = (payload.date && payload.date.toLowerCase() !== "hoy") ? payload.date : _today();
  var currency = payload.currency || "ARS";
  var items = payload.items || [];
  var rowsAdded = 0;

  // --- Escribir ítems detallados en Compras ---
  var aggregated = {}; // { categoría: subtotal }

  items.forEach(function(item) {
    if (!item.name) return;

    var subcategory = item.category || "Otros";
    var category = item.general_category || "Otros";

    _ensureCategoryExists(ss, category);

    comprasSheet.appendRow([
      date,
      store,
      item.name || "",
      subcategory,
      category,
      item.qty || 1,
      item.unit_price || "",
      item.total_price || "",
      currency,
    ]);
    rowsAdded++;

    var itemTotal = parseFloat(item.total_price) || 0;
    if (!aggregated[category]) aggregated[category] = 0;
    aggregated[category] += itemTotal;
  });

  // Dropdowns en Compras (accesorio: las filas ya están escritas)
  var lastComprasRow = comprasSheet.getLastRow();
  if (rowsAdded > 0) {
    _bestEffort("dropdowns de Compras", function () {
      _applyComprasValidation(ss, comprasSheet, lastComprasRow - rowsAdded + 1, lastComprasRow);
    });
  }

  // --- Escribir filas agregadas en Gastos ---
  var categories = Object.keys(aggregated);
  var gastosRowsAdded = 0;
  var dolarRate = parseFloat(payload.dolar_rate) || null;

  categories.forEach(function(category) {
    var subtotal = aggregated[category];
    var gastoName = categories.length > 1 ? (store + " - " + category) : store;
    gastosSheet.appendRow([gastoName, category, subtotal, _toUSD(subtotal, dolarRate), date]);
    gastosRowsAdded++;
  });

  // Dropdowns en Gastos + marcar las filas "Compras" como ya reflejadas en Compras
  if (gastosRowsAdded > 0) {
    var lastGastosRow = gastosSheet.getLastRow();
    var firstGastosRow = lastGastosRow - gastosRowsAdded + 1;
    _bestEffort("dropdowns y marcas de Gastos", function () {
      _applyGastosValidation(ss, gastosSheet, firstGastosRow, lastGastosRow);

      // Estas filas ya tienen su detalle en Compras (vienen del ticket): marcarlas
      // para que onEdit no las duplique si el usuario las reedita a mano.
      var addedCats = gastosSheet.getRange(firstGastosRow, 2, gastosRowsAdded, 1).getValues();
      for (var gi = 0; gi < gastosRowsAdded; gi++) {
        if ((addedCats[gi][0] || "").toString().toLowerCase() === "compras") {
          gastosSheet.getRange(firstGastosRow + gi, 1).setNote(COMPRAS_SYNC_NOTE);
        }
      }
    });
  }

  // Las filas ya están escritas: de acá en adelante es cosmético (ver _bestEffort).
  var formatted = _bestEffort("formato", function () {
    _refreshComprasFormatting(ss, comprasSheet, rowsAdded);
    _refreshGastosFormatting(ss, gastosSheet, gastosRowsAdded);
  });

  return {
    status: "ok",
    rows_added: rowsAdded,
    gastos_rows_added: gastosRowsAdded,
    store: store,
    date: date,
    formatted: formatted,
  };
}


function _handleAddManualExpense(payload) {
  var ss = _getSpreadsheet();
  var gastosSheet = _getOrCreateSheet(ss, SHEETS.GASTOS, GASTOS_HEADERS);

  _ensureCategoriesSheet(ss);

  var name = payload.name || "Sin nombre";
  var category = payload.category || "Otros";
  var amount = parseFloat(payload.amount) || 0;
  var date = (payload.date && payload.date.toLowerCase() !== "hoy") ? payload.date : _today();
  var subcategory = payload.subcategory || "";
  var store = payload.store || "";

  _ensureCategoryExists(ss, category);

  var dolarRate = parseFloat(payload.dolar_rate) || null;
  gastosSheet.appendRow([name, category, amount, _toUSD(amount, dolarRate), date]);

  // ─── A partir de acá el gasto YA está registrado ───
  // Nada de lo que sigue puede volver como "no se pudo registrar el gasto":
  // todo va en best-effort y se reporta con flags (ver _bestEffort).

  var lastRow = gastosSheet.getLastRow();
  _bestEffort("dropdowns de Gastos", function () {
    _applyGastosValidation(ss, gastosSheet, lastRow, lastRow);
  });

  // Toda compra (venga con subcategoría o no) también va a la hoja Compras.
  var comprasRowAdded = false;
  var comprasSheet = null;
  if (subcategory || category === "Compras") {
    comprasRowAdded = _bestEffort("espejo en Compras", function () {
      comprasSheet = _getOrCreateSheet(ss, SHEETS.COMPRAS, COMPRAS_HEADERS);
      var comercio = store || "Sin comercio";

      comprasSheet.appendRow([
        date,
        comercio,
        name,
        subcategory,
        category,
        1,           // qty
        amount,      // unit_price
        amount,      // total
        "ARS",
      ]);

      var lastComprasRow = comprasSheet.getLastRow();
      _applyComprasValidation(ss, comprasSheet, lastComprasRow, lastComprasRow);
    });
  }

  // Si es Compras, marcar la fila para que onEdit no la duplique al reeditarla.
  // (Se marca ANTES de ordenar para que la nota viaje con su fila.)
  if (category === "Compras") {
    _bestEffort("marca de sincronía", function () {
      gastosSheet.getRange(lastRow, 1).setNote(COMPRAS_SYNC_NOTE);
    });
  }

  // Ordenar y colorear es lo caro: es lo que hacía que la respuesta llegara
  // tarde y el bot diera por fallado un gasto que sí había entrado.
  var formatted = _bestEffort("formato", function () {
    if (comprasSheet) _refreshComprasFormatting(ss, comprasSheet, 1);
    _refreshGastosFormatting(ss, gastosSheet, 1);
  });

  return {
    status: "ok",
    name: name,
    category: category,
    amount: amount,
    date: date,
    compras_row_added: comprasRowAdded,
    formatted: formatted,
  };
}


/**
 * ¿Este request_id llegó a escribirse?
 *
 * Lo usa el bot cuando se le cortó la respuesta y no sabe si el gasto entró:
 * en vez de decirle al usuario "no pude confirmar" (o peor, reintentar y
 * duplicar), pregunta. Pide el lock para no contestar "no" mientras la
 * escritura original todavía está corriendo.
 */
function _handleCheckRequest(requestId) {
  if (!requestId) {
    return { status: "error", message: "Falta request_id" };
  }

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(LOCK_WAIT_MS);
  } catch (err) {
    return { status: "error", message: "El script está ocupado, no puedo confirmar todavía." };
  }

  try {
    var cached = CacheService.getScriptCache().get("req_" + requestId);
    return {
      status: "ok",
      found: !!cached,
      result: cached ? JSON.parse(cached) : null,
    };
  } finally {
    lock.releaseLock();
  }
}


function _handleGetCategories() {
  var ss = _getSpreadsheet();
  _ensureCategoriesSheet(ss);

  var categories = _readCategories(ss);
  var subcategories = _readSubcategories(ss);

  return {
    status: "ok",
    categories: categories,
    subcategories: subcategories,
  };
}


// ─── Hoja Categorías ────────────────────────────────────────

/**
 * Layout de la hoja Categorías:
 *   Col A: Categoría
 *   Col B: Descripción
 *   Col C: Color (celda pintada con el color de la categoría — editable desde Sheets)
 *   Col D: Subcategoría
 *   Col E: Categoría Padre
 *   Col F: Descripción subcategoría
 */
function _ensureCategoriesSheet(ss) {
  var sheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (sheet) return sheet;

  sheet = ss.insertSheet(SHEETS.CATEGORIAS);

  // Headers categorías (A-B-C)
  sheet.getRange(1, 1).setValue(CATEGORIAS_HEADERS[0]);
  sheet.getRange(1, 2).setValue(CATEGORIAS_HEADERS[1]);
  sheet.getRange(1, 3).setValue(CATEGORIAS_HEADERS[2]);

  // Headers subcategorías (D-E-F)
  sheet.getRange(1, 4).setValue(SUBCATEGORIAS_HEADERS[0]);
  sheet.getRange(1, 5).setValue(SUBCATEGORIAS_HEADERS[1]);
  sheet.getRange(1, 6).setValue(SUBCATEGORIAS_HEADERS[2]);

  // Categorías con color inicial de la paleta
  for (var i = 0; i < DEFAULT_CATEGORIES.length; i++) {
    sheet.getRange(i + 2, 1).setValue(DEFAULT_CATEGORIES[i][0]);
    sheet.getRange(i + 2, 2).setValue(DEFAULT_CATEGORIES[i][1]);
    sheet.getRange(i + 2, 3).setBackground(CATEGORY_PALETTE[i % CATEGORY_PALETTE.length]);
  }

  // Subcategorías (D-E-F)
  for (var j = 0; j < DEFAULT_SUBCATEGORIES.length; j++) {
    sheet.getRange(j + 2, 4).setValue(DEFAULT_SUBCATEGORIES[j][0]);
    sheet.getRange(j + 2, 5).setValue(DEFAULT_SUBCATEGORIES[j][1]);
    sheet.getRange(j + 2, 6).setValue(DEFAULT_SUBCATEGORIES[j][2] || "");
  }

  return sheet;
}


function _readCategories(ss) {
  var sheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!sheet) return [];

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  var data = sheet.getRange(2, 1, lastRow - 1, 2).getValues();
  var categories = [];

  for (var i = 0; i < data.length; i++) {
    if (data[i][0]) {
      categories.push({ name: data[i][0], description: data[i][1] || "" });
    }
  }
  return categories;
}


function _readSubcategories(ss) {
  var sheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!sheet) return [];

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  var data = sheet.getRange(2, 4, lastRow - 1, 3).getValues();
  var subcategories = [];

  for (var i = 0; i < data.length; i++) {
    if (data[i][0]) {
      var entry = { name: data[i][0], parent: data[i][1] || "" };
      if (data[i][2]) entry.description = data[i][2];
      subcategories.push(entry);
    }
  }
  return subcategories;
}


/**
 * Auto-crea una categoría si no existe. Le asigna el siguiente color de la paleta
 * en col C para que quede lista para formatear.
 */
function _ensureCategoryExists(ss, category) {
  if (!category || category === "Otros") return;

  var sheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!sheet) sheet = _ensureCategoriesSheet(ss);

  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    var catData = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    for (var i = 0; i < catData.length; i++) {
      if (catData[i][0] === category) return; // ya existe
    }
  }

  // Encontrar última fila con dato en col A
  var newRow = 2;
  if (lastRow >= 2) {
    var colA = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    for (var j = colA.length - 1; j >= 0; j--) {
      if (colA[j][0]) { newRow = j + 3; break; }
    }
  }

  // Asignar siguiente color de la paleta
  var existingCount = _countDataRows(sheet, 1);
  var newColor = CATEGORY_PALETTE[existingCount % CATEGORY_PALETTE.length];

  sheet.getRange(newRow, 1).setValue(category);
  sheet.getRange(newRow, 2).setValue("");
  sheet.getRange(newRow, 3).setBackground(newColor);
}


// ─── Dropdowns ──────────────────────────────────────────────

function _applyComprasValidation(ss, sheet, fromRow, toRow) {
  var catSheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!catSheet) return;

  var catCount = _countDataRows(catSheet, 1);
  if (catCount > 0) {
    var catRange = catSheet.getRange(2, 1, catCount, 1);
    var catRule = SpreadsheetApp.newDataValidation()
      .requireValueInRange(catRange, true).setAllowInvalid(true).build();
    for (var r = fromRow; r <= toRow; r++) {
      sheet.getRange(r, 5).setDataValidation(catRule); // Col E = Categoría
    }
  }

  var subCount = _countDataRows(catSheet, 4);
  if (subCount > 0) {
    var subRange = catSheet.getRange(2, 4, subCount, 1);
    var subRule = SpreadsheetApp.newDataValidation()
      .requireValueInRange(subRange, true).setAllowInvalid(true).build();
    for (var r2 = fromRow; r2 <= toRow; r2++) {
      sheet.getRange(r2, 4).setDataValidation(subRule); // Col D = Subcategoría
    }
  }
}


function _applyGastosValidation(ss, sheet, fromRow, toRow) {
  var catSheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!catSheet) return;

  var catCount = _countDataRows(catSheet, 1);
  if (catCount > 0) {
    var catRange = catSheet.getRange(2, 1, catCount, 1);
    var catRule = SpreadsheetApp.newDataValidation()
      .requireValueInRange(catRange, true).setAllowInvalid(true).build();
    for (var r = fromRow; r <= toRow; r++) {
      sheet.getRange(r, 2).setDataValidation(catRule); // Col B = Categoría
    }
  }
}


function _countDataRows(sheet, col) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return 0;
  var data = sheet.getRange(2, col, lastRow - 1, 1).getValues();
  var count = 0;
  for (var i = 0; i < data.length; i++) {
    if (data[i][0]) count = i + 1;
  }
  return count;
}


// ─── Resumen ────────────────────────────────────────────────

function buildSummary() {
  var ss = _getSpreadsheet();
  var gastosSheet = ss.getSheetByName(SHEETS.GASTOS);
  if (!gastosSheet) return;

  var data = gastosSheet.getDataRange().getValues();
  if (data.length < 2) return;

  var summary = {};
  for (var i = 1; i < data.length; i++) {
    var cat = data[i][1] || "Otros";
    var amount = parseFloat(data[i][2]) || 0;
    var monthKey = _extractMonth(data[i][4]);
    if (!summary[monthKey]) summary[monthKey] = {};
    summary[monthKey][cat] = (summary[monthKey][cat] || 0) + amount;
  }

  var summarySheet = ss.getSheetByName(SHEETS.RESUMEN);
  if (summarySheet) { summarySheet.clear(); }
  else { summarySheet = ss.insertSheet(SHEETS.RESUMEN); }

  var allCategories = {};
  Object.keys(summary).forEach(function(m) {
    Object.keys(summary[m]).forEach(function(c) { allCategories[c] = true; });
  });
  var categories = Object.keys(allCategories).sort();

  summarySheet.appendRow(["Mes"].concat(categories).concat(["TOTAL MES"]));

  Object.keys(summary).sort().forEach(function(month) {
    var row = [month];
    var monthTotal = 0;
    categories.forEach(function(cat) {
      var val = summary[month][cat] || 0;
      row.push(val);
      monthTotal += val;
    });
    row.push(monthTotal);
    summarySheet.appendRow(row);
  });
}


// ─── Trigger onOpen ─────────────────────────────────────────

/**
 * Trigger simple que corre cada vez que se abre la planilla.
 *
 * 1. Reordena Gastos y Compras dejando lo más reciente arriba. El bot ya ordena
 *    en cada carga, pero las filas escritas A MANO no: onEdit deliberadamente no
 *    ordena (la fila saltaría de lugar mientras se la está escribiendo). Esto las
 *    acomoda al abrir, así nunca hace falta ordenar la hoja de la Z a la A.
 * 2. Deja el menú "Bot de gastos" para reordenar a demanda.
 *
 * El reordenado pide el lock: si el bot está escribiendo justo en ese momento se
 * saltea (es cosmético y la próxima apertura lo arregla).
 */
function onOpen() {
  _bestEffort("menú", function () {
    SpreadsheetApp.getUi()
      .createMenu("🤖 Bot de gastos")
      .addItem("Ordenar por fecha (más reciente arriba)", "reformatAllSheets")
      .addItem("Completar montos U$D faltantes", "backfillAllGastosCurrencyNow")
      .addSeparator()
      .addItem("Configurar planilla (hojas, resumen y gráfico)", "setupPlanilla")
      .addToUi();
  });

  _bestEffortWithLock(10000, "orden al abrir la planilla", function () {
    reformatAllSheets();
  });
}


// ─── Trigger onEdit ─────────────────────────────────────────

/** Nota-marcador en col A de Gastos: la fila ya está reflejada en Compras. */
const COMPRAS_SYNC_NOTE = "Sincronizado con la hoja Compras";

/**
 * ÚNICO onEdit del proyecto (Apps Script comparte un namespace global entre
 * archivos .gs, así que no puede haber dos funciones onEdit).
 *
 * Hace tres cosas, todas solo ante ediciones MANUALES (los writes del bot vía
 * Web App no disparan onEdit):
 *   1. Colores por fila (delegado en Formatting.gs).
 *   2. Completa el monto contraparte (AR$ ↔ U$D) en TODAS las filas de Gastos
 *      a las que les falte, usando el TC cacheado.
 *   3. Si se cargó a mano una fila con categoría "Compras", la replica en la
 *      hoja Compras (si todavía no estaba).
 */
function onEdit(e) {
  if (!e || !e.range) return;

  var sheet = e.range.getSheet();
  var ss = sheet.getParent();

  // 1) Colores (no ordena, para mantener la respuesta rápida)
  _applyFormattingOnEdit(e);

  if (sheet.getName() === SHEETS.GASTOS) {
    // 2) Completar moneda contraparte en todas las filas que falten
    _backfillGastosCurrency(sheet);
    // 3) Espejar a Compras las filas "Compras" cargadas a mano
    _mirrorManualComprasFromGastos(ss, e);
  }
}


/**
 * Recorre TODA la hoja Gastos y, en cada fila con un solo monto cargado,
 * completa el otro: USD = AR$ / TC, o AR$ = USD × TC. Nunca pisa valores ya
 * cargados. El TC se lee del caché que el bot guarda al registrar gastos.
 */
function _backfillGastosCurrency(sheet) {
  var rate = parseFloat(
    PropertiesService.getScriptProperties().getProperty("last_dolar_rate")
  ) || 0;
  if (!rate) return;

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  // Read-modify-write de toda la hoja: si el bot está escribiendo/ordenando al
  // mismo tiempo, escribir el snapshot viejo le pisaría los montos a otra fila.
  _bestEffortWithLock(10000, "backfill de moneda", function () {
    var lastRowNow = sheet.getLastRow();
    if (lastRowNow < 2) return;

    var range = sheet.getRange(2, 3, lastRowNow - 1, 2); // cols C (AR$) y D (U$D)
    var values = range.getValues();
    var changed = false;

    for (var i = 0; i < values.length; i++) {
      var ars = parseFloat(values[i][0]) || 0;
      var usd = parseFloat(values[i][1]) || 0;
      if (ars > 0 && usd <= 0) {
        values[i][1] = Math.round((ars / rate) * 100) / 100;
        changed = true;
      } else if (usd > 0 && ars <= 0) {
        values[i][0] = Math.round(usd * rate * 100) / 100;
        changed = true;
      }
    }

    if (changed) range.setValues(values);
  });
}


/**
 * Revisa las filas tocadas por la edición y, por cada una con categoría
 * "Compras" cargada a mano (sin la nota-marcador), agrega la fila equivalente
 * a la hoja Compras y la marca para no duplicarla en ediciones posteriores.
 */
function _mirrorManualComprasFromGastos(ss, e) {
  var gastosSheet = e.range.getSheet();
  var startRow = e.range.getRow();
  var endRow = startRow + e.range.getNumRows() - 1;

  for (var row = Math.max(startRow, 2); row <= endRow; row++) {
    _maybeMirrorComprasRow(ss, gastosSheet, row);
  }
}


function _maybeMirrorComprasRow(ss, gastosSheet, row) {
  if (row > gastosSheet.getLastRow()) return;

  var nameCell = gastosSheet.getRange(row, 1);
  if (nameCell.getNote() === COMPRAS_SYNC_NOTE) return; // ya reflejada / vino de ticket

  var vals = gastosSheet.getRange(row, 1, 1, GASTOS_HEADERS.length).getValues()[0];
  var name = (vals[0] || "").toString().trim();
  var category = (vals[1] || "").toString().trim();
  var amount = parseFloat(vals[2]) || 0;
  var date = _formatDateCell(vals[4]); // puede venir como Date real, no como texto

  // Solo categoría "Compras" y con datos mínimos para ser una fila real
  if (category.toLowerCase() !== "compras") return;
  if (!name || amount <= 0) return;

  var comprasSheet = _getOrCreateSheet(ss, SHEETS.COMPRAS, COMPRAS_HEADERS);
  comprasSheet.appendRow([
    date,            // Fecha
    "Sin comercio",  // Comercio
    name,            // Producto
    "",              // Subcategoría (desconocida en carga manual)
    "Compras",       // Categoría
    1,               // Cantidad
    amount,          // Precio Unit.
    amount,          // Total
    "ARS",           // Moneda
  ]);

  var lastComprasRow = comprasSheet.getLastRow();
  _applyComprasValidation(ss, comprasSheet, lastComprasRow, lastComprasRow);

  // La fila ya está espejada; marcarla antes del reformateo (que es cosmético y
  // puede saltearse si el bot tiene el lock) para no duplicarla en la próxima edición.
  nameCell.setNote(COMPRAS_SYNC_NOTE);

  _bestEffortWithLock(10000, "formato de Compras", function () {
    _refreshComprasFormatting(ss, comprasSheet, 1);
  });
}


/**
 * Diagnóstico + arreglo: corre el backfill de moneda sobre TODA la hoja Gastos
 * desde el editor (permisos completos). Loguea el TC cacheado para ver si el
 * problema es que no hay TC o que el trigger simple no podía leerlo.
 */
function backfillAllGastosCurrencyNow() {
  var ss = _getSpreadsheet();
  var sheet = ss.getSheetByName(SHEETS.GASTOS);
  if (!sheet) { Logger.log("No existe la hoja Gastos."); return; }

  var rate = parseFloat(
    PropertiesService.getScriptProperties().getProperty("last_dolar_rate")
  ) || 0;
  Logger.log("TC cacheado (last_dolar_rate) = " + rate);

  if (!rate) {
    Logger.log("No hay TC cacheado. Registrá un gasto por Telegram y reintentá.");
    return;
  }

  _backfillGastosCurrency(sheet);
  Logger.log("Backfill completado sobre todas las filas con monto faltante.");
}


/**
 * Utilidad de un solo uso: crea el trigger INSTALABLE de onEdit (corre con
 * permisos completos, a diferencia del trigger simple). Solo hace falta si el
 * trigger simple falla por permisos. Correr UNA vez desde el editor.
 * Es idempotente: si ya existe el trigger instalable, no crea otro.
 */
function createOnEditTrigger() {
  var ss = _getSpreadsheet();
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "onEdit" &&
        triggers[i].getEventType() === ScriptApp.EventType.ON_EDIT) {
      Logger.log("Ya existe un trigger instalable de onEdit. Nada que hacer.");
      return;
    }
  }
  ScriptApp.newTrigger("onEdit").forSpreadsheet(ss).onEdit().create();
  Logger.log("Trigger instalable de onEdit creado.");
}


// ─── Dólar cripto ────────────────────────────────────────────

function _toUSD(amountARS, dolarRate) {
  if (!dolarRate) return "";
  return Math.round((amountARS / dolarRate) * 100) / 100;
}


// ─── Helpers ────────────────────────────────────────────────

function _getSpreadsheet() {
  return SPREADSHEET_ID
    ? SpreadsheetApp.openById(SPREADSHEET_ID)
    : SpreadsheetApp.getActiveSpreadsheet();
}

function _getOrCreateSheet(ss, name, headers) {
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.appendRow(headers);
  }
  return sheet;
}

function _today() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "dd/MM/yyyy");
}

/** true si el valor que devolvió getValues() es un Date real y no texto. */
function _isDate(val) {
  return Object.prototype.toString.call(val) === "[object Date]" && !isNaN(val.getTime());
}

/**
 * Normaliza una celda de fecha a "dd/MM/yyyy".
 * Sheets suele guardar las fechas como Date reales, y ahí toString() devuelve
 * "Sat Aug 01 2026 ..." — no el formato con barras que espera el resto del código.
 */
function _formatDateCell(val) {
  if (!val) return _today();
  if (_isDate(val)) {
    return Utilities.formatDate(val, Session.getScriptTimeZone(), "dd/MM/yyyy");
  }
  return val.toString();
}

function _extractMonth(rawDate) {
  if (!rawDate) return "Sin fecha";
  if (_isDate(rawDate)) {
    return Utilities.formatDate(rawDate, Session.getScriptTimeZone(), "yyyy-MM");
  }
  var parts = rawDate.toString().split("/");
  if (parts.length === 3) return parts[2] + "-" + parts[1];
  return "Sin fecha";
}

function _jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
