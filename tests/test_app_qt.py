# -*- coding: utf-8 -*-
"""La aplicacion Qt: escaner (decision 15), recordar ajustes (decision 21) y
re-guardar un config sin cambiar la huella de geometria."""

import json
import os
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QSettings  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ventana(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ROI_DATOS", str(tmp_path / "datos"))
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        QSettings.setPath(fmt, QSettings.UserScope, str(tmp_path / "ajustes"))
    (tmp_path / "datos" / "procesos" / "IP14 - DISPLAY").mkdir(parents=True)
    (tmp_path / "datos" / "procesos" / "IP14 - BACKGLASS").mkdir(parents=True)
    cwd = os.getcwd()
    import roi_app
    v = roi_app.VentanaPrincipal()
    recibidos = []
    v._escaneado = recibidos.append
    v.recibidos = recibidos
    yield v
    v.close()
    os.chdir(cwd)


SERIAL = "FYJ341770HF1WMWADA148RU26B"


@pytest.mark.parametrize("widget", ["spin_cam", "cmb_receta"])
def test_escaner_con_foco_en_un_control(app, ventana, widget):
    """El foco en el selector de camara o en el combo se tragaba el codigo."""
    w = getattr(ventana, widget)
    ventana.show()
    w.setFocus()
    app.processEvents()
    cam_antes = ventana.spin_cam.value()
    receta_antes = ventana.cmb_receta.currentText()
    QTest.keyClicks(w, SERIAL)
    QTest.keyClick(w, Qt.Key_Return)
    assert ventana.recibidos == [SERIAL]
    assert ventana.spin_cam.value() == cam_antes
    assert ventana.cmb_receta.currentText() == receta_antes


def test_tecleo_lento_no_forma_un_codigo(app, ventana, monkeypatch):
    """Una persona tecleando no es un lector: entre teclas pasa mas de la pausa."""
    import roi_app
    t = [1000.0]
    monkeypatch.setattr(roi_app.time, "time", lambda: t[0])
    w = ventana.spin_cam
    for ch in "ABC123XYZ":
        t[0] += roi_app.PAUSA_ESCANER + 0.1
        QTest.keyClicks(w, ch)
    t[0] += roi_app.PAUSA_ESCANER + 0.1
    QTest.keyClick(w, Qt.Key_Return)
    assert ventana.recibidos == []


def test_pausa_escaner():
    import roi_app
    assert roi_app.PAUSA_ESCANER == 0.5


def test_escaneo_sin_inspeccion_corriendo_responde(app, tmp_path, monkeypatch):
    """Todo escaneo produce una respuesta visible, nunca silencio."""
    monkeypatch.setenv("ROI_DATOS", str(tmp_path / "datos"))
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        QSettings.setPath(fmt, QSettings.UserScope, str(tmp_path / "ajustes"))
    cwd = os.getcwd()
    import roi_app
    v = roi_app.VentanaPrincipal()
    try:
        v._escaneado(SERIAL)
        assert "no está corriendo" in v.statusBar().currentMessage() \
            or "Iniciar" in v.statusBar().currentMessage()
    finally:
        v.close()
        os.chdir(cwd)


def test_recuerda_receta_camara_y_casillas(app, tmp_path, monkeypatch):
    monkeypatch.setenv("ROI_DATOS", str(tmp_path / "datos"))
    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        QSettings.setPath(fmt, QSettings.UserScope, str(tmp_path / "ajustes"))
    for r in ("IP14 - DISPLAY", "IP14 - BACKGLASS"):
        (tmp_path / "datos" / "procesos" / r).mkdir(parents=True, exist_ok=True)
    cwd = os.getcwd()
    import roi_app
    try:
        v = roi_app.VentanaPrincipal()
        v.cmb_receta.setCurrentText("IP14 - DISPLAY")
        v.spin_cam.setValue(2)
        v.chk_cierre.setChecked(True)
        v.close()
        v2 = roi_app.VentanaPrincipal()
        assert v2.cmb_receta.currentText() == "IP14 - DISPLAY"
        assert v2.spin_cam.value() == 2
        assert v2.chk_cierre.isChecked()
        v2.close()
    finally:
        os.chdir(cwd)


def test_reguardar_config_no_cambia_la_huella(app, receta, tmp_path):
    """Abrir un config en el marcador y guardarlo sin tocar nada no puede
    invalidar la calibracion."""
    import copy
    import inspector
    import marcador

    cfg = receta("IP15PM - DISPLAY")
    huella_antes = inspector.huella_geometria(cfg)
    ruta = inspector.ruta_config()
    with open(ruta, encoding="utf-8") as f:
        previo = {k: v for k, v in json.load(f).items() if k != "puntos"}

    W, H = cfg["image_size"]
    ref = tmp_path / "ref.png"
    img = QImage(W, H, QImage.Format_RGB888)
    img.fill(0)
    assert img.save(str(ref))

    lz = marcador.Lienzo()
    assert lz.cargar_imagen(str(ref))
    lz.cargar_config(ruta)
    lz.guardar_config(ruta, "IP15PM - DISPLAY", "iPhone", previo=previo)

    cfg2 = inspector.cargar_config()
    assert inspector.huella_geometria(cfg2) == huella_antes
    assert set(cfg2["zonas"]) == set(cfg["zonas"])
