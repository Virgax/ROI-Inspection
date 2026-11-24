# Sherlock Vision · ROI — Traspaso a Claude Code

**Para:** Claude Code, que continúa el proyecto desde aquí.
**Dueño:** Jaime Estevez (Airlink Distribution DR).
**Fecha:** 24-nov-2026.

Este documento es el punto de partida. Léelo entero antes de tocar código: la
sección 3 recoge decisiones que costaron días de planta y que parecen
arbitrarias si no se conoce la historia. Romper una de ellas no da error: da una
estación que sigue trabajando y juzga mal.

---

## 0. Qué se pide

1. **Un repositorio privado en GitHub** con todo el código. Única fuente de verdad.
2. **Versiones publicadas como GitHub Releases.**
3. **Actualización automática en las estaciones:** la app instalada en cada Mac
   mini comprueba GitHub, descarga la versión nueva y la aplica sola.

### Por qué GitHub y no un servidor propio

Se valoró Railway y se descartó para esto:

- **La inspección no puede correr en la nube.** Cámara, escáner USB y fixture
  están físicamente en cada Mac mini.
- **La nube no alcanza el SQL Server.** `192.168.181.248,13999` es una IP de la
  red local de la planta.
- Lo único que necesita vivir fuera es **guardar y servir versiones**, y eso
  GitHub Releases ya lo hace, sin un servicio que pagar, vigilar y mantener vivo.

Si algún día se quiere un panel web, Railway puede alojarlo; no es necesario
para nada de lo de este documento.

---

## 1. Qué es el sistema

Inspección automática de presencia de tornillos en housings de iPhone, y captura
de evidencia fotográfica antes del cierre definitivo de la unidad.

```
          ┌───────────────────────── Mac mini (Intel) ──────────────────────────┐
          │                                                                      │
cámara ──▶│  ArUco (4 marcadores) → homografía → lienzo rectificado              │
 USB      │        → recorte por ROI → plantillas NCC+SQDIFF → votación          │
          │        → PASA / FALTAN / VERIFICAR                                   │
escáner ─▶│                                                                      │
 USB      │  Cierre (si la casilla está marcada):                                │
          │   giftcard → valida GCN → foto housing → pide la parte →             │
          │   lee su Data Matrix (o escáner, o sin serial) → foto parte → SQL    │
          │                                                                      │
          │  ~/Documents/ROI   recetas, log CSV, banco de evidencia              │
          │  ~/.roi            conexion.json, admin.json, github.json (600)      │
          └──────────┬──────────────────────────────────────┬────────────────────┘
                     │ red local                            │ HTTPS
                     ▼                                      ▼
       SQL Server 2022 · AIRLINK                 GitHub Releases (privado)
       192.168.181.248,13999                     versiones firmadas
```

### Flujo de cierre

Máquina de estados en `cierre.py`:

```
INACTIVO → GIFTCARD → VALIDANDO → PIDE_LCD → QR_LCD ─┬─▶ VALIDA_LCD → REGISTRANDO → HECHO
                                                     └─▶ ESPERA_SCAN ─┘
                                     (cualquiera) ──────────────────────────────────▶ ERROR
```

- `PIDE_LCD`: se pide retirar el housing y colocar la parte. Sale por **tres**
  caminos: la pieza sale, se lee un código en la zona de la parte, o pasan 3 s.
- `QR_LCD`: busca el código 10 s (`SEG_RENDIRSE_CAMARA`). Si no sale:
- `ESPERA_SCAN`: pide el escáner, cuenta atrás de 20 s (`SEG_ESPERA_SCAN`). Si
  nadie escanea, **guarda igual sin serial**.
- `HECHO` / `ERROR`: se limpian al retirar la pieza o por límite de tiempo.

### Recetas y modelos de dos lados

Una **receta** = una vista física = una geometría = una calibración. Vive en
`~/Documents/ROI/procesos/<nombre>/` con `config_rois.json`, `plantillas.npz`,
`umbrales.json` y `referencia.png`.

En iPhone 14, 14 Plus, 15 y 15 Plus el housing tiene **dos lados** con tornillos
propios: uno se cierra con el display y otro con el backglass. Son **dos
recetas**: `IP14 - DISPLAY` e `IP14 - BACKGLASS`.

**La parte se deduce del nombre de la receta** (`cierre._etapa_de_cfg`):
`BACKGLASS` / `BACK GLASS` / `BACK-GLASS` → BACKGLASS; `DISPLAY` / `LCD` →
DISPLAY; si no dice nada, DISPLAY. Un bloque `etapa` en el config la sobrescribe.
Lo deducido se anuncia al arrancar y se ve en el panel (ámbar si cayó al valor
por defecto).

Cada etapa es un cierre **independiente**: lee su propio giftcard, puede hacerse
en otro momento u otra estación, y escribe **su propia fila**. El enlace entre los
dos lados es el GCN en la base. Por eso **el orden no es asunto del programa**.

---

## 2. Inventario

No hay git. El código está repartido en copias hechas a mano.

```
~/Desktop/ROI/                         (Mac M1 de Jaime, la de desarrollo)
├── app/                               la aplicación Qt
│   ├── roi_app.py        715  ventana principal, hilo de inspección, escáner
│   ├── admin.py         1021  panel de administración (recetas, ROIs, calibración)
│   ├── marcador.py       285  marcado nativo de ROIs sobre la referencia
│   ├── seguridad.py      135  acceso admin: PBKDF2, semilla sin texto plano
│   ├── motor.py          130  localiza e importa el motor
│   ├── rutas.py          140  dónde vive cada dato (~/Documents/ROI)
│   ├── empaquetar.py     217  PyInstaller → .app
│   ├── instalar.sh            instalación de una pasada en una Mac mini
│   └── ROI.icns               icono
├── ROI Ver11 Yolo 15PM/               el MOTOR (copia de referencia)
│   ├── inspector.py     1935  núcleo: rectificado, puntuación, modo terminal
│   ├── cierre.py         820  máquina de estados del cierre
│   ├── roi_db.py         517  SQL: validación, guardado, extras
│   ├── roi_qr.py         247  QR (OpenCV) + Data Matrix (zxing-cpp)
│   ├── veredicto.py      172  votación temporal (ver deuda §7)
│   ├── banco.py          203  banco de evidencia local
│   ├── contaminacion.py  347  detector de suciedad (APAGADO por defecto)
│   ├── detector_anomalias.py  línea base estadística + YOLO
│   ├── juez_cruz.py      242  "Juez 2": rescata Phillips, nunca veta
│   ├── marcar_zona_qr.py      herramienta de terminal (heredada)
│   ├── analizar_log.py / migrar_log.py / ver_plantillas.py / diagnostico_juez.py
│   └── README.md              documentación del motor (§15 desactualizada, ver §7)
├── ROI Ver11 Yolo 15P/                COPIA del motor, otro modelo (mismo código)
├── PARA_MAC_MINI/                     paquete de despliegue (otra COPIA)
├── sql/                               scripts de base de datos
└── BACKUP/, ROI_respaldo_*/           respaldos — NO son código vivo
```

Unas 9.200 líneas de Python. La copia de referencia del motor es
`ROI Ver11 Yolo 15PM/`; las demás deben ser idénticas byte a byte.

Las recetas **no son código** y no van al repositorio: son datos de cada estación,
calibrados con su cámara.

---

## 3. Decisiones que no se tocan

Cada una se tomó tras romperse algo. Si una parece mejorable, **pregunta a Jaime
antes de cambiarla**.

### Base de datos

1. **Nunca `cur.callproc()`.** pymssql liga las cadenas como `VARCHAR(8000)` y
   recorta el base64 a 8.000 caracteres **sin error**. Medido: se enviaron
   1.572.932 caracteres y llegaron 8.000. Se usa
   `DECLARE @h VARCHAR(MAX)=%s; EXEC dbo.SP_Save_PRD_ClosingEvidence ...`.
2. **Ningún índice único sobre GCN.** `PRD_ClosingEvidence` es evidencia previa al
   cierre definitivo, no un libro de unidades cerradas. Una unidad puede cerrarse
   varias veces (otra pieza, otro momento) y con dos lados tiene dos filas. Los
   previos se **cuentan por etapa** y se avisan; **nunca bloquean**.
   ⚠️ `claude/esquema_cierre_verificado_2026-09-18.md` llegó a recomendar ese
   índice; está corregido. Manda `integracion_sql_probada_2026-09-18.md`.
3. **La validación de emparejamiento nunca bloquea** (`exigir_emparejamiento=False`).
   Un serial que no coincide se informa junto a "UNIDAD CERRADA" y la evidencia se
   guarda. La foto es lo único irrecuperable; el serial se puede completar después.
4. **Los extras (serial, estación, etapa, parte) se escriben después del commit**,
   en su propio try. Si el procedimiento o una columna faltan, la evidencia ya está
   guardada y solo se avisa por consola. El operario nunca debe ver "falló el
   cierre" de una unidad que sí quedó registrada.
5. **DDL y procedimientos en archivos separados.** SQL Server valida nombres de
   columna al compilar; `GO` lo interpreta el cliente, no el servidor. Con los dos
   en un lote, el procedimiento falla con *Invalid column name*.
6. **AlAppUser no puede crear tablas ni procedimientos.** Lo hace Jaime con otro
   usuario. Las estaciones nunca ejecutan DDL.
7. **Comprobar que una columna existe antes de consultarla** (`COL_LENGTH`) cuando
   puede no haberse migrado todavía. Un aviso nunca puede convertirse en error.

### Visión

8. **Rectificar siempre a `cfg["image_size"]`**, nunca al tamaño calculado. Los
   centros de los ArUco tiemblan en fracción de píxel y el redondeo da 1599, 1600 o
   1601 en capturas seguidas. Solo la captura de referencia decide el lienzo, y
   **promedia los centros de ~1 s** para que sea reproducible.
9. **`hay_pieza` no sirve para saber si la pieza salió.** Mira las ROIs de
   tornillo, y con el display o el backglass puesto ven superficie lisa y votan
   "dudoso". Toda salida de estado que dependa de ella tiene además una salida
   **por tiempo**. Esto rompió el cierre dos veces.
10. **Perder los marcadores lo decide la secuencia** (`Cierre.sin_tapete()`), no el
    bucle de vídeo. En `GIFTCARD` cancela; desde `PIDE_LCD` la pieza sale a
    propósito y solo cancela tras ~20 s. Cancelar en el bucle hacía que sacar el
    housing —el siguiente paso del propio proceso— tirase la secuencia.
11. **Los tiempos que ve el operario van en segundos de reloj**, no en fotogramas.
    Contar fotogramas suponiendo 15 fps hacía que una cuenta atrás de "20 s" durase
    12 en una máquina a 25 fps.
12. **El Data Matrix de la parte es el de arriba, junto a la manzana.** Serial de
    26 caracteres con `WMW` en medio (ej. `FYJ341770HF1WMWADA148RU26B`). El de la
    etiqueta blanca del medio (`GVC350600S41PHTAP+A0410`) es otra cosa. El símbolo
    mide ~48 px en el lienzo: **~2,4 px/módulo cuando el lector necesita 4**. Por
    eso la cámara lee a veces y existe el respaldo de escáner. Leer del fotograma
    crudo gana ~×1,09; no lo arregla. El arreglo real es óptico o un lector DPM.
13. **Patrón de GCN `^[A-Z]{1,3}\d{4,8}$`.** Cubre `CX666666` y los RMA `R10172`.
    El patrón solo decide "parece un GCN"; **la autoridad es `SPC_Receiving`**.
14. **Contaminación apagada por defecto.** Su línea base depende del tamaño del
    lienzo de cada receta y hoy no hay ninguna construida para las Mac mini.

### Aplicación y despliegue

15. **El escáner se escucha con un filtro a nivel de aplicación**, no en la
    ventana: si el foco está en el selector de cámara o en el combo, se tragan el
    código. Se distingue de una persona por velocidad (`PAUSA_ESCANER = 0.5` s).
    Todo escaneo produce una respuesta visible, nunca silencio.
16. **Datos fuera del código.** Recetas, log y banco en `~/Documents/ROI`;
    credenciales en `~/.roi/` con permisos 600. Actualizar el programa nunca puede
    tocar una calibración.
17. **Python 3.9 en las Mac mini → versiones clavadas:** `PySide6==6.10.3` (6.11 es
    cp310), `zxing-cpp==2.3.0` (3.x es cp312), `numpy==2.0.2`, `opencv-python<5`.
    Instalar con `--no-compile`: PySide6 trae una plantilla Jinja con extensión
    `.py` que tumba la precompilación de pip.
18. **Construir en Intel.** Lo construido en Intel corre en Intel y en Apple
    Silicon por Rosetta; al revés no abre. Las estaciones son Mac mini 2018.
19. **`NSCameraUsageDescription` en el `Info.plist` y re-firma ad-hoc.** Sin la
    clave, macOS mata la app con SIGKILL al abrir la cámara, sin diálogo.
20. **Copiar un `.app` entre Macs puede romper el permiso de cámara en silencio.**
    TCC no puede atribuir la petición y deniega sin preguntar. Se arregla con
    `xattr -dr com.apple.quarantine` + `codesign --force --deep --sign -`, o
    construyendo en esa máquina. **Esto condiciona el diseño de §5.**
21. **La app recuerda receta, cámara y casillas** (`QSettings`). Una estación que un
    día arranca sin la casilla de cierre no da error: deja de capturar evidencia.

---

## 4. Base de datos

`AIRLINK` en `192.168.181.248,13999`, SQL Server 2022, usuario `AlAppUser`
(`db_datareader` + `db_datawriter` + `CAL_AppRole`).

| tabla | uso |
|---|---|
| `dbo.SPC_Receiving` | GCN → `Imei`. Autoridad sobre si un GCN existe. Sin índice en GCN (38–45 ms por consulta). |
| `[AirlinkDR].[dbo].[HistoryUnitProcess]` | de aquí sale `IdHistoryUnitProcess`. Otra base. |
| `dbo.PRD_LinkMainPartToUnit` | emparejamientos: `GCN, imei, LCD, Housing, BackGlass, Battery, …` |
| `dbo.PRD_ClosingEvidence` | la evidencia. Sin PK ni índices. |

`PRD_ClosingEvidence`: `Id, Date, Time, IdHistoryUnitProcess, ProcessName,
Housing_PhotoBase64, GCN, IMEI, LCD_PhotoBase64`, más las añadidas:

| columna | script | contenido |
|---|---|---|
| `LCD_Serial`, `LCD_SerialOrigen`, `LCD_SerialFecha` | `03a` | serial de la parte; origen `CAMARA` / `ESCANER` / `CONCILIADO` |
| `Estacion` | `03a` | `platform.node()` de la Mac que escribió |
| `Etapa` | `04a` | nombre de la receta (`IP14 - BACKGLASS`) |
| `Parte` | `04a` | `DISPLAY` / `BACKGLASS` |
| `VersionApp` | **por crear** (§5) | versión de la app que produjo la fila |

`LCD_PhotoBase64` y `LCD_Serial` conservan el nombre histórico aunque traigan un
backglass: renombrarlos rompería el procedimiento que ya corre. `Parte` dice qué es.

**Procedimientos:** `SP_Save_PRD_ClosingEvidence` (de Jaime, no tocar) y
`SP_Set_PRD_ClosingEvidence_LCDSerial` (`04b`, 6 parámetros; no sobrescribe un
serial ya puesto).

**Scripts en `sql/`, orden:** `03a_columnas` → `04a_etapas_columnas` →
`03a_verificar` (4 filas) + `04a_verificar` (2 filas) → `04b_procedimiento`.
Consultas: `00_cobertura` (¿aparece el serial por su cuenta?), `04c_lados_pendientes`
(unidades con un solo lado). `03b` está sustituido por `04b` salvo su procedimiento
de conciliación, pendiente de que haya datos para decidir si sirve.

**Riesgo abierto:** `AIRLINK` tiene **37,63 GB de log** en recovery FULL sin backups
de log, y cada cierre escribe ~3 MB de base64. Decisión del DBA, no de este código.

---

## 5. Objetivo: actualización automática desde GitHub

### El principio: el `.app` casi nunca cambia; el código sí

Reemplazar el `.app` entero en cada actualización choca con la decisión 20. Con
firma ad-hoc, la identidad que TCC asocia al permiso de cámara es el hash del
propio binario, que cambia en cada construcción: el permiso puede perderse **sin
aviso**. En una planta eso es una estación que "se actualizó" y ya no ve. Además,
lo descargado lleva cuarentena de Gatekeeper.

Así que se separa en dos:

| capa | qué contiene | cambia | cómo se entrega |
|---|---|---|---|
| **Runtime** (`.app`) | Python, PySide6, OpenCV, numpy, pymssql, zxing-cpp, cryptography, y un **arranque** mínimo | casi nunca: solo si cambia una dependencia binaria | reinstalación manual (`instalar.sh`) |
| **Carga** (payload) | todo `app/*.py` y el motor `motor/*.py` | en cada mejora | descarga automática desde GitHub Releases |

El 99% de lo que se ha cambiado en este proyecto son archivos `.py`. Eso viaja
solo, sin tocar el binario y sin tocar TCC.

> **Alternativa a valorar con Jaime:** con un certificado *Developer ID* de Apple
> (cuenta de desarrollador de pago), la identidad que ve TCC pasa a ser el equipo
> y el identificador del paquete, estables entre versiones, y se podría
> actualizar el `.app` entero con Sparkle. Hoy no lo hay; el diseño de carga
> funciona sin él. Verificar antes de decidir.

### Arranque (bootstrap)

Dentro del `.app`, lo único que no se actualiza:

1. Leer `~/Library/Application Support/ROI/codigo/actual` → versión activa.
2. Si existe y es compatible, anteponer
   `~/Library/Application Support/ROI/codigo/<version>/` a `sys.path`.
3. Si no, usar la carga que trae el propio `.app` (la de fábrica). **La app tiene
   que arrancar siempre**, con o sin red, con o sin GitHub.
4. **Vuelta atrás automática:** antes de importar, escribir `arrancando`; la app
   escribe `arranque_ok` cuando la ventana está construida y el motor importado.
   Si el arranque anterior se quedó en `arrancando`, volver a la versión previa y
   registrarlo. Dos fallos seguidos → versión de fábrica.

`motor.py` ya busca el motor en varias carpetas por orden (`ROI_MOTOR`,
`sys._MEIPASS/motor`, …); extenderlo con la carpeta de la carga activa encaja con
lo que hay.

Código en `Application Support` y no en `~/Documents/ROI` a propósito: los datos
son el activo de la estación y se respaldan y miran desde el Finder; el código es
reemplazable y no debe estar al alcance de un arrastre.

### Compatibilidad

Cada carga declara `runtime_minimo`. El `.app` declara su `runtime`. Si la carga
pide un runtime más nuevo, **no se instala** y la app avisa: *"Esta actualización
necesita reinstalar la aplicación"*. Así una mejora que importe un paquete nuevo no
deja una estación rota a medio actualizar.

### Distribución con GitHub Releases

- **Repositorio privado.** El código es el sistema de inspección de la planta.
- Cada versión es un *release* con etiqueta `vX.Y.Z` y tres archivos adjuntos:
  `carga-X.Y.Z.zip`, `manifiesto.json` y `manifiesto.sig`.
- **Canales:** un release marcado *pre-release* es el canal **piloto**; el último
  release normal es **estable**. Una estación en piloto ve ambos.
- Las estaciones consultan la API de GitHub (`/repos/{dueño}/{repo}/releases`) y
  descargan los adjuntos con un **token de solo lectura** — *fine-grained*,
  limitado a ese repositorio y a permiso de lectura de contenidos — guardado en
  `~/.roi/github.json`, 600. Los adjuntos de un repo privado se piden por la API
  con `Accept: application/octet-stream`.
- Respetar los límites de la API: comprobar al arrancar y luego cada pocas horas,
  no en bucle.

### Integridad — obligatorio

La carga es código que corre con la cámara y las credenciales de SQL de la planta.
Quien consiga el token o la cuenta de GitHub podría subir un release. Por eso
**descargar no basta: la carga tiene que estar firmada**.

- **Manifiesto firmado con Ed25519.** La clave pública va dentro del `.app`.
  **La clave privada no vive en GitHub** —ni en el repo ni en los secretos de
  Actions—: se firma en local al publicar. Con eso, comprometer GitHub permite
  subir archivos pero no hacer que una estación los acepte.
- El manifiesto lleva `version`, `sha256`, `tamano`, `runtime_minimo`, `canal`,
  `fecha`, `notas`. La app verifica firma y sha256 **antes** de descomprimir.
- Descompresión protegida contra rutas que se salen del directorio (zip slip).
- Python 3.9 no trae Ed25519: añadir `cryptography` al runtime (tiene ruedas
  abi3 para macOS x86_64; verificarlo al construir).

### Cuándo se actualiza

- Se comprueba al arrancar y cada pocas horas **con la inspección detenida**.
- Se descarga y verifica en segundo plano, y se deja preparada.
- **Se aplica al reiniciar**, nunca en caliente, nunca a mitad de un cierre.
- En pantalla: *"Actualización 1.4.0 lista — se aplicará al reiniciar"*.
- Botón en Administración para buscar y aplicar ahora.

### Saber qué versión corre cada estación

Sin servidor propio no hay "latido" que recibir, pero no hace falta: las
estaciones **ya escriben en SQL**.

- Añadir `VersionApp VARCHAR(20)` a `PRD_ClosingEvidence` (script nuevo, separado,
  lo corre Jaime) y pasarlo como 7º parámetro de
  `SP_Set_PRD_ClosingEvidence_LCDSerial`. **Cada fila de evidencia dice qué versión
  la produjo**, que para auditar vale más que un panel.
- Con eso, una consulta da la última versión vista por estación:
  `SELECT Estacion, MAX(VersionApp), MAX([Date]) FROM ... GROUP BY Estacion`.
- Respetar las decisiones 4 y 7: si la columna aún no existe, no pasa nada.

### Publicar

```
python herramientas/publicar.py --canal piloto --notas "RMA: patrón de GCN"
```

1. Corre las pruebas. Si fallan, no publica.
2. Empaqueta `app/` + `motor/` en un zip con número de versión.
3. Calcula sha256 y firma el manifiesto con la clave local.
4. Crea el release en GitHub y sube los tres archivos.

GitHub Actions puede correr las pruebas en cada push. **No** debe firmar ni
publicar: eso pondría la clave privada en GitHub, que es justo lo que la firma
existe para evitar.

Construir el **runtime** (`.app`) sigue siendo en una Mac Intel (decisión 18).
Publicar una **carga** no necesita macOS: es empaquetar `.py`.

### Migraciones de base de datos

**Las estaciones nunca ejecutan DDL** (decisión 6). Si una versión necesita
columnas nuevas, las notas del release lo dicen y Jaime corre el script. Mientras
tanto la versión tiene que funcionar sin ellas (decisiones 4 y 7).

---

## 6. Plan de trabajo, en orden

1. **Repositorio privado en GitHub.** `app/`, `motor/` (una única copia: fuera
   `15P`, `15PM` y `PARA_MAC_MINI` como fuentes), `sql/`, `herramientas/`, `docs/`.
   `.gitignore` para recetas, logs, banco, `dist/`, `build/` y cualquier
   `conexion.json`, `admin.json` o `github.json`. Crear un `CLAUDE.md` con la
   sección 3 resumida, para que esté presente en cada sesión.
2. **Pruebas** que fijen la sección 3. Ya se probó todo esto a mano durante el
   desarrollo; hay que convertirlo en `pytest`. Patrones que funcionaron:
   - Qt sin pantalla: `QT_QPA_PLATFORM=offscreen`.
   - Base falsa: un cursor que responde según el texto de la consulta.
   - `Cierre` con `activo=True` y `disponible=True` forzado **sobre un objeto
     recién creado**. Forzarlo sobre uno con `activo=False`, o reutilizar uno que
     ya encoló una tarea, da falsos fallos: pasó tres veces.
   - Configs pasados por `inspector.cargar_config`, no crudos: el cierre recibe
     `cfg["zonas"]`, que solo existe tras cargarlo.
   Casos mínimos: patrones de GCN; deducción de parte por nombre; temporizadores
   de `PIDE_LCD`, `QR_LCD`, `ESPERA_SCAN`, `HECHO`, `ERROR` con reloj falso;
   `sin_tapete` por estado; emparejamiento que no bloquea; repeticiones por etapa;
   `COL_LENGTH` ausente; re-guardar un config sin cambiar la huella; filtro de
   escáner con foco en el selector de cámara.
3. **`inspector.py` debe usar `veredicto.py`.** Hoy tiene su propia copia de la
   votación. Esa divergencia ya rompió el cierre una vez.
4. **Arranque + cargador de carga** con vuelta atrás.
5. **Herramienta de publicación** con firma local y subida a GitHub Releases.
6. **Actualizador en la app**, con el token de solo lectura.
7. **`VersionApp`** en la evidencia (script SQL separado + 7º parámetro).
8. **Una última reinstalación** de las dos Mac mini con el runtime nuevo (arranque +
   `cryptography`). A partir de ahí, las mejoras llegan solas.
9. **Canal piloto** en una estación, y promoción a estable.

---

## 7. Deuda y pendientes

| tema | estado |
|---|---|
| `inspector.py` duplica `veredicto.py` | **alto** — ya causó un fallo |
| Código del motor en varias copias | se resuelve con el repo |
| README del motor §15 | describe el cierre viejo (solo display, tecla ENTER); reescribir |
| Backglass sin validar emparejamiento | `PRD_LinkMainPartToUnit.BackGlass` existe; validar igual que el display, sin bloquear |
| Banco de evidencia local en cada Mac | consolidar; el SMB a `\\adrfs` (192.168.181.252) falló desde Mac |
| Línea base de contaminación por receta | el mecanismo existe (`_resolver`), faltan las bases |
| Calibraciones con pocas unidades | meter 2–3 más por receta con el panel |
| `MAX_GCN = 10` | sube cuando `PRD_ClosingEvidence.GCN` pase a `varchar(50)` |
| Fotos en columnas `text` | tipo retirado; `VARCHAR(MAX)` — DBA |
| Log de `AIRLINK` (37,63 GB) | DBA |
| Fixture vacío con ruido registra NO_PASA | decisión de proceso pendiente (`hallazgos_2026-09-17` §3) |
| Windows 11 | después de Mac. El motor ya ramifica por plataforma en sonido y cámara (`CAP_DSHOW` en Windows); el diseño de carga de §5 vale igual con un runtime por sistema |

---

## 8. Credenciales — nunca en el repo ni en este documento

| qué | dónde vive |
|---|---|
| SQL (`AlAppUser`) | `~/.roi/conexion.json`, 600. Se crea con `roi_db.py --crear-config`, que la pide con `getpass`. **Se compartió por chat: rotarla.** |
| Acceso admin | `~/.roi/admin.json`, 600, hash PBKDF2. En `seguridad.py` solo hay sal y hash de la semilla, nunca la clave. **Cambiarla desde el panel.** |
| Token de GitHub de las estaciones | `~/.roi/github.json`, 600. *Fine-grained*, solo lectura, solo ese repo. |
| Clave privada Ed25519 | solo en la máquina desde la que se publica. **Nunca en GitHub**, ni en el repo ni en secretos de Actions. |

---

## 9. Referencias

En el proyecto de claude.ai "ROI - Screw Verification":

- `claude/integracion_sql_probada_2026-09-18.md` — la prueba de extremo a extremo
  contra SQL y las dos trampas de truncamiento. **Autoridad sobre la base.**
- `claude/esquema_cierre_verificado_2026-09-18.md` — esquema verificado.
- `claude/hallazgos_2026-09-17_registro_y_log.md` — por qué el log contaba
  parpadeos y cómo se arregló.

En el código: `README.md` del motor (§1–§14 vigentes, §15 desactualizada), y los
comentarios largos de cada módulo, que explican el **porqué** de cada decisión.
No acortarlos: son lo que impide que se repitan los errores.
