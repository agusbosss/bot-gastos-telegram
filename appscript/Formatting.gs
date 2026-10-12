/**
 * Formatting.gs — Formato automático de hojas Gastos y Compras
 *
 * Funciona en dos modos:
 *   1. onEdit (trigger automático): se dispara con cualquier edición manual
 *      en Gastos, Compras, o al cambiar un color en la hoja Categorías.
 *      Aplica colores por fila pero NO ordena (demasiado lento en tiempo real).
 *
 *   2. reformatAllSheets(): ordena por fecha (más reciente arriba) Y reformatea
 *      todo, incluyendo filas viejas. Corre sola al abrir la planilla (onOpen en
 *      Code.gs) y está también en el menú "Bot de gastos".
 *
 * Cómo cambiar el color de una categoría:
 *   1. Abrí la hoja "Categorías"
 *   2. En la columna C, buscá la fila de la categoría que querés cambiar
 *   3. Pintá el fondo de esa celda con el color que prefieras (Format → Fill color)
 *   4. El cambio se refleja automáticamente en Gastos y Compras al guardar
 *
 * No hace falta instalar ningún trigger: onEdit y onOpen (en Code.gs) son
 * triggers simples y corren solos. Agregar además un trigger instalable de
 * onEdit haría que todo corra dos veces.
 */


// Colores de filas alternas (blanco y gris muy claro)
const ROW_ALT_COLORS = ["#FFFFFF", "#F2F2F2"];

// Paleta de colores de fallback (se usa cuando una categoría no tiene color en col C)
const CATEGORY_PALETTE = [
  "#D9EAD3", // verde salvia
  "#CFE2F3", // azul suave
  "#FFF2CC", // amarillo pálido
  "#FCE5CD", // durazno
  "#EAD1DC", // malva
  "#D0E4E8", // teal claro
  "#F4CCCC", // salmón
  "#D9D2E9", // lavanda
  "#E2EFDA", // menta
  "#F9CB9C", // ámbar
];


// ─── Formato en edición ──────────────────────────────────────

/**
 * Aplica colores en cada edición manual del spreadsheet.
 * Solo colores (sin ordenar) para mantener la respuesta rápida.
 *
 * NOTA: NO es el trigger onEdit. El único onEdit del proyecto vive en
 * Code.gs y llama a esta función. (Apps Script comparte un namespace
 * global entre archivos .gs, así que no puede haber dos onEdit.)
 */
function _applyFormattingOnEdit(e) {
  if (!e) return;
  var sheet = e.range.getSheet();
  var sheetName = sheet.getName();
  var ss = sheet.getParent();

  if (sheetName === SHEETS.GASTOS) {
    _applyColors(ss, sheet, "gastos");
  } else if (sheetName === SHEETS.COMPRAS) {
    _applyColors(ss, sheet, "compras");
  } else if (sheetName === SHEETS.CATEGORIAS && e.range.getColumn() === 3) {
    // El usuario cambió un color en col C de Categorías → refrescar ambas hojas
    var gastosSheet = ss.getSheetByName(SHEETS.GASTOS);
    var comprasSheet = ss.getSheetByName(SHEETS.COMPRAS);
    if (gastosSheet) _applyColors(ss, gastosSheet, "gastos");
    if (comprasSheet) _applyColors(ss, comprasSheet, "compras");
  }
}


// ─── Funciones públicas ──────────────────────────────────────

/**
 * Reformatea todas las hojas: ordena por fecha + aplica colores.
 * Correr esta función UNA VEZ desde el editor para formatear filas viejas,
 * o cada vez que cargues datos manualmente y quieras ordenarlos.
 */
function reformatAllSheets() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var gastosSheet = ss.getSheetByName(SHEETS.GASTOS);
  var comprasSheet = ss.getSheetByName(SHEETS.COMPRAS);
  if (gastosSheet) _refreshGastosFormatting(ss, gastosSheet);
  if (comprasSheet) _refreshComprasFormatting(ss, comprasSheet);
}


// ─── Lógica de formato ───────────────────────────────────────

/**
 * Formato completo (sort + colores) para Gastos. Llamado por el bot al insertar.
 * `newRows`: filas recién agregadas al final, para que queden arriba de todo.
 */
function _refreshGastosFormatting(ss, sheet, newRows) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  _sortByDate(sheet, 5, 2, GASTOS_HEADERS.length, newRows); // col E = Fecha
  _applyColors(ss, sheet, "gastos");
}


/**
 * Formato completo (sort + colores) para Compras. Llamado por el bot al insertar.
 * `newRows`: filas recién agregadas al final, para que queden arriba de todo.
 */
function _refreshComprasFormatting(ss, sheet, newRows) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  _sortByDate(sheet, 1, 2, COMPRAS_HEADERS.length, newRows); // col A = Fecha
  _applyColors(ss, sheet, "compras");
}


/**
 * Aplica colores alternos por fila y color de categoría en la celda correspondiente.
 * mode: "gastos" o "compras"
 */
function _applyColors(ss, sheet, mode) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  var numDataRows = lastRow - 1;
  var colorMap = _readCategoryColorMap(ss);

  var numCols, catCol, subcatCol;
  if (mode === "gastos") {
    numCols = GASTOS_HEADERS.length;  // 4
    catCol = 2;                        // col B = Categoría (1-based)
    subcatCol = null;
  } else {
    numCols = COMPRAS_HEADERS.length; // 10
    catCol = 5;                        // col E = Categoría (1-based)
    subcatCol = 4;                     // col D = Subcategoría (1-based)
  }

  var subcatColorMap = subcatCol ? _readSubcategoryColorMap(ss, colorMap) : {};
  var categoryValues = sheet.getRange(2, catCol, numDataRows, 1).getValues();
  var subcatValues = subcatCol
    ? sheet.getRange(2, subcatCol, numDataRows, 1).getValues()
    : null;

  var backgrounds = [];

  for (var i = 0; i < numDataRows; i++) {
    var altColor = ROW_ALT_COLORS[i % 2];
    var catName = (categoryValues[i][0] || "").toString();
    var catColor = colorMap[catName] || altColor;
    var row = [];
    for (var c = 0; c < numCols; c++) {
      if (c === catCol - 1) {
        row.push(catColor);
      } else if (subcatCol && c === subcatCol - 1) {
        var subcatName = (subcatValues[i][0] || "").toString();
        row.push(subcatColorMap[subcatName] || catColor);
      } else {
        row.push(altColor);
      }
    }
    backgrounds.push(row);
  }

  var dataRange = sheet.getRange(2, 1, numDataRows, numCols);
  dataRange.setBackgrounds(backgrounds);
  dataRange.setBorder(
    true, false, true, false, false, true,
    "#AAAAAA", SpreadsheetApp.BorderStyle.SOLID
  );
}


/**
 * Lee los colores de categoría desde la col C de la hoja Categorías.
 * Si una celda no tiene color (fondo blanco), usa la paleta de fallback.
 * Retorna un objeto { "NombreCategoria": "#hexcolor" }.
 */
function _readCategoryColorMap(ss) {
  var catSheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!catSheet) return {};

  var lastRow = catSheet.getLastRow();
  if (lastRow < 2) return {};

  var names = catSheet.getRange(2, 1, lastRow - 1, 1).getValues();
  var colors = catSheet.getRange(2, 3, lastRow - 1, 1).getBackgrounds();

  var colorMap = {};
  var paletteIdx = 0;

  for (var i = 0; i < names.length; i++) {
    if (!names[i][0]) continue;
    var name = names[i][0].toString();
    var bg = colors[i][0];
    // Celda sin color = fondo blanco → usar paleta
    if (!bg || bg === "#ffffff" || bg === "#FFFFFF") {
      bg = CATEGORY_PALETTE[paletteIdx % CATEGORY_PALETTE.length];
    }
    colorMap[name] = bg;
    paletteIdx++;
  }

  return colorMap;
}


/**
 * Lee el mapeo subcategoría → color desde la hoja Categorías (cols D y E).
 * Col D = nombre de subcategoría, col E = categoría padre.
 * Cada subcategoría recibe su propio color de paleta (paleta separada de la de categorías).
 * Retorna { "NombreSubcat": "#hexcolor" }.
 */
function _readSubcategoryColorMap(ss, categoryColorMap) {
  var catSheet = ss.getSheetByName(SHEETS.CATEGORIAS);
  if (!catSheet) return {};

  var lastRow = catSheet.getLastRow();
  if (lastRow < 2) return {};

  // Paleta secundaria con colores más saturados para distinguir de las categorías
  var SUBCAT_PALETTE = [
    "#B6D7A8", // verde medio
    "#9FC5E8", // azul medio
    "#FFE599", // amarillo medio
    "#F9B37B", // naranja medio
    "#D5A6BD", // rosa medio
    "#A2C4C9", // teal medio
    "#E06666", // rojo suave
    "#B4A7D6", // violeta medio
    "#93C47D", // verde intenso
    "#F6B26B", // ámbar medio
    "#76A5AF", // azul petróleo
    "#C27BA0", // fucsia suave
  ];

  var subcatData = catSheet.getRange(2, 4, lastRow - 1, 2).getValues(); // cols D-E
  var subcatColorMap = {};
  var paletteIdx = 0;

  for (var i = 0; i < subcatData.length; i++) {
    var subcatName = (subcatData[i][0] || "").toString();
    if (!subcatName) continue;
    subcatColorMap[subcatName] = SUBCAT_PALETTE[paletteIdx % SUBCAT_PALETTE.length];
    paletteIdx++;
  }

  return subcatColorMap;
}


// ─── Sort por fecha ──────────────────────────────────────────

/**
 * Ancho de la tabla: las columnas con encabezado contiguas desde la A.
 *
 * El sort reordena filas enteras, así que solo puede mover lo que REALMENTE es
 * parte de la fila. Todo lo que viva a la derecha de la primera columna sin
 * encabezado —una tabla de resumen al costado, notas sueltas— es contenido
 * independiente: si el sort lo arrastrara, cada gasto nuevo lo empujaría una
 * fila hacia abajo (y `setValues` de paso le convertiría las fórmulas en
 * valores fijos).
 *
 * Se mide sobre la fila 1 en vez de fijar un número para que agregar una
 * columna a la tabla no requiera tocar el código.
 */
function _tableWidth(sheet, minCols) {
  var fallback = Math.max(minCols || 0, 1);
  var lastCol = sheet.getLastColumn();
  if (lastCol < 1) return fallback;

  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0];
  var width = 0;
  while (width < headers.length && headers[width].toString().trim() !== "") width++;

  return Math.max(width, fallback);
}


/**
 * Ordena las filas de datos por fecha, de la MÁS RECIENTE a la más vieja
 * (equivalente a "Ordenar Z → A" a mano). Así el último gasto queda siempre
 * en la fila 2 y no hay que reordenar la hoja cada vez que se abre.
 *
 * `newRows` (opcional) = cuántas filas acaba de agregar el bot al final de la
 * hoja con appendRow. Ese bloque se mueve al principio ANTES de ordenar, para
 * que dentro de un mismo día lo recién cargado quede arriba de lo anterior
 * (el sort es estable: con fechas iguales respeta el orden que ya había).
 * Sin `newRows` el orden dentro de cada día no se toca, así que reordenar de
 * nuevo la hoja no cambia nada.
 *
 * Solo mueve las columnas de la tabla (ver `_tableWidth`): lo que haya a la
 * derecha se queda quieto donde está.
 */
function _sortByDate(sheet, dateCol, fromRow, numCols, newRows) {
  var lastRow = sheet.getLastRow();
  if (lastRow < fromRow + 1) return;

  var numRows = lastRow - fromRow + 1;
  numCols = _tableWidth(sheet, numCols);
  var dataRange = sheet.getRange(fromRow, 1, numRows, numCols);
  var values = dataRange.getValues();
  var notes = dataRange.getNotes(); // mover las notas-marcador junto con su fila
  var colIdx = dateCol - 1;

  // Combinar valores + notas para que se ordenen juntos. setValues NO mueve las
  // notas por su cuenta (quedarían pegadas a la posición física de la celda).
  var combined = values.map(function(v, i) { return { v: v, n: notes[i] }; });

  // Las filas recién agregadas (siempre al final, por appendRow) van al frente.
  var n = Math.min(parseInt(newRows) || 0, combined.length);
  if (n > 0) {
    combined = combined.slice(combined.length - n).concat(combined.slice(0, combined.length - n));
  }

  // Índice de desempate: mantiene el orden previo entre filas de la misma fecha
  // sin depender de que el sort del runtime sea estable.
  combined.forEach(function(c, i) { c.i = i; });

  combined.sort(function(a, b) {
    var diff = _parseDateDDMMYYYY(b.v[colIdx]) - _parseDateDDMMYYYY(a.v[colIdx]); // desc
    return diff !== 0 ? diff : a.i - b.i;
  });

  dataRange.setValues(combined.map(function(c) { return c.v; }));
  dataRange.setNotes(combined.map(function(c) { return c.n; }));
}


/**
 * Fecha de una celda → timestamp comparable.
 *
 * OJO: Sheets guarda casi siempre estas fechas como Date reales, no como texto.
 * En ese caso `val.toString()` es "Sat Aug 01 2026 00:00:00 GMT-0300", que no
 * tiene 3 partes separadas por "/" → antes devolvía 0 para TODAS las filas, el
 * comparador siempre daba 0 y el sort por fecha no ordenaba nada.
 */
function _parseDateDDMMYYYY(val) {
  if (!val) return 0;
  if (_isDate(val)) return val.getTime();

  var parts = val.toString().split("/");
  if (parts.length === 3) {
    return new Date(
      parseInt(parts[2]),
      parseInt(parts[1]) - 1,
      parseInt(parts[0])
    ).getTime();
  }
  return 0;
}
