# Sherlock Vision — Sistema de Inspección Visual de Tornillos

Inspección automática de presencia/ausencia de tornillos en enclosures de teléfonos
mediante visión por computadora. Airlink Distribution DR.

---

## 1. Qué hace el sistema

Verifica que todos los tornillos de un enclosure estén colocados, usando una cámara
cenital fija y marcadores de referencia. Para cada posición marcada, emite uno de tres
veredictos:

- **Presente** (verde) — el tornillo está.
- **Faltante** (rojo) — el tornillo no está.
- **Verificar** (amarillo) — hay algo que no se reconoce ni como tornillo ni como hueco
  conocido (oclusor, hueco atípico, objeto extraño). Requiere revisión humana.

El veredicto global de la pieza es **PASA** (todo presente), **NO PASA** (algún faltante)
o **VERIFICAR** (ningún faltante pero algún dudoso).

Además de tornillos, el sistema admite **otros tipos de parte** (Pin, Cushion y
personalizados), marcados como puntos o como cajas rectangulares dibujadas con el mouse.
La detección por plantillas es la misma para cualquier parte.

---

## 2. Requisitos

### Software
- Python 3.x
- OpenCV: `pip3 install opencv-python numpy --break-system-packages`
- Verificación: `python3 -c "import cv2; print('OK', cv2.__version__, '| aruco' if hasattr(cv2,'aruco') else 'FALTA aruco')"`

### Hardware
- Cámara cenital fija (actual: cámara industrial UHD 4K, 3840x2160, salida HDMI/USB, MJPEG 25 fps).
- Montaje estable a ~38" del tapete (lente de barril, foco manual fijo).
- Iluminación difusa. Configuración recomendada: dos paneles LED a 35-45 grados desde la
  vertical, simétricos, 40-60 cm del tapete. Evitar reflejos especulares hacia el lente.
- Tapete impreso con 4 marcadores ArUco (DICT_4X4_50, IDs 0-3).

---

## 3. Arquitectura de detección

Cadena de procesamiento por frame:

1. **Detección de marcadores** — localiza los 4 ArUco (IDs 0=sup-izq, 1=sup-der,
   2=inf-izq, 3=inf-der).
2. **Homografía** — rectifica la imagen usando los centros de los marcadores como
   referencia, produciendo un lienzo canónico estable (independiente de pequeñas
   variaciones de encuadre).
3. **Clasificación por posición** — para cada ROI:
   - Compara el recorte contra dos plantillas memorizadas (tornillo presente / hueco vacío).
   - Similitud híbrida: NCC (correlación estructural, ignora brillo) + SQDIFF
     (cercanía absoluta, robusta en zonas lisas), 50/50.
   - Puntaje 0-100: mayor al umbral => se parece más a "presente".
4. **Piso de similitud absoluta** — aun si puntúa como presente, si el parecido absoluto
   al tornillo es menor que el piso calibrado, se marca "verificar" (amarillo). Cierra el
   falso positivo de huecos limpios y oclusores.
5. **Votación temporal** — ventana de 15 frames; presente si >=8 votos. Con tres estados,
   toma la mayoría. Absorbe manos de paso y parpadeos momentáneos.
6. **Estabilidad** — 10 frames con veredicto idéntico => pieza estable, se registra y suena.

### Anclaje importante
Los marcadores anclan el **tapete**, no el teléfono. El sistema tolera desalineos de
+-~0.7 mm (margen de búsqueda de la plantilla). Un teléfono corrido más que eso desplaza
los tornillos fuera de sus ROIs. **Solución de raíz: fijación física del teléfono (nido/topes).**

---

## 4. Estructura de archivos

```
ROI/
├── inspector.py            # motor de inspección
├── calibrador_roi.html     # herramienta de marcado de posiciones (navegador)
├── inspecciones_log.csv    # registro de todas las inspecciones (columna "proceso")
├── README.md
└── procesos/
    ├── Ensamble 1/
    │   ├── referencia.png       # imagen rectificada de referencia
    │   ├── config_rois.json     # posiciones, tamaños, tipo de parte y uid por ROI
    │   ├── plantillas.npz       # plantillas presente/vacío por posición
    │   └── umbrales.json        # umbrales, separaciones, pisos
    └── Ensamble 2/
        └── (mismo cuarteto)
```

Cada **proceso** (etapa de ensamblaje) es independiente: misma cámara y tapete, pero
posiciones y calibración propias. El tapete y los ArUco se comparten físicamente.

---

## 5. Puesta en marcha de un proceso

Toda cámara nueva, tapete nuevo o cambio de foco/resolución obliga a repetir esto (~25 min).

### Paso 1 — Capturar referencia
```bash
python3 inspector.py --capturar-referencia --receta "Ensamble 1"
```
Coloca el housing completo, marcadores visibles, ESPACIO para guardar. Crea
`procesos/Ensamble 1/referencia.png`.

### Paso 2 — Marcar posiciones (calibrador)
1. Abre `calibrador_roi.html` en el navegador.
2. En el dropdown **Proceso**, selecciona el proceso correcto (o "+ Añadir proceso...").
3. **Cargar imagen** => la `referencia.png` de ESE proceso.
4. Elige la herramienta en la barra sobre el lienzo (atajos de teclado V / P / B):
   - **Mover (V)** — seleccionar, arrastrar puntos y desplazar la vista. El clic NO
     agrega posiciones (evita marcas accidentales).
   - **Punto (P)** — clic en el centro de cada tornillo. ROI cuadrada del tamaño global.
   - **Caja (B)** — arrastrar para dibujar una ROI rectangular (pines, cushions,
     partes alargadas). ESC cancela la caja en curso.
5. **Tipo de parte** (panel derecho): se asigna a las ROIs nuevas — Tornillo, Pin,
   Cushion o "+ Añadir tipo...". Cada ROI puede cambiar de tipo en la lista. El nombre
   automático usa la inicial del tipo con numeración propia por tipo (T01..., P01...,
   C01...).
6. Nombres editables y **repetibles**: dos tornillos "34" son válidos. Cada ROI tiene una
   identidad interna única (**uid**) y se calibra y evalúa por separado; la etiqueta es
   solo visual. Los nombres repetidos se resaltan en ámbar en la lista y en operación se
   distinguen como 34(1), 34(2).
7. Tamaño de ROI: selecciona la ROI y arrastra sus **tiradores** (bordes y esquinas), o
   usa los campos ancho x alto. Regla: la cabeza del tornillo debe ocupar entre 1/3 y 1/2
   del recorte.
8. **Exportar** => mueve el `config_rois.json` descargado a `procesos/<proceso>/`.

Los tres deben coincidir: **dropdown = imagen cargada = carpeta destino**. El nombre del
proceso queda incrustado en el config; el inspector avisa si está en la carpeta equivocada.

#### Identidad de las ROIs (uid) y compatibilidad
- El config (version 2) guarda por punto: `uid` (identidad única e inmutable), `id`
  (etiqueta visible), `tipo`, coordenadas y tamaño. Plantillas, umbrales, scores y
  votación se indexan por uid — nunca por etiqueta. Renombrar una ROI no afecta su
  calibración.
- Configs antiguos sin uid siguen funcionando: la etiqueta se usa como uid, conservando
  la calibración existente. Si tenían nombres repetidos, el sistema los separa
  automáticamente (34, 34#2), pero esa receta debe **recalibrarse** (antes las
  plantillas de nombres duplicados se sobrescribían entre sí).
- Recomendado: re-exportar el config desde el calibrador actual para que todos los
  puntos tengan uid propio.

### Paso 3 — Calibrar
```bash
python3 inspector.py --calibrar --receta "Ensamble 1"
```
- **Fase 1 (presente):** housing completo. ESPACIO para muestrear. Luego el sistema
  ofrece agregar más unidades: ESPACIO = otra unidad, Q/ENTER = pasar a fase 2.
- **Fase 2 (vacío):** remueve los tornillos, recoloca el housing. Igual que la fase 1,
  admite varias unidades (útil si los huecos difieren entre teléfonos — limpio vs. sucio).

Multi-unidad es **opcional** pero recomendado: promedia la variación real de la flota y
calcula umbrales estadísticos + pisos de similitud.

---

## 6. Operación

```bash
python3 inspector.py --vivo
```
- Si hay varios procesos, aparece un menú para elegir.
- Teclas: **Q/ESC** salir · **S** captura · **R** cambiar de proceso en caliente ·
  **D** modo debug (muestra puntaje/umbral por posición: verde holgado, ámbar a menos de 8
  del umbral, rojo por debajo).

En el panel lateral, el banner y el log CSV, las partes que no son tornillo se muestran
con su tipo — "Cushion C1", "Pin P7" — para que el operador sepa qué buscar. El tipo
Tornillo se omite por ser el default.

Inspección de una foto:
```bash
python3 inspector.py --foto imagen.png --receta "Ensamble 1"
```

Opciones globales: `--camara N` (índice, default 0), `--resolucion {auto,4k,1440,1080}`
(default auto = la mejor disponible).

---

## 7. Lectura de la tabla de calibración

Ejemplo de fila:
```
ROI     presente   vacio    umbral   separacion   margen   piso
T07       75.5      31.8     53.2       43.7       18.9    0.71
```

- **presente / vacio** — puntaje promedio de cada estado.
- **umbral** — frontera de decisión, sesgada a NO generar falsos faltantes (se coloca bajo
  el peor presente observado).
- **separacion** — distancia entre promedios. >=30 excelente, <15 REVISAR.
- **margen** — colchón del tornillo presente MÁS DÉBIL de la flota sobre el umbral. Es el
  indicador de robustez real: margen bajo (<5) = alguna unidad estuvo a punto de dar falso
  faltante.
- **piso** — piso de similitud absoluta. Por debajo de él, un "presente" se convierte en
  "verificar".

Alertas: **REVISAR** (separación <15) · **SOLAPE** (presente y vacío se cruzan entre unidades).

---

## 8. Ajuste de umbrales a mano (parche temporal)

Solo cuando una posición aislada da problemas y hay margen del lado del vacío. Editar
`procesos/<proceso>/umbrales.json`, campo `"umbral"` de la posición:
- Bajar hacia `media_vacio`, nunca a menos de `media_vacio + 8`.
- No aplicable si la separación es <15 (no hay margen que ceder).
- **`--calibrar` sobrescribe los ajustes manuales.** La recalibración es la solución limpia.

---

## 9. Diagnóstico de posiciones frágiles

Posiciones con separación baja o margen bajo. Causas y soluciones:

1. **Punto de vista ingrato** — el hueco vacío se parece a la cabeza. Solución: reposicionar
   la ROI al rasgo que MÁS cambia entre estados, o aceptar el estado amarillo.
2. **Desenfoque (profundidad de campo)** — zonas profundas del enclosure fuera del rango
   nítido. Funciona si el desenfoque es CONSTANTE (la plantilla se calibra con él), pero
   erosiona separación. Solución óptica: cerrar el diafragma (más profundidad de campo) a
   cambio de más luz.
3. **Variación entre unidades** — un teléfono con esa posición distinta. Solución: más
   unidades en la calibración (5-7 es el punto dulce).
4. **Muestra pequeña** — con 2-3 unidades el margen es ruidoso. Recalibrar con más unidades
   distingue fragilidad crónica de mala suerte estadística.

Herramienta de diagnóstico: **modo debug (tecla D)** en vivo muestra si una posición baila
cerca de su umbral.

---

## 10. Replicación a otros bancos

1. `pip3 install opencv-python numpy --break-system-packages`
2. Copiar la carpeta ROI SIN los `umbrales.json`/`plantillas.npz` ajenos (cada banco calibra
   los suyos). El `config_rois.json` sí es transferible si el tapete es idéntico.
3. Permiso de cámara al terminal/Python.
4. Smoke test de OpenCV (ver sección 2).
5. Puesta en marcha completa por banco (~5 min/proceso con un housing patrón de quita-y-pon).
6. Misma versión de Python en toda la flota. Mac mini recomendado para estaciones dedicadas.

Comisionamiento = QC de instalación: comparar la tabla de separación del banco nuevo contra
la línea base certificada.

---

## 11. Respaldos

La carpeta ROI ES el sistema completo. Respaldar antes de cada tanda de cambios o tras cada
calibración certificada:
```bash
cp -R ~/Downloads/ROI ~/Downloads/ROI_respaldo_AAAA-MM-DD_descripcion
```
Restaurar = borrar/renombrar la activa y duplicar el respaldo como ROI.

---

## 12. Parámetros internos (referencia)

| Constante | Valor | Significado |
|-----------|-------|-------------|
| `FRAMES_VOTO` | 15 | Ventana de votación temporal |
| `VOTOS_PRESENTE` | 8 | Mayoría para declarar presente |
| `FRAMES_ESTABLE` | 10 | Frames idénticos para estabilidad |
| `FRAMES_ESTABLE_CAMBIO` | 45 | Frames para registrar un veredicto distinto sobre la misma pieza |
| `LADO_MAX_CANONICO` | 3200 | Lado mayor del lienzo rectificado (soporta 4K) |
| `FRAMES_SIN_MARCADOR_RESET` | 30 | Frames sin marcadores para resetear sesión |

Los cuatro de votación se definen **solo** en `veredicto.py`; `inspector.py` los
re-exporta. El modo terminal y la app Qt usan la misma clase `veredicto.Veredicto`.

---

## 13. Estado actual y pendientes

### Implementado y en operación
- Detección ArUco + homografía + clasificación por plantillas (NCC+SQDIFF).
- Resolución 4K automática (MJPEG + buffer mínimo).
- ROIs individuales por punto, rectangulares, con identidad única (uid).
- Procesos múltiples con cambio en caliente (tecla R).
- Calibración multi-unidad en ambas fases, e incremental con `--agregar-unidad`.
- Umbrales estadísticos sesgados a no generar falsos faltantes.
- Piso de similitud absoluta + estado "verificar" (amarillo).
- Votación temporal de tres estados y modo debug (tecla D).
- Tipos de parte por ROI (Tornillo, Pin, Cushion + personalizados).
- **Juez 2 (cruz Phillips), solo rescate** — se consulta únicamente cuando el
  clasificador dice FALTANTE con el puntaje cerca del umbral. Nunca degrada una
  posición aprobada. Conmutable en vivo con la tecla J.
- **Detección de contaminación** — línea base de normalidad + clasificador YOLO en
  hilo aparte, dispara a los 15 frames estables. Recuadros magenta VERIFICAR.
- **Captura limpia con la tecla C** — el rectificado sin overlay, para dataset.
- **ZONAS que no se inspeccionan** — una ROI con tipo `Giftcard`, `LCD` o `Display`
  se aparta al cargar el config y no entra en calibración, puntajes, votación,
  huella ni log. Existe porque el giftcard sigue puesto en la fase "vacío": si se
  calibrara como una parte, su plantilla de presente y la de vacío serían la misma
  imagen y esa posición tumbaría piezas buenas.
- **Secuencia de cierre (`--cierre`)** — ver sección 15.

### Corregido (17-18 sep 2026)
- **Re-armado por parpadeo.** El registro se re-armaba con cualquier cambio de
  veredicto, así que una posición que oscilaba entre `falta` y `dudoso` hacía que
  la MISMA pieza se registrara indefinidamente: el 8-sep hay 2403 filas que
  contienen solo 14 veredictos distintos. Ahora una pieza se registra una vez por
  veredicto distinto (`Veredicto.registrados` en `veredicto.py`), y un veredicto nuevo sobre la
  misma pieza exige `FRAMES_ESTABLE_CAMBIO` = 45 frames.
- **Log con cuatro formatos bajo una cabecera de seis columnas.** Normalizado con
  `migrar_log.py`; cabecera y filas salen ahora de una sola lista `CAMPOS_LOG`.
- **`analizar_log.py`** agrupa por sesión de pieza para no contar parpadeos.

### Pendientes / futuro (por prioridad)
1. **Calibración con más unidades.** La actual (5 presentes / 1 vacía) deja la
   separación mínima en 13.9, bajo el umbral de REVISAR. El lado "vacío" es el que
   falta muestrear. Punto dulce: 5-7 de cada estado.
2. **Línea base de contaminación desalineada.** `base82_prom.npz` se construyó a
   3200x1659 y el proceso trabaja hoy a 3200x1601. `contaminacion.py` reescala y
   el mapa se enciende entero (medido en el otro modelo: p99 de 4.2 a 40.4 sigmas).
   Reconstruirla con capturas del lienzo actual, o renombrarla para desactivarla.
3. **Fijación física del teléfono (nido/topes)** — mata el corrimiento en origen.
4. **Luz rasante (dark field).** No es solo para las posiciones frágiles: el
   segundo Data Matrix del display, grabado sobre blindaje mate, es gris sobre
   gris y NO se decodifica. Ese dato no está en el píxel y ningún preprocesado lo
   saca. Es un problema óptico, no de software.
5. **Emparejamiento sin LCD asignado.** La validación del display solo comprueba
   de verdad cuando `PRD_LinkMainPartToUnit.LCD` trae valor; hoy cubre el 6,4% de
   las unidades con actividad reciente. Mientras tanto avisa y deja pasar. Con
   cobertura completa se activa `exigir_emparejamiento=True`.
6. **Aplicación instalable con interfaz** — ver sección 16.
7. **Alimentar el dataset desde la producción diaria** — ver sección 16.
8. **Anomaly detection (PatchCore/PaDiM)** como paradigma futuro.


### Nota de diseño
La fiabilidad en visión industrial se gana en este orden: **iluminación -> fijación -> óptica
-> algoritmo.** El sistema actual está muy desarrollado en algoritmo; las mayores ganancias
pendientes están en las capas físicas (luz y fijación).

---

## 14. Preparación para anomaly detection: requisitos y captura de dataset

### 14.1 Qué necesita el detector para funcionar correctamente

1. **Dataset de normalidad (lo más crítico).** 50-200 fotos de piezas PASA por proceso,
   rectificadas y alineadas. La diversidad vale más que la cantidad: distintas unidades,
   distintos días, la deriva natural de la iluminación. Un modelo por proceso, igual que
   las recetas.
2. **Alineación geométrica consistente.** El modelo aprende qué es normal EN CADA ZONA de
   la imagen; todas las fotos deben estar en el mismo marco. La homografía ArUco ya lo
   resuelve. La fijación física del teléfono (pendiente #1) lo reforzaría: menos variación
   de posición = normalidad más estrecha = detector más sensible.
3. **Iluminación estable.** La dependencia más frágil: el modelo memoriza la apariencia
   normal con la luz actual. Cambiar o mover paneles, o luz externa variable =>
   reentrenar (trivial con el dataset acumulado, pero hay que saberlo).
4. **Set de validación con anomalías reales para fijar el umbral.** Las fotos
   NO PASA / VERIFICAR acumuladas, más defectos escenificados a propósito (tornillo
   suelto, grapa, flex doblado). El umbral se coloca en el hueco entre buenas y anómalas,
   igual que hoy con presente/vacío.
5. **Cómputo y fase de sombra.** Una Mac mini lo corre en CPU en 1-2 s por pieza
   (suficiente para el disparo por estabilidad); `anomalib` trae PatchCore listo. Antes de
   dejarlo bloquear piezas: unas semanas en modo sombra (registra veredictos en el log sin
   afectar PASA/NO PASA) y auditar falsos positivos.

Los puntos 1, 2 y 4 salen solos de la operación diaria una vez implementada la captura
de abajo. Por eso la captura es la llave: cada semana que corre, el detector futuro nace
mejor entrenado.

### 14.2 Diseño de la captura en el sistema actual (pendiente #8)

**Punto de enganche:** en `modo_vivo`, el bloque donde el veredicto se declara estable y
`sesion_registrada` pasa a True — el mismo instante del registro CSV y el beep. Garantiza
exactamente UNA captura por pieza, con la pieza quieta y ya juzgada.

**Qué guardar:** el `warped` LIMPIO (la imagen rectificada, antes de dibujar el overlay).
El frame con círculos y banner no sirve para entrenar. JPEG calidad ~90 (1-3 MB por pieza
a 3200 px; PNG sería 5-10x más pesado sin beneficio para este uso).

**Dónde y con qué nombre:**
```
procesos/<proceso>/dataset/
├── PASA/       20260730_143512.jpg      # set de entrenamiento (normalidad)
├── NO_PASA/    20260730_150233.jpg      # validación: anomalías reales
└── VERIFICAR/  20260730_151804.jpg      # validación: casos dudosos
```
El nombre (fecha+hora) cruza con la fila del CSV, que ya guarda fecha, hora, proceso y
resultado. Opcional: columna `archivo` en el CSV para el cruce directo.

**Control:** flag de línea de comandos (p. ej. `--dataset`) para activar/desactivar la
captura, apagada por default hasta validar el consumo de disco. Aviso en consola cada N
capturas con el tamaño acumulado de la carpeta.

**Disco y respaldos:** a volumen típico de banco, unos pocos GB por mes. La carpeta
`dataset/` se EXCLUYE del respaldo rutinario de ROI (sección 11) — respaldo propio, menos
frecuente. El dataset no es crítico para operar: perderlo atrasa el detector futuro, no
detiene la inspección.

**Higiene del dataset (al usarlo, no al capturarlo):**
- Entrenar solo con PASA *confirmadas*: si el operador detectó que una pieza marcada PASA
  en realidad estaba mal (escape), esa foto se mueve a NO_PASA.
- No hace falta depurar duplicados ni seleccionar "las mejores": la variación real de la
  flota es exactamente lo que el modelo debe aprender.
- Un dataset por proceso; no mezclar Ensamble 1 y Ensamble 2.

---

## 15. Secuencia de cierre: evidencia de housing y display

Se activa con `--cierre`. Sin ese flag el inspector trabaja exactamente igual que
antes, así que el modo de siempre nunca queda en riesgo.

### Flujo

1. El inspector da **PASA estable**. Un PASA que aún parpadea no arranca nada: es
   una pieza que el operario está acomodando, no una terminada.
2. Se busca el **giftcard** en su zona y se extrae el GCN. El formato tiene campos
   separados por `|` y el GCN es el ÚLTIMO: `NON052|QC-FG|FG|CX413674`. Se toma el
   último campo y no una posición fija porque los campos de la izquierda describen
   el lote y la ruta, y cambian entre giftcards.
3. Se **valida la unidad** contra la base (ver sección 15.2).
4. Se guarda la foto rectificada **limpia** del housing, antes de dibujar overlay.
5. Se pide retirar el housing. La secuencia no avanza hasta que la pieza SALE del
   fixture: si no, seguiría el mismo housing en cuadro y se le tomaría la foto
   como si fuera el display.
6. Se lee el **Data Matrix del display**, que debe estar quieto 8 frames antes de
   disparar, o la foto de evidencia sale movida.
7. Se validan emparejamiento y se registra todo con una sola llamada al
   procedimiento.

Teclas: **ENTER** confirma un final, **X** cancela en cualquier punto.

### 15.1 Zonas de lectura

Viven en `config_rois.json`, y se pueden definir de dos maneras:

- Como **ROI con tipo** en el calibrador: `Giftcard`, `LCD` o `Display`. Es la
  preferida, porque se ve y se ajusta junto al resto de la geometría.
- Como clave suelta: `zona_qr` (housing) y `etapa_lcd.zona_qr` (display), que
  escribe `marcar_zona_qr.py`. No toca `puntos`, así que no invalida la huella.

**El tamaño de la zona importa en los dos sentidos.** Medido sobre el Data Matrix
del display, de 52 px de lado:

| zona | resultado |
|------|-----------|
| 110x110 (ajustada) | no lee — sin zona de silencio |
| 210x210 | lee, 3 ms |
| 310x310 | lee, 32 ms |
| 440x460 (muy holgada) | no lee — el código queda diminuto en el área de búsqueda |

### 15.2 Validaciones, antes de llamar al procedimiento

`SP_Save_PRD_ClosingEvidence` no comprueba nada: busca IMEI e
`IdHistoryUnitProcess` y hace el INSERT. Si algo falta, el fallo llega como un
error de SQL Server que no le dice nada al operario, o no llega. Por eso
`roi_db.verificar_unidad()` comprueba antes:

| caso | mensaje |
|------|---------|
| GCN no existe | «El GCN … no existe. Verifique el giftcard.» |
| sin IMEI | «No tiene IMEI registrado. Avise a soporte.» |
| IMEI ≠ 15 caracteres | «Se guardaría cortado. Avise a soporte.» |
| **recepción anulada** | «Está anulado: volvió a recibirse como CX… Use ese giftcard.» |
| sin historial de proceso | «No tiene historial de proceso. No se puede cerrar.» |

Un IMEI con guion delante (`-353431654144997`) NO es un error de captura: así se
anula una recepción para poder volver a recibir la misma unidad si regresa en
otro lote. 1.434 de 1.440 anulados reaparecen limpios bajo otro GCN, así que casi
siempre se puede decir cuál es el bueno.

**Los duplicados NO se bloquean.** `PRD_ClosingEvidence` es evidencia previa al
cierre definitivo, no un libro de unidades cerradas: la misma unidad puede volver
a cerrarse con otra LCD, y la misma LCD puede acabar en otra unidad. Se informa
cuántos registros previos hay y se sigue.

### 15.3 Dos trampas de truncamiento silencioso

Ninguna da error. Las dos destruyen datos.

- **`cur.callproc()` recorta el base64 a 8.000 caracteres.** Medido: de 1.572.932
  caracteres enviados llegaban 8.000. pymssql liga las cadenas como VARCHAR(8000)
  aunque el parámetro sea VARCHAR(MAX). Se usa
  `DECLARE @h VARCHAR(MAX)=%s; EXEC ...`, que pasa el contenido completo.
- **`@Imei varchar(15)` del procedimiento trunca al asignar.** Un IMEI de 32
  caracteres se guarda como los primeros 15, sin aviso. El IMEI de un teléfono
  son siempre 15 dígitos (50.417 de 50.797), así que en la práctica no muerde,
  pero la comprobación se queda como red de seguridad.

### 15.4 Dependencias

```bash
pip3 install pymssql "zxing-cpp==2.3.0" --break-system-packages
```

`zxing-cpp` lee **Data Matrix**, que OpenCV no lee — solo QR. La versión se fija:
de la 3.0 en adelante no publican rueda para Python 3.9 y pip intenta compilar
desde fuente, que falla en CMake. Si falta la librería, el giftcard se sigue
leyendo con OpenCV y solo se pierde el display.

Credenciales en `~/.roi/conexion.json` (permisos 600), NUNCA en la carpeta ROI,
que se respalda y se copia entre bancos.

---

## 16. Banco de evidencia

Guarda, de cada pieza que el inspector registra, la imagen rectificada limpia y
una ficha con lo que el sistema decidió y con qué números. **No juzga, no
etiqueta y no toca la calibración.** Está activo por defecto; `--sin-banco` lo
apaga.

```
banco_evidencia/<receta>/<AAAA-MM-DD>/
├── 180530942_CX413674_NO_PASA.jpg
├── 180531104_CX413675_PASA.jpg
└── fichas.jsonl
```

Vive FUERA de la carpeta del programa, al lado de las carpetas de modelo. Es un
activo que crece con los meses y no debe viajar en los respaldos del sistema ni
al copiar la carpeta a otro banco de trabajo.

### Por qué no alimenta la calibración directamente

Porque el sistema estaría aprendiendo de sus propios veredictos. Una pieza que
pasó siendo mala se convierte en plantilla de "presente", empuja esa clase hacia
el estado vacío y **reduce la separación**; y cada reducción hace más probable el
siguiente falso PASA. Es un lazo que se degrada solo, despacio y sin avisar.

Tampoco se le pregunta nada al operario. Una pregunta por pieza es fricción en la
línea y su respuesta no se puede auditar después. El banco guarda **hechos** —la
imagen y los números— y qué entra a la calibración lo decide un **jurado
externo**, mirando la evidencia, fuera del momento de producción.

### La ficha

Una línea JSON por pieza, con el veredicto, las posiciones en falta y dudosas, y
el puntaje, umbral, similitud y estado de **cada** posición:

```json
{"archivo": "180530942_CX413674_NO_PASA.jpg", "gcn": "CX413674",
 "resultado": "NO_PASA", "faltantes": ["T12"], "dudosos": [],
 "revisado": false, "veredicto_jurado": null,
 "detalle": {"T01": {"puntaje": 57.4, "umbral": 50.2,
                     "similitud": 0.991, "estado": "ok"}, ...}}
```

El umbral se guarda junto al puntaje porque cambia con cada recalibración: dentro
de un mes, un puntaje suelto ya no diría si la posición iba holgada o apurada.
`revisado` y `veredicto_jurado` los escribe el jurado, nunca la aplicación.

Con ese detalle el jurado puede filtrar sin abrir una sola imagen — «los PASA con
menos margen», «todos los fallos de T12» — y abrir solo lo que merece mirarse.

### Coste y comportamiento

- **719 KB por pieza** (JPEG q90 a 3200 px). A 300 piezas/día son ~216 MB al día,
  ~4,3 GB al mes, ~52 GB al año por banco.
- Escribe en un hilo aparte: `guardar()` devuelve en milisegundos y el bucle de
  vídeo no pierde frames.
- Si la cola se llena, **descarta esa pieza y lo dice**. El banco es un extra y
  ninguna pieza suya vale detener la inspección; que se llene significa que el
  disco no sigue el ritmo, y eso hay que saberlo, no disimularlo.
- El nombre lleva milisegundos: dos piezas del mismo GCN en el mismo segundo
  —una pieza que se re-registra tras corregirse— no se pisan, y justamente ese
  par, antes y después, es de lo más útil que puede recibir el jurado.
