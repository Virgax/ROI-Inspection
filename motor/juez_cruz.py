#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
juez_cruz.py — "Juez 2": confirmacion de presencia de tornillo por la SIMETRIA DE 4
de la cruz Phillips.

Segundo juez que complementa al clasificador de plantillas. En posiciones Phillips
cuyo puntaje cae en la zona dudosa, decide mirando la propiedad que DEFINE a una
cruz Phillips: sus cuatro brazos a 90 grados.

--- Como funciona (simetria de 4 / "cruzness") ---
Recorre el centro del recorte en circulo midiendo la OSCURIDAD por angulo. Una cruz
Phillips tiene 4 ranuras oscuras separadas 90 grados, asi que ese perfil sube y baja
EXACTAMENTE 4 veces por vuelta. Se mide la fuerza de esa componente de frecuencia 4
(via FFT del perfil angular). Ventajas:
  - Invariante a la rotacion: girar la cruz solo cambia la FASE, no la magnitud de la
    frecuencia 4 -> rescata tornillos en cualquier angulo (incluso en X).
  - Especifica de la cruz: un hueco vacio, un borron de sombra o un flex/blindaje al
    lado NO tienen simetria de 4 -> no se confunden con un tornillo (a diferencia de
    la correlacion de plantilla, que se dejaba dominar por el anillo del avellanado,
    o del ratio de energia, que un borron central podia enganar).
  - Tolerante a la iluminacion: es una razon de energias dentro de la misma imagen.
Banda radial ADAPTABLE: la cruz ocupa distinto radio segun el tamano del tornillo
(T01 grande vs T03/T04 chicos). Se prueban varias bandas y se toma la mejor, asi el
metodo se ajusta solo al tamano de cada cruz sin recalibrar.

La firma radial (energia centro vs anillo) se conserva como criterio de RESPALDO y
como guardia de "hay estructura real en el centro" (PISO_CENTRO). El veredicto exige
AMBAS cosas: que haya simetria de cruz Y estructura central suficiente. Asi el error
peligroso (rescatar un hueco como presente) necesita fallar dos pruebas a la vez.

UMBRAL_CRUZNESS y PISO_CENTRO se afinan con diagnostico_juez.py sobre tu calibracion.
"""

import cv2
import numpy as np

# --- zona dudosa: se consulta al juez si el puntaje esta cerca del umbral ---
ZONA_DUDOSA_ABAJO = 12.0
ZONA_DUDOSA_ARRIBA = 6.0

# --- CRITERIO PRINCIPAL: simetria de 4 (afinable con diagnostico_juez.py) ---
# Se fija CONSERVADOR a proposito. Como el juez ahora SOLO RESCATA (nunca veta),
# los dos errores no cuestan igual:
#   - rescatar un hueco = dejar pasar un tornillo faltante  -> GRAVE
#   - no rescatar un tornillo = decide el clasificador solo -> inocuo (es el
#     comportamiento que ya tenias antes de existir el juez)
# Por eso conviene un umbral alto: el juez interviene solo cuando esta muy seguro.
# fraccion de energia del perfil angular en la frecuencia 4 (los 4 brazos).
# tornillo -> alto ; hueco/borron -> bajo. Medido en piezas reales: tornillos
# 0.156-0.262, huecos 0.038-0.082 -> umbral ~0.12 con margen.
UMBRAL_CRUZNESS = 0.145

# --- busqueda del centro de la cruz (el recorte no siempre queda centrado) ---
# rejilla de +-RADIO px con paso PASO, sobre el lienzo de LADO_PERFIL.
RADIO_BUSQUEDA_CENTRO = 5
PASO_BUSQUEDA_CENTRO = 2

# --- GUARDIA de estructura central (respaldo): energia de borde minima en el
#     centro para aceptar el rescate. Es absoluto (depende del contraste de tu
#     camara/iluminacion). Afinable; se re-deriva si cambias luz o encuadre. ---
PISO_CENTRO = 125.0

# --- bandas radiales para la simetria de 4 (fracciones del radio del disco) ---
# se prueban varias y se toma la mejor -> se adapta al tamano de la cruz.
BANDAS_CRUZ = ((0.20, 0.62), (0.28, 0.74), (0.36, 0.88))
N_ANGULOS_PERFIL = 180
LADO_PERFIL = 64

# --- geometria de la firma radial de respaldo (fracciones del radio) ---
LADO_TRABAJO = 44
FRAC_R_CENTRO = 0.50
FRAC_R_ANILLO_IN = 0.62
FRAC_R_ANILLO_OUT = 0.95
UMBRAL_RATIO = 0.30   # solo respaldo / informe

# fraccion central del crop presente que se guarda como plantilla de cruz (calibracion)
FRAC_CENTRAL = 0.6


# ------------------------------ utilidades ---------------------------------

def _gris(img):
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _bordes(img, lado=LADO_TRABAJO):
    g = _gris(img)
    g = cv2.resize(g.astype(np.uint8), (lado, lado), interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


_R_CACHE = {}


def _radios(lado):
    if lado not in _R_CACHE:
        c = lado / 2.0
        yy, xx = np.mgrid[0:lado, 0:lado]
        _R_CACHE[lado] = np.sqrt((yy - c) ** 2 + (xx - c) ** 2)
    return _R_CACHE[lado]


# ---------------------- CRITERIO PRINCIPAL: simetria de 4 -------------------

_GEOM_CACHE = {}


def _geometria(lado):
    """Precalcula (una sola vez) los desplazamientos de muestreo para cada banda.
    Vectorizar esto es critico: el juez corre en el lazo en vivo, y la version con
    bucles de Python tardaba ~170 ms por posicion (inusable). Asi baja a <2 ms."""
    key = lado
    if key not in _GEOM_CACHE:
        ang = np.linspace(0, 2 * np.pi, N_ANGULOS_PERFIL, endpoint=False)
        sa, ca = np.sin(ang), np.cos(ang)
        bandas = []
        for r0, r1 in BANDAS_CRUZ:
            radios = np.linspace(r0 * lado / 2.0, r1 * lado / 2.0, 7)
            # (n_radios, n_angulos)
            dy = radios[:, None] * sa[None, :]
            dx = radios[:, None] * ca[None, :]
            bandas.append((dy, dx))
        _GEOM_CACHE[key] = bandas
    return _GEOM_CACHE[key]


def _perfiles_en(g, cx, cy, lado):
    """Perfiles de oscuridad por angulo (uno por banda) alrededor de (cx, cy).
    Totalmente vectorizado con numpy."""
    salida = []
    for dy, dx in _geometria(lado):
        ys = np.clip((cy + dy).astype(np.int32), 0, lado - 1)
        xs = np.clip((cx + dx).astype(np.int32), 0, lado - 1)
        vals = 255.0 - g[ys, xs]          # (n_radios, n_angulos)
        salida.append(vals.mean(axis=0))  # (n_angulos,)
    return salida


def _cz_desde(g, cx, cy, lado):
    """Simetria de 4 medida desde un centro dado (cx, cy). Toma la mejor banda."""
    mejor = 0.0
    for p in _perfiles_en(g, cx, cy, lado):
        p = p - p.mean()
        F = np.abs(np.fft.rfft(p))
        tot = float(F[1:].sum())
        if tot > 1e-6:
            v = float(F[4] / (tot + 1e-6))
            if v > mejor:
                mejor = v
    return mejor


def cruzness(img, lado=LADO_PERFIL, radio_busqueda=RADIO_BUSQUEDA_CENTRO,
             paso=PASO_BUSQUEDA_CENTRO):
    """Fuerza de la simetria de 4 (los cuatro brazos de la cruz), BUSCANDO el
    centro real de la cruz en vez de asumir que esta en el medio del recorte.

    Por que buscar el centro: el recorte no siempre queda centrado en el tornillo
    (desfase del encuadre/rectificado). Medir los circulos desde un centro
    equivocado rompe la simetria y hunde el valor aunque la cruz sea perfecta
    (medido: la misma cruz daba 0.105 desde el centro geometrico y 0.194 desde su
    centro real, 4 px mas alla). El clasificador principal no sufre esto porque
    matchTemplate DESLIZA la plantilla; aqui se hace el equivalente: se prueba una
    rejilla pequena de centros y se toma el mejor calce.

    Invariante a la rotacion (solo cambia la fase de la frecuencia 4, no su
    magnitud) y adimensional (fraccion de energia), asi que es mucho mas portable
    entre unidades e iluminaciones que una energia absoluta."""
    if img is None:
        return 0.0
    g = cv2.resize(_gris(img).astype(np.uint8), (lado, lado)).astype(np.float32)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    c0 = lado / 2.0
    mejor = 0.0
    for dy in range(-radio_busqueda, radio_busqueda + 1, paso):
        for dx in range(-radio_busqueda, radio_busqueda + 1, paso):
            mejor = max(mejor, _cz_desde(g, c0 + dx, c0 + dy, lado))
    return mejor


# ---------------------- RESPALDO: firma radial ------------------------------

def firma_radial(img, lado=LADO_TRABAJO):
    """(centro, anillo, ratio): energia de borde en el centro (la cruz) vs el
    anillo (borde del alojamiento)."""
    b = _bordes(img, lado)
    R = _radios(lado)
    rc = FRAC_R_CENTRO * (lado / 2.0)
    ri = FRAC_R_ANILLO_IN * (lado / 2.0)
    ro = FRAC_R_ANILLO_OUT * (lado / 2.0)
    centro = float(b[R <= rc].mean())
    anillo = float(b[(R > ri) & (R <= ro)].mean())
    return centro, anillo, centro / max(anillo, 1e-6)


# --------------------------- API del juez ----------------------------------

def extraer_plantilla_cruz(crop_tornillo):
    """Centro (FRAC_CENTRAL) de un crop de tornillo presente -> plantilla de cruz
    (dato de apoyo; el veredicto NO depende de ella). Se usa en calibracion."""
    if crop_tornillo is None or crop_tornillo.size == 0:
        return None
    g = _gris(crop_tornillo)
    h, w = g.shape[:2]
    ch, cw = int(h * FRAC_CENTRAL), int(w * FRAC_CENTRAL)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    centro = g[y0:y0 + ch, x0:x0 + cw]
    return centro.astype(np.uint8) if centro.size else None


def evaluar(crop_actual, tpl_cruz=None):
    """Medicion completa (sin decidir): dict con cruzness, centro, anillo, ratio."""
    if crop_actual is None:
        return {"cruzness": 0.0, "centro": 0.0, "anillo": 0.0, "ratio": 0.0}
    cz = cruzness(crop_actual)
    centro, anillo, ratio = firma_radial(crop_actual)
    return {"cruzness": cz, "centro": centro, "anillo": anillo, "ratio": ratio}


def hay_cruz(crop_actual, tpl_cruz=None, umbral_cruzness=UMBRAL_CRUZNESS,
             piso_centro=PISO_CENTRO, **_ignora):
    """Decide si hay tornillo. Exige AMBAS cosas:
       - simetria de 4 (cruzness) suficiente -> tiene forma de cruz, y
       - estructura central suficiente (centro >= piso) -> no es superficie plana.
    Un tornillo girado da cruzness alto igual (invariante a rotacion). Un hueco,
    borron o pieza vecina no tienen simetria de 4 -> no se rescatan.
    Devuelve (encontrada: bool, cruzness: float, ratio: float). 'cruzness' ocupa el
    lugar de la antigua 'correlacion' en los registros de inspector.py."""
    m = evaluar(crop_actual, tpl_cruz)
    encontrada = (m["cruzness"] >= umbral_cruzness) and (m["centro"] >= piso_centro)
    return encontrada, round(m["cruzness"], 3), round(m["ratio"], 3)


def en_zona_dudosa(puntaje, umbral):
    """True si el puntaje esta cerca del umbral (vale consultar al juez)."""
    return (umbral - ZONA_DUDOSA_ABAJO) <= puntaje <= (umbral + ZONA_DUDOSA_ARRIBA)
