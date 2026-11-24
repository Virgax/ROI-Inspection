# -*- coding: utf-8 -*-
"""El modo terminal (inspector.modo_vivo) y la app Qt deciden con la MISMA
votacion: veredicto.Veredicto.

inspector.py tuvo su propia copia de la votacion y esa divergencia ya rompio el
cierre una vez. Estas pruebas hacen correr el bucle de verdad con una camara
falsa y comprueban que registra exactamente lo que dice veredicto.py."""

import types

import numpy as np
import pytest

import inspector
import veredicto


def test_no_hay_copia_de_la_votacion():
    import inspect
    src = inspect.getsource(inspector.modo_vivo)
    assert "veredicto.Veredicto" in src
    for resto in ("n_true", "historial_veredicto", "veredictos_registrados"):
        assert resto not in src, resto
    for k in ("FRAMES_VOTO", "VOTOS_PRESENTE", "FRAMES_ESTABLE",
              "FRAMES_ESTABLE_CAMBIO"):
        assert getattr(inspector, k) == getattr(veredicto, k)


CLAVES = ["T01", "T02", "T03"]
IMG = np.zeros((90, 160, 3), np.uint8)


def _guion():
    """Una jornada corta: pieza buena, pieza con T02 que se corrige, parpadeo
    falta/dudoso, hueco vacio entre piezas y una perdida de marcadores."""
    ok = {"T01": True, "T02": True, "T03": True}
    falta = {"T01": True, "T02": False, "T03": True}
    vacio = {"T01": False, "T02": False, "T03": False}
    g = []
    g += [ok] * 80
    g += [vacio] * 40
    g += [falta] * 60
    g += [ok] * 80                                  # correccion del operario
    g += [vacio] * 40
    for i in range(300):                            # parpadeo
        g.append({"T01": True, "T02": True,
                  "T03": False if (i // 4) % 2 else "dudoso"})
    g += [None] * (inspector.FRAMES_SIN_MARCADOR_RESET + 5)   # sin tapete
    g += [ok] * 80
    return g


def _esperado(guion):
    ver = veredicto.Veredicto(CLAVES)
    sin, regs = 0, []
    for p in guion:
        if p is None:
            sin += 1
            if sin > inspector.FRAMES_SIN_MARCADOR_RESET:
                ver.reiniciar()
            continue
        sin = 0
        r = ver.observar(p)
        if r["registrar"]:
            regs.append((r["resultado"], tuple(r["faltantes"]), tuple(r["dudosos"])))
            ver.marcar_registrado(r["veredicto"])
        if r["listo"] and r["n_presentes"] == 0 and not r["dudosos"]:
            ver.nueva_pieza()
    return regs


def test_modo_vivo_registra_lo_mismo_que_veredicto(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    guion = _guion()
    cfg = {"puntos": [{"id": k, "x": 10, "y": 10} for k in CLAVES],
           "image_size": [160, 90], "roi_size": 10, "zonas": {}}
    for p in cfg["puntos"]:
        p["_clave"] = p["id"]
    monkeypatch.setattr(inspector, "_cargar_receta_vivo",
                        lambda: (cfg, {"umbral_global": 50}, {}))

    class Cam:
        def read(self):
            return True, IMG

        def release(self):
            pass

    monkeypatch.setattr(inspector, "abrir_camara", lambda *a, **k: Cam())
    it = iter(guion)
    terminado = [False]

    def procesar(frame, cfg_, umb, pl):
        try:
            p = next(it)
        except StopIteration:
            terminado[0] = True
            p = {k: True for k in CLAVES}
        if p is None:
            return None, None, None, {}, None
        return IMG, {k: 60.0 for k in CLAVES}, p, {}, {}

    monkeypatch.setattr(inspector, "_procesar_frame", procesar)
    regs = []
    monkeypatch.setattr(inspector, "registrar_inspeccion",
                        lambda res, falt, n, *a, dudosos=None, **k:
                        regs.append((res, tuple(falt), tuple(dudosos or ()))))
    monkeypatch.setattr(inspector, "beep", lambda ok: None)
    monkeypatch.setattr(inspector, "componer_vista", lambda v, *a, **k: v)
    monkeypatch.setattr(inspector, "dibujar_overlay", lambda *a, **k: None)
    monkeypatch.setattr(inspector, "_vista_pantalla", lambda v, *a, **k: v)
    monkeypatch.setattr(inspector.cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(inspector.cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setattr(inspector.cv2, "waitKey",
                        lambda ms: ord("q") if terminado[0] else 255)
    monkeypatch.setattr(inspector, "_RECETA", "PRUEBA")

    args = types.SimpleNamespace(camara=0, resolucion="auto", cierre=False,
                                 sin_contaminacion=True, sin_banco=True)
    inspector.modo_vivo(args)

    esperado = _esperado(guion)
    assert regs == esperado
    # Y el guion ejercita lo que importa: buena, falta, correccion, sin
    # registros por el parpadeo mas alla del primero, y la pieza tras el reset.
    assert esperado[0][0] == "PASA"
    assert ("NO_PASA", ("T02",), ()) in esperado
    assert len(esperado) <= 6
