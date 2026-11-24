# -*- coding: utf-8 -*-
"""Decision 8: rectificar SIEMPRE a cfg["image_size"].

Los centros ArUco tiemblan en fraccion de pixel; el tamano calculado da 1599,
1600 o 1601 en capturas seguidas, y como el lienzo entra en la huella de
geometria, eso invalidaba calibraciones sin motivo."""

import cv2
import numpy as np
import pytest

import inspector


def _frame_con_marcadores(desplaz=0.0, escala=1.0):
    W, H = 1920, 1080
    img = np.full((H, W), 255, np.uint8)
    dic = cv2.aruco.getPredefinedDictionary(inspector.ARUCO_DICT)
    lado = 120
    pos = {0: (100, 100), 1: (W - 100 - lado, 100),
           2: (100, H - 100 - lado), 3: (W - 100 - lado, H - 100 - lado)}
    for i, (x, y) in pos.items():
        m = cv2.aruco.generateImageMarker(dic, i, lado)
        img[y:y + lado, x:x + lado] = m
    # Movimiento subpixel: el mismo fixture visto con una diferencia minima.
    M = np.float32([[escala, 0, desplaz], [0, escala, desplaz * 0.7]])
    img = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR,
                         borderValue=255)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_el_tamano_calculado_baila():
    """Premisa de la decision: el calculo a partir de los centros no es estable."""
    tams = set()
    for k in range(12):
        f = _frame_con_marcadores(desplaz=k * 0.13, escala=1.0 + k * 0.0004)
        c = inspector.detectar_marcadores(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
        assert all(i in c for i in inspector.IDS_ESQUINAS)
        tams.add(inspector.tam_canonico_desde_centros(c))
    assert len(tams) > 1


def test_procesar_frame_usa_el_tamano_del_config(monkeypatch):
    cfg = {"image_size": [1600, 900], "puntos": []}
    monkeypatch.setattr(inspector, "extraer_todos", lambda w, c: {})
    monkeypatch.setattr(inspector, "puntuar_crops", lambda cr, c, p: ({}, {}))
    monkeypatch.setattr(inspector, "clasificar", lambda *a, **k: {})
    for k in range(12):
        f = _frame_con_marcadores(desplaz=k * 0.13, escala=1.0 + k * 0.0004)
        warped, *_ = inspector._procesar_frame(f, cfg, {}, {})
        assert warped is not None
        assert warped.shape[1] == 1600 and warped.shape[0] == 900


def test_sin_cuatro_marcadores_no_hay_rectificado():
    f = np.full((1080, 1920, 3), 255, np.uint8)
    warped, scores, pres, centros, sims = inspector._procesar_frame(
        f, {"image_size": [1600, 900]}, {}, {})
    assert warped is None
