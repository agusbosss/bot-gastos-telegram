# Guía para agentes de IA

Bot de Telegram que registra gastos (texto, voz y fotos de tickets) en Google
Sheets. Python (FastAPI) + Claude + Google Apps Script.

- **Instalación paso a paso de la versión que funciona hoy (Railway):**
  [`README.md`](README.md).
- **Todas las formas de desplegarlo** (otros hostings, sin servidor solo con Apps
  Script, en una compu propia, con otros modelos o gratis), qué está
  implementado y qué no, y cómo implementar cada una:
  [`docs/formas-de-desplegarlo.md`](docs/formas-de-desplegarlo.md).

Antes de cambiar el prompt, el envío de gastos o el Apps Script, leé la sección
**"Reglas que cualquier versión tiene que respetar"** de ese documento: cada
regla salió de un error real que hacía perder o duplicar gastos.

Si la persona que te pidió ayuda no programa, guiala con el README y hacé los
pasos de terminal por ella cuando puedas. Las keys y tokens van en variables de
entorno o en las Propiedades del script de Apps Script, nunca en el código.
