# -*- coding: utf-8 -*-
"""Maquina de estados del cierre: decisiones 9, 10 y 11.

Los temporizadores que ve el operario (QR_LCD -> ESPERA_SCAN, la cuenta atras
de ESPERA_SCAN) van en segundos de reloj; se prueban con un reloj falso y
fotogramas a ritmos distintos. Los internos (PIDE_LCD, HECHO, ERROR) cuentan
fotogramas y tienen una salida que no depende de `hay_pieza`."""

import threading
import time as _time_real
import types

import numpy as np
import pytest

import cierre


# ---------------------------------------------------------------------------
class Reloj:
    """Sustituye al modulo `time` dentro de cierre.py. `sleep` es el de verdad
    porque lo usa el hilo de trabajo."""

    def __init__(self):
        self.t = 1_000_000.0

    def time(self):
        return self.t

    def avanzar(self, seg):
        self.t += seg

    @staticmethod
    def sleep(s):
        _time_real.sleep(s)


class DBFalsa:
    """Lo que cierre.py usa de roi_db, con registro de llamadas."""

    class ErrorCierre(Exception):
        def __init__(self, codigo, mensaje):
            super().__init__(mensaje)
            self.codigo = codigo

    def __init__(self, previos_etapa=0, aviso_lcd=None):
        self.previos_etapa = previos_etapa
        self.aviso_lcd = aviso_lcd
        self.guardados = []
        self.lcd_consultados = []

    def cargar_config(self):
        return {}

    def verificar_unidad(self, gcn, etapa=None):
        return {"gcn": gcn, "previos_etapa": self.previos_etapa,
                "registros_previos": self.previos_etapa}

    def verificar_lcd(self, gcn, serial, exigir_emparejamiento=False):
        self.lcd_consultados.append((gcn, serial, exigir_emparejamiento))
        if self.aviso_lcd:
            return {"validado": False, "aviso": self.aviso_lcd}
        return {"validado": True, "aviso": None}

    def a_base64(self, img, calidad):
        return ("b64", 3, "sha")

    def guardar_cierre(self, gcn, b64h, b64l, **kw):
        self.guardados.append(dict(kw, gcn=gcn))
        return {"id": 4242}


class QRFalso:
    """Lo que cierre.py usa de roi_qr. `lcd` es lo que se 'lee' en la zona
    de la parte en este momento; `gcn` lo del giftcard."""

    def __init__(self):
        self.gcn = None
        self.lcd = None

    def gcn_en(self, warped, zona):
        return (self.gcn, self.gcn)

    def leer(self, warped, zona):
        return [self.lcd] if self.lcd else []

    def leer_crudo(self, frame, zona, centros, tam):
        return [self.lcd] if self.lcd else []


@pytest.fixture
def entorno(receta, monkeypatch):
    reloj = Reloj()
    db = DBFalsa()
    qr = QRFalso()
    monkeypatch.setattr(cierre, "time", reloj)
    monkeypatch.setattr(cierre, "roi_db", db)
    monkeypatch.setattr(cierre, "roi_qr", qr)
    creados = []

    def nuevo(nombre="IP15PM - DISPLAY", **kw):
        cfg = receta(nombre, **kw)
        # Objeto RECIEN creado, con activo=True: disponible sale True porque
        # roi_db.cargar_config no falla. Nunca reutilizar uno entre pruebas.
        c = cierre.Cierre(cfg, nombre, verbose=False)
        creados.append(c)
        return c

    e = types.SimpleNamespace(reloj=reloj, db=db, qr=qr, nuevo=nuevo)
    yield e
    for c in creados:
        c.cerrar()


IMG = np.zeros((900, 1600, 3), np.uint8)


def esperar(c, estados, seg=2.0):
    """Espera a que el hilo de trabajo deje la secuencia en uno de `estados`."""
    if isinstance(estados, str):
        estados = (estados,)
    fin = _time_real.time() + seg
    while _time_real.time() < fin:
        if c.estado in estados and not c._ocupado:
            return
        _time_real.sleep(0.005)
    raise AssertionError(f"estado {c.estado!r}, se esperaba {estados}")


def llevar_a_pide_lcd(e, c):
    c.procesar(IMG, estable=True, es_pasa=True)
    assert c.estado == cierre.GIFTCARD
    e.qr.gcn = "CX666666"
    c.procesar(IMG, True, True)
    esperar(c, cierre.PIDE_LCD)
    e.qr.gcn = None


def llevar_a_qr_lcd(e, c):
    llevar_a_pide_lcd(e, c)
    c.procesar(IMG, True, False, hay_pieza=False)
    assert c.estado == cierre.QR_LCD


# ---------------------------------------------------------------------------
def test_disponible(entorno):
    c = entorno.nuevo()
    assert c.disponible, c.motivo_no_disponible
    assert c.parte == "DISPLAY"


def test_inactivo_es_inerte(receta):
    c = cierre.Cierre(receta(), "IP15PM - DISPLAY", activo=False, verbose=False)
    assert not c.disponible
    c.procesar(IMG, True, True)
    c.sin_tapete()
    assert c.estado == cierre.INACTIVO


def test_arranca_solo_con_pasa_estable(entorno):
    c = entorno.nuevo()
    c.procesar(IMG, estable=False, es_pasa=True)
    assert c.estado == cierre.INACTIVO
    c.procesar(IMG, estable=True, es_pasa=False)
    assert c.estado == cierre.INACTIVO
    c.procesar(IMG, estable=True, es_pasa=True)
    assert c.estado == cierre.GIFTCARD


# --- PIDE_LCD: tres salidas --------------------------------------------------

def test_pide_lcd_sale_si_la_pieza_sale(entorno):
    c = entorno.nuevo()
    llevar_a_pide_lcd(entorno, c)
    c.procesar(IMG, True, False, hay_pieza=False)
    assert c.estado == cierre.QR_LCD


def test_pide_lcd_sale_si_lee_codigo_aunque_hay_pieza_siga(entorno):
    c = entorno.nuevo()
    llevar_a_pide_lcd(entorno, c)
    entorno.qr.lcd = "FYJ341770HF1WMWADA148RU26B"
    c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.QR_LCD


def test_pide_lcd_sale_por_tiempo_con_hay_pieza_clavado(entorno):
    """Decision 9: con el display puesto `hay_pieza` se queda en True y no hay
    lectura. Sin la salida por tiempo la secuencia se quedaba aqui para siempre."""
    c = entorno.nuevo()
    llevar_a_pide_lcd(entorno, c)
    for _ in range(cierre.FRAMES_LIMITE_PIDE_LCD - 1):
        c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.PIDE_LCD
    c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.QR_LCD


# --- QR_LCD y ESPERA_SCAN: segundos de reloj ---------------------------------

@pytest.mark.parametrize("fps", [10, 15, 25, 60])
def test_qr_lcd_se_rinde_a_los_10_segundos_a_cualquier_fps(entorno, fps):
    """Decision 11: la duracion real no depende de los fotogramas por segundo."""
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    n_antes = int(cierre.SEG_RENDIRSE_CAMARA * fps) - 1
    for _ in range(n_antes):
        entorno.reloj.avanzar(1.0 / fps)
        c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.QR_LCD
    entorno.reloj.avanzar(2.0 / fps)
    c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.ESPERA_SCAN
    assert c.foto_lcd is not None          # foto tomada al rendirse


@pytest.mark.parametrize("fps", [12, 25])
def test_espera_scan_guarda_sin_serial_a_los_20_segundos(entorno, fps):
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    entorno.reloj.avanzar(cierre.SEG_RENDIRSE_CAMARA + 0.1)
    c.procesar(IMG, True, False)
    assert c.estado == cierre.ESPERA_SCAN

    paso = 1.0 / fps
    t = 0.0
    while t + paso < cierre.SEG_ESPERA_SCAN - 0.01:
        entorno.reloj.avanzar(paso)
        t += paso
        c.procesar(IMG, True, False)
        assert c.estado == cierre.ESPERA_SCAN, f"guardo a los {t:.2f}s"
    entorno.reloj.avanzar(cierre.SEG_ESPERA_SCAN - t + 0.01)
    c.procesar(IMG, True, False)
    esperar(c, cierre.HECHO)
    assert c.sin_serial
    assert entorno.db.guardados[-1]["serial_lcd"] is None
    assert "SIN serial" in c.mensaje


def test_cuenta_atras_avanza_aunque_se_refresque_el_mensaje(entorno):
    """El refresco del texto cada 10 fotogramas reiniciaba el contador: la
    pantalla decia 'quedan 39s' para siempre."""
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    entorno.reloj.avanzar(cierre.SEG_RENDIRSE_CAMARA + 0.1)
    c.procesar(IMG, True, False)
    # El texto se refresca cada 10 fotogramas: el 30 cae a los 15 s.
    for _ in range(30):
        entorno.reloj.avanzar(0.5)
        c.procesar(IMG, True, False)
    assert c.estado == cierre.ESPERA_SCAN
    assert "espera 6s" in c.mensaje, c.mensaje


def test_escaner_en_espera_scan(entorno):
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    entorno.reloj.avanzar(cierre.SEG_RENDIRSE_CAMARA + 0.1)
    c.procesar(IMG, True, False)
    assert c.escanear("fyj341770hf1wmwada148ru26b")
    esperar(c, cierre.HECHO)
    g = entorno.db.guardados[-1]
    assert g["serial_lcd"] == "FYJ341770HF1WMWADA148RU26B"
    assert g["origen"] == "ESCANER"
    assert g["parte"] == "DISPLAY"


def test_escaneo_fuera_de_momento_se_rechaza(entorno):
    c = entorno.nuevo()
    assert not c.escanear("FYJ341770HF1WMWADA148RU26B")      # INACTIVO
    llevar_a_pide_lcd(entorno, c)
    assert not c.escanear("FYJ341770HF1WMWADA148RU26B")      # PIDE_LCD


def test_lectura_de_camara_completa_el_cierre(entorno):
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    entorno.qr.lcd = "FYJ341770HF1WMWADA148RU26B"
    for _ in range(cierre.FRAMES_QUIETO_LCD):
        c.procesar(IMG, True, False)
    esperar(c, cierre.HECHO)
    assert entorno.db.guardados[-1]["origen"] == "CAMARA"


# --- Decision 3 dentro del cierre: el aviso no impide guardar ----------------

def test_emparejamiento_que_no_coincide_guarda_igual(entorno):
    entorno.db.aviso_lcd = "El display X no coincide con el emparejamiento"
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    entorno.qr.lcd = "FYJ341770HF1WMWADA148RU26B"
    for _ in range(cierre.FRAMES_QUIETO_LCD):
        c.procesar(IMG, True, False)
    esperar(c, cierre.HECHO)
    assert "UNIDAD CERRADA" in c.mensaje and "no coincide" in c.mensaje
    assert entorno.db.lcd_consultados[-1][2] is False
    assert len(entorno.db.guardados) == 1


def test_backglass_no_consulta_emparejamiento_de_lcd(entorno):
    c = entorno.nuevo("IP14 - BACKGLASS", tipo_parte="Backglass")
    assert c.parte == "BACKGLASS"
    llevar_a_qr_lcd(entorno, c)
    entorno.qr.lcd = "FYJ341770HF1WMWADA148RU26B"
    for _ in range(cierre.FRAMES_QUIETO_LCD):
        c.procesar(IMG, True, False)
    esperar(c, cierre.HECHO)
    assert entorno.db.lcd_consultados == []
    assert entorno.db.guardados[-1]["parte"] == "BACKGLASS"
    assert entorno.db.guardados[-1]["etapa"] == "IP14 - BACKGLASS"


# --- Decision 2 dentro del cierre: repetir avisa, no bloquea -----------------

def test_repeticion_en_la_etapa_avisa_y_sigue(entorno):
    entorno.db.previos_etapa = 2
    c = entorno.nuevo()
    llevar_a_pide_lcd(entorno, c)
    assert "ya cerrada 2 veces en esta etapa" in c.mensaje


# --- HECHO y ERROR: salida rapida y limite duro ------------------------------

def _a_final(c, estado):
    c._poner(estado, "final", cierre.VERDE)


@pytest.mark.parametrize("estado,minimo,limite", [
    (cierre.HECHO, cierre.FRAMES_MINIMOS_HECHO, cierre.FRAMES_LIMITE_HECHO),
    (cierre.ERROR, cierre.FRAMES_MINIMOS_ERROR, cierre.FRAMES_LIMITE_ERROR),
])
def test_final_se_limpia_al_retirar_tras_el_minimo(entorno, estado, minimo, limite):
    c = entorno.nuevo()
    _a_final(c, estado)
    for _ in range(minimo - 1):
        c.procesar(IMG, True, False, hay_pieza=False)
    assert c.estado == estado            # nadie lo ha leido aun
    c.procesar(IMG, True, False, hay_pieza=False)
    assert c.estado == cierre.INACTIVO


@pytest.mark.parametrize("estado,limite", [
    (cierre.HECHO, cierre.FRAMES_LIMITE_HECHO),
    (cierre.ERROR, cierre.FRAMES_LIMITE_ERROR),
])
def test_final_se_limpia_por_limite_con_hay_pieza_clavado(entorno, estado, limite):
    """Decision 9: un ERROR se quedo clavado en pantalla para siempre."""
    c = entorno.nuevo()
    _a_final(c, estado)
    for _ in range(limite - 1):
        c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == estado
    c.procesar(IMG, True, False, hay_pieza=True)
    assert c.estado == cierre.INACTIVO


def test_falta_zona_de_la_parte_es_error_tras_validar(entorno):
    c = entorno.nuevo(con_parte=False)
    llevar_a_pide_lcd(entorno, c)
    c.procesar(IMG, True, False)
    assert c.estado == cierre.ERROR
    assert "NO se registro" in c.mensaje


def test_sin_zona_giftcard_no_disponible(entorno):
    c = entorno.nuevo(con_giftcard=False)
    assert not c.disponible
    assert "giftcard" in c.motivo_no_disponible


# --- Decision 10: perder el tapete lo decide la secuencia --------------------

def test_sin_tapete_en_giftcard_cancela(entorno):
    c = entorno.nuevo()
    c.procesar(IMG, True, True)
    assert c.estado == cierre.GIFTCARD
    c.sin_tapete()
    assert c.estado == cierre.INACTIVO


def test_sin_tapete_desde_pide_lcd_aguanta(entorno):
    """Sacar el housing es el siguiente paso del proceso: no cancela."""
    c = entorno.nuevo()
    llevar_a_pide_lcd(entorno, c)
    for _ in range(cierre.FRAMES_SIN_TAPETE_CANCELA - 1):
        c.sin_tapete()
    assert c.estado == cierre.PIDE_LCD
    assert c.gcn == "CX666666" and c.foto_housing is not None
    c.sin_tapete()
    assert c.estado == cierre.INACTIVO


def test_volver_a_ver_el_tapete_reinicia_la_cuenta(entorno):
    c = entorno.nuevo()
    llevar_a_qr_lcd(entorno, c)
    for _ in range(cierre.FRAMES_SIN_TAPETE_CANCELA - 1):
        c.sin_tapete()
    c.procesar(IMG, True, False)
    for _ in range(cierre.FRAMES_SIN_TAPETE_CANCELA - 1):
        c.sin_tapete()
    assert c.estado != cierre.INACTIVO


def test_sin_tapete_en_inactivo_no_hace_nada(entorno):
    c = entorno.nuevo()
    c.sin_tapete()
    assert c.estado == cierre.INACTIVO


# --- suprime_registro --------------------------------------------------------

def test_no_se_registran_tornillos_mientras_esta_la_parte(entorno):
    c = entorno.nuevo()
    assert not c.suprime_registro()
    llevar_a_pide_lcd(entorno, c)
    assert c.suprime_registro()
