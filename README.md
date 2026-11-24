# Sherlock Vision · ROI

Inspección automática de presencia de tornillos en housings de iPhone y captura de
evidencia fotográfica antes del cierre de la unidad. Airlink Distribution DR.

- `app/` — aplicación de escritorio (Qt) para las Mac mini.
- `motor/` — núcleo de inspección. Ver `motor/README.md`.
- `sql/` — scripts de base de datos (orden en `sql/README.md`).
- `docs/` — traspaso, esquema verificado, instalación.
- `tests/` — pruebas (`QT_QPA_PLATFORM=offscreen python -m pytest`).

Instalación en una Mac mini: `bash app/instalar.sh` (ver `docs/LEEME_instalacion_mac_mini.txt`).

Para quien trabaje en el código: leer `CLAUDE.md` primero.
