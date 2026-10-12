/**
 * Setup.gs — Arma la planilla completa desde cero.
 *
 * Correr setupPlanilla() UNA vez desde el editor de Apps Script, con la
 * planilla recién creada (vacía). Deja:
 *   - Gastos:     tabla de gastos + resumen por categoría entre dos fechas (G:J)
 *   - Compras:    detalle de cada producto de los tickets
 *   - Dashboard:  gráfico de compras por subcategoría
 *   - Categorías: categorías (A:C) y subcategorías (D:F) que usa el bot
 *   - El token secreto que comparten la planilla y el bot (SECRET_TOKEN)
 *
 * Es idempotente: si la corrés de nuevo no duplica nada ni pisa lo que ya
 * cargaste; solo completa lo que falte.
 */


const HEADER_BG = "#E8EAED";
const MONEY_FORMAT = "[$$]#,##0.00";
const DATE_FORMAT = "dd/MM/yyyy";


function setupPlanilla() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // El bot manda las fechas como "dd/MM/yyyy": con locale argentino Sheets las
  // interpreta como fechas reales (día/mes) y el orden y el resumen funcionan.
  ss.setSpreadsheetLocale("es_AR");
  ss.setSpreadsheetTimeZone("America/Argentina/Buenos_Aires");

  var token = _ensureSecretToken();

  var gastos = _setupGastosSheet(ss);
  var compras = _setupComprasSheet(ss);
  var dashboard = _setupDashboardSheet(ss);
  var categorias = _setupCategoriasSheet(ss);

  _orderSheets(ss, [gastos, compras, dashboard, categorias]);
  _removeEmptyDefaultSheet(ss);

  var msg =
    "Planilla lista.\n\n" +
    "Token secreto (va en APPS_SCRIPT_SECRET del bot):\n" + token + "\n\n" +
    "Siguiente paso: Implementar → Nueva implementación → Aplicación web.";
  Logger.log(msg);

  // Si se corrió desde el menú de la planilla, mostrarlo ahí también.
  _bestEffort("aviso en pantalla", function () {
    SpreadsheetApp.getUi().alert(msg);
  });
}


/** Genera el token la primera vez y lo devuelve siempre igual después. */
function _ensureSecretToken() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty("SECRET_TOKEN");
  if (!token) {
    token = Utilities.getUuid().replace(/-/g, "") + Utilities.getUuid().replace(/-/g, "");
    props.setProperty("SECRET_TOKEN", token);
  }
  return token;
}


// ─── Gastos ─────────────────────────────────────────────────

function _setupGastosSheet(ss) {
  var sheet = _getOrCreateSheet(ss, SHEETS.GASTOS, GASTOS_HEADERS);
  _ensureHeaders(sheet, GASTOS_HEADERS);

  sheet.setFrozenRows(1);
  sheet.getRange("C2:D").setNumberFormat(MONEY_FORMAT);
  sheet.getRange("E2:E").setNumberFormat(DATE_FORMAT);
  sheet.getRange("E2:E").setDataValidation(
    SpreadsheetApp.newDataValidation().requireDate().setAllowInvalid(true).build()
  );
  sheet.setColumnWidth(1, 260);
  sheet.setColumnWidth(2, 130);
  sheet.setColumnWidth(3, 120);
  sheet.setColumnWidth(4, 120);
  sheet.setColumnWidth(5, 110);
  sheet.setColumnWidth(6, 30);  // separador entre la tabla y el resumen
  sheet.setColumnWidth(7, 140);

  if (!sheet.getRange("G8").getValue()) _setupGastosSummary(sheet);
  return sheet;
}


/**
 * Resumen por categoría entre dos fechas, al costado de la tabla (G:J).
 *
 * La columna F queda vacía a propósito: el sort de Formatting.gs solo mueve las
 * columnas con encabezado contiguas desde la A (ver _tableWidth), así que todo
 * lo que esté a la derecha de F se queda quieto cuando se ordenan los gastos.
 *
 * "Desde" y "Hasta" arrancan en el mes actual; se pueden pisar a mano con
 * cualquier fecha.
 */
function _setupGastosSummary(sheet) {
  sheet.getRange("G6").setValue("Resumen por categoría").setFontWeight("bold").setFontSize(12);

  sheet.getRange("G8:G9").setValues([["Desde"], ["Hasta"]]).setFontWeight("bold");
  sheet.getRange("H8").setFormula("=EOMONTH(TODAY(),-1)+1");
  sheet.getRange("H9").setFormula("=EOMONTH(TODAY(),0)");
  sheet.getRange("H8:H9").setNumberFormat(DATE_FORMAT).setBackground("#FFF2CC");

  sheet.getRange("G12:J12")
    .setValues([["Categoría", "Monto (AR$)", "Monto (U$D)", "%"]])
    .setFontWeight("bold")
    .setBackground(HEADER_BG);

  sheet.getRange("G13").setValue("Total").setFontWeight("bold");
  sheet.getRange("H13").setFormula('=SUMIFS($C$2:$C,$E$2:$E,">="&$H$8,$E$2:$E,"<="&$H$9)');
  sheet.getRange("I13").setFormula('=SUMIFS($D$2:$D,$E$2:$E,">="&$H$8,$E$2:$E,"<="&$H$9)');
  sheet.getRange("J13").setFormula('=IF($H$13=0,"",1)');
  sheet.getRange("H13:J13").setFontWeight("bold");

  sheet.getRange("G14").setFormula(
    '=IFERROR(QUERY($A$2:$E,"select B, sum(C), sum(D) where B is not null' +
    ' and E >= date \'"&TEXT($H$8,"yyyy-mm-dd")&"\'' +
    ' and E <= date \'"&TEXT($H$9,"yyyy-mm-dd")&"\'' +
    ' group by B order by sum(C) desc label B \'\', sum(C) \'\', sum(D) \'\'",0),"")'
  );
  // Un solo array que ocupa exactamente tantas filas como categorías haya:
  // si se completara la columna con fórmulas sueltas, la hoja "terminaría" más
  // abajo y cada gasto nuevo del bot (appendRow) aterrizaría debajo de todo.
  sheet.getRange("J14").setFormula('=IFERROR(ARRAYFORMULA(FILTER(H14:H,H14:H<>"")/$H$13),"")');

  sheet.getRange("H13:I60").setNumberFormat(MONEY_FORMAT);
  sheet.getRange("J13:J60").setNumberFormat("0.00%");
}


// ─── Compras ────────────────────────────────────────────────

function _setupComprasSheet(ss) {
  var sheet = _getOrCreateSheet(ss, SHEETS.COMPRAS, COMPRAS_HEADERS);
  _ensureHeaders(sheet, COMPRAS_HEADERS);

  sheet.setFrozenRows(1);
  sheet.getRange("A2:A").setNumberFormat(DATE_FORMAT);
  sheet.getRange("G2:H").setNumberFormat(MONEY_FORMAT);
  sheet.setColumnWidth(1, 100);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 300);
  sheet.setColumnWidth(4, 160);
  sheet.setColumnWidth(5, 130);
  return sheet;
}


// ─── Dashboard ──────────────────────────────────────────────

/**
 * Total de compras por subcategoría (de la hoja Compras) + gráfico de barras.
 * La tabla A3:B la calcula una QUERY (desde A4), así que el gráfico se actualiza solo.
 */
function _setupDashboardSheet(ss) {
  var sheet = ss.getSheetByName("Dashboard") || ss.insertSheet("Dashboard");

  if (!sheet.getRange("A4").getFormula()) {
    sheet.getRange("A1").setValue("Compras por subcategoría").setFontWeight("bold").setFontSize(12);
    sheet.getRange("A3:B3")
      .setValues([["Subcategoría", "Total ($)"]])
      .setFontWeight("bold")
      .setBackground(HEADER_BG);
    sheet.getRange("A4").setFormula(
      '=IFERROR(QUERY(Compras!A2:I,"select D, sum(H) where D is not null' +
      ' group by D order by sum(H) desc label D \'\', sum(H) \'\'",0),"")'
    );
    sheet.getRange("B4:B").setNumberFormat(MONEY_FORMAT);
    sheet.setColumnWidth(1, 180);
    sheet.setColumnWidth(2, 120);
  }

  if (sheet.getCharts().length === 0) {
    var chart = sheet.newChart()
      .setChartType(Charts.ChartType.COLUMN)
      .addRange(sheet.getRange("A3:B60"))
      .setNumHeaders(1)
      .setPosition(1, 4, 0, 0)
      .setOption("title", "Compras")
      .setOption("legend", { position: "none" })
      .setOption("width", 720)
      .setOption("height", 420)
      .build();
    sheet.insertChart(chart);
  }
  return sheet;
}


// ─── Categorías ─────────────────────────────────────────────

function _setupCategoriasSheet(ss) {
  var sheet = _ensureCategoriesSheet(ss);

  sheet.getRange("A1:F1").setFontWeight("bold").setBackground(HEADER_BG);
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 140);
  sheet.setColumnWidth(2, 380);
  sheet.setColumnWidth(3, 50);
  sheet.setColumnWidth(4, 180);
  sheet.setColumnWidth(5, 140);
  sheet.setColumnWidth(6, 320);
  return sheet;
}


// ─── Helpers ────────────────────────────────────────────────

/** Escribe los encabezados si la fila 1 está vacía, y les da formato. */
function _ensureHeaders(sheet, headers) {
  var range = sheet.getRange(1, 1, 1, headers.length);
  if (!range.getValues()[0].join("")) range.setValues([headers]);
  range.setFontWeight("bold").setBackground(HEADER_BG);
}


function _orderSheets(ss, sheets) {
  for (var i = 0; i < sheets.length; i++) {
    ss.setActiveSheet(sheets[i]);
    ss.moveActiveSheet(i + 1);
  }
  ss.setActiveSheet(sheets[0]);
}


/** Borra la "Hoja 1" que trae toda planilla nueva, solo si sigue vacía. */
function _removeEmptyDefaultSheet(ss) {
  ["Hoja 1", "Hoja1", "Sheet1", "Sheet 1"].forEach(function (name) {
    var sheet = ss.getSheetByName(name);
    if (sheet && sheet.getLastRow() === 0 && ss.getSheets().length > 1) {
      ss.deleteSheet(sheet);
    }
  });
}
