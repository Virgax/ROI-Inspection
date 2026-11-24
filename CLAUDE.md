# CLAUDE.md — Sherlock Vision · ROI

Inspección de presencia de tornillos en housings de iPhone + captura de evidencia
antes del cierre. Corre en Mac mini Intel (Python 3.9) con cámara, escáner USB y
SQL Server local de planta. Documento completo: `docs/HANDOFF_CLAUDE_CODE.md`.

## Estructura

- `app/` — aplicación Qt (PySide6): ventana, admin, marcado de ROIs, empaquetado.
- `motor/` — núcleo de inspección (`inspector.py`, `cierre.py`, `roi_db.py`, …).
  **Una sola copia.** `app/motor.py` la localiza (carpeta hermana de `app/`).
- `sql/` — scripts para Jaime. `sql/obsoleto/` no se ejecuta.
- `docs/` — handoff, esquema verificado, instalación.

Recetas, logs y banco viven en `~/Documents/ROI`; credenciales en `~/.roi/` (600).
Nada de eso entra al repo.

## Decisiones que NO se tocan sin preguntar a Jaime

Romper una no da error: da una estación que sigue trabajando y juzga mal.

**Base de datos**
1. Nunca `cur.callproc()`: pymssql recorta cadenas a 8.000 caracteres sin error.
   Usar `DECLARE @h VARCHAR(MAX)=%s; EXEC dbo.SP_Save_PRD_ClosingEvidence ...`.
2. Ningún índice único sobre GCN. Una unidad se cierra varias veces y con dos
   lados tiene dos filas. Los previos se cuentan por etapa y se avisan; nunca bloquean.
3. La validación de emparejamiento nunca bloquea (`exigir_emparejamiento=False`).
4. Los extras (serial, estación, etapa, parte, versión) se escriben DESPUÉS del
   commit, en su propio try. El operario nunca ve "falló" si la evidencia quedó.
5. DDL y procedimientos en archivos separados.
6. Las estaciones nunca ejecutan DDL (`AlAppUser` no puede).
7. Comprobar con `COL_LENGTH` que una columna existe antes de usarla. Un aviso
   nunca se convierte en error.

**Visión**
8. Rectificar siempre a `cfg["image_size"]`. La referencia promedia ~1 s de centros ArUco.
9. `hay_pieza` no sirve para saber si la pieza salió: toda salida de estado que
   dependa de ella tiene además salida por tiempo.
10. Perder los marcadores lo decide `Cierre.sin_tapete()`, no el bucle de vídeo.
11. Los tiempos del operario van en segundos de reloj, no en fotogramas.
12. El Data Matrix de la parte es el de arriba junto a la manzana (26 car., `WMW`
    en medio). ~2,4 px/módulo: por eso existe el respaldo de escáner.
13. GCN `^[A-Z]{1,3}\d{4,8}$`; la autoridad es `SPC_Receiving`.
14. Contaminación apagada por defecto.

**App y despliegue**
15. El escáner se escucha con filtro a nivel de aplicación; se distingue por
    velocidad (`PAUSA_ESCANER = 0.5`). Todo escaneo da respuesta visible.
16. Datos fuera del código. Actualizar nunca toca una calibración.
17. Python 3.9: `PySide6==6.10.3`, `zxing-cpp==2.3.0`, `numpy==2.0.2`,
    `opencv-python<5`. Instalar con `--no-compile`.
18. Construir el `.app` en Intel.
19. `NSCameraUsageDescription` en `Info.plist` + re-firma ad-hoc.
20. Copiar un `.app` entre Macs puede romper el permiso de cámara (TCC) en
    silencio. Por eso las actualizaciones envían código (carga), no el `.app`.
21. La app recuerda receta, cámara y casillas (`QSettings`).

## Actualizaciones (objetivo, §5 del handoff)

Runtime (`.app`) casi fijo + carga (`app/*.py`, `motor/*.py`) descargada de GitHub
Releases. Manifiesto firmado Ed25519; **la clave privada nunca en GitHub** (ni repo
ni secretos de Actions). Se aplica al reiniciar, nunca a mitad de un cierre.
Vuelta atrás automática si el arranque falla.

## Convenciones

- Código, comentarios y mensajes en español.
- Los comentarios largos explican el porqué de cada decisión: **no acortarlos**.
- Compatibilidad con Python 3.9 (sin `match`, sin `X | Y` en tipos en tiempo de ejecución).
- Pruebas: `QT_QPA_PLATFORM=offscreen`; base falsa por texto de consulta;
  `Cierre` con `activo/disponible` forzados sobre un objeto recién creado;
  configs pasados por `inspector.cargar_config`.
