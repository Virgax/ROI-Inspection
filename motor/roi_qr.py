#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roi_qr.py — Lectura de codigos QR sobre la imagen rectificada.

QUE LEE
Dos cosas distintas, con el mismo mecanismo:
  - El giftcard del HOUSING, que trae el GCN de la unidad dentro de una cadena
    con varios campos:  'A02 SM312|REC|PH|CX666666'  ->  GCN = CX666666
  - El QR del DISPLAY, del que sale el GCN del LCD.

POR QUE SOBRE EL RECTIFICADO Y NO SOBRE EL FRAME CRUDO
El rectificado ya esta enderezado por la homografia de los ArUco, asi que el QR
llega plano y a escala conocida. Sobre el frame crudo llegaria en perspectiva y
el detector tendria que resolver la deformacion por su cuenta. Ademas, si el QR
esta siempre en la misma zona de la pieza, esa zona son las mismas coordenadas
en todos los frames, y se puede recortar: buscar en 200x200 px en vez de en 5
megapixeles es del orden de cien veces menos trabajo.

SIN DEPENDENCIAS NUEVAS
Usa el detector que ya trae OpenCV. Se evito pyzbar a proposito: arrastra una
libreria de sistema (zbar) que habria que instalar en cada Mac de la flota.

USO
    import roi_qr
    # toda la imagen:
    codigos = roi_qr.leer(warped)
    # solo la zona calibrada de la receta (mucho mas rapido):
    codigos = roi_qr.leer(warped, zona=cfg.get('zona_qr'))
    gcn = roi_qr.gcn_de_giftcard(codigos[0]) if codigos else None
"""

import re

import cv2
import numpy as np

# Formato del GCN: dos letras y seis digitos (CX666666). Si en produccion
# aparecen GCN con otra forma, esto es lo unico que hay que tocar.
# Formas de GCN vistas en produccion:
#   CX666666  -> 2 letras + 6 digitos (recepcion normal)
#   R10172    -> 1 letra  + 5 digitos (giftcards de RMA)
#
# Se acepta la familia entera en vez de ir anadiendo casos sueltos cada vez que
# aparece uno. Este patron solo decide "esto PARECE un GCN"; quien manda de
# verdad es SPC_Receiving, que responde GCN_NO_EXISTE si no esta. Ser un poco
# amplio aqui cuesta una consulta; ser estricto costo una unidad que no se
# podia cerrar y un mensaje -"no es un giftcard valido"- que no se parecia en
# nada a la causa.
PATRON_GCN = re.compile(r"^[A-Z]{1,3}\d{4,8}$")

# Separador de campos del giftcard: 'A02 SM312|REC|PH|CX666666'
SEPARADOR = "|"

_detector = cv2.QRCodeDetector()

# Lector de Data Matrix. OpenCV NO lee ese formato -solo QR-, y los codigos de
# los displays son Data Matrix grabados, asi que sin esto la etapa del LCD no
# tiene con que trabajar. Es opcional a proposito: si falta, el giftcard (que si
# es QR) se sigue leyendo con OpenCV y solo se pierde la lectura del display.
#   pip3 install "zxing-cpp==2.3.0" --break-system-packages
# La version se fija: de la 3.0 en adelante ya no publican rueda para Python 3.9
# y pip intenta compilar desde fuente, que falla en CMake.
try:
    import zxingcpp
except ImportError:
    zxingcpp = None


def _leer_zxing(img, escalas):
    """Lee cualquier formato (Data Matrix, QR, barras) probando varias escalas.

    Se prueban VARIAS y no una porque a estos tamanos el resultado no es
    monotono: el Data Matrix del display, de 52 px de lado y desenfocado,
    decodifica a x2 y a x6 pero NO a x1, x3 ni x4. Interpolar cambia como caen
    los bordes de cada modulo sobre la rejilla de pixeles, y unas escalas
    aciertan donde otras emborronan. Probar varias cuesta milisegundos en una
    zona pequena y es la diferencia entre leer y no leer."""
    if zxingcpp is None:
        return []
    for esc in escalas:
        muestra = img if esc == 1 else cv2.resize(
            img, None, fx=esc, fy=esc, interpolation=cv2.INTER_CUBIC)
        try:
            res = zxingcpp.read_barcodes(muestra)
        except Exception:
            continue
        textos = [r.text.strip() for r in res if r.text and r.text.strip()]
        if textos:
            return textos
    return []


def leer(bgr, zona=None, escalas=(1.0, 2.0)):
    """Devuelve la lista de textos de los QR hallados.

    `zona` es (x0, y0, x1, y1) en pixeles del rectificado. Si se da, solo se
    busca ahi: es el caso normal en operacion, porque el giftcard va siempre en
    el mismo sitio de la pieza.

    `escalas` existe porque un QR pequeno dentro de una imagen de 3200 px de
    ancho puede quedar por debajo de lo que el detector resuelve. Al no
    encontrar nada se reintenta con la zona ampliada al doble, que recupera la
    mayoria de esos casos por poco costo. El orden importa: la escala 1.0 va
    primero porque es la que acierta casi siempre y la que menos cuesta.
    """
    img = bgr
    if zona is not None:
        x0, y0, x1, y1 = (int(v) for v in zona)
        h, w = bgr.shape[:2]
        # recortar a los limites reales: una zona mal calibrada no debe reventar
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            return []
        img = bgr[y0:y1, x0:x1]

    # Primero zxing, que lee Data Matrix Y QR de una pasada; OpenCV queda como
    # respaldo para cuando zxing no este instalado.
    hallados = _leer_zxing(img, (2, 1, 3, 4, 6))
    if hallados:
        return hallados

    for esc in escalas:
        muestra = img
        if esc != 1.0:
            muestra = cv2.resize(img, None, fx=esc, fy=esc,
                                 interpolation=cv2.INTER_CUBIC)
        try:
            ok, textos, _pts, _ = _detector.detectAndDecodeMulti(muestra)
        except cv2.error:
            # el detector lanza si la imagen es demasiado pequena o degenerada
            continue
        if ok:
            hallados = [t.strip() for t in textos if t and t.strip()]
            if hallados:
                return hallados
    return []


def gcn_de_giftcard(texto):
    """Extrae el GCN del contenido del giftcard, o None si no lo reconoce.

    El GCN es el ULTIMO campo separado por '|':
        'A02 SM312|REC|PH|CX666666'  ->  'CX666666'

    Se toma el ultimo campo y no una posicion fija porque los campos de la
    izquierda describen el lote y la ruta, y esos cambian; el GCN va al final.
    Aun asi no se acepta a ciegas: tiene que cumplir PATRON_GCN. Un giftcard mal
    impreso o un QR de otra cosa devuelve None, y la estacion pide reescanear en
    vez de mandar basura a la base de datos.

    Tambien acepta un texto que sea SOLO el GCN, sin separadores, porque el QR
    del display puede venir asi.
    """
    if not texto:
        return None
    campos = [c.strip() for c in texto.split(SEPARADOR) if c.strip()]
    if not campos:
        return None
    candidato = campos[-1].upper()
    if PATRON_GCN.match(candidato):
        return candidato
    # respaldo: buscar un GCN valido en cualquier campo. Cubre el caso de un
    # giftcard con un campo extra despues del GCN.
    for c in campos:
        c = c.strip().upper()
        if PATRON_GCN.match(c):
            return c
    return None


def gcn_en(bgr, zona=None):
    """Atajo: lee los QR de la imagen y devuelve (gcn, texto_crudo).

    Devuelve (None, texto) si vio un QR pero no reconocio un GCN dentro, para
    que la estacion pueda mostrar QUE leyo. Un 'no se detecto codigo' y un 'lei
    algo que no es un GCN' son dos problemas distintos para el operario: el
    primero se resuelve moviendo la pieza, el segundo avisando de que el
    giftcard es el equivocado.
    """
    textos = leer(bgr, zona)
    if not textos:
        return None, None
    for t in textos:
        g = gcn_de_giftcard(t)
        if g:
            return g, t
    return None, textos[0]


def zona_en_crudo(zona, centros, tam, forma_frame, margen=0.25):
    """Convierte una zona del lienzo rectificado a un recorte del frame CRUDO.

    POR QUE LEER DEL CRUDO
    El rectificado es una imagen REMUESTREADA: la homografia interpola, y de
    paso el lienzo se limita a LADO_MAX_CANONICO, asi que con una camara 4K la
    imagen ademas se reduce. Las dos cosas se comen detalle fino. Para un
    tornillo da igual -se compara una forma de 60 px-, pero un Data Matrix vive
    exactamente de ese detalle: medido sobre piezas reales, el simbolo mide unos
    48 px de lado, que para sus ~20 modulos son 2,4 px por modulo cuando el
    lector necesita 4.

    Leer sobre los pixeles que dio el sensor no inventa resolucion, pero
    devuelve la que se perdio por el camino, que es lo unico gratis que queda.

    Devuelve (x0, y0, x1, y1) del frame crudo, ya recortado a sus bordes, o None
    si la zona cae fuera.
    """
    if not zona or not centros:
        return None
    if not all(i in centros for i in (0, 1, 2, 3)):
        return None
    W, H = tam
    src = np.float32([centros[0], centros[1], centros[2], centros[3]])
    dst = np.float32([[0, 0], [W - 1, 0], [0, H - 1], [W - 1, H - 1]])
    # La inversa de la misma homografia que usa `rectificar`: lienzo -> frame.
    M = cv2.getPerspectiveTransform(dst, src)
    x0, y0, x1, y1 = [float(v) for v in zona]
    esquinas = np.float32([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])
    proy = cv2.perspectiveTransform(esquinas, M)[0]
    # Caja envolvente con margen: la proyeccion es un cuadrilatero, no un
    # rectangulo, y el display no cae dos veces en el mismo sitio.
    mx = (proy[:, 0].max() - proy[:, 0].min()) * margen
    my = (proy[:, 1].max() - proy[:, 1].min()) * margen
    fx0 = int(max(0, proy[:, 0].min() - mx))
    fy0 = int(max(0, proy[:, 1].min() - my))
    fx1 = int(min(forma_frame[1], proy[:, 0].max() + mx))
    fy1 = int(min(forma_frame[0], proy[:, 1].max() + my))
    if fx1 - fx0 < 12 or fy1 - fy0 < 12:
        return None
    return (fx0, fy0, fx1, fy1)


def leer_crudo(frame, zona, centros, tam):
    """Lee codigos en la zona indicada, pero sobre el frame sin rectificar.

    Si algo falta -no hay frame, no estan los 4 marcadores, la zona cae fuera-
    devuelve [] y quien llama recurre al rectificado. Nunca es peor: es un
    intento mas, con mejores pixeles."""
    if frame is None:
        return []
    caja = zona_en_crudo(zona, centros, tam, frame.shape)
    if caja is None:
        return []
    x0, y0, x1, y1 = caja
    return _leer_zxing(frame[y0:y1, x0:x1], (2, 3, 1, 4, 6))
