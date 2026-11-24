#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detector_anomalias.py — Detecta contaminacion y objetos extranos (tornillos
sueltos, virutas, residuos) comparando cada unidad contra una LINEA BASE de
unidades buenas. Produce un mapa de calor que senala DONDE hay algo que no encaja.

Idea: no se le ensena que es un tornillo ni que es contaminacion. Se le ensena
como se ve una unidad NORMAL en cada punto, y se marca todo lo que se desvie. Por
eso detecta cosas que nunca vio, y por eso solo necesita imagenes BUENAS.

Como funciona:
  1. La imagen se divide en parches. Cada parche se describe con 5 rasgos:
     media de azul, verde y rojo (nivel y color), desviacion interna (textura) y
     energia de borde (estructura). El color y los bordes importan porque una
     anomalia puede no cambiar el brillo: un tornillo sobre un blindaje metalico
     tiene un nivel parecido al fondo, pero aporta contorno, sombra y ranura.
  2. Con las unidades buenas se calcula, PARA CADA POSICION de parche, el valor
     normal de esos rasgos y cuanto varian legitimamente entre unidades.
  3. Ante una unidad nueva se mide, por parche, a cuantas desviaciones tipicas
     esta de su normal. Ese numero es el mapa de calor.
  Lo que se mide no es "cuanto cambio" sino "cuanto cambio comparado con lo que
  normalmente varia AHI". Por eso una mancha tenue sobre una zona estable pesa
  mas que un desplazamiento grande sobre un flex que de por si se mueve.

Dos decisiones que evitan los fallos clasicos del metodo:
  - BUSQUEDA LOCAL: cada parche se compara tambien contra los parches vecinos de
    la linea base y se toma el mejor calce. Sin esto, un desalineamiento de pocos
    pixeles enciende todos los contornos de la pieza. Es el mismo principio por el
    que matchTemplate desliza la plantilla en el inspector de tornillos.
  - SUELO DE DESVIACION: en zonas perfectisimamente estables la desviacion tiende
    a cero y cualquier cambio minimo daria un valor enorme. Se le pone un suelo.

IMPORTANTE: todas las imagenes deben venir del MISMO montaje (misma camara, misma
posicion, misma iluminacion) y estar RECTIFICADAS (las que guarda la tecla S del
inspector). Si la iluminacion cambia entre la linea base y la evaluacion, el mapa
se enciende entero: eso es una limitacion del metodo, no un fallo.

Uso:
    python3 detector_anomalias.py --entrenar buenas/
    python3 detector_anomalias.py --validar prueba_buenas/
    python3 detector_anomalias.py --evaluar unidad.png
    python3 detector_anomalias.py --evaluar carpeta/ --umbral 12
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

ARCHIVO_BASE = "linea_base.npz"
EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")

# --- parametros del metodo ---
LADO_PARCHE = 16        # px; un tornillo suele abarcar varios parches
PASO = 8                # solapamiento: paso menor que el lado localiza mejor
RADIO_BUSQUEDA = 1      # parches de vecindad para absorber desalineacion
FRAC_SUELO_DESV = 0.25  # suelo de desviacion, como fraccion de la mediana
UMBRAL_SIGMAS = 10.0    # a partir de aqui se marca (afinable con --validar)
AREA_MINIMA = 1         # parches contiguos minimos para considerar una region

ESCALA_PATRON = 1       # se trabaja a mitad de resolucion: 4x menos calculo
                        # y el tornillo (~39 px) sigue midiendo ~20 px
LADO_PATRON = 48        # px de la imagen original (optimo medido)
BUSQUEDA_PATRON = 24    # px de la imagen original (optimo medido)
PASO_BUSQUEDA = 6       # rejilla de desplazamientos, en px reducidos
SUELO_DESV_CORR = 0.03  # suelo de la desviacion de correlacion

# --- sexto rasgo: concentracion de borde (ver `rasgos`) ---
USAR_CONCENTRACION = True   # se apaga con --sin-concentracion para comparar
FRAC_CENTRO_CONC = 0.4      # fraccion central del parche que se mide


# ----------------------------- rasgos ---------------------------------------

def rasgos(img, lado=LADO_PARCHE, paso=PASO):
    """Rasgos por parche: (ny, nx, 6) = medias B,G,R + desviacion + borde +
    concentracion de borde.

    El sexto rasgo (concentracion) existe porque los otros cinco no distinguen
    "objeto compacto" de "superficie con textura": un tornillo suelto sobre un
    blindaje puede tener la MISMA magnitud de borde promedio que el blindaje
    texturado que lo rodea, y aun asi ser radicalmente distinto en como se
    reparte ese borde. El tornillo concentra su estructura en un punto; el
    blindaje la tiene repartida. Medido sobre tornillos reales, la concentracion
    separo el tornillo de su entorno inmediato en 7 de 7 casos, mientras que la
    magnitud sola y la entropia de orientacion no lo lograban de forma fiable.

    Se calcula como la energia de borde de la zona central del parche dividida
    entre la del parche completo: dos boxFilter concentricos, sin bucles.

    Vectorizado con boxFilter: calcular esto con bucles de Python sobre miles de
    parches seria inviable en el lazo de produccion."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    f = img.astype(np.float32)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)

    k = (lado, lado)
    capas = [cv2.boxFilter(f[:, :, c], -1, k, normalize=True) for c in range(3)]
    m1 = cv2.boxFilter(g, -1, k, normalize=True)
    m2 = cv2.boxFilter(g * g, -1, k, normalize=True)
    capas.append(np.sqrt(np.maximum(m2 - m1 * m1, 0)))       # textura interna
    capas.append(cv2.boxFilter(mag, -1, k, normalize=True))  # estructura

    if USAR_CONCENTRACION:
        # concentracion: energia de borde del centro / la del parche completo.
        # Escala 0-100 para que su magnitud sea comparable a los demas rasgos
        # (importa por el suelo de desviacion, que es absoluto).
        lado_in = max(3, int(round(FRAC_CENTRO_CONC * lado)))
        s_out = cv2.boxFilter(mag, -1, k, normalize=False)
        s_in = cv2.boxFilter(mag, -1, (lado_in, lado_in), normalize=False)
        capas.append(100.0 * s_in / np.maximum(s_out, 1e-6))

    off = lado // 2
    ys = np.arange(off, img.shape[0] - off, paso)
    xs = np.arange(off, img.shape[1] - off, paso)
    if len(ys) == 0 or len(xs) == 0:
        raise ValueError("La imagen es demasiado pequena para el tamano de parche.")
    return np.stack([c[np.ix_(ys, xs)] for c in capas], axis=-1).astype(np.float32)


# --------------------------- linea base -------------------------------------

def _listar(carpeta):
    if not os.path.isdir(carpeta):
        sys.exit(f"[ERROR] No existe la carpeta '{carpeta}'.")
    rutas = sorted(os.path.join(carpeta, f) for f in os.listdir(carpeta)
                   if f.lower().endswith(EXT))
    if not rutas:
        sys.exit(f"[ERROR] No hay imagenes en '{carpeta}'.")
    return rutas


def entrenar(carpeta, lado, paso, salida):
    """Construye la linea base acumulando de a una imagen.

    La acumulacion incremental es obligatoria, no una optimizacion: apilar 200
    imagenes rectificadas en memoria pediria mas de 12 GB. Sumando sobre dos
    acumuladores el consumo queda fijo, sin importar cuantas imagenes sean, y
    ademas permite anadir mas imagenes despues sin recalcular desde cero."""
    rutas = _listar(carpeta)
    print(f"\nConstruyendo linea base con {len(rutas)} imagenes de '{carpeta}'")
    suma = suma2 = None
    forma = None
    n = 0
    for i, r in enumerate(rutas, 1):
        img = cv2.imread(r, cv2.IMREAD_COLOR)
        if img is None:
            print(f"  [!] no se pudo leer {os.path.basename(r)}, se omite")
            continue
        if forma is None:
            forma = img.shape[:2]
        elif img.shape[:2] != forma:
            sys.exit(f"[ERROR] '{os.path.basename(r)}' mide {img.shape[1]}x{img.shape[0]} "
                     f"y las demas {forma[1]}x{forma[0]}.\n"
                     "        Todas las imagenes deben ser del mismo montaje y estar "
                     "rectificadas al mismo tamano.")
        rr = rasgos(img, lado, paso)
        suma = rr.copy() if suma is None else suma + rr
        suma2 = rr * rr if suma2 is None else suma2 + rr * rr
        n += 1
        if i % 10 == 0 or i == len(rutas):
            print(f"  {i}/{len(rutas)}")
    if n < 5:
        sys.exit(f"[ERROR] Solo se pudieron leer {n} imagenes. Hacen falta al menos 5 "
                 "(y para que sea util, 30 o mas).")

    media = suma / n
    desv = np.sqrt(np.maximum(suma2 / n - media * media, 0))
    # suelo por rasgo: donde la desviacion es casi nula, cualquier cambio minimo
    # daria un valor enorme y llenaria el mapa de falsos positivos
    suelo = np.maximum(FRAC_SUELO_DESV * np.median(desv, axis=(0, 1)), 1.0)
    desv = np.maximum(desv, suelo)

    np.savez_compressed(salida, media=media, desv=desv,
                        n=np.array([n]), lado=np.array([lado]),
                        paso=np.array([paso]),
                        alto=np.array([forma[0]]), ancho=np.array([forma[1]]),
                        suma=suma, suma2=suma2)
    print(f"\n[OK] Linea base guardada en '{salida}'")
    print(f"     {n} imagenes | {media.shape[0]}x{media.shape[1]} parches "
          f"de {lado}px (paso {paso}) | {media.shape[2]} rasgos")
    if n < 30:
        print(f"\n[!] Con solo {n} imagenes la desviacion es un estimador ruidoso y")
        print("    tendras falsas alarmas. Para uso real apunta a 50-200 imagenes,")
        print("    capturadas en dias y lotes distintos para cubrir la variacion normal.")


def cargar_base(ruta):
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No existe '{ruta}'. Crea la linea base primero:\n"
                 "        python3 detector_anomalias.py --entrenar buenas/")
    d = np.load(ruta)
    return {"media": d["media"], "desv": d["desv"], "n": int(d["n"][0]),
            "lado": int(d["lado"][0]), "paso": int(d["paso"][0]),
            "alto": int(d["alto"][0]), "ancho": int(d["ancho"][0])}


# --------------------------- evaluacion -------------------------------------

def mapa_anomalia(img, base, radio=RADIO_BUSQUEDA):
    """Devuelve el mapa de desviaciones (en sigmas), un valor por parche.

    Para cada parche se prueba tambien contra los vecinos de la linea base y se
    toma el MEJOR calce (el minimo). Asi un corrimiento de pocos pixeles no se
    confunde con una anomalia; sin esto, todos los contornos de la pieza se
    encenderian."""
    if img.shape[:2] != (base["alto"], base["ancho"]):
        sys.exit(f"[ERROR] La imagen mide {img.shape[1]}x{img.shape[0]} y la linea "
                 f"base se hizo con {base['ancho']}x{base['alto']}.")
    r = rasgos(img, base["lado"], base["paso"])
    media, desv = base["media"], base["desv"]
    mejor = None
    for dj in range(-radio, radio + 1):
        for di in range(-radio, radio + 1):
            m = np.roll(np.roll(media, dj, axis=0), di, axis=1)
            s = np.roll(np.roll(desv, dj, axis=0), di, axis=1)
            z = (np.abs(r - m) / s).max(axis=-1)   # el rasgo que mas se desvia
            mejor = z if mejor is None else np.minimum(mejor, z)
    return mejor


# --- FILTRO DE FORMA (segunda etapa) ---
# El detector de anomalias marca TODO lo que cambio: el tornillo, pero tambien
# un flex movido, un rayon cosmetico, una sombra. Es bueno encontrando, malo
# distinguiendo. El filtro de forma es un segundo juez que mira cada candidato y
# descarta lo que no puede ser un tornillo POR SU GEOMETRIA.
#
# Se apoya en algo ya medido: los tornillos miden 29-48 px y producen regiones
# compactas; el defecto cosmetico del riel producia una franja larga y el sticker
# una mancha enorme. Un tornillo es redondo y pequeno: eso descarta franjas y
# manchas sin tocar la deteccion.
#
# Los limites van en PIXELES, no en parches, porque el paso de muestreo cambia
# entre metodos (4 px en promedios, 24 en patron) y una region de "10 parches"
# significa cosas distintas en cada uno.
LADO_MIN_PX = 12        # menor que esto no es un tornillo, es ruido
LADO_MAX_PX = 110       # mayor que esto es una franja o una mancha
ASPECTO_MAX = 3.0       # un tornillo es redondo: ancho/alto cercano a 1
LLENADO_MIN = 0.30      # fraccion de su caja que ocupa la region


def filtro_forma(r, paso, lado_min=LADO_MIN_PX, lado_max=LADO_MAX_PX,
                 aspecto_max=ASPECTO_MAX, llenado_min=LLENADO_MIN):
    """True si la region PODRIA ser un tornillo por su geometria.

    Devuelve tambien el motivo del descarte, para poder auditar cuando el filtro
    se lleva por delante un tornillo real en vez de un falso."""
    # +1 parche: una region de 1 parche cubre `lado` px, no 0
    w_px = (r["w"] + 1) * paso
    h_px = (r["h"] + 1) * paso
    mayor, menor = max(w_px, h_px), min(w_px, h_px)
    if mayor < lado_min:
        return False, "muy pequena"
    if mayor > lado_max:
        return False, "muy grande"
    if menor > 0 and mayor / menor > aspecto_max:
        return False, "alargada"
    llenado = r["area"] / max(float(r["w"] * r["h"]), 1.0)
    if llenado < llenado_min:
        return False, "dispersa"
    return True, ""


def regiones(mapa, umbral, area_min=AREA_MINIMA, paso=None, forma=None):
    """Agrupa parches contiguos que superan el umbral. Un parche aislado suele
    ser ruido; varios juntos son una anomalia real.

    Si se pasa `paso` y `forma`, aplica ademas el filtro de forma (segunda
    etapa): descarta las regiones cuya geometria no puede corresponder a un
    tornillo. Sin `forma`, se comporta como siempre."""
    bin_ = (mapa > umbral).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bin_, 8)
    salida = []
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if area < area_min:
            continue
        r = {"x": x, "y": y, "w": w, "h": h, "area": int(area),
             "sigmas": float(mapa[lab == k].max())}
        if forma is not None and paso:
            ok, motivo = filtro_forma(r, paso, **forma)
            if not ok:
                continue
        salida.append(r)
    return sorted(salida, key=lambda d: -d["sigmas"])


def cargar_gt(ruta, alto, ancho):
    """Carga el ground truth de tornillos marcado a mano (JSON del marcador).

    Existe porque hasta ahora cada cambio del metodo se juzgaba a ojo, mirando
    paneles y recordando cual region era el tornillo. Eso es lento y se presta a
    conclusiones equivocadas. Con el GT, cada corrida da un numero: cuantos
    tornillos se detectaron y cuantas regiones sobraron.

    Devuelve {archivo: [(cx, cy, w, h), ...]} en pixeles de la imagen que se
    esta evaluando. Si el JSON se marco sobre imagenes de otro tamano (p.ej.
    exportadas escaladas), las coordenadas se reescalan y se avisa."""
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No existe el ground truth '{ruta}'.")
    with open(ruta, "r", encoding="utf-8") as f:
        d = json.load(f)
    salida = {}
    aviso = None
    for im in d.get("imagenes", []):
        w_gt, h_gt = im.get("image_size", [ancho, alto])
        ex, ey = ancho / float(w_gt), alto / float(h_gt)
        if abs(ex - 1) > 0.01 or abs(ey - 1) > 0.01:
            aviso = (w_gt, h_gt, ex, ey)
        salida[im["archivo"]] = [
            (t["cx"] * ex, t["cy"] * ey, t["w"] * ex, t["h"] * ey)
            for t in im.get("tornillos", [])]
    if aviso:
        print(f"[!] El ground truth se marco sobre imagenes de {aviso[0]}x{aviso[1]} "
              f"y estas miden {ancho}x{alto}.\n"
              f"    Las coordenadas se reescalan x{aviso[2]:.2f}, pero comprueba que "
              f"sea la misma orientacion.")
    return salida


def evaluar_contra_gt(mapa, umbral, gt_tornillos, base, tolerancia=1.5,
                      forma=None):
    """Cruza las regiones detectadas con los tornillos marcados a mano.

    Un tornillo cuenta como DETECTADO si alguna region cae sobre el (dentro de
    `tolerancia` veces su tamano desde su centro). Las regiones que no
    corresponden a ningun tornillo son falsos positivos.

    Devuelve (detectados, lista_por_tornillo, n_falsos)."""
    paso = base["paso"]
    off = base["lado"] // 2
    regs = regiones(mapa, umbral, paso=paso, forma=forma)
    # centro de cada region en pixeles de la imagen
    centros = [(off + (r["x"] + r["w"] / 2.0) * paso,
                off + (r["y"] + r["h"] / 2.0) * paso, r) for r in regs]
    usadas = set()
    detalle = []
    for (cx, cy, tw, th) in gt_tornillos:
        radio = tolerancia * max(tw, th)
        mejor = None
        for k, (rx, ry, r) in enumerate(centros):
            dist = np.hypot(rx - cx, ry - cy)
            if dist <= radio and (mejor is None or r["sigmas"] > mejor[1]["sigmas"]):
                mejor = (k, r, dist)
        if mejor is not None:
            usadas.add(mejor[0])
            detalle.append((True, mejor[1]["sigmas"], mejor[1]["area"], mejor[2]))
        else:
            detalle.append((False, 0.0, 0, 0.0))
    falsos = len(centros) - len(usadas)
    return sum(1 for d in detalle if d[0]), detalle, falsos


def panel(img, mapa, base, umbral, solo_marcada=True, forma=None):
    """Vista de resultado. Por defecto (solo_marcada=True) devuelve UNICAMENTE la
    imagen con las regiones marcadas, a tamano completo. Con solo_marcada=False
    devuelve el panel triple (unidad + mapa de calor + marcada) para depurar.
    El calor se pinta SOLO donde supera el umbral: colorear toda la imagen
    convierte la vista en un cuadro ilegible y el operario pierde la referencia."""
    H, W = img.shape[:2]
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    grande = cv2.resize(mapa, (W, H), interpolation=cv2.INTER_CUBIC)

    tope = max(float(mapa.max()), umbral + 1e-3)
    norm = np.clip((grande - umbral) / (tope - umbral), 0, 1)
    calor = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
    m = (grande > umbral).astype(np.float32)[..., None]
    vista_calor = (img * (1 - m * 0.55) + calor * (m * 0.55)).astype(np.uint8)

    marcado = img.copy()
    esc = W / mapa.shape[1]
    for r in regiones(mapa, umbral, paso=base['paso'], forma=forma):
        x, y = int(r["x"] * esc), int(r["y"] * esc)
        w, h = int(r["w"] * esc), int(r["h"] * esc)
        cv2.rectangle(marcado, (x - 6, y - 6), (x + w + 6, y + h + 6), (200, 60, 240), 2)
        cv2.putText(marcado, f"{r['sigmas']:.0f}s", (x - 6, max(16, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 60, 240), 2, cv2.LINE_AA)

    def con_titulo(v, t):
        barra = np.full((28, W, 3), 25, np.uint8)
        cv2.putText(barra, t, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (235, 235, 235), 1, cv2.LINE_AA)
        return np.vstack([barra, v])

    # Por defecto se devuelve SOLO la vista marcada, a tamano completo: es la
    # unica que el operario necesita para decidir, y con varias unidades el panel
    # triple se hace demasiado ancho para revisar. El panel triple (unidad +
    # calor + marcada) sigue disponible con solo_marcada=False para depurar.
    if solo_marcada:
        return con_titulo(marcado, f"regiones detectadas (>{umbral:.0f} sigmas)")

    sep = np.full((H + 28, 4, 3), 45, np.uint8)
    return np.hstack([con_titulo(img, "1. unidad"), sep,
                      con_titulo(vista_calor, f"2. mapa de calor (>{umbral:.0f} sigmas)"),
                      sep, con_titulo(marcado, "3. regiones detectadas")])


def evaluar(ruta, base, umbral, dir_salida, gt=None, fn_mapa=None, forma=None,
            area=None, rois=None):
    rutas = _listar(ruta) if os.path.isdir(ruta) else [ruta]
    os.makedirs(dir_salida, exist_ok=True)
    tot_det = tot_tor = tot_fal = 0
    if gt:
        print(f"\n{'imagen':30s} | tornillo | sigmas | parches | falsos")
        print("-" * 74)
    else:
        print(f"\n{'imagen':38s} | {'max sigmas':11s} | regiones")
        print("-" * 68)
    for r in rutas:
        img = cv2.imread(r, cv2.IMREAD_COLOR)
        nombre = os.path.basename(r)
        if img is None:
            print(f"{nombre[:38]:38s} | no se pudo leer")
            continue
        mapa = (fn_mapa(img, base) if fn_mapa else mapa_anomalia(img, base))
        mapa = aplicar_mascaras(mapa, base, area, rois)
        regs = regiones(mapa, umbral, paso=base['paso'], forma=forma)
        if gt is not None and nombre in gt:
            n_det, detalle, falsos = evaluar_contra_gt(mapa, umbral, gt[nombre],
                                                       base, forma=forma)
            tot_det += n_det
            tot_tor += len(detalle)
            tot_fal += falsos
            # OJO: no llamar `area` a esta variable. El parametro `area` de
            # evaluar() es la mascara del housing, y reusar el nombre aqui la
            # sobreescribia con un entero: la mascara funcionaba en la primera
            # imagen y reventaba en la segunda.
            for i, (ok, sig, n_parches, dist) in enumerate(detalle):
                etq = "SI" if ok else "NO"
                extra = (f"{sig:6.1f} | {n_parches:7d}" if ok
                         else f"{'-':>6s} | {'-':>7s}")
                print(f"{nombre[:30]:30s} | {etq:^8s} | {extra} | {falsos:6d}")
        else:
            print(f"{nombre[:38]:38s} | {mapa.max():8.1f}    | {len(regs)}")
            for g in regs[:4]:
                print(f"    region {g['sigmas']:6.1f} sigmas, {g['area']} parches")
        destino = os.path.join(dir_salida, os.path.splitext(nombre)[0] + "_anomalia.png")
        cv2.imwrite(destino, panel(img, mapa, base, umbral, forma=forma))
    if gt and tot_tor:
        print("-" * 74)
        print(f"RESULTADO: {tot_det}/{tot_tor} tornillos detectados "
              f"({100.0*tot_det/tot_tor:.0f}%) | {tot_fal} falsos positivos "
              f"({tot_fal/len(rutas):.1f} por imagen)")
        extra = (f"| {base['media'].shape[2]} rasgos"
                 if base['media'].ndim == 3 else
                 f"| patron, busqueda +-{base.get('busqueda','?')}px")
        print(f"           umbral {umbral} | parche {base['lado']}px "
              f"paso {base['paso']} {extra} | base de {base['n']} imagenes")
    print(f"\n[OK] Paneles guardados en '{dir_salida}/'")


def validar(carpeta, base, dir_salida):
    """Evalua unidades BUENAS que NO estan en la linea base, para medir la tasa de
    falsa alarma y derivar el umbral con datos en vez de a ojo. Sin este paso, el
    umbral es una apuesta."""
    rutas = _listar(carpeta)
    print(f"\nValidando con {len(rutas)} unidades buenas de '{carpeta}'")
    picos = []
    for r in rutas:
        img = cv2.imread(r, cv2.IMREAD_COLOR)
        if img is None:
            continue
        picos.append((os.path.basename(r), float(mapa_anomalia(img, base).max())))
    if not picos:
        sys.exit("[ERROR] No se pudo leer ninguna imagen.")
    vals = sorted(p for _, p in picos)
    print(f"\n{'imagen':38s} | max sigmas")
    print("-" * 54)
    for n, v in sorted(picos, key=lambda t: -t[1]):
        print(f"{n[:38]:38s} | {v:8.1f}")
    print(f"\nmax sigmas en unidades BUENAS: min {vals[0]:.1f}  "
          f"mediana {vals[len(vals)//2]:.1f}  max {vals[-1]:.1f}")
    sug = round(vals[-1] * 1.25, 1)
    print(f"\nUMBRAL sugerido: {sug}  (25% por encima del peor caso bueno)")
    print("Por debajo de ese valor empezarias a marcar unidades correctas.")
    print("Comprueba ahora con unidades CONTAMINADAS que superen ese umbral:")
    print("    python3 detector_anomalias.py --evaluar contaminadas/ "
          f"--umbral {sug}")
    if vals[-1] > 25:
        print("\n[!] Hay unidades buenas con picos muy altos. Suele significar que la")
        print("    iluminacion o el encuadre cambiaron entre la linea base y estas")
        print("    imagenes. Revisa los paneles antes de fiarte del umbral.")
        os.makedirs(dir_salida, exist_ok=True)
        peor = max(picos, key=lambda t: t[1])[0]
        img = cv2.imread(os.path.join(carpeta, peor), cv2.IMREAD_COLOR)
        if img is not None:
            mapa = mapa_anomalia(img, base)
            cv2.imwrite(os.path.join(dir_salida, "peor_caso_bueno.png"),
                        panel(img, mapa, base, sug * 0.6))
            print(f"    Guardado el peor caso en '{dir_salida}/peor_caso_bueno.png'")


# ============================================================================
# MASCARAS GEOMETRICAS
# ============================================================================
# Dos zonas de la imagen generan falsos positivos sin poder contener nunca
# contaminacion relevante:
#
#   1. FUERA DEL HOUSING. El fixture, los marcadores ArUco y el fondo tienen
#      bordes muy fuertes, y cualquier micro-desplazamiento de la pieza los
#      desalinea y dispara la anomalia. Es el peor sitio posible para mirar.
#
#   2. LOS BANCOS DE TORNILLO conocidos (T01..T23 del config del inspector). Un
#      tornillo correctamente asentado no es contaminacion. Y si un tornillo
#      suelto cae justo encima de un banco, el inspector de tornillos lo delata
#      por otra via: al tapar la cruz, esa ROI da faltante o dudoso.
#
# Enmascarar es gratis en computo y no puede perder deteccion fuera de esas
# zonas, asi que se aplica antes de buscar regiones.


def cargar_area(ruta, alto, ancho):
    """Area valida (el housing), marcada como un rectangulo con el marcador."""
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No existe '{ruta}'.")
    with open(ruta, "r", encoding="utf-8") as f:
        d = json.load(f)
    ims = d.get("imagenes", [])
    if not ims or not ims[0].get("tornillos"):
        sys.exit(f"[ERROR] '{ruta}' no tiene ningun rectangulo marcado.")
    im = ims[0]
    w_m, h_m = im.get("image_size", [ancho, alto])
    ex, ey = ancho / float(w_m), alto / float(h_m)
    c = im["tornillos"][0]
    a = (int(c["x0"] * ex), int(c["y0"] * ey),
         int(c["x1"] * ex), int(c["y1"] * ey))
    if abs(ex - 1) > 0.01 or abs(ey - 1) > 0.01:
        print(f"[!] El area se marco en {w_m}x{h_m} y las imagenes son "
              f"{ancho}x{alto}: reescalada x{ex:.3f}, x{ey:.3f}")
    return a


def cargar_rois(ruta, alto, ancho, margen=1.0):
    """ROIs de los bancos de tornillo, del config del inspector.

    `margen` agranda cada ROI: el config puede venir de un lienzo de tamano algo
    distinto, y conviene cubrir el desfase antes que dejar medio banco fuera."""
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No existe '{ruta}'.")
    with open(ruta, "r", encoding="utf-8") as f:
        d = json.load(f)
    w_c, h_c = d.get("image_size", [ancho, alto])
    ex, ey = ancho / float(w_c), alto / float(h_c)
    if abs(ex - 1) > 0.02 or abs(ey - 1) > 0.02:
        print(f"[!] El config de ROIs es de {w_c}x{h_c} y las imagenes son "
              f"{ancho}x{alto}. Se reescala (x{ex:.3f}, x{ey:.3f}), pero el "
              f"desfase puede desplazar los bancos hasta "
              f"{abs(alto - h_c * ey):.0f} px: revisa que las mascaras caigan "
              f"donde deben.")
    base_lado = float(d.get("roi_size", 60))
    fuera = []
    for pt in d.get("puntos", []):
        w = float(pt.get("roi_w", base_lado)) * ex * margen
        h = float(pt.get("roi_h", base_lado)) * ey * margen
        cx, cy = float(pt["x"]) * ex, float(pt["y"]) * ey
        fuera.append({"id": pt.get("id", "?"),
                      "x0": int(cx - w / 2), "y0": int(cy - h / 2),
                      "x1": int(cx + w / 2), "y1": int(cy + h / 2)})
    return fuera


def aplicar_mascaras(mapa, base, area=None, rois=None):
    """Pone a cero el mapa de anomalia fuera del housing y sobre los bancos.

    Se anula el MAPA, no las regiones ya formadas: asi una region no puede
    crecer desde una zona invalida hacia una valida y sobrevivir por el trozo
    bueno."""
    if area is None and not rois:
        return mapa
    m = mapa.copy()
    paso, off = base["paso"], base["lado"] // 2
    ny, nx = m.shape
    # pixel al que corresponde cada indice de parche
    px = off + np.arange(nx) * paso
    py = off + np.arange(ny) * paso
    if area is not None:
        x0, y0, x1, y1 = area
        valido_x = (px >= x0) & (px <= x1)
        valido_y = (py >= y0) & (py <= y1)
        m[~valido_y, :] = 0.0
        m[:, ~valido_x] = 0.0
    for r in (rois or []):
        ix = np.where((px >= r["x0"]) & (px <= r["x1"]))[0]
        iy = np.where((py >= r["y0"]) & (py <= r["y1"]))[0]
        if len(ix) and len(iy):
            m[np.ix_(iy, ix)] = 0.0
    return m


def main():
    ap = argparse.ArgumentParser(
        description="Deteccion de anomalias por estadistica de parches")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--entrenar", metavar="CARPETA",
                   help="construye la linea base con unidades BUENAS")
    g.add_argument("--evaluar", metavar="RUTA",
                   help="evalua una imagen o una carpeta")
    g.add_argument("--validar", metavar="CARPETA",
                   help="mide la falsa alarma con unidades buenas no usadas en la base")
    ap.add_argument("--base", default=ARCHIVO_BASE, help="archivo de linea base")
    ap.add_argument("--umbral", type=float, default=UMBRAL_SIGMAS)
    ap.add_argument("--lado", type=int, default=LADO_PARCHE)
    ap.add_argument("--paso", type=int, default=PASO)
    ap.add_argument("--salida", default="resultados_anomalias")
    ap.add_argument("--gt", default=None, metavar="JSON",
                    help="ground truth de tornillos (del marcador HTML): mide "
                         "automaticamente cuantos se detectan y cuantos falsos hay")
    ap.add_argument("--sin-concentracion", action="store_true",
                    help="desactiva el sexto rasgo (concentracion de borde), "
                         "para comparar contra el metodo de 5 rasgos")
    ap.add_argument("--metodo", choices=["promedio", "patron"], default="promedio",
                    help="promedio = estadistica de rasgos (original); "
                         "patron = correlacion de patrones de gradiente")
    ap.add_argument("--busqueda", type=int, default=BUSQUEDA_PATRON,
                    help="radio de busqueda en px (solo --metodo patron)")
    ap.add_argument("--forma", action="store_true",
                    help="activa el filtro de forma: descarta las regiones cuya "
                         "geometria no puede ser un tornillo (segunda etapa)")
    ap.add_argument("--lado-min", type=int, default=LADO_MIN_PX)
    ap.add_argument("--lado-max", type=int, default=LADO_MAX_PX)
    ap.add_argument("--aspecto", type=float, default=ASPECTO_MAX)
    ap.add_argument("--llenado", type=float, default=LLENADO_MIN)
    ap.add_argument("--area", default=None, metavar="JSON",
                    help="area valida (housing) marcada con el marcador: "
                         "descarta fixture, ArUco y fondo")
    ap.add_argument("--rois", default=None, metavar="JSON",
                    help="config_rois.json del inspector: descarta los bancos "
                         "de tornillo conocidos")
    ap.add_argument("--roi-margen", type=float, default=1.0,
                    help="agranda las ROIs de los bancos (1.0 = tal cual)")
    args = ap.parse_args()

    forma = None
    if args.forma:
        forma = {"lado_min": args.lado_min, "lado_max": args.lado_max,
                 "aspecto_max": args.aspecto, "llenado_min": args.llenado}
        print(f"[i] Filtro de forma ACTIVO: lado {args.lado_min}-{args.lado_max}px, "
              f"aspecto <={args.aspecto}, llenado >={args.llenado}")

    global USAR_CONCENTRACION
    if args.sin_concentracion:
        USAR_CONCENTRACION = False

    if args.metodo == "patron":
        if args.entrenar:
            entrenar_patron(args.entrenar, args.base,
                            args.lado if args.lado != LADO_PARCHE else LADO_PATRON,
                            args.busqueda)
            return
        if args.validar:
            sys.exit("[i] --validar aun no esta implementado para --metodo patron. "
                     "Usa --evaluar con --gt para medir.")
        base = cargar_base_patron(args.base)
        gt = cargar_gt(args.gt, base["alto"], base["ancho"]) if args.gt else None
        area = cargar_area(args.area, base["alto"], base["ancho"]) if args.area else None
        rois = (cargar_rois(args.rois, base["alto"], base["ancho"], args.roi_margen)
                if args.rois else None)
        evaluar(args.evaluar, base, args.umbral, args.salida, gt,
                fn_mapa=mapa_patron, forma=forma, area=area, rois=rois)
        return

    if args.entrenar:
        entrenar(args.entrenar, args.lado, args.paso, args.base)
    elif args.validar:
        validar(args.validar, cargar_base(args.base), args.salida)
    else:
        base = cargar_base(args.base)
        gt = None
        if args.gt:
            gt = cargar_gt(args.gt, base["alto"], base["ancho"])
        area = cargar_area(args.area, base["alto"], base["ancho"]) if args.area else None
        rois = (cargar_rois(args.rois, base["alto"], base["ancho"], args.roi_margen)
                if args.rois else None)
        if area:
            print(f"[i] Area valida: x {area[0]}-{area[2]}, y {area[1]}-{area[3]}")
        if rois:
            print(f"[i] {len(rois)} bancos de tornillo enmascarados")
        evaluar(args.evaluar, base, args.umbral, args.salida, gt, forma=forma,
                area=area, rois=rois)




# ============================================================================
# METODO 2: CORRELACION DE PATRON
# ============================================================================
# El metodo de promedios (arriba) describe cada parche con 5 numeros. Eso
# funciona para manchas y objetos grandes, pero DESTRUYE LA FORMA: un tornillo
# suelto sobre un riel metalico puede dar el mismo promedio de borde que el riel
# solo, y pasa desapercibido. Medido sobre tornillos reales, tres de siete daban
# 0.3, 0.3 y 2.7 sigmas: invisibles.
#
# Aqui no se promedia. Se guarda el PATRON de gradiente de cada posicion en cada
# unidad buena, y se mide cuanto se PARECE el patron actual al normal de ese
# sitio (correlacion normalizada, NCC). Un tornillo cambia la forma aunque no
# cambie el promedio, asi que la correlacion se hunde. Es el mismo principio del
# banco de plantillas del inspector, aplicado a toda la imagen.
#
# Medido con --gt sobre 7 tornillos (parche 48px, busqueda +-24px): la
# correlacion cae de ~0.77 (variacion normal entre unidades buenas) a ~0.44 con
# tornillo, y separa los tres casos que el promedio no ve.
#
# BUSQUEDA LOCAL: las partes internas se mueven unas decimas de milimetro al
# ensamblar (mediana medida: ~5 px). Sin buscar el mejor calce, ese corrimiento
# se confundiria con anomalia. Pero la busqueda no puede ser muy amplia: medido,
# a +-40 px el propio tornillo encuentra donde parecerse y deja de destacar
# (paso de 6/7 a 5/7). El optimo medido es +-24 px.
#
# VECTORIZACION: la NCC local de TODA la imagen se calcula con boxFilter, no
# parche por parche. Recorrer ~36.000 posiciones con matchTemplate individual
# tardaria minutos; asi son segundos.



def _gradiente(img):
    """Magnitud de gradiente Sobel, a escala reducida. Es la misma senal que el
    rasgo 5 del metodo de promedios, pero SIN promediar: se conserva la forma."""
    if img.ndim == 3:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        g = img
    if ESCALA_PATRON > 1:
        g = cv2.resize(g, (g.shape[1] // ESCALA_PATRON, g.shape[0] // ESCALA_PATRON),
                       interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _ncc_local(A, B, lado):
    """Correlacion normalizada LOCAL entre A y B, para todas las posiciones a la
    vez. Devuelve un mapa del mismo tamano.

    NCC = cov(A,B) / (std(A) * std(B)), con todas las medias calculadas sobre
    ventanas de `lado`. Todo con boxFilter: sin esto habria que llamar a
    matchTemplate decenas de miles de veces por imagen."""
    k = (lado, lado)
    mA = cv2.boxFilter(A, -1, k, normalize=True)
    mB = cv2.boxFilter(B, -1, k, normalize=True)
    mAB = cv2.boxFilter(A * B, -1, k, normalize=True)
    mAA = cv2.boxFilter(A * A, -1, k, normalize=True)
    mBB = cv2.boxFilter(B * B, -1, k, normalize=True)
    cov = mAB - mA * mB
    vA = np.maximum(mAA - mA * mA, 0.0)
    vB = np.maximum(mBB - mB * mB, 0.0)
    return cov / (np.sqrt(vA * vB) + 1e-3)


def _mejor_corr(objetivo, referencias, lado, radio, paso):
    """Mejor correlacion de `objetivo` contra CUALQUIERA de las `referencias`,
    probando desplazamientos (para absorber el movimiento de las piezas).

    Se toma el MAXIMO, no el promedio: cada unidad buena es un ejemplo valido
    de como puede verse ese sitio. Si el patron actual se parece a alguno, es
    normal. Mismo razonamiento que el banco de plantillas del inspector."""
    mejor = None
    for ref in referencias:
        for dy in range(-radio, radio + 1, paso):
            for dx in range(-radio, radio + 1, paso):
                r = np.roll(np.roll(ref, dy, axis=0), dx, axis=1)
                c = _ncc_local(objetivo, r, lado)
                mejor = c if mejor is None else np.maximum(mejor, c)
    return mejor


def entrenar_patron(carpeta, salida, lado=LADO_PATRON, busqueda=BUSQUEDA_PATRON):
    """Construye la linea base del metodo de patron.

    Guarda los mapas de gradiente de las unidades buenas (para poder comparar
    patrones en evaluacion) y, por posicion, la MEDIA y DESVIACION de las
    correlaciones que las buenas alcanzan entre si.

    Ese par media/desviacion por posicion es lo que normaliza el metodo: una
    caida de correlacion no vale lo mismo en una zona estable (donde las buenas
    siempre calzan a 0.90) que en una revuelta (donde ya discrepan a 0.40). Sin
    esta normalizacion, las zonas ruidosas inundarian el mapa de falsos."""
    rutas = _listar(carpeta)
    lado_r = max(8, lado // ESCALA_PATRON)
    radio_r = max(1, busqueda // ESCALA_PATRON)
    print(f"\nLinea base de PATRON con {len(rutas)} imagenes de '{carpeta}'")
    print(f"  parche {lado}px  busqueda +-{busqueda}px  "
          f"(a 1/{ESCALA_PATRON}: {lado_r}px, +-{radio_r}px)")

    grads, forma = [], None
    for i, r in enumerate(rutas, 1):
        img = cv2.imread(r, cv2.IMREAD_COLOR)
        if img is None:
            print(f"  [!] no se pudo leer {os.path.basename(r)}")
            continue
        if forma is None:
            forma = img.shape[:2]
        elif img.shape[:2] != forma:
            sys.exit(f"[ERROR] '{os.path.basename(r)}' mide "
                     f"{img.shape[1]}x{img.shape[0]} y las demas "
                     f"{forma[1]}x{forma[0]}.")
        grads.append(_gradiente(img))
    n = len(grads)
    if n < 5:
        sys.exit(f"[ERROR] Solo {n} imagenes legibles. Hacen falta al menos 5.")

    print(f"  calculando la variacion normal (dejando una fuera)...")
    mapas = []
    for i in range(n):
        otros = [grads[j] for j in range(n) if j != i]
        mapas.append(_mejor_corr(grads[i], otros, lado_r, radio_r, PASO_BUSQUEDA))
        if (i + 1) % 5 == 0 or i + 1 == n:
            print(f"    {i+1}/{n}")
    pila = np.stack(mapas)
    media = pila.mean(axis=0)
    desv = np.maximum(pila.std(axis=0), SUELO_DESV_CORR)

    np.savez_compressed(
        salida,
        metodo=np.array(["patron"]),
        grads=np.stack(grads).astype(np.float16),
        media=media.astype(np.float32), desv=desv.astype(np.float32),
        n=np.array([n]), lado=np.array([lado]), paso=np.array([lado // 2]),
        busqueda=np.array([busqueda]), escala=np.array([ESCALA_PATRON]),
        alto=np.array([forma[0]]), ancho=np.array([forma[1]]))
    print(f"\n[OK] Linea base de patron guardada en '{salida}'")
    print(f"     {n} imagenes | correlacion normal: media {media.mean():.3f}  "
          f"min {media.min():.3f}")
    if n < 30:
        print(f"\n[!] Con {n} imagenes la variacion normal es un estimador "
              "ruidoso. Para uso real apunta a 50+.")


def cargar_base_patron(ruta):
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No existe '{ruta}'. Crea la linea base primero con "
                 "--entrenar --metodo patron")
    d = np.load(ruta)
    if "metodo" not in d or str(d["metodo"][0]) != "patron":
        sys.exit(f"[ERROR] '{ruta}' no es una linea base de patron. "
                 "Usa --metodo promedio o entrena una nueva.")
    return {"grads": [g.astype(np.float32) for g in d["grads"]],
            "media": d["media"], "desv": d["desv"], "n": int(d["n"][0]),
            "lado": int(d["lado"][0]), "paso": int(d["paso"][0]),
            "busqueda": int(d["busqueda"][0]), "escala": int(d["escala"][0]),
            "alto": int(d["alto"][0]), "ancho": int(d["ancho"][0])}


def mapa_patron(img, base):
    """Mapa de anomalia por correlacion de patron, en sigmas.

    Un valor alto significa: 'el patron de gradiente de este sitio no se parece
    a ninguna version conocida de este sitio, mucho mas de lo que las unidades
    buenas difieren entre si aqui'."""
    if img.shape[:2] != (base["alto"], base["ancho"]):
        sys.exit(f"[ERROR] La imagen mide {img.shape[1]}x{img.shape[0]} y la "
                 f"linea base se hizo con {base['ancho']}x{base['alto']}.")
    esc = base["escala"]
    lado_r = max(8, base["lado"] // esc)
    radio_r = max(1, base["busqueda"] // esc)
    g = _gradiente(img)
    corr = _mejor_corr(g, base["grads"], lado_r, radio_r, PASO_BUSQUEDA)
    # solo la CAIDA cuenta: correlacionar mejor de lo normal no es anomalia
    z = np.maximum(base["media"] - corr, 0.0) / base["desv"]
    # muestrear en la rejilla de parches, igual que el metodo de promedios,
    # para que regiones() y panel() funcionen sin cambios
    paso_r = max(1, base["paso"] // esc)
    off = lado_r // 2
    ys = np.arange(off, z.shape[0] - off, paso_r)
    xs = np.arange(off, z.shape[1] - off, paso_r)
    if len(ys) == 0 or len(xs) == 0:
        sys.exit("[ERROR] La imagen es demasiado pequena para el parche.")
    return z[np.ix_(ys, xs)].astype(np.float32)



if __name__ == "__main__":
    main()
