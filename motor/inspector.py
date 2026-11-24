#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inspector de tornillos — Airlink Distribution DR
Sistema de detección de tornillos faltantes en housings (iPhone 15 Pro)
mediante ROIs fijas + alineación por marcadores ArUco.

Modos de uso:
  1) Capturar el frame de referencia (con el housing COMPLETO en el fixture):
       python3 inspector.py --capturar-referencia
     Guarda referencia.png (imagen rectificada). Cárgala en calibrador_roi.html,
     marca los tornillos y exporta config_rois.json a esta misma carpeta.

  2) Calibrar umbrales (guiado, con cámara):
       python3 inspector.py --calibrar
     O con dos fotos rectificadas (housing completo / housing sin tornillos):
       python3 inspector.py --calibrar-fotos completa.png vacia.png

  3) Inspección en vivo:
       python3 inspector.py --vivo

  4) Inspeccionar una foto individual:
       python3 inspector.py --foto imagen.jpg

Opciones: --camara N (índice de cámara, default 0), --config archivo.json
Teclas en modo vivo: Q/ESC salir · S guardar captura anotada
"""

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime

import cv2
import numpy as np

import juez_cruz
# Deteccion de contaminacion (tornillos sueltos y piezas metalicas). Es opcional:
# si faltan sus archivos o ultralytics no esta instalado, el Detector queda
# desactivado y el inspector funciona exactamente igual que sin el.
import contaminacion
# Secuencia de cierre (giftcard -> housing -> display -> registro en SQL Server).
# Igual que contaminacion: si le falta algo (zonas sin marcar, sin configuracion
# de base de datos) queda desactivada y el inspector funciona como siempre.
import cierre
# Banco de evidencia: guarda cada pieza registrada para que un jurado externo
# decida despues que entra a la calibracion. No juzga ni altera el veredicto.
import banco
# Votacion temporal, estabilidad y decision de registro. Vive en su propio
# modulo y SOLO alli: este archivo tuvo su propia copia de la misma logica, la
# aplicacion Qt usaba la de veredicto.py, y esa divergencia ya rompio el cierre
# una vez. Dos implementaciones de lo mismo acaban juzgando distinto segun por
# donde se lance el programa.
import veredicto

# registro de las decisiones tomadas por el Juez 2 en la ultima clasificacion:
# {rid: (estado, correlacion, puntaje)} con estado "rescatado" o "sin_cruz".
# Lo usa el overlay/terminal para mostrar cuando el juez actua.
_JUEZ_ACTIVO = {}

# interruptor global del Juez 2. Se puede alternar en vivo con la tecla J para
# comparar el comportamiento con y sin la confirmacion de cruz. Cuando esta en
# False, el veredicto lo da solo el clasificador principal (banco de plantillas).
_JUEZ_HABILITADO = True


def _toggle_juez():
    """Alterna el Juez 2 (tecla J en vivo) e informa el nuevo estado."""
    global _JUEZ_HABILITADO
    _JUEZ_HABILITADO = not _JUEZ_HABILITADO
    print(f"[i] Juez 2 (confirmacion de cruz) "
          f"{'ACTIVADO' if _JUEZ_HABILITADO else 'DESACTIVADO'}")
    return _JUEZ_HABILITADO

# ----------------------------- constantes -----------------------------------

ARUCO_DICT = cv2.aruco.DICT_4X4_50
IDS_ESQUINAS = [0, 1, 2, 3]        # 0=sup-izq 1=sup-der 2=inf-izq 3=inf-der
LADO_MAX_CANONICO = 3200           # lado mayor de la imagen rectificada
VENTANA = "Inspector Airlink"

# Parametros de la votacion. Se definen en veredicto.py y aqui solo se
# re-exportan, para que quien los lea desde este modulo vea los mismos valores
# que usa la decision de verdad. NO volver a darles valor aqui: dos sitios con
# el mismo numero son dos sitios que un dia dejan de coincidir.
#
# FRAMES_ESTABLE_CAMBIO: frames que debe sostener un veredicto NUEVO para
# registrarse cuando la pieza ya tiene un registro. Es mas exigente que
# FRAMES_ESTABLE a proposito: el primer veredicto de una pieza puede declararse
# rapido, pero un SEGUNDO registro sobre la misma pieza solo se justifica si el
# operario la corrigio de verdad, y una correccion real se sostiene. Con 45
# frames (~3 s) una posicion que oscila entre 'falta' y 'dudoso' nunca alcanza
# el listo, y una correccion si.
FRAMES_VOTO = veredicto.FRAMES_VOTO
VOTOS_PRESENTE = veredicto.VOTOS_PRESENTE
FRAMES_ESTABLE = veredicto.FRAMES_ESTABLE
FRAMES_ESTABLE_CAMBIO = veredicto.FRAMES_ESTABLE_CAMBIO
FRAMES_SIN_MARCADOR_RESET = 30

DIR_RECETAS = "procesos"
ARCHIVO_LOG = "inspecciones_log.csv"     # log unico, compartido, con columna receta

# Columnas del log, en orden. Estan aqui y no dentro de registrar_inspeccion
# porque la cabecera del archivo y las filas que se escriben TIENEN que salir de
# la misma lista: el log acumulo cuatro formatos distintos porque la cabecera se
# escribia una sola vez, al crear el archivo, y nadie la volvia a mirar cuando se
# agregaban columnas. Ver migrar_log.py.
CAMPOS_LOG = ["fecha", "hora", "proceso", "resultado", "faltantes",
              "n_faltantes", "dudosos", "n_dudosos", "n_total", "detalle"]

_aviso_cabecera_dado = False


def _revisar_cabecera_log():
    """Avisa (una vez) si el log existente tiene una cabecera de otra version.

    No reescribe nada ni deja de registrar: perder inspecciones por un problema
    de formato seria mucho peor que un log heterogeneo. Solo lo hace visible,
    que es justo lo que falto antes."""
    global _aviso_cabecera_dado
    if _aviso_cabecera_dado or not os.path.exists(ARCHIVO_LOG):
        return
    _aviso_cabecera_dado = True
    try:
        with open(ARCHIVO_LOG, newline="", encoding="utf-8") as f:
            cab = next(csv.reader(f), None)
    except OSError:
        return
    if cab is not None and cab != CAMPOS_LOG:
        print(f"[!] La cabecera de {ARCHIVO_LOG} tiene {len(cab)} columnas y el "
              f"inspector escribe {len(CAMPOS_LOG)}. Las filas nuevas se "
              f"registran igual, pero cualquier lector que se guie por la "
              f"cabecera (pandas, Excel) leera mal el archivo.\n"
              f"    Corrige con: python3 migrar_log.py --aplicar")

# --- receta activa: todas las rutas de datos cuelgan de recetas/<nombre>/ ---
_RECETA = None                            # nombre de la receta seleccionada


def set_receta(nombre):
    global _RECETA
    _RECETA = nombre


def dir_receta(nombre=None):
    return os.path.join(DIR_RECETAS, nombre or _RECETA)


def ruta_config(nombre=None):
    return os.path.join(dir_receta(nombre), "config_rois.json")


def ruta_umbrales(nombre=None):
    return os.path.join(dir_receta(nombre), "umbrales.json")


def ruta_plantillas(nombre=None):
    return os.path.join(dir_receta(nombre), "plantillas.npz")


def ruta_referencia(nombre=None):
    return os.path.join(dir_receta(nombre), "referencia.png")


def recetas_existentes():
    if not os.path.isdir(DIR_RECETAS):
        return []
    return sorted(d for d in os.listdir(DIR_RECETAS)
                  if os.path.isdir(os.path.join(DIR_RECETAS, d))
                  and not d.startswith("."))

# colores BGR
VERDE = (105, 215, 83)
ROJO = (74, 75, 226)
GRIS = (150, 150, 150)
AMARILLO = (46, 189, 255)
BLANCO = (235, 235, 232)
NEGRO = (17, 14, 13)
PANEL_BG = (37, 31, 28)


# ----------------------------- utilidades -----------------------------------

def beep(ok: bool):
    """Sonido de veredicto, no bloqueante."""
    try:
        if platform.system() == "Darwin":
            snd = "/System/Library/Sounds/Glass.aiff" if ok else "/System/Library/Sounds/Basso.aiff"
            subprocess.Popen(["afplay", snd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def clave(p):
    """Clave INTERNA y unica de una posicion. Es con la que se indexan puntajes,
    plantillas, recortes, votos y el log.

    Por que no basta la etiqueta (`id`): el calibrador permite a proposito que dos
    posiciones lleven la misma etiqueta (hay tornillos que comparten nombre) y les
    asigna un `uid` unico para distinguirlas. Pero el inspector indexaba por
    etiqueta, asi que dos posiciones homonimas se sobrescribian y una no se
    inspeccionaba. El arreglo estaba a medias: existia el `uid` y nadie lo leia.

    La clave se calcula en `cargar_config` y se guarda en `p["_clave"]`:
      - etiqueta UNICA en el config -> la clave ES la etiqueta. Asi las
        calibraciones existentes siguen siendo validas (las claves del .npz y de
        umbrales.json no cambian) y no hace falta recalibrar.
      - etiqueta REPETIDA -> se le anade un sufijo derivado del uid
        (p.ej. `T05#k3f9`), de modo que cada posicion tiene su propio estado.
    El sufijo aparece en el log a proposito: un registro ambiguo ("fallo T05" con
    dos T05) no sirve para trazabilidad."""
    return p.get("_clave") or p["id"]


def _asignar_claves(cfg):
    """Calcula la clave interna de cada posicion (ver `clave`)."""
    conteo = {}
    for p in cfg["puntos"]:
        conteo[p.get("id")] = conteo.get(p.get("id"), 0) + 1
    repetidas = set()
    for p in cfg["puntos"]:
        rid = p.get("id")
        if conteo.get(rid, 0) > 1:
            uid = str(p.get("uid") or "")
            sufijo = uid[-4:] if len(uid) >= 4 else f"{cfg['puntos'].index(p):02d}"
            p["_clave"] = f"{rid}#{sufijo}"
            repetidas.add(rid)
        else:
            p["_clave"] = rid
    if repetidas:
        # verificar que los sufijos realmente desambiguaron
        claves = [p["_clave"] for p in cfg["puntos"]]
        if len(set(claves)) != len(claves):
            sys.exit("[ERROR] Hay posiciones con la misma etiqueta y sin uid unico, "
                     "asi que no se pueden distinguir.\n        Vuelve a exportar el "
                     "config desde calibrador_roi.html (asigna uid a cada posicion).")
        print(f"[i] Etiquetas repetidas: {', '.join(sorted(repetidas))}. "
              f"Cada posicion se evalua por separado con su propia clave "
              f"(p.ej. {sorted(p['_clave'] for p in cfg['puntos'] if p['id'] in repetidas)[0]}).")


def huella_geometria(cfg):
    """Firma corta y estable de la geometria con la que se calibro: posiciones,
    tamanos de ROI y tamano del lienzo rectificado.

    Existe porque nada impedia mover o redimensionar una ROI en el calibrador y
    seguir usando las plantillas viejas: el sistema corria en silencio comparando
    recortes nuevos contra plantillas de otra geometria, sin ningun aviso. Con la
    huella, la calibracion y el config quedan atados y el desajuste se detecta."""
    partes = [f"{int(cfg.get('image_size', [0, 0])[0])}x"
              f"{int(cfg.get('image_size', [0, 0])[1])}"]
    for p in sorted(cfg["puntos"], key=lambda q: str(clave(q))):
        mw, mh = _medidas(p, cfg)
        # 1 decimal en las coordenadas: tolera reescrituras del JSON, detecta
        # cualquier movimiento real de la ROI
        partes.append(f"{clave(p)}:{p['x']:.1f},{p['y']:.1f},{mw * 2}x{mh * 2}")
    return "|".join(partes)


def _validar_geometria(cfg):
    """Comprueba que la geometria de las ROIs sea utilizable ANTES de operar.

    Las etiquetas (id) DEBEN ser unicas: el sistema indexa puntajes, plantillas,
    recortes y votos por id, asi que dos posiciones con la misma etiqueta se
    sobrescriben y una de ellas NUNCA se inspecciona — sin ningun aviso, y
    reportando PASA. Ademas el log registra la posicion por etiqueta, asi que
    etiquetas repetidas harian la trazabilidad ambigua incluso si se arreglara el
    indexado. Es preferible fallar aqui, ruidosamente, que dejar pasar un defecto.

    Tambien avisa (sin abortar) de ROIs que se solapan o que se salen de la imagen
    rectificada, porque ambas cosas contaminan los recortes."""
    puntos = cfg["puntos"]
    ids = [p.get("id") for p in puntos]

    # Las etiquetas duplicadas YA NO son error: se soportan mediante la clave
    # interna (ver `clave`). Solo se exige que exista alguna forma de distinguirlas,
    # lo cual valida `_asignar_claves`.

    # 2) etiquetas vacias o no utilizables
    malas = [i for i, r in enumerate(ids) if not r or not str(r).strip()]
    if malas:
        sys.exit(f"[ERROR] Hay {len(malas)} posicion(es) sin etiqueta. "
                 "Asigna una etiqueta a cada una en el calibrador.")

    # 3) avisos de geometria (no abortan)
    W, H = cfg.get("image_size", (0, 0))
    fuera = []
    for p in puntos:
        mw, mh = _medidas(p, cfg)
        if (p["x"] - mw < 0 or p["y"] - mh < 0
                or (W and p["x"] + mw > W) or (H and p["y"] + mh > H)):
            fuera.append(clave(p))
    if fuera:
        print(f"[!] AVISO: estas ROIs se salen de la imagen rectificada y su recorte "
              f"quedara incompleto: {', '.join(fuera)}")

    solapan = []
    for i in range(len(puntos)):
        for j in range(i + 1, len(puntos)):
            a, b = puntos[i], puntos[j]
            aw, ah = _medidas(a, cfg)
            bw, bh = _medidas(b, cfg)
            # separacion en cada eje; si ambas son negativas, los rectangulos se cruzan
            sx = abs(a["x"] - b["x"]) - (aw + bw)
            sy = abs(a["y"] - b["y"]) - (ah + bh)
            sep = max(sx, sy)
            if sep < 5:
                solapan.append((clave(a), clave(b), sep))
    if solapan:
        print("[!] AVISO: ROIs demasiado juntas (pueden capturar el tornillo vecino "
              "si la pieza se desplaza):")
        for ra, rb, sep in solapan:
            estado = "SE SOLAPAN" if sep < 0 else f"a solo {sep:.1f} px"
            print(f"      {ra} <-> {rb}: {estado}")


def cargar_config(ruta=None):
    ruta = ruta or ruta_config()
    if not os.path.exists(ruta):
        sys.exit(f"[ERROR] No se encontró {ruta}. Marca las posiciones con "
                 f"calibrador_roi.html sobre la referencia de este proceso y exporta ahi.")
    with open(ruta, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("puntos"):
        sys.exit("[ERROR] La configuración no tiene posiciones de tornillos.")

    # Separar las ZONAS de las POSICIONES INSPECCIONABLES.
    #
    # El calibrador permite marcar recuadros que no son una parte que se pone o
    # se quita, sino un area de interes: el giftcard, por ejemplo, que se marca
    # para saber donde leer su codigo. Si se dejaran en `puntos`, el sistema los
    # calibraria como si fueran tornillos, y ahi el metodo se rompe: en la fase
    # "vacio" el giftcard SIGUE puesto, asi que su plantilla de presente y la de
    # vacio serian la misma imagen. Separacion cero, umbral sin sentido, y en
    # produccion esa posicion podria dar "falta" y tumbar a NO PASA una pieza
    # perfecta.
    #
    # Se apartan aqui, al cargar, y no mas adelante, para que TODO lo que viene
    # despues -calibracion, plantillas, puntajes, votacion, huella, log- siga
    # viendo solo lo que de verdad se inspecciona, sin tener que acordarse de
    # excluirlas en cada sitio.
    TIPOS_ZONA = {"giftcard", "lcd", "display", "backglass", "parte"}
    zonas, inspeccionables = {}, []
    for p in cfg["puntos"]:
        if str(p.get("tipo", "")).strip().lower() in TIPOS_ZONA:
            zonas.setdefault(str(p.get("tipo")).strip().lower(), []).append(p)
        else:
            inspeccionables.append(p)
    cfg["zonas"] = zonas
    if zonas:
        cfg["puntos"] = inspeccionables
        detalle = ", ".join(f"{t}: {', '.join(str(q.get('id')) for q in ps)}"
                            for t, ps in sorted(zonas.items()))
        print(f"[i] Zonas marcadas que NO se inspeccionan ({detalle}). "
              f"Quedan {len(inspeccionables)} posiciones inspeccionables.")
        if not inspeccionables:
            sys.exit("[ERROR] El config solo tiene zonas y ninguna posicion que inspeccionar.")

    proc = cfg.get("proceso")
    if proc and _RECETA and proc.strip() and proc.strip() != _RECETA:
        print(f"[!] AVISO: este config fue marcado para el proceso '{proc}', "
              f"pero está en la carpeta de '{_RECETA}'. "
              f"Verifica que no lo guardaste en la carpeta equivocada.")
    _asignar_claves(cfg)
    _validar_geometria(cfg)
    return cfg


def cargar_umbrales():
    if not os.path.exists(ruta_umbrales()):
        return None
    with open(ruta_umbrales(), "r", encoding="utf-8") as f:
        return json.load(f)


RESOLUCIONES = {"4k": (3840, 2160), "1440": (2560, 1440), "1080": (1920, 1080)}


def abrir_camara(indice, resolucion="auto"):
    if platform.system() == "Darwin":
        cap = cv2.VideoCapture(indice, cv2.CAP_AVFOUNDATION)
    else:
        cap = cv2.VideoCapture(indice)
    if not cap.isOpened():
        sys.exit(f"[ERROR] No se pudo abrir la cámara {indice}. "
                 f"Prueba otro índice con --camara 1, --camara 2...")
    # MJPEG: imprescindible para altas resoluciones a buen framerate por USB
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    if resolucion == "auto":
        candidatas = [RESOLUCIONES["4k"], RESOLUCIONES["1440"], RESOLUCIONES["1080"]]
    else:
        candidatas = [RESOLUCIONES[resolucion]]
    real = None
    for w, h in candidatas:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        ok, frame = cap.read()          # el frame real es el veredicto
        if ok and frame is not None and frame.shape[1] >= w * 0.95:
            real = (frame.shape[1], frame.shape[0])
            break
    if real is None:
        ok, frame = cap.read()
        if not ok or frame is None:
            sys.exit(f"[ERROR] La cámara {indice} no entrega video.")
        real = (frame.shape[1], frame.shape[0])
    # buffer minimo: siempre el frame mas reciente (menos latencia)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    print(f"[i] Cámara {indice}: {real[0]}x{real[1]} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps (MJPEG)")
    return cap


# --------------------------- ArUco y rectificado ----------------------------

_detector = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
    cv2.aruco.DetectorParameters())


def detectar_marcadores(frame_gray):
    """Devuelve dict {id: centro (x,y)} de los marcadores detectados."""
    corners, ids, _ = _detector.detectMarkers(frame_gray)
    centros = {}
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            centros[int(i)] = c[0].mean(axis=0)
    return centros


def tam_canonico_desde_centros(centros):
    """Calcula el tamaño rectificado preservando la proporción del fixture."""
    tl, tr, bl, br = (centros[i] for i in IDS_ESQUINAS)
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    esc = min(1.0, LADO_MAX_CANONICO / max(w, h))
    return int(round(w * esc)), int(round(h * esc))


def rectificar(frame, centros, tam):
    """Homografía: centros de marcadores -> esquinas del lienzo canónico."""
    W, H = tam
    src = np.float32([centros[0], centros[1], centros[2], centros[3]])
    dst = np.float32([[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(frame, M, (W, H))


# ------------------------------ clasificador --------------------------------
# Comparacion por plantillas: durante la calibracion se memoriza como se ve
# cada posicion CON tornillo y SIN tornillo (poste vacio). En produccion cada
# ROI se compara contra ambas plantillas con correlacion normalizada (NCC),
# que distingue estructura (cruz phillips vs. rosca) y es robusta a cambios
# leves de iluminacion. Puntaje 0-100: >50 se parece mas al estado "presente".

def extraer_crop(gray, x, y, mitad_w, mitad_h=None):
    if mitad_h is None:
        mitad_h = mitad_w
    h, w = gray.shape[:2]
    x0, x1 = int(round(x - mitad_w)), int(round(x + mitad_w))
    y0, y1 = int(round(y - mitad_h)), int(round(y + mitad_h))
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    return gray[y0:y1, x0:x1]


def _recorte_central(img):
    """Devuelve la region central (70% por eje) de UNA imagen, sin promediar."""
    h, w = img.shape[:2]
    th = max(8, int(h * 0.7))
    tw = max(8, int(w * 0.7))
    y0 = (h - th) // 2
    x0 = (w - tw) // 2
    return img[y0:y0 + th, x0:x0 + tw].astype(np.uint8)


def _promediar_muestras(crops):
    """Promedia los N frames capturados de UNA MISMA unidad (el tornillo esta
    quieto entre frames, asi que promediar reduce ruido SIN borronear la cruz).
    Devuelve una sola imagen. Es distinto de promediar entre unidades distintas
    (eso si borronearia, porque la cruz cambia de angulo entre unidades)."""
    if len(crops) == 1:
        return crops[0].astype(np.uint8)
    return np.mean(np.stack(crops).astype(np.float32), axis=0).astype(np.uint8)


def _muestra_mas_nitida(banco):
    """Del banco (N,h,w), devuelve la muestra individual mas nitida (mayor
    varianza de Laplaciano). Se usa para capturar la plantilla de cruz del
    Juez 2 de una toma definida, en vez del promedio borroso."""
    if banco.ndim == 2:
        return banco
    mejor, mejor_n = banco[0], -1.0
    for i in range(banco.shape[0]):
        n = float(cv2.Laplacian(banco[i], cv2.CV_64F).var())
        if n > mejor_n:
            mejor_n, mejor = n, banco[i]
    return mejor


def _plantilla_central(crops):
    """(LEGADO) Promedia una lista de crops y devuelve su region central.
    Se conserva para compatibilidad con calibraciones antiguas de plantilla unica.
    El motor nuevo usa banco de plantillas individuales (ver _banco_plantillas)."""
    m = np.mean(np.stack(crops).astype(np.float32), axis=0).astype(np.uint8)
    return _recorte_central(m)


def _apilar_crops(crops):
    """Apila recortes CRUDOS (ROI completa) en un array (N,h,w), unificando el
    tamano al menor comun por si algun recorte difiere en 1 px. Se usa para
    guardar los recortes originales y poder agregar unidades luego."""
    hmin = min(c.shape[0] for c in crops)
    wmin = min(c.shape[1] for c in crops)
    return np.stack([c[:hmin, :wmin] for c in crops]).astype(np.uint8)


def _banco_plantillas(crops):
    """Convierte una lista de crops en un BANCO de plantillas individuales,
    cada una recortada a su region central y SIN promediar. Asi cada plantilla
    conserva su detalle nitido (la cruz no se difumina). Devuelve un array
    apilado (N, h, w)."""
    recortes = [_recorte_central(c) for c in crops]
    # unificar tamano (por si algun crop difiere por 1 px) al menor comun
    hmin = min(r.shape[0] for r in recortes)
    wmin = min(r.shape[1] for r in recortes)
    recortes = [r[:hmin, :wmin] for r in recortes]
    return np.stack(recortes).astype(np.uint8)



def _similitud(crop, tpl):
    """Similitud 0-1 entre crop y UNA plantilla: correlacion estructural (NCC)
    combinada con cercania absoluta (SQDIFF), robusta tambien en zonas lisas
    donde la NCC pura se vuelve ruido."""
    ncc = float(cv2.matchTemplate(crop, tpl, cv2.TM_CCOEFF_NORMED).max())
    sq = float(cv2.matchTemplate(crop, tpl, cv2.TM_SQDIFF_NORMED).min())
    return 0.5 * max(ncc, 0.0) + 0.5 * (1.0 - min(sq, 1.0))


def _similitud_banco(crop, banco):
    """Similitud 0-1 entre crop y un BANCO de plantillas (array (N,h,w) o una
    sola plantilla 2D). Compara el crop contra cada plantilla individual y
    devuelve el MEJOR parecido de todas.

    Razon del maximo (no promedio): cada plantilla del banco es un ejemplo REAL
    y valido de ese estado (tornillo o hueco). Si el crop en vivo se parece mucho
    a CUALQUIERA de esos ejemplos, es evidencia legitima de que pertenece a ese
    estado. No se descarta ninguna coincidencia: no hay forma de saber a priori
    a cual unidad de calibracion se parecera un telefono real en vivo, asi que
    penalizar la mejor coincidencia asumiendola 'anomala' descartaria informacion
    valida. El maximo responde la pregunta correcta: '¿se parece a alguna
    variacion conocida?'. Se promedian los RESULTADOS por estado, nunca las
    imagenes, para no difuminar el detalle (la cruz)."""
    if crop is None:
        return 0.0
    # normalizar a lista de plantillas 2D
    if banco.ndim == 2:
        plantillas = [banco]
    else:
        plantillas = [banco[i] for i in range(banco.shape[0])]
    return max(_similitud(crop, t) for t in plantillas)


def puntaje_roi(crop, banco_p, banco_a):
    """Devuelve (puntaje, sim_presente).
    banco_p / banco_a pueden ser un banco (N,h,w) o una plantilla unica 2D
    (compatibilidad con calibraciones antiguas).
    puntaje 0-100: >50 el crop se parece mas a 'presente' que a 'vacio'.
    sim_presente 0-1: parecido ABSOLUTO al tornillo (para el piso de similitud)."""
    if crop is None:
        return 0.0, 0.0
    cp = _similitud_banco(crop, banco_p)
    ca = _similitud_banco(crop, banco_a)
    return 50.0 + 50.0 * (cp - ca), cp


def puntaje_simple(crop, tpl_p, tpl_a):
    """Solo el puntaje 0-100 (para calibracion, donde no se usa el piso)."""
    return puntaje_roi(crop, tpl_p, tpl_a)[0]


def _medidas(p, cfg):
    """(mitad_ancho, mitad_alto) de la ROI del punto.
    Acepta roi_w/roi_h (rectangular), roi (legado, cuadrado) o el global."""
    w = p.get("roi_w") or p.get("roi_h") or p.get("roi") or cfg["roi_size"]
    h = p.get("roi_h") or p.get("roi_w") or p.get("roi") or cfg["roi_size"]
    return float(w) / 2.0, float(h) / 2.0


def extraer_todos(bgr, cfg):
    """Convierte a gris UNA vez y extrae los recortes de todas las posiciones UNA
    vez. Antes se hacia dos veces por frame (puntuar_todos + crops_todos), lo que
    duplicaba la conversion de una imagen de varios megapixeles (~2.7 ms cada una)
    y los 23 recortes. Devuelve el diccionario de recortes en gris."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    crops = {}
    for p in cfg["puntos"]:
        mw, mh = _medidas(p, cfg)
        crops[clave(p)] = extraer_crop(gray, p["x"], p["y"], mw, mh)
    return crops


def puntuar_crops(crops, cfg, plantillas):
    """Puntua recortes ya extraidos. Devuelve (scores, sims) donde sims[id] es el
    parecido absoluto al tornillo."""
    scores, sims = {}, {}
    for p in cfg["puntos"]:
        rid = clave(p)
        crop = crops.get(rid)
        if crop is None or crop.size == 0:
            scores[rid], sims[rid] = 0.0, 0.0
            continue
        tpl_p, tpl_a = plantillas[rid]
        sc, sp = puntaje_roi(crop, tpl_p, tpl_a)
        scores[rid] = sc
        sims[rid] = sp
    return scores, sims


def puntuar_todos(bgr, cfg, plantillas):
    """Compatibilidad: extrae y puntua en un paso."""
    return puntuar_crops(extraer_todos(bgr, cfg), cfg, plantillas)


def crops_todos(bgr, cfg):
    """Compatibilidad: alias de extraer_todos."""
    return extraer_todos(bgr, cfg)


def cargar_plantillas(cfg):
    if not os.path.exists(ruta_plantillas()):
        return None
    data = np.load(ruta_plantillas())
    plantillas = {}
    for p in cfg["puntos"]:
        k = clave(p)
        kp, ka = k + "_p", k + "_a"
        if kp not in data or ka not in data:
            return None
        plantillas[k] = (data[kp], data[ka])
        # Juez 2: plantilla de cruz Phillips (si existe para esta posicion)
        kc = k + "_cruz"
        if kc in data:
            plantillas[k + "_cruz"] = data[kc]
    return plantillas


def clasificar(scores, umbrales, sims=None, crops=None, plantillas=None):
    """Devuelve {id: estado} donde estado es:
       True  = presente
       False = faltante
       None  = 'no reconocido' (amarillo): no se parece ni a tornillo ni a hueco
               conocido (piso de similitud absoluta no alcanzado).
    El estado None solo se emite si hay sims y la receta tiene pisos calibrados.

    JUEZ 2 (cruz Phillips) — SOLO RESCATE: se consulta unicamente cuando el
    clasificador principal dice FALTANTE y el puntaje quedo cerca del umbral.
    Si halla la cruz, rescata a presente (recupera falsos faltantes). Si NO la
    halla, no interviene: manda el clasificador. El juez nunca puede degradar
    una posicion que el clasificador ya aprobo."""
    glob = umbrales.get("umbral_global", 50.0)
    por_roi = umbrales.get("por_roi", {})
    out = {}
    _JUEZ_ACTIVO.clear()   # registro de decisiones del Juez 2 en esta clasificacion
    for rid, s in scores.items():
        info = por_roi.get(rid, {})
        umbral = info.get("umbral", glob)
        piso = info.get("piso_similitud")
        presente = s >= umbral

        # ---- Juez 2: SOLO RESCATE (nunca veta) ----
        # Se consulta unicamente si el clasificador principal dice FALTANTE y el
        # puntaje quedo cerca del umbral. Si el juez halla la cruz, rescata a
        # presente. Si NO la halla, NO hace nada: manda el clasificador. Asi el
        # juez solo puede ayudar, nunca degradar una posicion que ya iba bien
        # (antes vetaba posiciones aprobadas, p.ej. T19 con 54/50 -> rojo).
        if (_JUEZ_HABILITADO and not presente and info.get("phillips")
                and crops is not None and plantillas is not None
                and juez_cruz.en_zona_dudosa(s, umbral)):
            tpl_cruz = plantillas.get(rid + "_cruz")
            crop = crops.get(rid)
            if tpl_cruz is not None and crop is not None:
                encontrada, corr, _ = juez_cruz.hay_cruz(crop, tpl_cruz)
                if encontrada:
                    # la cruz esta presente -> es un tornillo real, rescatar.
                    # NO se hace 'continue': el rescate tambien debe pasar por el
                    # piso de similitud de abajo. Antes lo saltaba, asi que una
                    # posicion rescatada se libraba de la red de seguridad que
                    # detecta oclusores/objetos extranos.
                    _JUEZ_ACTIVO[rid] = ("rescatado", round(corr, 3), round(s, 1))
                    presente = True
                else:
                    # no hallo la cruz: se registra para el overlay/log, pero NO
                    # se fuerza el veredicto. Manda el clasificador.
                    _JUEZ_ACTIVO[rid] = ("no_rescatado", round(corr, 3), round(s, 1))

        # piso: si el sistema DICE presente (por el clasificador O por rescate del
        # Juez 2) pero el parecido absoluto al tornillo es demasiado bajo, no es un
        # tornillo real -> "dudoso" (amarillo)
        if presente and sims is not None and piso is not None:
            if sims.get(rid, 1.0) < piso:
                out[rid] = "dudoso"
                continue
        out[rid] = presente
    return out


# ------------------------------- overlay ------------------------------------

def dibujar_overlay(vista, cfg, presencia, estado_txt):
    # factor de escala: todo lo dibujado crece con el lienzo (1600 px = 1.0)
    e = max(1.0, vista.shape[1] / 1600.0)
    g = max(2, int(round(3 * e)))          # grosor de trazos
    faltantes = []
    dudosos = []
    for p in cfg["puntos"]:
        mw, mh = _medidas(p, cfg)
        mw, mh = int(mw), int(mh)
        x, y = int(p["x"]), int(p["y"])
        pres = presencia.get(clave(p))
        if pres == "dudoso":
            color = AMARILLO
            dudosos.append(clave(p))
        elif pres is None:
            color = GRIS
        elif pres:
            color = VERDE
        else:
            color = ROJO
            faltantes.append(clave(p))
        rectangular = abs(mw - mh) > 3
        r = max(int(10 * e), min(mw, mh) // 2)
        if rectangular:
            cv2.rectangle(vista, (x - mw, y - mh), (x + mw, y + mh), color, g)
        else:
            cv2.circle(vista, (x, y), r, color, g)
        if pres is False:
            d = int(r * 0.55)
            cv2.line(vista, (x - d, y - d), (x + d, y + d), color, g)
            cv2.line(vista, (x - d, y + d), (x + d, y - d), color, g)
            tx = x + (mw if rectangular else r) + int(6 * e)
            cv2.putText(vista, p["id"], (tx, y + int(5 * e)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8 * e, ROJO,
                        max(2, int(round(2 * e))), cv2.LINE_AA)
        elif pres == "dudoso":
            tx = x + (mw if rectangular else r) + int(6 * e)
            cv2.putText(vista, p["id"] + " ?", (tx, y + int(5 * e)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8 * e, AMARILLO,
                        max(2, int(round(2 * e))), cv2.LINE_AA)
    # banner inferior
    h, w = vista.shape[:2]
    if estado_txt:
        alto_b = int(46 * e)
        cv2.rectangle(vista, (0, h - alto_b), (w, h), NEGRO, -1)
        cv2.putText(vista, estado_txt, (int(16 * e), h - int(14 * e)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9 * e, BLANCO,
                    max(2, int(round(2 * e))), cv2.LINE_AA)
    return faltantes, dudosos


def dibujar_panel(alto, cfg, presencia, estable, scores):
    """Panel lateral con veredicto, conteo y lista de faltantes.
    Todas las medidas escalan con la altura (900 px = 1.0), asi el panel se ve
    identico en cualquier resolucion sin deformarse."""
    e = alto / 900.0
    ancho = int(320 * e)
    panel = np.full((alto, ancho, 3), PANEL_BG, np.uint8)

    def s_(v):
        return int(v * e)

    def txt(s, y, escala=0.7, color=BLANCO, grosor=2, x=18):
        cv2.putText(panel, s, (s_(x), s_(y)), cv2.FONT_HERSHEY_SIMPLEX,
                    escala * e, color, max(1, int(round(grosor * e))),
                    cv2.LINE_AA)

    txt(cfg.get("modelo", "Housing"), 40, 0.8)
    sub = f"{len(cfg['puntos'])} posiciones"
    if _RECETA:
        sub = f"{_RECETA}  -  {sub}"
    txt(sub, 68, 0.55, GRIS, 1)

    total = len(cfg["puntos"])
    n_pres = sum(1 for v in presencia.values() if v is True)
    n_falt = sum(1 for v in presencia.values() if v is False)
    n_dud = sum(1 for v in presencia.values() if v == "dudoso")

    if any(v is None for v in presencia.values()) or not presencia:
        cv2.rectangle(panel, (s_(18), s_(90)), (s_(302), s_(150)), GRIS,
                      max(2, s_(2)))
        txt("ANALIZANDO...", 128, 0.85, GRIS)
    elif n_pres == 0 and n_dud == 0:
        cv2.rectangle(panel, (s_(18), s_(90)), (s_(302), s_(150)), GRIS,
                      max(2, s_(2)))
        txt("ESPERANDO PIEZA", 128, 0.8, GRIS)
    elif n_falt == 0 and n_dud == 0:
        cv2.rectangle(panel, (s_(18), s_(90)), (s_(302), s_(150)), VERDE, -1)
        txt("PASA", 132, 1.1, NEGRO, 3, x=105)
    elif n_falt > 0:
        cv2.rectangle(panel, (s_(18), s_(90)), (s_(302), s_(150)), ROJO, -1)
        txt("NO PASA", 132, 1.1, BLANCO, 3, x=75)
    else:
        cv2.rectangle(panel, (s_(18), s_(90)), (s_(302), s_(150)), AMARILLO, -1)
        txt("VERIFICAR", 132, 0.95, NEGRO, 3, x=70)

    txt(f"Presentes: {n_pres}/{total}", 195, 0.7, VERDE)
    txt(f"Faltantes: {n_falt}", 225, 0.7, ROJO if n_falt else GRIS)
    if n_dud:
        txt(f"Verificar: {n_dud}", 255, 0.7, AMARILLO)
        txt("estable" if estable else "midiendo...", 285, 0.5,
            VERDE if estable else AMARILLO, 1)
        y = 328
    else:
        txt("estable" if estable else "midiendo...", 258, 0.5,
            VERDE if estable else AMARILLO, 1)
        y = 305

    if n_falt or n_dud:
        etiqueta = "Faltantes / a verificar:"
        txt(etiqueta, y, 0.55, GRIS, 1)
        y += 30
        for rid, v in presencia.items():
            if v is False and y * e < alto - s_(60):
                sc = scores.get(rid)
                extra = f"  ({sc:.0f})" if sc is not None else ""
                txt(f"- {rid}{extra}", y, 0.65, ROJO)
                y += 30
        for rid, v in presencia.items():
            if v == "dudoso" and y * e < alto - s_(60):
                sc = scores.get(rid)
                extra = f"  ({sc:.0f})" if sc is not None else ""
                txt(f"? {rid}{extra}", y, 0.65, AMARILLO)
                y += 30
    cv2.putText(panel, "Q salir | S captura | C limpia | R proceso", (s_(18), alto - s_(20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44 * e, GRIS,
                max(1, int(round(e))), cv2.LINE_AA)
    return panel


def componer_vista(warped, cfg, presencia, estable, scores, alto_max=760):
    """Reduce el lienzo a la altura de pantalla y construye el panel a esa
    altura exacta (nunca se deforma)."""
    h = warped.shape[0]
    if h > alto_max:
        esc = alto_max / h
        warped = cv2.resize(warped, (int(warped.shape[1] * esc), alto_max),
                            interpolation=cv2.INTER_AREA)
    panel = dibujar_panel(warped.shape[0], cfg, presencia, estable, scores)
    return np.hstack([warped, panel])


# ------------------------------- registro -----------------------------------

def registrar_inspeccion(resultado, faltantes, total, scores=None, presencia=None,
                         umbrales=None, sims=None, dudosos=None, hist=None):
    """Registra la inspeccion en el CSV.

    Ademas del veredicto, guarda el DETALLE por posicion en la columna 'detalle':
        T01:62.3/49.9:0.71:ok
        ^id  ^puntaje/umbral ^similitud ^estado
    El puntaje y la similitud son la MEDIANA de la ventana de votacion (no el
    ultimo frame), para que concuerden con el veredicto que sale de esa ventana.
    Faltantes y dudosos van en columnas SEPARADAS: antes se concatenaban y
    'n_faltantes' contaba tambien los dudosos.
    Sirve para auditar despues cualquier unidad rara sin reproducirla: con solo
    saber que posiciones fallaron no se puede distinguir un tornillo realmente
    ausente de una falsa alarma, ni ver con cuanto margen se decidio.

    La SIMILITUD (parecido absoluto al tornillo, 0-1) se registra porque es el
    dato que hoy nadie tiene: el 'piso_similitud' de la calibracion sale siempre
    0.55 por construccion (cada muestra se compara contra el banco que la
    contiene, dando 1.0), asi que no se sabe donde cae la similitud de unidades
    LEGITIMAS en produccion. Acumulando esta columna unos dias, el piso se puede
    fijar con evidencia real en vez de a ojo. Estados: ok / falta / dudoso,
    con sufijo '+J' si el Juez 2 rescato la posicion."""
    faltantes = list(faltantes)
    dudosos = list(dudosos or [])
    nuevo = not os.path.exists(ARCHIVO_LOG)
    if not nuevo:
        _revisar_cabecera_log()
    with open(ARCHIVO_LOG, "a", newline="", encoding="utf-8") as f:
        wcsv = csv.writer(f)
        if nuevo:
            wcsv.writerow(CAMPOS_LOG)
        detalle = ""
        if scores:
            glob = (umbrales or {}).get("umbral_global", 50.0)
            por_roi = (umbrales or {}).get("por_roi", {})
            partes = []
            for rid in sorted(scores):
                umb = por_roi.get(rid, {}).get("umbral", glob)
                est = (presencia or {}).get(rid)
                etq = ("ok" if est is True else
                       "falta" if est is False else
                       "dudoso" if est == "dudoso" else "?")
                # marca del Juez 2 si intervino en esta posicion
                if rid in _JUEZ_ACTIVO and _JUEZ_ACTIVO[rid][0] == "rescatado":
                    etq += "+J"
                # El veredicto sale de VOTAR una ventana de frames, asi que el
                # puntaje del ultimo frame puede contradecirlo (p.ej. estado 'ok'
                # con un puntaje bajo el umbral). Cuando hay historial se registra
                # la MEDIANA de la ventana, que si representa la decision.
                pu = scores[rid]
                sm = (sims or {}).get(rid)
                if hist and rid in hist and hist[rid]:
                    vals = sorted(v for v, _ in hist[rid])
                    pu = vals[len(vals) // 2]
                    sv = sorted(t for _, t in hist[rid] if t is not None)
                    if sv:
                        sm = sv[len(sv) // 2]
                txt_sim = f"{sm:.2f}" if sm is not None else "-"
                partes.append(f"{rid}:{pu:.1f}/{umb:.1f}:{txt_sim}:{etq}")
            detalle = ";".join(partes)
        ahora = datetime.now()
        wcsv.writerow([ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M:%S"),
                       _RECETA, resultado, ";".join(faltantes), len(faltantes),
                       ";".join(dudosos), len(dudosos), total, detalle])


# ------------------------------ modos ---------------------------------------

def modo_capturar_referencia(args):
    cap = abrir_camara(args.camara, args.resolucion)
    print("[i] Coloca el housing COMPLETO en el fixture con los 4 marcadores visibles.")
    print("[i] ESPACIO = guardar referencia rectificada  |  Q/ESC = salir")
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        centros = detectar_marcadores(gray)
        vista = frame.copy()
        for i, c in centros.items():
            cv2.circle(vista, tuple(c.astype(int)), 10, VERDE, 2)
            cv2.putText(vista, str(i), tuple((c + 14).astype(int)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, VERDE, 2)
        completos = all(i in centros for i in IDS_ESQUINAS)
        msg = "ESPACIO para capturar" if completos else \
              f"Marcadores visibles: {sum(i in centros for i in IDS_ESQUINAS)}/4"
        cv2.putText(vista, msg, (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    VERDE if completos else AMARILLO, 2, cv2.LINE_AA)
        cv2.imshow(VENTANA, vista)
        k = cv2.waitKey(1) & 0xFF
        if k in (27, ord("q")):
            break
        if k == 32 and completos:
            tam = tam_canonico_desde_centros(centros)
            ref = rectificar(frame, centros, tam)
            os.makedirs(dir_receta(), exist_ok=True)
            cv2.imwrite(ruta_referencia(), ref)
            print(f"[OK] referencia de '{_RECETA}' guardada ({tam[0]}x{tam[1]} px) "
                  f"en {ruta_referencia()}.")
            print("[->] Cárgala en calibrador_roi.html, marca los tornillos y "
                  "exporta config_rois.json aquí. Luego corre --calibrar.")
            break
    cap.release()
    cv2.destroyAllWindows()


def _muestrear(cap, cfg, n_frames, etiqueta):
    """Toma n_frames validos y devuelve {roi_id: [crops]}."""
    print(f"[i] Midiendo '{etiqueta}'... mantén la pieza quieta.")
    tam = tuple(cfg["image_size"])
    acumulado = {clave(p): [] for p in cfg["puntos"]}
    tomados = 0
    while tomados < n_frames:
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        centros = detectar_marcadores(gray)
        if not all(i in centros for i in IDS_ESQUINAS):
            n = sum(i in centros for i in IDS_ESQUINAS)
            vista = _vista_pantalla(frame.copy())
            cv2.putText(vista, f"Marcadores visibles: {n}/4 - despeja la vista",
                        (16, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, AMARILLO, 2,
                        cv2.LINE_AA)
            cv2.imshow(VENTANA, vista)
            cv2.waitKey(1)
            continue
        warped = rectificar(frame, centros, tam)
        crops = crops_todos(warped, cfg)
        if any(c is None for c in crops.values()):
            continue
        for rid, c in crops.items():
            acumulado[rid].append(c)
        tomados += 1
        vista = _vista_pantalla(warped)
        cv2.putText(vista, f"Muestreando {tomados}/{n_frames}", (16, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, VERDE, 2, cv2.LINE_AA)
        cv2.imshow(VENTANA, vista)
        cv2.waitKey(1)
    return acumulado


def _vista_pantalla(img, max_w=1600):
    if img.shape[1] > max_w:
        esc = max_w / img.shape[1]
        return cv2.resize(img, (max_w, int(img.shape[0] * esc)),
                          interpolation=cv2.INTER_AREA)
    return img


def _esperar_espacio(cap, mensaje, espera_min=2.0):
    print(f"[i] {mensaje}  (ESPACIO para continuar, Q para cancelar)")
    # drenar teclas que hayan quedado en el buffer (evita saltarse fases)
    for _ in range(10):
        cv2.waitKey(20)
    t0 = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        vista = _vista_pantalla(frame.copy())
        restante = espera_min - (time.time() - t0)
        txt = mensaje if restante <= 0 else f"{mensaje}  [espera {restante:.0f}s...]"
        cv2.putText(vista, txt, (16, 40), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, AMARILLO, 2, cv2.LINE_AA)
        cv2.imshow(VENTANA, vista)
        k = cv2.waitKey(1) & 0xFF
        if k == 32 and restante <= 0:
            return True
        if k in (27, ord("q")):
            return False


def _guardar_calibracion(cfg, crops_p, crops_a, n_unidades=1, n_vacios=1):
    """Construye plantillas por ROI y calcula umbrales + piso de similitud.
    El umbral se sesga a NO generar falsos faltantes (bajo el peor presente).
    El piso de similitud detecta 'no reconocido': un estado que no se parece
    ni al tornillo ni a un hueco conocido (oclusores, huecos atipicos)."""
    plantillas, por_roi, seps = {}, {}, []
    print(f"\n  (calibrado con {n_unidades} unidad(es) presente, {n_vacios} vacia(s))")
    print("  ROI     presente   vacio    umbral   separacion   margen   piso")
    for p in cfg["puntos"]:
        rid = clave(p)
        # BANCO de plantillas: guardar las N muestras individuales (nitidas),
        # en vez de una sola promediada (que difuminaba el detalle / la cruz).
        banco_p = _banco_plantillas(crops_p[rid])
        banco_a = _banco_plantillas(crops_a[rid])
        plantillas[rid + "_p"] = banco_p
        plantillas[rid + "_a"] = banco_a
        # RECORTES CRUDOS (ROI completa, sin recortar al 70%): se guardan para
        # poder AGREGAR unidades despues sin repetir toda la calibracion
        # (--agregar-unidad). Son necesarios porque recalcular umbrales exige que
        # el recorte sea mas grande que la plantilla, para que matchTemplate pueda
        # deslizarla; las plantillas guardadas ya vienen recortadas y no sirven
        # para eso. Ocupan poco (unos pocos KB por posicion y unidad).
        plantillas[rid + "_pc"] = _apilar_crops(crops_p[rid])
        plantillas[rid + "_ac"] = _apilar_crops(crops_a[rid])
        # Juez 2: si es Phillips, capturar la plantilla de cruz de la muestra
        # presente MAS NITIDA (no del promedio), para que la cruz quede definida.
        if p.get("phillips"):
            mejor = _muestra_mas_nitida(banco_p)
            tpl_cruz = juez_cruz.extraer_plantilla_cruz(mejor)
            if tpl_cruz is not None:
                plantillas[rid + "_cruz"] = tpl_cruz
        # puntaje y similitud absoluta de cada muestra (contra el banco)
        rp = [puntaje_roi(c, banco_p, banco_a) for c in crops_p[rid]]
        ra = [puntaje_roi(c, banco_p, banco_a) for c in crops_a[rid]]
        sp = [r[0] for r in rp]           # puntajes presente
        sa = [r[0] for r in ra]           # puntajes vacio
        sim_pres = [r[1] for r in rp]     # similitud absoluta al tornillo (presente)
        mp, ma = float(np.mean(sp)), float(np.mean(sa))
        peor_pres = float(np.min(sp))
        mejor_vac = float(np.max(sa))
        if peor_pres > mejor_vac:
            umbral = round((peor_pres + mejor_vac) / 2, 1)
        else:
            umbral = round((mp + ma) / 2, 1)
        margen = round(peor_pres - umbral, 1)
        sep = round(mp - ma, 1)
        seps.append(sep)
        # Piso de similitud absoluta: red de seguridad de ULTIMO RECURSO para
        # detectar algo claramente anomalo (oclusor, objeto extrano) que puntua
        # como presente sin serlo. Se calibra MUY TOLERANTE a proposito: la
        # similitud de calibracion esta inflada (cada muestra se compara contra
        # el banco que la contiene, dando ~1.0), y un tornillo real en vivo
        # siempre da menos. Con holgura amplia (0.45) el piso solo dispara ante
        # algo drasticamente distinto de un tornillo, no ante variacion normal.
        # Asi se evitan los falsos 'dudoso' (amarillo) en tornillos legitimos.
        # Piso de similitud absoluta: red de seguridad de ULTIMO RECURSO para
        # detectar algo claramente anomalo (oclusor, objeto extrano) que puntua
        # como presente sin serlo.
        #
        # OJO: este numero esta INFLADO. Cada muestra se compara contra un banco
        # que la contiene, asi que se encuentra a si misma y da ~1.0, y el piso
        # sale 0.55 para todas las posiciones, siempre. Es una constante
        # disfrazada de valor calibrado.
        #
        # Se probo medirlo dejando-uno-fuera (cada muestra contra el banco sin
        # ella). Es la magnitud correcta, pero el resultado fue peor: entre las
        # unidades de calibracion la similitud sale de 0.70 a 0.97, asi que el
        # piso SUBIRIA a ~0.72, cuando las piezas de linea rondan 0.28. Ese
        # abismo -unidades de calibracion muy parecidas entre si, piezas reales
        # nada parecidas a ellas- no lo arregla la formula del piso: apunta a que
        # la pieza no se asienta igual, o a que el banco no representa lo que
        # viene por la linea. Hasta entenderlo, se deja el valor tolerante.
        peor_sim_pres = min(sim_pres)
        piso = round(max(0.0, peor_sim_pres - 0.45), 3)
        por_roi[rid] = {"umbral": umbral, "media_presente": round(mp, 1),
                        "media_vacio": round(ma, 1), "separacion": sep,
                        "peor_presente": round(peor_pres, 1),
                        "mejor_vacio": round(mejor_vac, 1),
                        "piso_similitud": piso,
                        "phillips": bool(p.get("phillips"))}
        alerta = ""
        if peor_pres <= mejor_vac:
            alerta = "  <-- SOLAPE (presente y vacio se cruzan)"
        elif sep < 15:
            alerta = "  <-- REVISAR (separacion baja)"
        print(f"  {rid:6s}  {mp:8.1f} {ma:8.1f} {umbral:8.1f} {sep:10.1f} "
              f"{margen:8.1f} {piso:6.2f}{alerta}")
    np.savez(ruta_plantillas(), **plantillas)
    data = {"creado": datetime.now().isoformat(timespec="seconds"),
            "metodo": "plantillas_ncc",
            "huella_geometria": huella_geometria(cfg),
            "n_unidades": n_unidades,
            "n_vacios": n_vacios,
            "umbral_global": round(float(np.mean([v['umbral'] for v in por_roi.values()])), 1),
            "por_roi": por_roi}
    with open(ruta_umbrales(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Calibracion de '{_RECETA}' guardada "
          f"({n_unidades} unidad(es) presente, {n_vacios} vacia(s)). "
          f"Separacion promedio: {np.mean(seps):.1f} puntos "
          f"(>=30 excelente, <15 revisar).")


def _cargar_crops_guardados(cfg):
    """Carga los recortes crudos de una calibracion previa. Devuelve
    (crops_p, crops_a, n_unidades, n_vacios) o None si la calibracion no los
    tiene (calibraciones hechas antes de esta funcion)."""
    if not os.path.exists(ruta_plantillas()):
        return None
    data = np.load(ruta_plantillas())
    crops_p, crops_a = {}, {}
    for p in cfg["puntos"]:
        rid = clave(p)
        kp, ka = rid + "_pc", rid + "_ac"
        if kp not in data or ka not in data:
            return None          # calibracion antigua: sin recortes crudos
        bp, ba = data[kp], data[ka]
        crops_p[rid] = [bp[i].astype(np.uint8) for i in range(bp.shape[0])]
        crops_a[rid] = [ba[i].astype(np.uint8) for i in range(ba.shape[0])]
    if not crops_p:
        return None
    algun = next(iter(crops_p))
    return crops_p, crops_a, len(crops_p[algun]), len(crops_a[algun])


def modo_agregar_unidad(args):
    """Calibracion INCREMENTAL: agrega una unidad (con tornillos o vacia) a la
    calibracion existente y recalcula umbrales, sin repetir las unidades ya
    capturadas. Util cuando en produccion aparece una unidad de apariencia
    distinta (p.ej. un tornillo mas hundido que puntuaba bajo): se incorpora al
    banco y el sistema la reconoce en adelante."""
    cfg = cargar_config(args.config)
    previo = _cargar_crops_guardados(cfg)
    if previo is None:
        sys.exit("[ERROR] La calibracion de este proceso no tiene los recortes "
                 "originales guardados (es de una version anterior).\n"
                 "        Haz una calibracion completa una vez con --calibrar; "
                 "desde entonces podras agregar unidades con --agregar-unidad.")
    crops_p, crops_a, n_unidades, n_vacios = previo
    print(f"\n[i] Calibracion actual de '{_RECETA}': "
          f"{n_unidades} unidad(es) presente, {n_vacios} vacia(s).")

    # que tipo de unidad se agrega
    tipo = (args.agregar_unidad or "").strip().lower()
    if tipo not in ("presente", "vacia"):
        print("\n  Que tipo de unidad quieres agregar?")
        print("    1) CON tornillos (presente)")
        print("    2) SIN tornillos (vacia)")
        sel = input("  Numero: ").strip()
        tipo = {"1": "presente", "2": "vacia"}.get(sel)
        if tipo is None:
            sys.exit("Opcion invalida.")

    cap = abrir_camara(args.camara, args.resolucion)
    try:
        agregadas = 0
        while True:
            es_pres = (tipo == "presente")
            n_actual = (n_unidades + agregadas) if es_pres else (n_vacios + agregadas)
            etiqueta = (f"AGREGAR unidad {'CON' if es_pres else 'SIN'} tornillos "
                        f"- nueva unidad {n_actual + 1}")
            if not _esperar_espacio(cap, etiqueta,
                                    espera_min=5.0 if agregadas == 0 else 2.0):
                break
            muestras = _muestrear(cap, cfg, 15,
                                  f"{'presente' if es_pres else 'vacia'} nueva")
            if muestras is None:
                break
            # los 15 frames son la MISMA unidad quieta -> se promedian en una
            # sola plantilla nitida por unidad (igual que en --calibrar)
            for rid, lista in muestras.items():
                crop = _promediar_muestras(lista)
                (crops_p if es_pres else crops_a).setdefault(rid, []).append(crop)
            agregadas += 1
            print(f"[OK] Unidad {'presente' if es_pres else 'vacia'} agregada "
                  f"({agregadas} en esta sesion).")
            print("[i] ESPACIO para agregar OTRA del mismo tipo, "
                  "Q/ENTER para recalcular y guardar.")
            if not _esperar_otra_unidad(cap, agregadas):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if agregadas == 0:
        print("[i] No se agrego ninguna unidad; la calibracion queda igual.")
        return
    n_p = len(crops_p[next(iter(crops_p))])
    n_a = len(crops_a[next(iter(crops_a))])
    print(f"\n[i] Recalculando umbrales con {n_p} presente(s) y {n_a} vacia(s)...")
    _guardar_calibracion(cfg, crops_p, crops_a, n_p, n_a)


def modo_calibrar(args):
    cfg = cargar_config(args.config)
    cap = abrir_camara(args.camara, args.resolucion)
    try:
        # ---- FASE 1: una o varias unidades con tornillos ----
        pres = {clave(p): [] for p in cfg["puntos"]}
        n_unidades = 0
        while True:
            etiqueta = f"FASE 1: housing COMPLETO - unidad {n_unidades + 1}"
            if not _esperar_espacio(cap, etiqueta):
                if n_unidades == 0:
                    return
                break
            muestras = _muestrear(cap, cfg, 15, f"presente u{n_unidades + 1}")
            # Los 15 frames de ESTA unidad son el mismo tornillo quieto: se
            # promedian en UNA plantilla por unidad (nitida, sin borronear la
            # cruz porque no hay cambio de angulo entre frames de la misma
            # unidad). Asi 3 unidades = 3 plantillas, no 45.
            for rid, cs in muestras.items():
                pres[rid].append(_promediar_muestras(cs))
            n_unidades += 1
            print(f"[OK] Unidad {n_unidades} capturada.")
            print("[i] Para calibracion MULTI-UNIDAD, coloca OTRA unidad completa y "
                  "presiona ESPACIO. Si ya terminaste, presiona Q para pasar a la fase 2.")
            if not _esperar_otra_unidad(cap, n_unidades):
                break

        # ---- FASE 2: una o varias unidades sin tornillos ----
        print("[OK] Fase 1 completada con "
              f"{n_unidades} unidad(es). AHORA REMUEVE LOS TORNILLOS de las "
              "posiciones marcadas y recoloca el housing en la silueta.")
        aus = {clave(p): [] for p in cfg["puntos"]}
        n_vacios = 0
        while True:
            etiqueta = f"FASE 2: housing SIN tornillos - unidad {n_vacios + 1}"
            if not _esperar_espacio(cap, etiqueta, espera_min=5.0 if n_vacios == 0 else 2.0):
                if n_vacios == 0:
                    return
                break
            muestras = _muestrear(cap, cfg, 15, f"vacio u{n_vacios + 1}")
            # una plantilla por unidad vacia (promedio de sus 15 frames quietos)
            for rid, cs in muestras.items():
                aus[rid].append(_promediar_muestras(cs))
            n_vacios += 1
            print(f"[OK] Unidad vacia {n_vacios} capturada.")
            print("[i] Para capturar mas variantes del hueco vacio (util si los huecos "
                  "difieren entre unidades, ej. mas limpios o brillantes), coloca OTRA "
                  "unidad sin tornillos y ESPACIO. Si terminaste, Q para calcular.")
            if not _esperar_otra_unidad_vacio(cap, n_vacios):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    _guardar_calibracion(cfg, pres, aus, n_unidades, n_vacios)


def _esperar_otra_unidad_vacio(cap, n):
    """Igual que _esperar_otra_unidad pero con textos de la fase vacio."""
    for _ in range(10):
        cv2.waitKey(20)
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        vista = _vista_pantalla(frame.copy())
        cv2.putText(vista, f"Unidades vacias capturadas: {n}", (16, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, VERDE, 2, cv2.LINE_AA)
        cv2.putText(vista, "ESPACIO = otra unidad vacia   |   Q/ENTER = calcular",
                    (16, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.7, AMARILLO, 2, cv2.LINE_AA)
        cv2.imshow(VENTANA, vista)
        k = cv2.waitKey(1) & 0xFF
        if k == 32:
            return True
        if k in (27, ord("q"), 13, 10):
            return False


def _esperar_otra_unidad(cap, n):
    """Devuelve True si el operador quiere agregar otra unidad (ESPACIO),
    False si desea terminar la fase 1 (Q/ENTER)."""
    for _ in range(10):
        cv2.waitKey(20)
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        vista = _vista_pantalla(frame.copy())
        cv2.putText(vista, f"Unidades capturadas: {n}", (16, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, VERDE, 2, cv2.LINE_AA)
        cv2.putText(vista, "ESPACIO = otra unidad   |   Q/ENTER = pasar a fase 2",
                    (16, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.7, AMARILLO, 2, cv2.LINE_AA)
        cv2.imshow(VENTANA, vista)
        k = cv2.waitKey(1) & 0xFF
        if k == 32:
            return True
        if k in (27, ord("q"), 13, 10):
            return False


def modo_calibrar_fotos(args):
    """Calibración a partir de dos imágenes rectificadas (o crudas con ArUco)."""
    cfg = cargar_config(args.config)
    tam = tuple(cfg["image_size"])

    def preparar(ruta):
        img = cv2.imread(ruta)
        if img is None:
            sys.exit(f"[ERROR] No se pudo leer {ruta}")
        if (img.shape[1], img.shape[0]) == tam:
            return img  # ya rectificada
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        centros = detectar_marcadores(gray)
        if not all(i in centros for i in IDS_ESQUINAS):
            sys.exit(f"[ERROR] {ruta}: no coincide con el tamano rectificado "
                     f"{tam} y no se detectaron los 4 marcadores.")
        return rectificar(img, centros, tam)

    c_pres = crops_todos(preparar(args.calibrar_fotos[0]), cfg)
    c_aus = crops_todos(preparar(args.calibrar_fotos[1]), cfg)
    if any(v is None for v in list(c_pres.values()) + list(c_aus.values())):
        sys.exit("[ERROR] Alguna ROI cae fuera de la imagen rectificada.")
    _guardar_calibracion(cfg,
                         {k: [v] for k, v in c_pres.items()},
                         {k: [v] for k, v in c_aus.items()})


def _procesar_frame(frame, cfg, umbrales, plantillas):
    """Devuelve (warped, scores, presencia, centros, sims); presencia puede tener
    None (amarillo). sims se devuelve para poder registrarlo en el log."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    centros = detectar_marcadores(gray)
    if not all(i in centros for i in IDS_ESQUINAS):
        return None, None, None, centros, None
    warped = rectificar(frame, centros, tuple(cfg["image_size"]))
    crops = extraer_todos(warped, cfg)                      # gris + recortes: una vez
    scores, sims = puntuar_crops(crops, cfg, plantillas)
    presencia = clasificar(scores, umbrales, sims, crops, plantillas)
    return warped, scores, presencia, centros, sims


def modo_foto(args):
    cfg = cargar_config(args.config)
    umbrales = cargar_umbrales()
    plantillas = cargar_plantillas(cfg)
    if umbrales is None or plantillas is None:
        sys.exit(f"[ERROR] Falta calibracion para esta configuracion "
                 f"del proceso '{_RECETA}'. Corre --calibrar primero.")
    verificar_huella(cfg, umbrales)
    img = cv2.imread(args.foto)
    if img is None:
        sys.exit(f"[ERROR] No se pudo leer {args.foto}")
    tam = tuple(cfg["image_size"])
    if (img.shape[1], img.shape[0]) == tam:
        warped = img
        crops = extraer_todos(warped, cfg)
        scores, sims = puntuar_crops(crops, cfg, plantillas)
        presencia = clasificar(scores, umbrales, sims, crops, plantillas)
    else:
        warped, scores, presencia, centros, sims = _procesar_frame(
            img, cfg, umbrales, plantillas)
        if warped is None:
            sys.exit(f"[ERROR] Solo se detectaron "
                     f"{sum(i in centros for i in IDS_ESQUINAS)}/4 marcadores en la foto.")
    faltantes = [r for r, v in presencia.items() if v is False]
    # clasificar() devuelve True / False / "dudoso" — nunca None. Buscar None dejaba
    # esta lista SIEMPRE vacia, y una posicion dudosa no entraba en ninguna de las
    # dos: el veredicto salia PASA aunque hubiera posiciones sin reconocer.
    dudosos = [r for r, v in presencia.items() if v == "dudoso"]
    if faltantes:
        veredicto = f"NO PASA - faltan: {', '.join(faltantes)}"
    elif dudosos:
        veredicto = f"VERIFICAR - no reconocido: {', '.join(dudosos)}"
    else:
        veredicto = "PASA"
    dibujar_overlay(warped, cfg, presencia, veredicto)
    salida = os.path.splitext(args.foto)[0] + "_inspeccion.png"
    cv2.imwrite(salida, componer_vista(warped, cfg, presencia, True, scores))
    print(f"[{veredicto}]")
    for rid in sorted(scores):
        u = umbrales["por_roi"].get(rid, {}).get("umbral", umbrales["umbral_global"])
        est = presencia[rid]
        etq = "presente" if est is True else ("FALTANTE" if est is False else "VERIFICAR")
        print(f"  {rid}: score {scores[rid]:6.1f}  umbral {u:5.1f}  {etq}")
    print(f"[OK] Imagen anotada: {salida}")
    if faltantes:
        res = "NO_PASA"
    elif dudosos:
        res = "VERIFICAR"
    else:
        res = "PASA"
    registrar_inspeccion(res, faltantes, len(cfg["puntos"]),
                         scores, presencia, umbrales, sims, dudosos=dudosos)


def _overlay_debug(vista, cfg, scores, umbrales):
    """Muestra puntaje/umbral junto a cada posicion. Color segun cercania:
    verde holgado, ambar a <8 del umbral, rojo por debajo."""
    e = max(1.0, vista.shape[1] / 1600.0)
    glob = umbrales.get("umbral_global", 50.0)
    por_roi = umbrales.get("por_roi", {})
    for p in cfg["puntos"]:
        rid = clave(p)
        sc = scores.get(rid)
        if sc is None:
            continue
        u = por_roi.get(rid, {}).get("umbral", glob)
        margen = sc - u
        if margen < 0:
            col = ROJO
        elif margen < 8:
            col = AMARILLO
        else:
            col = VERDE
        mw, mh = _medidas(p, cfg)
        x = int(p["x"]) - int(mw)
        y = int(p["y"]) + int(mh) + int(16 * e)
        cv2.putText(vista, f"{sc:.0f}/{u:.0f}", (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * e, col,
                    max(1, int(round(e))), cv2.LINE_AA)
        # marca del Juez 2: si esta posicion fue decidida por la cruz Phillips,
        # mostrar "J:ok" (rescatada) o "J:x" (sin cruz) debajo del puntaje.
        if rid in _JUEZ_ACTIVO:
            estado_j, corr_j, _ = _JUEZ_ACTIVO[rid]
            if estado_j == "rescatado":
                txt_j, col_j = f"J:ok {corr_j:.2f}", VERDE
            else:
                # el juez no rescato, pero NO vetó: el veredicto es del clasificador
                txt_j, col_j = f"J:- {corr_j:.2f}", AMARILLO
            cv2.putText(vista, txt_j, (x, y + int(16 * e)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45 * e, col_j,
                        max(1, int(round(e))), cv2.LINE_AA)


def verificar_huella(cfg, umbrales):
    """Comprueba que la calibracion corresponda a la geometria actual del config.
    Si no coincide, ABORTA: seguir seria comparar recortes nuevos contra plantillas
    de otra geometria, produciendo veredictos sin sentido y sin ningun aviso."""
    guardada = (umbrales or {}).get("huella_geometria")
    if not guardada:
        print("[!] AVISO: esta calibracion no guarda huella de geometria (es de una "
              "version anterior).\n"
              "    No se puede verificar que corresponda a las ROIs actuales. Si has "
              "movido o\n"
              "    redimensionado alguna posicion desde que calibraste, RECALIBRA "
              "antes de operar.")
        return
    actual = huella_geometria(cfg)
    if actual == guardada:
        return
    # informar QUE cambio, no solo que no coincide
    def _mapa(h):
        d = {}
        for parte in h.split("|")[1:]:
            if ":" in parte:
                rid, resto = parte.split(":", 1)
                d[rid] = resto
        return d
    ma, mg = _mapa(actual), _mapa(guardada)
    movidas = sorted(r for r in set(ma) & set(mg) if ma[r] != mg[r])
    nuevas = sorted(set(ma) - set(mg))
    quitadas = sorted(set(mg) - set(ma))
    lienzo = actual.split("|")[0] != guardada.split("|")[0]
    detalle = []
    if lienzo:
        detalle.append(f"cambio el tamano del lienzo rectificado "
                       f"({guardada.split('|')[0]} -> {actual.split('|')[0]})")
    if movidas:
        detalle.append(f"movidas o redimensionadas: {', '.join(movidas)}")
    if nuevas:
        detalle.append(f"posiciones nuevas sin calibrar: {', '.join(nuevas)}")
    if quitadas:
        detalle.append(f"posiciones que ya no existen: {', '.join(quitadas)}")
    sys.exit("[ERROR] La calibracion NO corresponde a la geometria actual de las "
             "ROIs.\n        " + "\n        ".join(detalle) +
             "\n\n        Las plantillas guardadas se tomaron con otra geometria, "
             "asi que los\n        veredictos no serian validos. RECALIBRA esta "
             "receta antes de operar.")


def _cargar_receta_vivo():
    """Carga cfg+umbrales+plantillas de la receta activa. Devuelve None si falta calibracion."""
    cfg = cargar_config()
    umbrales = cargar_umbrales()
    plantillas = cargar_plantillas(cfg)
    if umbrales is None or plantillas is None:
        return None
    verificar_huella(cfg, umbrales)
    return cfg, umbrales, plantillas


def modo_vivo(args):
    datos = _cargar_receta_vivo()
    if datos is None:
        sys.exit(f"[ERROR] Falta calibracion de la receta '{_RECETA}'. "
                 f"Corre --calibrar primero.")
    cfg, umbrales, plantillas = datos
    cap = abrir_camara(args.camara, args.resolucion)
    print("[i] Inspeccion en vivo. Q/ESC salir, S captura, R cambiar de proceso.")

    # Cargar la linea base y el modelo tarda varios segundos, asi que se hace una
    # sola vez aqui y no por pieza. Si algo falta, .ok queda en False y todas las
    # llamadas siguientes no hacen nada.
    detector_cont = contaminacion.Detector(
        activo=not getattr(args, "sin_contaminacion", False),
        carpeta=dir_receta() if _RECETA else None)
    secuencia = cierre.Cierre(cfg, _RECETA, activo=getattr(args, "cierre", False))
    # El banco vive FUERA de la carpeta del programa: es un activo que crece con
    # los meses y no debe viajar en los respaldos del sistema ni al copiar la
    # carpeta a otro banco de trabajo.
    banco_ev = banco.Banco(os.path.join("..", "banco_evidencia"), _RECETA,
                           activo=not getattr(args, "sin_banco", False))
    # identifica la pieza actual: cambia al retirarse el housing. Sirve para no
    # analizar dos veces lo mismo y para descartar un analisis que termine cuando
    # la pieza ya salio del fixture.
    pieza_id = 0
    # True mientras hay una pieza en el fixture. Necesario porque los marcadores
    # ArUco estan en el fixture, no en el telefono: con el hueco vacio la
    # homografia sigue siendo valida y sin esto no habria forma de saber que la
    # pieza salio.
    _cont_pieza_activa = False

    # Toda la votacion -ventana, estabilidad en dos niveles, "una vez por
    # veredicto y por pieza"- vive en veredicto.Veredicto, la MISMA clase que
    # usa la aplicacion Qt. Ver el docstring de veredicto.py para el porque de
    # cada regla; en especial el re-armado por parpadeo: el 8-sep-2026 se
    # registraron 2403 filas que solo contenian 14 veredictos distintos.
    #
    # `ver.hist` es el historial numerico de la ventana (puntaje, similitud), que
    # el log usa para registrar la MEDIANA y no el ultimo frame. `ver.rescates`
    # son las posiciones que el Juez 2 rescato durante la pieza actual.
    ver = veredicto.Veredicto([clave(p) for p in cfg["puntos"]])
    frames_sin_marcadores = 0
    debug = False

    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        warped, scores, presencia_frame, centros, sims = _procesar_frame(
            frame, cfg, umbrales, plantillas)

        if warped is None:
            frames_sin_marcadores += 1
            if frames_sin_marcadores > FRAMES_SIN_MARCADOR_RESET:
                ver.reiniciar()
                detector_cont.reset()
                pieza_id += 1
                _cont_pieza_activa = False
            vista = _vista_pantalla(frame.copy())
            n = sum(i in centros for i in IDS_ESQUINAS)
            cv2.putText(vista, f"Marcadores visibles: {n}/4", (16, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, AMARILLO, 2, cv2.LINE_AA)
            cv2.imshow(VENTANA, vista)
        else:
            frames_sin_marcadores = 0
            r = ver.observar(presencia_frame, scores, sims)
            presencia = r["presencia"]
            listo = r["listo"]
            faltantes, dudosos = r["faltantes"], r["dudosos"]
            n_pres = r["n_presentes"]
            estable = r["estable"]

            # Registrar y sonar UNA vez por veredicto y por pieza: lo decide
            # `r["registrar"]` (estabilidad corta para el primero, larga para una
            # correccion, nunca un veredicto ya registrado). Aqui solo se anade lo
            # que es del modo terminal: no escribir tornillos mientras la pieza
            # en el fixture ya no es el housing (etapa de la parte del cierre).
            if r["registrar"] and not secuencia.suprime_registro():
                res = r["resultado"]
                registrar_inspeccion(res, list(faltantes), len(cfg["puntos"]),
                                     scores, presencia, umbrales, sims,
                                     dudosos=list(dudosos), hist=ver.hist)
                # Mismo instante que el registro y el beep: la pieza esta quieta
                # y ya juzgada. Se pasa `warped`, el rectificado LIMPIO, nunca la
                # vista compuesta con circulos y banner.
                banco_ev.guardar(warped, res, faltantes, dudosos, scores,
                                 umbrales, sims,
                                 gcn=getattr(secuencia, "gcn", None),
                                 estacion=platform.node())
                if ver.rescates:
                    for rr in sorted(ver.rescates):
                        _e, cz, sc = ver.rescates[rr]
                        print(f"[Juez 2] {rr}: cruz confirmada (cz {cz}) "
                              f"-> RESCATADO a presente (puntaje {sc})")
                beep(res == "PASA")
                # DESPUES de escribir: si el registro fallara, el veredicto no
                # queda como hecho y se reintenta en el frame siguiente.
                ver.marcar_registrado(r["veredicto"])

            if not listo:
                banner = "Analizando..."
            elif n_pres == 0 and not dudosos:
                banner = "Esperando pieza..."
                # Aqui, y solo aqui, empieza una pieza nueva: la anterior salio del
                # fixture. Es el unico re-armado valido (junto con la perdida de
                # marcadores), porque es el unico momento en que sabemos que lo que
                # viene no es la misma pieza vista otra vez.
                ver.nueva_pieza()
                # La pieza salio del fixture aunque los marcadores del fixture
                # sigan visibles: hay que limpiar la contaminacion de la pieza
                # anterior. Atarlo a `frames_sin_marcadores` no basta, porque los
                # ArUco estan en el fixture y se siguen viendo con el hueco vacio.
                if detector_cont.zonas(pieza_id) or _cont_pieza_activa:
                    detector_cont.reset()
                    pieza_id += 1
                _cont_pieza_activa = False
            elif faltantes:
                banner = f"FALTAN {len(faltantes)}: {', '.join(faltantes)}"
            elif dudosos:
                banner = f"VERIFICAR {len(dudosos)}: {', '.join(dudosos)}"
            else:
                banner = "Housing completo - PASA"

            # Solo se analiza si HAY pieza y el veredicto ya es ESTABLE. Dos
            # motivos: con el hueco vacio se analizaria el fixture desnudo contra
            # una linea base de housings (todo seria anomalo), y con la pieza
            # recien puesta la imagen aun se mueve, y un cuadro movido desalinea
            # el rectificado y dispara anomalias en cada borde fuerte.
            if listo and estable and (n_pres > 0 or dudosos):
                _cont_pieza_activa = True
                detector_cont.actualizar(warped, pieza_id)

            # La contaminacion se ANADE al banner en vez de sustituirlo: son dos
            # inspecciones independientes (faltan tornillos / sobra material) y el
            # operario necesita ver las dos a la vez. Un housing puede estar
            # completo y aun asi tener una viruta dentro.
            _txt_cont, _n_cont = detector_cont.estado(pieza_id)
            if _n_cont > 0:
                banner += f"  |  CONTAMINACION: {_n_cont}"
            elif _n_cont == -1:
                banner += "  |  analizando contaminacion..."

            vista = warped.copy()
            dibujar_overlay(vista, cfg, presencia, banner)
            # escala 1.0: `vista` es el rectificado a tamano completo, que es el
            # sistema de coordenadas en el que el detector devuelve las zonas
            detector_cont.dibujar(vista, pieza_id, escala=1.0)
            # La secuencia mira el rectificado LIMPIO (`warped`), no `vista`: la
            # foto de evidencia no puede llevar los circulos ni el banner
            # encima. Dibuja sobre `vista`, que es lo que ve el operario.
            secuencia.procesar(warped, estable, not faltantes and not dudosos,
                               hay_pieza=(n_pres > 0 or bool(dudosos)),
                               frame=frame, centros=centros)
            secuencia.dibujar(vista, escala=1.0)
            if debug:
                _overlay_debug(vista, cfg, scores, umbrales)
            # El Juez 2 NO se reporta por frame. Motivo: su intervencion depende de
            # que el puntaje este bajo el umbral, y los puntajes fluctuan, asi que el
            # conjunto de posiciones rescatadas cambia de un frame a otro (T17 en uno,
            # T01 en el siguiente...). Cualquier aviso ligado al frame parpadea e
            # inunda la terminal. En su lugar se ACUMULA quien rescato durante la
            # pieza y se reporta UNA vez, al registrarse el veredicto, que es el
            # unico momento en que el rescate tiene consecuencia real.
            for rr, v in _JUEZ_ACTIVO.items():
                if v[0] == "rescatado":
                    ver.rescates[rr] = v
            compuesto = componer_vista(vista, cfg, presencia, estable, scores)
            cv2.imshow(VENTANA, compuesto)

        k = cv2.waitKey(1) & 0xFF
        # La secuencia mira la tecla primero: mientras esta en marcha, ENTER
        # confirma y X cancela, y esas teclas no deben hacer otra cosa.
        secuencia.tecla(k)
        if k in (27, ord("q")):
            break
        if k == ord("d"):
            debug = not debug
            print(f"[i] Modo debug {'ON' if debug else 'OFF'}")
        if k == ord("j"):
            _toggle_juez()
        if k == ord("s") and warped is not None:
            nombre = f"captura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            cv2.imwrite(nombre, compuesto)
            print(f"[OK] Captura guardada: {nombre}")
        if k == ord("c") and warped is not None:
            # Captura LIMPIA: la imagen rectificada sin overlay. La tecla S guarda
            # la vista compuesta (circulos, etiquetas, puntajes, barra lateral),
            # que sirve para documentar un veredicto pero NO para construir un
            # dataset: un detector de anomalias aprenderia que esos dibujos son
            # parte de la pieza y marcaria como anomalo cualquier cambio en ellos.
            # Se guarda en color y sin comprimir: la contaminacion puede tener
            # firma cromatica y detalle fino que una compresion con perdida borra.
            carpeta = os.path.join("dataset", _RECETA)
            os.makedirs(carpeta, exist_ok=True)
            nombre = os.path.join(
                carpeta, f"limpia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            cv2.imwrite(nombre, warped)
            print(f"[OK] Captura limpia (sin overlay): {nombre}")
        if k == ord("r"):
            disponibles = recetas_existentes()
            if len(disponibles) > 1:
                print("\n=== Cambiar de proceso ===")
                for i, r in enumerate(disponibles, 1):
                    marca = " (actual)" if r == _RECETA else ""
                    print(f"  {i}) {r}{marca}")
                sel = input("Numero (ENTER = cancelar): ").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(disponibles):
                    nueva = disponibles[int(sel) - 1]
                    if nueva != _RECETA:
                        # guardar el nombre REAL del proceso anterior. Antes se
                        # restauraba cfg["modelo"] (p.ej. "iPhone 15 Pro"), que es el
                        # modelo de telefono y no el nombre del proceso: dejaba
                        # _RECETA apuntando a una carpeta inexistente y el log
                        # registraba un proceso equivocado.
                        receta_anterior = _RECETA
                        set_receta(nueva)
                        datos = _cargar_receta_vivo()
                        if datos is None:
                            print(f"[!] '{nueva}' no esta calibrada. Se mantiene "
                                  f"'{receta_anterior}'.")
                            set_receta(receta_anterior)
                        else:
                            cfg, umbrales, plantillas = datos
                            ver = veredicto.Veredicto(
                                [clave(p) for p in cfg["puntos"]])
                            print(f"[i] Proceso activo: {nueva}")
            else:
                print("[i] Solo hay un proceso disponible.")

    cap.release()
    cv2.destroyAllWindows()


# -------------------------------- main ---------------------------------------

def migrar_sistema_viejo():
    """Migra estructuras antiguas a procesos/:
    - carpeta 'recetas/' (nombre viejo) -> se renombra a 'procesos/'
    - archivos sueltos en la raiz (pre-procesos) -> procesos/Ensamble 1/"""
    if os.path.isdir("recetas") and not os.path.isdir(DIR_RECETAS):
        os.rename("recetas", DIR_RECETAS)
        print(f"[i] Carpeta 'recetas' renombrada a '{DIR_RECETAS}'.")
    if os.path.isdir(DIR_RECETAS):
        return
    sueltos = ["config_rois.json", "umbrales.json", "plantillas.npz", "referencia.png"]
    if not any(os.path.exists(f) for f in sueltos):
        return
    destino = os.path.join(DIR_RECETAS, "Ensamble 1")
    os.makedirs(destino, exist_ok=True)
    movidos = []
    for f in sueltos:
        if os.path.exists(f):
            os.rename(f, os.path.join(destino, f))
            movidos.append(f)
    print(f"[i] Sistema migrado: calibracion actual -> proceso 'Ensamble 1' "
          f"({', '.join(movidos)}).")


def seleccionar_receta(args):
    """Fija la receta activa desde --receta, o pide elegir en un menu."""
    migrar_sistema_viejo()
    disponibles = recetas_existentes()

    # capturar-referencia con --receta nueva: se permite crear
    if args.receta:
        set_receta(args.receta)
        if args.receta not in disponibles and not args.capturar_referencia:
            print(f"[i] El proceso '{args.receta}' aun no existe. "
                  f"Empieza con --capturar-referencia --receta \"{args.receta}\".")
        return

    if not disponibles:
        if args.capturar_referencia:
            set_receta("Ensamble 1")
            print("[i] Creando primer proceso: 'Ensamble 1'")
            return
        sys.exit("[ERROR] No hay procesos. Crea uno con "
                 "--capturar-referencia --receta \"Ensamble 1\".")

    if len(disponibles) == 1 and not args.capturar_referencia:
        set_receta(disponibles[0])
        print(f"[i] Proceso activo: {disponibles[0]}")
        return

    print("\n=== Procesos disponibles ===")
    for i, r in enumerate(disponibles, 1):
        print(f"  {i}) {r}")
    if args.capturar_referencia:
        print(f"  {len(disponibles)+1}) [crear nuevo proceso]")
    while True:
        sel = input("Selecciona el numero de proceso: ").strip()
        if sel.isdigit():
            n = int(sel)
            if 1 <= n <= len(disponibles):
                set_receta(disponibles[n-1])
                print(f"[i] Proceso activo: {_RECETA}")
                return
            if args.capturar_referencia and n == len(disponibles)+1:
                nombre = input("Nombre del nuevo proceso: ").strip()
                if nombre:
                    set_receta(nombre)
                    return
        print("Opcion invalida.")


def main():
    ap = argparse.ArgumentParser(
        description="Inspector de tornillos por ROIs fijas + ArUco (Airlink DR)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capturar-referencia", action="store_true",
                   help="guardar frame rectificado de referencia")
    g.add_argument("--calibrar", action="store_true",
                   help="calibracion guiada de umbrales (camara)")
    g.add_argument("--calibrar-fotos", nargs=2, metavar=("COMPLETA", "VACIA"),
                   help="calibrar con dos fotos (housing completo / sin tornillos)")
    g.add_argument("--agregar-unidad", nargs="?", const="", metavar="TIPO",
                   choices=["", "presente", "vacia"],
                   help="calibracion incremental: agrega una unidad a la "
                        "calibracion existente sin repetirla toda. "
                        "TIPO opcional: 'presente' o 'vacia' (si se omite, pregunta).")
    g.add_argument("--vivo", action="store_true", help="inspeccion en vivo")
    g.add_argument("--foto", metavar="IMAGEN", help="inspeccionar una foto")
    ap.add_argument("--camara", type=int, default=0, help="indice de camara (default 0)")
    ap.add_argument("--resolucion", choices=["auto", "4k", "1440", "1080"], default="auto",
                    help="resolucion de captura: auto = la mejor disponible (default)")
    ap.add_argument("--config", default=None,
                    help="archivo de configuracion (default: el del proceso activo)")
    ap.add_argument("--sin-contaminacion", action="store_true",
                    help="no marcar zonas de posible suciedad (los recuadros "
                         "magenta VERIFICAR). Util cuando la linea base no "
                         "corresponde al canvas de esta receta.")
    ap.add_argument("--sin-banco", action="store_true",
                    help="no guardar la evidencia de cada pieza en el banco. "
                         "Por defecto SI se guarda: es lo que permite afinar el "
                         "sistema despues con piezas reales de la linea.")
    ap.add_argument("--cierre", action="store_true",
                    help="activa la secuencia de cierre: lee el giftcard, valida "
                         "la unidad, pide el display y registra la evidencia en "
                         "SQL Server. Sin este flag el inspector trabaja igual "
                         "que siempre.")
    ap.add_argument("--receta", default=None,
                    help="nombre del proceso (Ensamble 1, Ensamble 2...). "
                         "Si se omite, se muestra un menu.")
    args = ap.parse_args()

    # resolver receta activa: flag, o unica existente, o menu
    seleccionar_receta(args)

    if args.capturar_referencia:
        modo_capturar_referencia(args)
    elif args.calibrar:
        modo_calibrar(args)
    elif args.calibrar_fotos:
        modo_calibrar_fotos(args)
    elif args.agregar_unidad is not None:
        modo_agregar_unidad(args)
    elif args.foto:
        modo_foto(args)
    else:
        modo_vivo(args)


if __name__ == "__main__":
    main()
