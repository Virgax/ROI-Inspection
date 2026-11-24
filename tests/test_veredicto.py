# -*- coding: utf-8 -*-
"""Votacion temporal (veredicto.py): cuando se escribe una fila del log.

La regla de fondo es "una vez por veredicto y por pieza": el 8-sep-2026 una
posicion que oscilaba entre 'falta' y 'dudoso' produjo 2403 filas con solo 14
veredictos distintos."""

import pytest

import veredicto as V


CLAVES = ["T01", "T02", "T03"]


def frame(t01=True, t02=True, t03=True):
    return {"T01": t01, "T02": t02, "T03": t03}


def alimentar(ver, n, **kw):
    regs = []
    r = None
    for _ in range(n):
        r = ver.observar(frame(**kw))
        if r["registrar"]:
            regs.append(r["veredicto"])
            ver.marcar_registrado(r["veredicto"])
    return r, regs


def test_no_decide_hasta_llenar_la_ventana():
    ver = V.Veredicto(CLAVES)
    r, _ = alimentar(ver, V.FRAMES_VOTO - 1)
    assert not r["listo"]
    assert all(v is None for v in r["presencia"].values())
    r, _ = alimentar(ver, 1)
    assert r["listo"]


def test_presente_exige_mayoria_clara():
    ver = V.Veredicto(["T01"])
    seq = [True] * (V.VOTOS_PRESENTE - 1) + [False] * (V.FRAMES_VOTO - V.VOTOS_PRESENTE + 1)
    for v in seq:
        r = ver.observar({"T01": v})
    assert r["presencia"]["T01"] is False
    ver = V.Veredicto(["T01"])
    seq = [True] * V.VOTOS_PRESENTE + [False] * (V.FRAMES_VOTO - V.VOTOS_PRESENTE)
    for v in seq:
        r = ver.observar({"T01": v})
    assert r["presencia"]["T01"] is True


def test_empate_falta_dudoso_va_a_dudoso():
    ver = V.Veredicto(["T01"])
    seq = ["dudoso"] * 7 + [False] * 7 + [True]
    for v in seq:
        r = ver.observar({"T01": v})
    assert r["presencia"]["T01"] == "dudoso"
    assert r["resultado"] == "VERIFICAR"


def test_resultados():
    ver = V.Veredicto(CLAVES)
    r, _ = alimentar(ver, V.FRAMES_VOTO)
    assert r["resultado"] == "PASA"
    ver = V.Veredicto(CLAVES)
    r, _ = alimentar(ver, V.FRAMES_VOTO, t02=False)
    assert r["resultado"] == "NO_PASA" and r["faltantes"] == ("T02",)


def test_primer_registro_con_estabilidad_corta():
    ver = V.Veredicto(CLAVES)
    r, regs = alimentar(ver, V.FRAMES_VOTO + V.FRAMES_ESTABLE - 2)
    assert regs == []
    r, regs = alimentar(ver, 1)
    assert regs == [((), ())]


def test_un_mismo_veredicto_no_se_registra_dos_veces():
    ver = V.Veredicto(CLAVES)
    _, regs = alimentar(ver, 200)
    assert len(regs) == 1


def test_oscilacion_no_registra_en_bucle():
    """Una posicion que parpadea entre falta y dudoso NO puede generar filas."""
    ver = V.Veredicto(CLAVES)
    _, regs = alimentar(ver, V.FRAMES_VOTO + V.FRAMES_ESTABLE, t03=False)
    assert len(regs) == 1
    total = 0
    for i in range(2000):
        bloque = ["dudoso"] * 8 + [False] * 7 if (i // 20) % 2 else [False] * 8 + ["dudoso"] * 7
        r = ver.observar(frame(t03=bloque[i % 15]))
        if r["registrar"]:
            total += 1
            ver.marcar_registrado(r["veredicto"])
    assert total <= 1


def test_correccion_real_necesita_estabilidad_larga():
    ver = V.Veredicto(CLAVES)
    _, regs = alimentar(ver, V.FRAMES_VOTO + V.FRAMES_ESTABLE, t02=False)
    assert regs == [(("T02",), ())]
    # El operario pone el tornillo: el nuevo veredicto tiene que sostenerse.
    _, regs = alimentar(ver, V.FRAMES_VOTO + V.FRAMES_ESTABLE)
    assert regs == []
    _, regs = alimentar(ver, V.FRAMES_ESTABLE_CAMBIO)
    assert regs == [((), ())]


def test_volver_a_un_veredicto_ya_registrado_no_escribe():
    ver = V.Veredicto(CLAVES)
    alimentar(ver, 100, t02=False)
    alimentar(ver, 100)
    _, regs = alimentar(ver, 200, t02=False)
    assert regs == []


def test_nueva_pieza_rearma():
    ver = V.Veredicto(CLAVES)
    alimentar(ver, 100)
    vacio = {"T01": False, "T02": False, "T03": False}
    for _ in range(V.FRAMES_VOTO):
        r = ver.observar(vacio)
    assert not r["hay_pieza"] and not r["registrar"]
    ver.nueva_pieza()
    _, regs = alimentar(ver, 100)
    assert regs == [((), ())]


def test_registro_fallido_se_reintenta():
    """Si no se llama a marcar_registrado, el veredicto se vuelve a ofrecer."""
    ver = V.Veredicto(CLAVES)
    n = 0
    for _ in range(V.FRAMES_VOTO + V.FRAMES_ESTABLE + 5):
        if ver.observar(frame())["registrar"]:
            n += 1
    assert n > 1


def test_mediana_de_la_ventana():
    ver = V.Veredicto(["T01"])
    for i in range(V.FRAMES_VOTO):
        ver.observar({"T01": True}, scores={"T01": float(i)}, sims={"T01": 0.5})
    assert ver.mediana_ventana("T01") == (7.0, 0.5)
    assert ver.mediana_ventana("NO") == (None, None)
