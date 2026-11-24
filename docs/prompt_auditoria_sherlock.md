# Solicitud de auditoría de ingeniería — sistema de visión "Sherlock Vision"

## Tu rol

Actúa como **ingeniero senior de visión por computadora y software industrial** contratado
para auditar un sistema que ya está corriendo en una línea de producción real.

**No asumas que el código es correcto.** El sistema "funciona" en el sentido de que produce
veredictos, pero eso no significa que la lógica sea sólida, que los cálculos sean válidos,
ni que los parámetros estén bien derivados. Varias veces se han descubierto errores
metodológicos que producían números que *parecían* buenos pero estaban medidos sobre la
entrada equivocada. Sé escéptico con todo, incluido con las explicaciones que aparecen en
los comentarios del código y en este documento.

Requisitos de tu revisión:

- **Verifica, no confíes.** Si un comentario afirma que algo hace X, comprueba en el código
  que efectivamente hace X. Ya hubo casos de comentarios que describían el comportamiento
  anterior tras un cambio.
- **Cita ubicaciones concretas** (archivo y línea o nombre de función) para cada hallazgo.
- **Prioriza por riesgo de producción**, no por elegancia. Un falso "PASA" que deja salir un
  teléfono con un tornillo faltante es el error más costoso del sistema.
- **Distingue** lo que comprobaste leyendo el código de lo que es hipótesis. Si necesitas
  datos que no tienes, dilo explícitamente en vez de especular.
- **No adules.** Si algo está bien, dilo en una línea y sigue. El valor está en los problemas.

---

## Qué hace el sistema

Inspección automática por visión para verificar la **presencia o ausencia de tornillos
Phillips** en el ensamblaje de carcasas de smartphone (proceso actual: iPhone 15 Pro) en una
línea de manufactura.

El operario coloca el teléfono en un fixture con marcadores fiduciales. El sistema captura,
rectifica la imagen, evalúa 23 posiciones de tornillo predefinidas y emite un veredicto
global **PASA / NO_PASA**, señalando qué posiciones fallaron. El resultado se registra en un
CSV para trazabilidad.

Requisitos del dominio:

- Debe funcionar con **múltiples modelos de teléfono** (cambios de referencia en la línea).
  Los parámetros que dependen de valores absolutos de brillo o energía no son portables entre
  modelos, y eso se considera un defecto de diseño.
- **Falsos negativos (NO_PASA sobre una pieza buena)** cuestan tiempo del operario.
- **Falsos positivos (PASA sobre una pieza con tornillo faltante)** son inaceptables: el
  defecto llega al cliente. La asimetría de costos es fundamental para evaluar cualquier
  umbral o heurística.

---

## Stack técnico

- **Python 3** en macOS (MacBook Pro M4, Apple Silicon)
- **OpenCV** (`cv2`) — captura, detección ArUco, homografía, `matchTemplate`, Sobel, GUI
  (`imshow`/`waitKey`)
- **NumPy** — álgebra, FFT (`np.fft.rfft`), almacenamiento comprimido (`np.savez` / `.npz`)
- Cámara industrial 4K vía `cv2.VideoCapture`
- Sin frameworks de ML, sin GPU, sin redes neuronales. Todo es visión clásica.
- GUI de marcado de ROIs en **HTML + JavaScript** puro (se abre en el navegador)

---

## Archivos y responsabilidades

| Archivo | Líneas aprox. | Responsabilidad |
|---|---|---|
| `inspector.py` | ~1470 | Pipeline principal: captura de referencia, calibración, inspección en vivo, inspección de foto, overlay, votación temporal, logging, gestión de "procesos" (recetas) |
| `juez_cruz.py` | ~240 | "Juez 2": clasificador secundario que confirma presencia detectando la cruz Phillips por simetría de 4 |
| `diagnostico_juez.py` | ~230 | Valida y afina los umbrales del Juez 2 contra los bancos de calibración guardados |
| `analizar_log.py` | ~190 | Analiza el CSV de producción: puntajes, similitudes y márgenes reales por posición |
| `calibrador_roi.html` | — | GUI en navegador para marcar las ROIs sobre una imagen de referencia y exportar `config_rois.json` |

**Archivos de datos** (por proceso, en `procesos/<nombre>/`):

- `config_rois.json` — geometría: `image_size`, `roi_size` global, y por punto: `id`, `x`, `y`,
  `roi_w`/`roi_h` opcionales, bandera `phillips`
- `umbrales.json` — por ROI: `umbral`, `media_presente`, `media_vacio`, `separacion`,
  `peor_presente`, `mejor_vacio`, `piso_similitud`, `phillips`
- `plantillas.npz` — bancos de plantillas y recortes crudos (ver estructura abajo)
- `referencia.png` — imagen rectificada de referencia

**Log compartido:** `inspecciones_log.csv` en la raíz.

---

## Pipeline de inspección, paso a paso

### 1. Rectificación geométrica

Se detectan 4 marcadores **ArUco** (`DICT_4X4_50`, IDs 0–3 en las esquinas). Si no están los
cuatro, el frame se descarta. Con los **centros** de los 4 marcadores se calcula una
**homografía** que los mapea a las cuatro esquinas del lienzo de tamaño fijo
`cfg["image_size"]` (p. ej. 3200×1625, con `LADO_MAX_CANONICO = 3200`).

Consecuencia intencional: la escala queda normalizada (la altura física de la pieza no
afecta). Consecuencia a evaluar: los marcadores están en el **fixture**, no en la pieza, así
que un desplazamiento de la pieza dentro del fixture no se corrige.

### 2. Extracción de ROIs

Para cada punto se recorta una ventana **centrada en `(x, y)`** con las medidas del punto
(`roi_w`/`roi_h`) o el `roi_size` global. Los recortes se toman de la imagen rectificada.

### 3. Clasificador principal (comparación de plantillas)

Cada posición tiene **dos bancos** de plantillas: `presente` (con tornillo) y `vacio` (sin
tornillo). Un banco contiene **una plantilla por unidad de calibración** (no promediadas
entre unidades: el ángulo de la cruz difiere por unidad y promediar destruye el patrón).

La similitud entre un recorte y una plantilla combina dos métricas de `matchTemplate`:

```
sim = 0.5 * max(NCC, 0) + 0.5 * (1 - min(SQDIFF_NORMED, 1))
```

donde `NCC = matchTemplate(crop, tpl, TM_CCOEFF_NORMED).max()` y
`SQDIFF = matchTemplate(crop, tpl, TM_SQDIFF_NORMED).min()`.

Contra un banco se toma el **máximo** sobre todas las plantillas. El puntaje final es:

```
puntaje = 50 + 50 * (sim_presente - sim_vacio)      # rango 0..100
```

Es una medida **relativa**: pregunta si el recorte se parece más al tornillo o al hueco, no
si se parece "suficiente" a un tornillo.

Nota importante para tu evaluación: la plantilla es el **70 % central** del recorte, así que
`matchTemplate` puede **deslizarla** dentro del recorte completo. Ese margen del 30 % es lo
que da tolerancia a desalineación.

### 4. Umbral por posición

Derivado en calibración. Si el peor presente supera al mejor vacío:

```
umbral = (peor_presente + mejor_vacio) / 2
```

### 5. Piso de similitud (red de seguridad)

Si el puntaje dice "presente" pero la similitud **absoluta** al tornillo es demasiado baja,
la posición se marca **"dudoso"** (amarillo) en vez de presente. La intención es detectar
oclusores u objetos extraños que puntúen alto por comparación relativa.

```
piso_similitud = max(0, peor_similitud_presente_en_calibracion - 0.45)
```

### 6. Juez 2 — confirmación por simetría de la cruz (`juez_cruz.py`)

Se consulta **solo si** se cumplen las cuatro condiciones: juez habilitado, posición marcada
`phillips`, el clasificador dijo **faltante**, y el puntaje está en la zona dudosa
(`[umbral - 12, umbral + 6]`).

Es **solo-rescate**: si encuentra la cruz, promueve la posición a presente; si no la
encuentra, **no interviene** y decide el clasificador. Nunca puede degradar una posición
aprobada. (Antes sí podía vetar, y eso causaba falsos NO_PASA.)

Mide dos cosas:

**`cruzness` (criterio principal).** Recorre el recorte en círculo alrededor de un centro,
muestreando la "oscuridad" (`255 - intensidad`) en `N_ANGULOS_PERFIL = 180` ángulos sobre una
banda radial. Una cruz Phillips tiene cuatro brazos a 90°, así que ese perfil angular sube y
baja **cuatro veces por vuelta**. Se aplica `np.fft.rfft` al perfil y se toma:

```
cruzness = |F[4]| / sum(|F[1:]|)
```

Es decir, la fracción de energía angular en la frecuencia 4. Propiedades: **invariante a la
rotación** (rotar cambia la fase, no la magnitud de `F[4]`) y **adimensional** (por tanto
razonablemente portable entre iluminaciones y modelos).

Se prueban **3 bandas radiales** (`BANDAS_CRUZ = ((0.20,0.62), (0.28,0.74), (0.36,0.88))`)
para adaptarse al tamaño de la cruz, y una **rejilla de centros** (`±5 px`, paso 2) porque el
recorte no siempre queda centrado en el tornillo. Se toma el máximo sobre todas las
combinaciones. El muestreo está vectorizado con NumPy y con caché de geometría
(`_GEOM_CACHE`): antes tardaba ~170 ms por posición y ahora ~3.7 ms.

**`centro` (guardia).** Energía de borde (magnitud de gradiente Sobel, sin normalizar) media
en el disco central del recorte. Es un valor **absoluto**.

Decisión: rescata **solo si** `cruzness >= UMBRAL_CRUZNESS` **y** `centro >= PISO_CENTRO`.

### 7. Votación temporal (anti-parpadeo)

Cada posición vota sobre una ventana de `FRAMES_VOTO = 15` frames; necesita
`VOTOS_PRESENTE = 8` para declararse presente. El veredicto global debe repetirse idéntico
`FRAMES_ESTABLE = 10` frames consecutivos para considerarse estable, y solo entonces se
registra en el log. Hay un reset tras `FRAMES_SIN_MARCADOR_RESET = 30` frames sin marcadores.

---

## Estructura de `plantillas.npz`

Claves por posición (`rid` = id de la ROI, p. ej. `T01`):

| Clave | Contenido |
|---|---|
| `<rid>_p` | banco presente: array `(N, h, w)` — plantillas, 70 % central del recorte |
| `<rid>_a` | banco vacío: array `(N, h, w)` |
| `<rid>_pc` | recortes **crudos** presente: array `(N, H, W)` — ROI completa |
| `<rid>_ac` | recortes crudos vacío |
| `<rid>_cruz` | plantilla de la cruz: 60 % central de la muestra más nítida |

Los recortes crudos existen para dos cosas: **calibración incremental**
(`--agregar-unidad`, que añade unidades sin repetir todo el proceso) y para que
`diagnostico_juez.py` mida sobre la **misma imagen que ve producción**.

Cada plantilla de unidad proviene del **promedio de 15 frames** de esa unidad quieta.

---

## Parámetros configurables actuales

**`inspector.py`:** `ARUCO_DICT = DICT_4X4_50`, `IDS_ESQUINAS = [0,1,2,3]`,
`LADO_MAX_CANONICO = 3200`, `FRAMES_VOTO = 15`, `VOTOS_PRESENTE = 8`,
`FRAMES_ESTABLE = 10`, `FRAMES_SIN_MARCADOR_RESET = 30`.

**`juez_cruz.py`:** `ZONA_DUDOSA_ABAJO = 12.0`, `ZONA_DUDOSA_ARRIBA = 6.0`,
`UMBRAL_CRUZNESS`, `PISO_CENTRO`, `RADIO_BUSQUEDA_CENTRO = 5`,
`PASO_BUSQUEDA_CENTRO = 2`, `BANDAS_CRUZ`, `N_ANGULOS_PERFIL = 180`, `LADO_PERFIL = 64`,
`LADO_TRABAJO = 44`, `FRAC_R_CENTRO = 0.50`, `FRAC_R_ANILLO_IN = 0.62`,
`FRAC_R_ANILLO_OUT = 0.95`, `FRAC_CENTRAL = 0.6`.

---

## Modos de ejecución

```
python3 inspector.py --capturar-referencia
python3 inspector.py --calibrar
python3 inspector.py --calibrar-fotos COMPLETA VACIA
python3 inspector.py --agregar-unidad [presente|vacia]
python3 inspector.py --vivo
python3 inspector.py --foto IMAGEN
python3 diagnostico_juez.py [--receta NOMBRE]
python3 analizar_log.py [--proceso NOMBRE] [--desde YYYY-MM-DD]
```

Teclas en modo vivo: `J` alterna el Juez 2, `D` modo debug (muestra puntaje/umbral y estado
del juez), `S` guarda captura, `Q` salir.

---

## Problemas ya identificados — **verifícalos, no los asumas**

Se listan para que no gastes tiempo redescubriéndolos, pero **confirma cada uno de forma
independiente**: algunos diagnósticos previos resultaron equivocados. Si crees que alguno
está mal caracterizado, dilo.

1. **Sesgo de auto-comparación en calibración.** Al derivar umbrales, cada muestra se compara
   contra un banco que **contiene su propia plantilla**, así que su similitud sale 1.0 y el
   puntaje queda inflado. Efecto medido: `piso_similitud` siempre resulta exactamente 0.55
   (`1.0 - 0.45`) en todas las posiciones y en cualquier calibración, así que no es un
   parámetro calibrado sino una constante. Y la `separacion` reportada exagera la real:
   una posición con 16.6 puntos en calibración mostró ~3–4 puntos en producción.
   **Evalúa si conviene validación cruzada (dejar-uno-fuera) y qué riesgos introduce.**
2. **No se valida la correspondencia entre `plantillas.npz` y `config_rois.json`.** Si se
   mueve o redimensiona una ROI y no se recalibra, el sistema corre en silencio con plantillas
   desalineadas. No hay huella de geometría ni verificación al arrancar.
3. **`ZONA_DUDOSA_ARRIBA = 6.0` es código muerto** desde que el juez exige que el clasificador
   haya dicho faltante (puntaje < umbral): la mitad superior de la ventana es inalcanzable.
4. **Umbrales del juez globales, no por posición**, y `PISO_CENTRO` es energía **absoluta**
   (no portable entre modelos de teléfono ni ante cambios de iluminación). Se midieron
   tornillos con energía central de 156–209 y huecos de 63–151 entre posiciones: se solapan,
   así que ningún umbral único los separa correctamente.
5. **Hay posiciones donde el Juez 2 es contraproducente**: sus huecos dan más `cruzness` que
   sus tornillos (separación negativa), así que rescataría huecos y no rescataría tornillos.
   No existe mecanismo para deshabilitarlo selectivamente salvo desmarcar `phillips` a mano.
6. **Dos ROIs a 1.6 px de solaparse** (`T19` y `T21`, ambas 60 px, centros a 61.6 px en y).
   No hay validación de solapamiento ni de que las ROIs caigan dentro de la imagen.
7. **Formatos de fecha inconsistentes**: el calibrador HTML escribe UTC con sufijo `Z`, el
   inspector escribe hora local sin zona. Impide comparar de forma fiable qué archivo es más
   reciente.
8. **Una posición depende del Juez 2 en el 94 % de las inspecciones** (250 rescates de 262
   lecturas correctas): su clasificador da el mismo puntaje con y sin tornillo en producción
   (~46 en ambos casos), aunque en calibración reportaba 40 puntos de separación. Sin el juez
   esa posición se cae. **Evalúa qué debería hacer el sistema ante una posición así: ¿avisar?
   ¿negarse a operar? ¿es aceptable esa dependencia?**
9. **El log no registra verdad de campo.** No hay forma de distinguir "detectó correctamente
   un tornillo ausente" de "falsa alarma", lo que hace ininterpretables las tasas de fallo y
   ya provocó al menos un diagnóstico erróneo. Se añadió una columna `detalle` con
   `puntaje/umbral:similitud:estado` por posición, pero sigue faltando la verdad de campo.
10. **La calibración usa solo 3 unidades presentes y 3 vacías.** Una unidad atípica mueve el
    umbral drásticamente: una posición pasó de 16.3 a 0.8 puntos de margen entre dos
    calibraciones consecutivas. **Evalúa la robustez estadística de derivar umbrales así.**

---

## Qué debes evaluar

Para cada punto, entrega hallazgos concretos con ubicación, impacto y recomendación.

### Bugs
Errores lógicos, condiciones invertidas, off-by-one, variables sobrescritas, rutas de código
inalcanzables, valores que no se propagan. Presta atención especial a los cambios recientes
alrededor del Juez 2, la votación y el logging, donde ya aparecieron regresiones (por ejemplo,
un registro que se sobrescribía al eliminar un `continue`, y una firma de cambio construida
sobre un valor que fluctuaba cada frame y provocaba spam continuo en terminal).

### Excepciones sin controlar
Accesos a claves inexistentes, JSON malformado, `.npz` corrupto o incompleto, cámara ausente
o desconectada a mitad de sesión, archivos sin permiso de escritura, división por cero,
recortes de tamaño cero, `matchTemplate` con plantilla mayor que la imagen, imágenes vacías o
en formato inesperado. ¿Qué pasa si el disco se llena mientras se escribe el CSV?

### Memory leaks y gestión de recursos
`VideoCapture` liberado en todas las rutas de salida (incluidas excepciones), ventanas de
OpenCV destruidas, `deque` con `maxlen` correcto, archivos `.npz` cerrados (`np.load` es
perezoso: ¿se mantienen descriptores abiertos?), acumuladores que crecen sin límite,
diccionarios globales que nunca se limpian, caché de geometría sin cota.

### Hilos y concurrencia
El sistema parece de un solo hilo. Verifica: ¿hay estado global mutable que asuma un único
hilo (`_RECETA`, `_JUEZ_ACTIVO`, cachés)? ¿El bucle de captura y el de GUI están acoplados de
forma que un frame lento congele la interfaz? ¿Sería seguro paralelizar la evaluación de las
23 ROIs, y qué habría que cambiar? ¿Hay riesgo si dos instancias corren a la vez y escriben
el mismo CSV o el mismo `.npz`?

### Rendimiento
Costo por frame y cómo escala. Dato conocido: el clasificador compara cada recorte contra
**todas** las plantillas del banco, así que el costo crece **linealmente con el número de
unidades de calibración**. Estimación previa: ~42 ms/frame con 3 unidades y 22 posiciones,
~140 ms con 10 unidades. El Juez 2 añade ~3.7 ms por posición consultada. Evalúa: ¿dónde
están los cuellos de botella reales?, ¿hay trabajo redundante entre frames?, ¿se recalculan
cosas que podrían cachearse?, ¿la votación temporal multiplica innecesariamente el costo?,
¿cómo afecta la latencia total al operario dado que hace falta estabilidad en 10 frames?

### Calidad del uso de OpenCV
Uso idiomático y correcto de la API: parámetros de detección ArUco (¿se ajustaron o se usan
por defecto?), robustez de la homografía con solo 4 puntos (sin RANSAC, sin validación de
error de reproyección, sin comprobar degeneración si los marcadores se detectan mal),
interpolación elegida en `warpAffine`/`resize`/`warpPerspective`, manejo de espacios de color
y tipos (`uint8` vs `float32`), `ksize` de Sobel, tamaños de `GaussianBlur`, uso de
`TM_CCOEFF_NORMED` frente a alternativas, y si mezclar NCC con SQDIFF en una suma ponderada
50/50 tiene fundamento o es arbitrario.

### Calidad del ROI
Dimensionamiento, centrado y encuadre. ¿La relación 70 % plantilla / 100 % recorte es la
adecuada, y qué la determina? ¿Qué pasa con ROIs cerca del borde de la imagen rectificada?
¿Deberían validarse solapamientos automáticamente? ¿Es correcto que el tamaño de ROI sea por
posición con fallback global? ¿Cómo debería elegirse el tamaño para un tornillo dado? ¿El
sistema es robusto a que la pieza se desplace dentro del fixture, dado que los marcadores
están en el fixture?

### Arquitectura
Separación de responsabilidades, acoplamientos, estado global, testabilidad. `inspector.py`
tiene ~1470 líneas y mezcla captura, procesamiento, clasificación, GUI, persistencia y CLI.
¿Cómo debería descomponerse? ¿Qué interfaces harían falta para poder probar la lógica de
clasificación sin cámara ni GUI? ¿Es adecuado el modelo de "procesos/recetas"? ¿Los
parámetros deberían vivir en código o en configuración?

### Código duplicado
Lógica repetida entre `inspector.py`, `diagnostico_juez.py` y `analizar_log.py` (por ejemplo,
carga de recetas, elección de proceso, parseo del CSV, cálculo de similitud, recortes
centrales, construcción de montajes visuales). ¿Qué debería extraerse a un módulo común y
cuál es el riesgo de que las copias divergan?

### Seguridad
Es un sistema industrial local, así que evalúa lo proporcional: rutas de archivo construidas
desde entrada del usuario (nombres de proceso — ¿traversal?), `np.load` sobre archivos no
verificados (¿`allow_pickle`?), inyección en CSV, permisos de archivos, si el HTML del
calibrador ejecuta algo inseguro o expone datos, y si hay algún `subprocess` con entrada no
saneada. También integridad: ¿puede corromperse un `.npz` o un JSON a medio escribir y dejar
el sistema en estado inválido? ¿Hay escritura atómica?

### Casos límite
Cero posiciones configuradas; una sola unidad de calibración; bancos de tamaños distintos
entre presente y vacío; todas las posiciones faltantes; ninguna marcada Phillips; ROI de
tamaño 1 px; imagen completamente negra o saturada; marcadores duplicados o mal detectados;
`peor_presente` menor que `mejor_vacio` (solapamiento en calibración — ¿qué hace el código?);
plantilla más grande que el recorte; log inexistente o con cabecera antigua; proceso con
nombre que colisiona con otro; recorte que cae parcialmente fuera de la imagen.

### Posibles falsos GREEN (declarar presente sin tornillo) — **máxima prioridad**
Este es el error inaceptable. Rastrea todas las rutas por las que una posición puede acabar
como presente y busca cómo podría ocurrir sin tornillo: rescates del Juez 2 en posiciones
donde no discrimina; el `piso_similitud` siendo una constante que quizá nunca dispara;
comparación relativa que puede favorecer al presente si la plantilla de vacío es mala; ROI
desalineada que capture un tornillo vecino; oclusores; reflejos; un tornillo de otra posición
entrando en el recorte por solapamiento; votación que declare presente con 8 de 15 frames
ruidosos.

### Posibles falsos RED (declarar faltante con tornillo puesto)
Cuesta tiempo pero no calidad. Rastrea: puntajes que rondan el umbral y parpadean; posiciones
cuya separación real es de pocos puntos; plantillas de calibración no representativas de la
producción; el `piso_similitud` marcando "dudoso" piezas legítimas; variación de iluminación
o de asentamiento del tornillo; unidades atípicas que hundieron el umbral.

### Mantenibilidad
Nombres, comentarios (¿describen el comportamiento actual o uno pasado?), números mágicos,
funciones demasiado largas, ausencia de pruebas automatizadas, dificultad para razonar sobre
el flujo de decisión. ¿Qué haría falta para que otra persona pudiera modificar el sistema con
confianza? ¿Cómo se detectaría una regresión hoy?

### Riesgos para producción
Modos de fallo silencioso (el sistema sigue operando pero mide mal); dependencia de
calibraciones frágiles; parámetros que hay que reajustar a mano al cambiar de modelo de
teléfono; qué pasa si cambia la iluminación de la estación, si se mueve la cámara, si se
sustituye el fixture; ausencia de verificación de integridad al arrancar; falta de alarma
cuando una posición depende por completo de una heurística de rescate; ausencia de verdad de
campo para medir precisión real. ¿Qué debería impedir que el sistema arranque?

---

## Formato de entrega

1. **Resumen ejecutivo** (máx. 10 líneas): los riesgos más graves para producción.
2. **Hallazgos priorizados** en tres niveles — *crítico* (puede producir un falso PASA o
   corrupción de datos), *importante* (fiabilidad, rendimiento, mantenibilidad),
   *menor* (limpieza). Para cada uno: ubicación, qué está mal, por qué importa, cómo
   arreglarlo, y cómo verificar el arreglo.
3. **Evaluación por cada categoría** de la lista anterior, incluso si el veredicto es "sin
   hallazgos relevantes" (dilo explícitamente, no lo omitas).
4. **Preguntas abiertas**: qué necesitarías medir o qué datos te faltan para completar el
   análisis, y qué experimento concreto lo resolvería.
5. **Plan de remediación sugerido**: en qué orden atacar, distinguiendo lo que se puede hacer
   sin recalibrar de lo que exige recalibración o intervención física en la estación.

Sé concreto y técnico. Prefiero un informe incómodo y útil a uno diplomático y vacío.
