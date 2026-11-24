#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admin.py — Modulo de administracion: recetas, ROIs y calibracion.

QUE RESUELVE
Hasta ahora, montar una receta nueva en un banco de trabajo pedia terminal:
capturar la referencia con un comando, marcar las ROIs en una pagina web,
descargar un JSON, moverlo a la carpeta correcta, y correr dos comandos mas para
calibrar. Cada uno de esos pasos es una oportunidad de equivocarse, y ya nos
costo una tarde. Aqui estan los cinco detras de una clave, en orden, y cada uno
dice si salio bien.

NO REIMPLEMENTA NADA
Todo lo que decide -rectificar, extraer recortes, calcular umbrales y el piso de
similitud, guardar la huella de geometria- se llama del motor. Lo unico nuevo es
la pantalla y el orden. Si manana cambia la forma de calibrar, cambia en un solo
sitio y esto la hereda.

POR QUE UNA CLAVE
Estas pantallas reescriben la calibracion de la estacion. Un despiste aqui no da
un error: da una estacion que sigue trabajando y juzga mal. Ver el estado no
pide clave; cambiarlo, si.
"""

import json
import os
import time
import traceback
from collections import deque
from types import SimpleNamespace

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap, QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QComboBox, QListWidget,
                               QMessageBox, QFileDialog, QInputDialog, QWidget,
                               QPlainTextEdit, QSpinBox, QGroupBox, QSplitter)

import marcador
import motor
import rutas
import seguridad

FONDO = "#1b1817"
PANEL = "#241f1c"
TEXTO = "#ebebe8"
VERDE = "#69d753"
ROJO = "#e24b4a"
AMBAR = "#ffbd2e"


# ======================================================================
class DialogoAcceso(QDialog):
    """Usuario y clave. Nada mas; lo que decide esta en seguridad.py."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Administración")
        self.setMinimumWidth(380)
        self.setStyleSheet(f"background:{FONDO}; color:{TEXTO};")
        v = QVBoxLayout(self)
        t = QLabel("Acceso de administración")
        f = QFont(); f.setPointSize(15); f.setBold(True); t.setFont(f)
        v.addWidget(t)
        v.addWidget(QLabel("Estas pantallas cambian la calibración de esta estación."))
        self.usuario = QLineEdit("admin")
        self.clave = QLineEdit(); self.clave.setEchoMode(QLineEdit.Password)
        # returnPressed: quien entra aqui lo hace muchas veces al dia y no
        # deberia tener que buscar el boton con el raton.
        self.clave.returnPressed.connect(self.accept)
        v.addWidget(QLabel("Usuario")); v.addWidget(self.usuario)
        v.addWidget(QLabel("Clave")); v.addWidget(self.clave)
        self.aviso = QLabel(""); self.aviso.setWordWrap(True)
        v.addWidget(self.aviso)
        h = QHBoxLayout(); h.addStretch(1)
        b = QPushButton("Cancelar"); b.clicked.connect(self.reject); h.addWidget(b)
        e = QPushButton("Entrar"); e.setDefault(True); e.clicked.connect(self.accept)
        h.addWidget(e)
        v.addLayout(h)

    def accept(self):
        esp = seguridad.bloqueado()
        if esp:
            self._error(f"Demasiados intentos. Espera {esp} s.")
            return
        if seguridad.verificar(self.usuario.text(), self.clave.text()):
            super().accept()
            return
        esp = seguridad.bloqueado()
        self._error(f"Credenciales incorrectas. Bloqueado {esp} s."
                    if esp else "Credenciales incorrectas.")

    def _error(self, msg):
        self.aviso.setText(msg)
        self.aviso.setStyleSheet(f"color:{ROJO};")
        self.clave.clear()
        self.clave.setFocus()


# ======================================================================
class HiloCamara(QThread):
    """Video con el conteo de marcadores, para capturar la referencia.

    Vive aparte del bucle de inspeccion: aqui no se puntua nada, solo hace falta
    ver que los 4 ArUco esten a la vista antes de congelar el rectificado."""

    fotograma = Signal(QImage)
    estado = Signal(int)
    fallo = Signal(str)
    capturada = Signal(object)          # imagen rectificada (BGR)

    def __init__(self, m, indice, tam=None, continuo=False):
        super().__init__()
        self.m = m
        self.indice = indice
        # continuo: no se corta despues de capturar. Para la pantalla que mete
        # varias unidades seguidas, donde cerrar y reabrir la camara entre una
        # y otra son dos segundos de espera por unidad y ninguna ventaja.
        self.continuo = continuo
        # Tamano al que rectificar. Con None se calcula de los marcadores, que
        # es lo correcto SOLO para la referencia, porque ahi es donde se decide
        # el lienzo. Para todo lo demas se pasa el del config.
        #
        # El calculo sale de la distancia entre los centros de los ArUco, que se
        # estiman con precision de fraccion de pixel y bailan entre fotogramas
        # -ruido, una sombra, el enfoque-. Al redondear, 1599,6 da 1600 y 1599,4
        # da 1599: nunca sale el mismo numero dos veces seguidas. Comparar esa
        # cifra contra la del config era pelearse con el ruido del sensor.
        #
        # El inspector en produccion nunca tuvo ese problema porque siempre
        # rectifica al tamano del config (ver _procesar_frame). Esto hace lo
        # mismo, que ademas es lo que garantiza que un recorte de calibracion
        # caiga en el MISMO sitio que caera luego en la linea.
        self.tam = tuple(tam) if tam else None
        self._parar = False
        self._disparar = False

    def detener(self):
        self._parar = True

    def disparar(self):
        self._disparar = True

    def run(self):
        I = self.m["inspector"]
        try:
            cap = I.abrir_camara(self.indice, "auto")
        except SystemExit as e:
            self.fallo.emit(str(e)); return
        except Exception as e:
            self.fallo.emit(f"No se pudo abrir la cámara {self.indice}: {e}"); return
        historial = deque(maxlen=15)      # ~1 s de centros, para promediar
        while not self._parar:
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            centros = I.detectar_marcadores(gray)
            n = sum(i in centros for i in I.IDS_ESQUINAS)
            if n == 4:
                historial.append({i: centros[i].copy() for i in I.IDS_ESQUINAS})
            vista = frame.copy()
            for i, c in centros.items():
                cv2.circle(vista, tuple(c.astype(int)), 12, (105, 215, 83), 3)
                cv2.putText(vista, str(i), tuple((c + 16).astype(int)),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (105, 215, 83), 2)
            if self._disparar:
                self._disparar = False
                if n == 4:
                    # Los centros se PROMEDIAN sobre el ultimo segundo en vez de
                    # tomarlos de un solo fotograma. La estimacion de cada centro
                    # tiene ruido de fraccion de pixel, y de ese ruido sale el
                    # tamano del lienzo: con un solo fotograma, dos referencias
                    # tomadas seguidas salen de 1599 y 1601, y como el tamano
                    # forma parte de la huella de geometria, recapturar la
                    # referencia invalidaba la calibracion sin motivo real.
                    # Promediando, el numero se repite.
                    estables = {i: np.mean([h[i] for h in historial], axis=0)
                                for i in I.IDS_ESQUINAS} if historial else centros
                    tam = self.tam or I.tam_canonico_desde_centros(estables)
                    self.capturada.emit(I.rectificar(frame, estables, tam))
                    if not self.continuo:
                        break
            self.estado.emit(n)
            rgb = cv2.cvtColor(vista, cv2.COLOR_BGR2RGB)
            al, an, _ = rgb.shape
            self.fotograma.emit(QImage(rgb.data, an, al, 3 * an,
                                       QImage.Format_RGB888).copy())
        cap.release()


class DialogoCaptura(QDialog):
    """Congela un rectificado con los 4 marcadores a la vista."""

    def __init__(self, m, indice, parent=None, titulo="Capturar referencia",
                 tam=None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setStyleSheet(f"background:{FONDO}; color:{TEXTO};")
        self.resize(1000, 700)
        self.imagen = None
        v = QVBoxLayout(self)
        self.lbl = QLabel("Abriendo cámara…"); self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("background:#000;")
        v.addWidget(self.lbl, 1)
        self.est = QLabel(""); self.est.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(15); f.setBold(True); self.est.setFont(f)
        v.addWidget(self.est)
        h = QHBoxLayout(); h.addStretch(1)
        c = QPushButton("Cancelar"); c.clicked.connect(self.reject); h.addWidget(c)
        self.btn = QPushButton("Capturar"); self.btn.setEnabled(False)
        self.btn.setMinimumWidth(160)
        self.btn.clicked.connect(lambda: self.hilo.disparar())
        h.addWidget(self.btn)
        v.addLayout(h)
        self.hilo = HiloCamara(m, indice, tam=tam)
        self.hilo.fotograma.connect(self._video)
        self.hilo.estado.connect(self._estado)
        self.hilo.fallo.connect(self._fallo)
        self.hilo.capturada.connect(self._capturada)
        self.hilo.start()

    def _video(self, img):
        self.lbl.setPixmap(QPixmap.fromImage(img).scaled(
            self.lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _estado(self, n):
        ok = (n == 4)
        self.btn.setEnabled(ok)
        self.est.setText("Listo para capturar" if ok else f"Marcadores visibles: {n}/4")
        self.est.setStyleSheet(f"color:{VERDE if ok else AMBAR};")

    def _fallo(self, msg):
        QMessageBox.critical(self, "Cámara", msg)
        self.reject()

    def _capturada(self, img):
        self.imagen = img
        self.accept()

    def closeEvent(self, ev):
        self.hilo.detener(); self.hilo.wait(3000); ev.accept()

    def reject(self):
        self.hilo.detener(); self.hilo.wait(3000); super().reject()


# ======================================================================
class DialogoAgregar(QDialog):
    """Mete unidades a la calibracion existente, de las dos clases, seguidas.

    Antes era un botón por unidad: abrir camara, capturar, cerrar, recalcular,
    repetir. Para cinco unidades eran cinco rondas completas, y recalcular la
    calibracion cinco veces cuando basta una.

    Aqui la camara se queda abierta y se pulsa CON o SIN segun lo que haya en
    el fixture. Los recortes se van acumulando y la calibracion se recalcula UNA
    vez al terminar: es el mismo resultado -los umbrales salen de todos los
    recortes juntos, no de un acumulado paso a paso- con una fraccion de la
    espera.

    Y si se cierra sin guardar no se toca nada, asi que probar no cuesta."""

    def __init__(self, m, indice, cfg, crops_p, crops_a, n_u, n_v, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Agregar unidades a la calibración")
        self.setStyleSheet(f"background:{FONDO}; color:{TEXTO};")
        self.resize(1040, 760)
        self.m, self.cfg = m, cfg
        self.crops_p, self.crops_a = crops_p, crops_a
        self.n_u, self.n_v = n_u, n_v
        self.ini_u, self.ini_v = n_u, n_v
        self.guardar = False
        self._pendiente = None        # "presente" | "vacia"

        v = QVBoxLayout(self)
        self.lbl = QLabel("Abriendo cámara…"); self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("background:#000;")
        v.addWidget(self.lbl, 1)

        self.est = QLabel(""); self.est.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(14); f.setBold(True); self.est.setFont(f)
        v.addWidget(self.est)

        self.cuenta = QLabel(""); self.cuenta.setAlignment(Qt.AlignCenter)
        self.cuenta.setStyleSheet("font-size:13px;")
        v.addWidget(self.cuenta)

        h = QHBoxLayout()
        self.b_con = QPushButton("Agregar esta CON tornillos")
        self.b_con.setMinimumHeight(46)
        self.b_con.clicked.connect(lambda: self._capturar("presente"))
        h.addWidget(self.b_con)
        self.b_sin = QPushButton("Agregar esta SIN tornillos")
        self.b_sin.setMinimumHeight(46)
        self.b_sin.clicked.connect(lambda: self._capturar("vacia"))
        h.addWidget(self.b_sin)
        v.addLayout(h)

        h2 = QHBoxLayout(); h2.addStretch(1)
        b = QPushButton("Descartar"); b.clicked.connect(self.reject); h2.addWidget(b)
        self.b_fin = QPushButton("Guardar y cerrar")
        self.b_fin.setMinimumWidth(190); self.b_fin.setEnabled(False)
        self.b_fin.clicked.connect(self._terminar)
        h2.addWidget(self.b_fin)
        v.addLayout(h2)

        self._refrescar()
        self.hilo = HiloCamara(m, indice, tam=tuple(cfg["image_size"]),
                               continuo=True)
        self.hilo.fotograma.connect(self._video)
        self.hilo.estado.connect(self._estado)
        self.hilo.fallo.connect(self._fallo)
        self.hilo.capturada.connect(self._llego)
        self.hilo.start()

    def _video(self, img):
        self.lbl.setPixmap(QPixmap.fromImage(img).scaled(
            self.lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _estado(self, n):
        ok = (n == 4)
        self.b_con.setEnabled(ok); self.b_sin.setEnabled(ok)
        self.est.setText("Coloca una unidad y pulsa el botón que corresponda"
                         if ok else f"Marcadores visibles: {n}/4")
        self.est.setStyleSheet(f"color:{VERDE if ok else AMBAR};")

    def _fallo(self, msg):
        QMessageBox.critical(self, "Cámara", msg); self.reject()

    def _refrescar(self):
        self.cuenta.setText(
            f"CON tornillos: {self.ini_u} → {self.n_u}          "
            f"SIN tornillos: {self.ini_v} → {self.n_v}")
        hay = (self.n_u != self.ini_u) or (self.n_v != self.ini_v)
        self.b_fin.setEnabled(hay)
        self.b_fin.setText("Guardar y cerrar" if hay else "Nada que guardar")

    def _capturar(self, tipo):
        self._pendiente = tipo
        self.b_con.setEnabled(False); self.b_sin.setEnabled(False)
        self.est.setText("Capturando… no muevas la pieza")
        self.hilo.disparar()

    def _llego(self, img):
        tipo, self._pendiente = self._pendiente, None
        if tipo is None:
            return
        I = self.m["inspector"]
        nuevos = I.crops_todos(img, self.cfg)
        if any(v is None for v in nuevos.values()):
            QMessageBox.warning(self, "Fuera de la imagen",
                                "Alguna posición cae fuera del rectificado. "
                                "Esta unidad no se agregó.")
            return
        destino = self.crops_p if tipo == "presente" else self.crops_a
        for k, val in nuevos.items():
            destino.setdefault(k, []).append(val)
        if tipo == "presente":
            self.n_u += 1
        else:
            self.n_v += 1
        self._refrescar()
        self.est.setText("Agregada. Cambia la unidad y sigue.")
        self.est.setStyleSheet(f"color:{VERDE};")

    def _terminar(self):
        self.guardar = True
        self.accept()

    def closeEvent(self, ev):
        self.hilo.detener(); self.hilo.wait(3000); ev.accept()

    def reject(self):
        self.hilo.detener(); self.hilo.wait(3000); super().reject()

    def accept(self):
        self.hilo.detener(); self.hilo.wait(3000); super().accept()


# ======================================================================
class PanelAdmin(QDialog):
    """Las cinco cosas que hacen falta para montar una receta, en orden."""

    def __init__(self, parent=None, indice_camara=0):
        super().__init__(parent)
        self.setWindowTitle("Administración — recetas y calibración")
        self.setStyleSheet(f"background:{FONDO}; color:{TEXTO};")
        self.resize(1400, 860)
        # El motor resuelve "procesos/..." contra el directorio de trabajo. La
        # ventana principal ya lo deja apuntando a la carpeta de datos, pero se
        # repite aqui -es idempotente- porque si no, abrir este panel desde otro
        # sitio mostraria "Sin referencia" para una receta que SI la tiene. Un
        # estado falso y tranquilo es peor que un error.
        rutas.preparar(verbose=False)
        motor.preparar_entorno(rutas.raiz_datos())
        self.m = motor.cargar()
        self.indice_camara = indice_camara
        self.cfg_previo = {}
        self._construir()
        self._cargar_recetas()

    # -- interfaz ----------------------------------------------------
    def _construir(self):
        raiz = QVBoxLayout(self)

        barra = QHBoxLayout()
        barra.addWidget(QLabel("Receta"))
        self.cmb = QComboBox(); self.cmb.setMinimumWidth(280)
        self.cmb.currentTextChanged.connect(self._seleccionar)
        barra.addWidget(self.cmb)
        b = QPushButton("Nueva receta…"); b.clicked.connect(self._nueva)
        barra.addWidget(b)
        b = QPushButton("Renombrar…"); b.clicked.connect(self._renombrar)
        barra.addWidget(b)
        barra.addSpacing(20)
        barra.addWidget(QLabel("Cámara"))
        self.spin = QSpinBox(); self.spin.setRange(0, 8)
        self.spin.setValue(self.indice_camara)
        barra.addWidget(self.spin)
        barra.addStretch(1)
        b = QPushButton("Cambiar clave…"); b.clicked.connect(self._cambiar_clave)
        barra.addWidget(b)
        raiz.addLayout(barra)

        div = QSplitter(Qt.Horizontal)

        izq = QWidget(); vi = QVBoxLayout(izq)
        vi.setContentsMargins(0, 0, 0, 0)
        self.lienzo = marcador.Lienzo()
        self.lienzo.cambio.connect(self._refrescar_lista)
        vi.addWidget(self.lienzo, 1)
        hh = QHBoxLayout()
        hh.addWidget(QLabel("Marcar como"))
        self.cmb_tipo = QComboBox(); self.cmb_tipo.addItems(marcador.TIPOS)
        self.cmb_tipo.currentTextChanged.connect(
            lambda t: setattr(self.lienzo, "tipo", t))
        hh.addWidget(self.cmb_tipo)
        hh.addWidget(QLabel("Lado por defecto"))
        self.spin_tam = QSpinBox(); self.spin_tam.setRange(20, 400)
        self.spin_tam.setValue(70)
        self.spin_tam.valueChanged.connect(
            lambda v: setattr(self.lienzo, "tam", v))
        hh.addWidget(self.spin_tam)
        hh.addStretch(1)
        hh.addWidget(QLabel("Arrastra para crear · arrastra encima para mover · "
                            "clic derecho para borrar"))
        vi.addLayout(hh)
        div.addWidget(izq)

        der = QWidget(); der.setMaximumWidth(430); vd = QVBoxLayout(der)

        g1 = QGroupBox("1 · Referencia"); v1 = QVBoxLayout(g1)
        self.lbl_ref = QLabel("—"); self.lbl_ref.setWordWrap(True)
        v1.addWidget(self.lbl_ref)
        b = QPushButton("Capturar referencia con la cámara")
        b.clicked.connect(self._capturar_referencia); v1.addWidget(b)
        b = QPushButton("Capturar con la parte puesta (display / backglass)…")
        b.setToolTip("Para marcar la zona del código del display o del "
                     "backglass, que no se ve en la referencia porque ahí la "
                     "parte todavía no está.")
        b.clicked.connect(self._capturar_display); v1.addWidget(b)
        vd.addWidget(g1)

        self.lbl_etapa = QLabel("")
        self.lbl_etapa.setWordWrap(True)
        self.lbl_etapa.setToolTip(
            "La parte que se pedirá sale del NOMBRE de la receta. "
            "Nombra «IP14 - BACKGLASS» o «IP14 - DISPLAY» y se encarga sola.")
        v1.addWidget(self.lbl_etapa)

        g2 = QGroupBox("2 · Posiciones"); v2 = QVBoxLayout(g2)
        self.lista = QListWidget()
        self.lista.setStyleSheet(f"background:{FONDO}; font-family:monospace;")
        v2.addWidget(self.lista)
        b = QPushButton("Guardar posiciones en la receta")
        b.clicked.connect(self._guardar_rois); v2.addWidget(b)
        vd.addWidget(g2, 1)

        g3 = QGroupBox("3 · Calibración"); v3 = QVBoxLayout(g3)
        self.lbl_cal = QLabel("—"); self.lbl_cal.setWordWrap(True)
        v3.addWidget(self.lbl_cal)
        b = QPushButton("Calibrar con la cámara (2 capturas)")
        b.clicked.connect(self._calibrar_camara); v3.addWidget(b)
        b = QPushButton("Calibrar desde dos fotos ya guardadas…")
        b.clicked.connect(self._calibrar_fotos); v3.addWidget(b)
        b = QPushButton("Agregar unidades (CON y SIN, las que quieras)…")
        b.clicked.connect(self._agregar); v3.addWidget(b)
        vd.addWidget(g3)

        self.reg = QPlainTextEdit(); self.reg.setReadOnly(True)
        self.reg.setPlaceholderText(
            "Lo que vaya pasando se cuenta aquí.\n\n"
            "Todo se guarda solo: las posiciones al pulsar «Guardar "
            "posiciones», y la calibración en cuanto termina. No hay nada "
            "más que confirmar.")
        self.reg.setMaximumHeight(170)
        self.reg.setStyleSheet(f"background:#141110; font-family:monospace; "
                               f"font-size:11px; color:{TEXTO};")
        vd.addWidget(self.reg)
        div.addWidget(der)
        div.setStretchFactor(0, 1)
        raiz.addWidget(div, 1)

        pie = QHBoxLayout()
        self.lbl_pie = QLabel("")
        self.lbl_pie.setWordWrap(True)
        pie.addWidget(self.lbl_pie, 1)
        b = QPushButton("Cerrar")
        b.setMinimumWidth(150)
        b.clicked.connect(self.accept)
        pie.addWidget(b)
        raiz.addLayout(pie)

    def _decir(self, msg, color=None):
        self.reg.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")
        self.reg.verticalScrollBar().setValue(
            self.reg.verticalScrollBar().maximum())

    # -- recetas -----------------------------------------------------
    def _cargar_recetas(self, elegir=None):
        rs = rutas.hay_recetas()
        self.cmb.blockSignals(True)
        self.cmb.clear(); self.cmb.addItems(rs)
        if elegir and elegir in rs:
            self.cmb.setCurrentText(elegir)
        self.cmb.blockSignals(False)
        self._seleccionar(self.cmb.currentText())

    def _valido(self, nombre):
        """(ok, motivo). Mismo criterio para crear y para renombrar."""
        n = (nombre or "").strip()
        if not n:
            return (False, "El nombre está vacío.")
        if n[0] in "._":
            # La app oculta a proposito lo que empieza por '.' o '_': son
            # carpetas de trabajo. Una receta con ese nombre existiria en el
            # disco y no apareceria nunca en la lista.
            return (False, "El nombre no puede empezar por '.' ni por '_'.")
        if "/" in n or ":" in n:
            return (False, "El nombre no puede llevar '/' ni ':'.")
        if os.path.exists(os.path.join(rutas.dir_procesos(), n)):
            return (False, f"Ya hay una receta llamada «{n}».")
        return (True, "")

    def _renombrar(self):
        """Cambia el nombre de la receta: carpeta, config y banco.

        El nombre no es solo una etiqueta: es el nombre de la carpeta, el campo
        `proceso` del config -el inspector avisa si no coinciden- y la carpeta
        del banco de evidencia. Cambiarlo a mano en el Finder deja las otras dos
        cosas apuntando al nombre viejo, y el aviso que sale despues no dice que
        el problema fue el renombrado.

        Lo que NO se toca es el log: las inspecciones ya registradas se quedan
        con el nombre que tenian cuando ocurrieron. Reescribir eso seria falsear
        el historial."""
        viejo = self.cmb.currentText()
        if not viejo:
            return
        nuevo, ok = QInputDialog.getText(
            self, "Renombrar receta",
            f"Nombre nuevo para «{viejo}»\n"
            f"(proceso y modelo, p.ej. «CLOSING - 15PM»):", text=viejo)
        nuevo = (nuevo or "").strip()
        if not ok or nuevo == viejo:
            return
        bien, motivo = self._valido(nuevo)
        if not bien:
            QMessageBox.warning(self, "Nombre no válido", motivo)
            return
        bien, motivo = self.aplicar_renombrado(viejo, nuevo)
        if not bien:
            QMessageBox.critical(self, "No se pudo renombrar", motivo)
            return
        self._cargar_recetas(elegir=nuevo)

    def aplicar_renombrado(self, viejo, nuevo):
        """El renombrado en si, sin dialogos. Aparte para poder probarlo."""
        I = self.m["inspector"]
        try:
            os.rename(os.path.join(rutas.dir_procesos(), viejo),
                      os.path.join(rutas.dir_procesos(), nuevo))
        except OSError as e:
            return (False, str(e))

        # El campo `proceso` del config, para que no salte el aviso de
        # "este config fue marcado para otro proceso".
        I.set_receta(nuevo)
        try:
            with open(I.ruta_config(), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["proceso"] = nuevo
            tmp = I.ruta_config() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp, I.ruta_config())
        except FileNotFoundError:
            pass          # aun no tiene config; nada que actualizar
        except Exception as e:
            self._decir(f"Renombrada, pero no se pudo actualizar el config: {e}")

        # El banco, para que la evidencia vieja y la nueva sigan juntas.
        bv = os.path.join(rutas.dir_banco(), viejo)
        bn = os.path.join(rutas.dir_banco(), nuevo)
        if os.path.isdir(bv) and not os.path.exists(bn):
            try:
                os.rename(bv, bn)
                self._decir("Banco de evidencia renombrado también.")
            except OSError as e:
                self._decir(f"El banco no se pudo renombrar: {e}")

        self._decir(f"«{viejo}» ahora es «{nuevo}».")
        self._decir("El log conserva el nombre viejo en las inspecciones ya "
                    "registradas: es historial y no se reescribe.")
        return (True, "")

    def _nueva(self):
        proc, ok = QInputDialog.getText(
            self, "Nueva receta", "Nombre de la receta\n"
            "(proceso y modelo, p.ej. «CLOSING - 15PM»):")
        proc = (proc or "").strip()
        if not ok or not proc:
            return
        # Se rechazan los nombres que empiezan por '_' o '.': la app los oculta
        # a proposito -son carpetas de trabajo- y una receta con ese nombre
        # existiria en el disco pero nunca apareceria en la lista.
        bien, motivo = self._valido(proc)
        if not bien:
            QMessageBox.warning(self, "Nombre no válido", motivo)
            return
        d = os.path.join(rutas.dir_procesos(), proc)
        modelo, _ = QInputDialog.getText(
            self, "Nueva receta", "Modelo (p.ej. «iPhone 15 Pro Max»):")
        os.makedirs(d, exist_ok=True)
        self._modelo_nuevo = (modelo or "").strip()
        self._decir(f"Receta «{proc}» creada. Falta capturar la referencia.")
        self._cargar_recetas(elegir=proc)

    def _seleccionar(self, nombre):
        if not nombre:
            return
        I = self.m["inspector"]
        I.set_receta(nombre)
        ref = I.ruta_referencia()
        if os.path.exists(ref):
            self.lienzo.cargar_imagen(ref)
            an, al = self.lienzo.tam_canvas()
            self.lbl_ref.setText(f"referencia.png · {an}×{al} px")
            self.lbl_ref.setStyleSheet(f"color:{VERDE};")
        else:
            self.lienzo.pix = None; self.lienzo.update()
            self.lbl_ref.setText("Sin referencia. Es el primer paso.")
            self.lbl_ref.setStyleSheet(f"color:{AMBAR};")
        cfg = self.lienzo.cargar_config(I.ruta_config())
        self.cfg_previo = {k: v for k, v in (cfg or {}).items() if k != "puntos"}
        self._etapa_detectada(nombre, cfg or {})
        self._refrescar_lista()
        self._estado_calibracion()

    def _etapa_detectada(self, nombre, cfg):
        """Ensena que parte se deduce del nombre de esta receta.

        La parte sale del nombre -«IP14 - BACKGLASS» habla por si solo- y eso
        es comodo porque no hay un segundo campo que ajustar. Pero una
        deduccion silenciosa es una trampa: alguien nombra la receta de otra
        forma, el programa decide otra cosa, y nadie se entera hasta que la
        pantalla le pide al operario la pieza equivocada. Aqui se ve antes."""
        try:
            etapa, parte = self.m["cierre"]._etapa_de_cfg(cfg, nombre)
        except Exception:
            return
        self.lbl_etapa.setText(f"Cierre: etapa «{etapa}» · pedirá el {parte}")
        # Ambar cuando cae al valor por defecto sin que el nombre lo diga: es el
        # caso en el que conviene mirar dos veces.
        dice = any(w in nombre.upper() for w in ("BACKGLASS", "BACK GLASS",
                                                "DISPLAY", "LCD"))
        self.lbl_etapa.setStyleSheet(
            f"font-size:12px; color:{VERDE if dice else AMBAR};")

    def _estado_calibracion(self):
        I = self.m["inspector"]
        u = None
        try:
            u = I.cargar_umbrales()
        except Exception:
            pass
        if not u:
            self.lbl_cal.setText("Sin calibrar.")
            self.lbl_cal.setStyleSheet(f"color:{AMBAR};")
            self._pie("Falta calibrar esta receta para poder inspeccionar.", AMBAR)
            return
        self.lbl_cal.setText(
            f"{u.get('n_unidades', '?')} unidad(es) con tornillos · "
            f"{u.get('n_vacios', '?')} vacía(s) · "
            f"umbral global {u.get('umbral_global', '?')}\n"
            f"calibrado {str(u.get('creado', ''))[:16]}")
        self.lbl_cal.setStyleSheet(f"color:{VERDE};")
        n_u = u.get("n_unidades", 0)
        if isinstance(n_u, int) and n_u < 3:
            self._pie(f"Lista para operar. Con {n_u} unidad(es) el umbral es "
                      f"provisional: agrega 2 o 3 más con tornillos cuando "
                      f"pasen por la línea y afinará solo.", AMBAR)
        else:
            self._pie("Lista para operar. Cierra este panel, marca «Cierre de "
                      "unidad» y pulsa Iniciar.", VERDE)

    def _pie(self, texto, color):
        self.lbl_pie.setText(texto)
        self.lbl_pie.setStyleSheet(f"color:{color}; font-size:12px;")

    def _refrescar_lista(self):
        self.lista.clear()
        for r in sorted(self.lienzo.rois, key=lambda r: str(r.get("id"))):
            self.lista.addItem(
                f"{str(r.get('id','')):<5} {r.get('tipo','?'):<9} "
                f"({r['x']:.0f}, {r['y']:.0f})  {r['roi_w']}×{r['roi_h']}")
        for a in self.lienzo.avisos():
            self.lista.addItem(f"  ! {a}")

    # -- pasos -------------------------------------------------------
    def _capturar_referencia(self):
        receta = self.cmb.currentText()
        if not receta:
            return
        d = DialogoCaptura(self.m, self.spin.value(), self)
        if d.exec() != QDialog.Accepted or d.imagen is None:
            return
        I = self.m["inspector"]
        I.set_receta(receta)
        os.makedirs(I.dir_receta(), exist_ok=True)
        cv2.imwrite(I.ruta_referencia(), d.imagen)
        al, an = d.imagen.shape[:2]
        self._decir(f"Referencia guardada: {an}×{al} px.")
        # Si ya habia ROIs marcadas para OTRO tamano de canvas, avisar: las
        # coordenadas viejas no significan lo mismo sobre una referencia nueva.
        previo = self.cfg_previo.get("image_size")
        if previo and list(previo) != [an, al]:
            QMessageBox.warning(
                self, "El canvas cambió",
                f"La referencia anterior era {previo[0]}×{previo[1]} y la nueva "
                f"es {an}×{al}.\n\nLas posiciones marcadas están en coordenadas "
                f"del canvas viejo y ya no caen donde deben. Vuelve a marcarlas "
                f"y a calibrar antes de operar.")
        self._seleccionar(receta)

    def _capturar_display(self):
        """Captura una foto CON el display puesto y la pone de fondo.

        La zona del codigo del display no se puede marcar sobre la referencia:
        la referencia es el housing, y en ese momento el display no esta. Sin
        esta captura habria que adivinar donde cae el codigo, o salir a la
        terminal con marcar_zona_qr.py y una foto sacada a mano.

        La captura se rectifica AL LIENZO DEL CONFIG, no al que salga del
        calculo, asi que las coordenadas que se marquen encima significan
        exactamente lo mismo que las de la referencia. Esa es la condicion que
        hace que esto funcione."""
        receta = self.cmb.currentText()
        if not receta:
            return
        I = self.m["inspector"]
        I.set_receta(receta)
        try:
            cfg = I.cargar_config()
        except SystemExit as e:
            QMessageBox.critical(self, "Faltan las posiciones", str(e)); return

        if QMessageBox.information(
                self, "Con el display puesto",
                "Monta la parte (display o backglass) en el fixture, como "
                "queda en el ensamble, con su código a la vista.\n\n"
                "Después marcas un recuadro sobre ese código y lo guardas.",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        d = DialogoCaptura(self.m, self.spin.value(), self,
                           titulo="Display puesto · para marcar su zona",
                           tam=tuple(cfg["image_size"]))
        if d.exec() != QDialog.Accepted or d.imagen is None:
            return

        ruta = os.path.join(I.dir_receta(), "ref_display.png")
        cv2.imwrite(ruta, d.imagen)
        self.lienzo.cargar_imagen(ruta)
        # El tipo se pone segun lo que diga el nombre de la receta, para que
        # la zona quede marcada como lo que de verdad es.
        try:
            _, parte = self.m["cierre"]._etapa_de_cfg(cfg, receta)
        except Exception:
            parte = "DISPLAY"
        tipo = "Backglass" if parte == "BACKGLASS" else "LCD"
        self.cmb_tipo.setCurrentText(tipo)
        self.lienzo.tipo = tipo
        self._mostrando = "display"
        self.lbl_ref.setText(f"Mostrando la foto CON la parte puesta. Marca "
                             f"un recuadro sobre el código del {parte} y pulsa "
                             f"«Guardar posiciones».")
        self.lbl_ref.setStyleSheet(f"color:{AZUL};")
        self._decir(f"Foto cargada. El tipo ya está en {tipo}: arrastra "
                    f"sobre el código del {parte} y guarda.")
        self._decir("Márcala con holgura: la parte no cae dos veces "
                    "exactamente en el mismo sitio.")

    def _guardar_rois(self):
        receta = self.cmb.currentText()
        if not receta or not self.lienzo.pix:
            return
        av = self.lienzo.avisos()
        graves = [a for a in av if "SE SOLAPAN" in a or "se sale" in a]
        if graves:
            r = QMessageBox.question(
                self, "Hay avisos",
                "\n".join(graves) + "\n\n¿Guardar de todas formas?")
            if r != QMessageBox.Yes:
                return
        I = self.m["inspector"]
        I.set_receta(receta)
        modelo = (self.cfg_previo.get("modelo")
                  or getattr(self, "_modelo_nuevo", "") or "")
        cfg = self.lienzo.guardar_config(I.ruta_config(), receta, modelo,
                                         previo=self.cfg_previo)
        self.cfg_previo = {k: v for k, v in cfg.items() if k != "puntos"}
        n = len([r for r in self.lienzo.rois if r.get("tipo") == "Tornillo"])
        self._decir(f"Posiciones guardadas: {n} tornillo(s) + "
                    f"{len(self.lienzo.rois) - n} zona(s).")
        if av:
            self._decir("Avisos: " + " | ".join(av))
        if getattr(self, "_mostrando", "") == "display":
            # Volver a la referencia: dejar de fondo la foto del display haria
            # creer que las posiciones de tornillo se marcaron sobre ella.
            self._mostrando = "referencia"
            self._seleccionar(receta)
            self._decir("Zona de la parte guardada. Vuelvo a la referencia.")
            return
        self._decir("Las plantillas y umbrales anteriores ya no corresponden a "
                    "esta geometría: recalibra.")

    def _calibrar_camara(self):
        """Calibracion completa con dos capturas de LA MISMA unidad.

        Antes esto pedia dos fotos ya guardadas, y conseguirlas era el problema:
        para capturar con la app hace falta una calibracion, y para calibrar
        hacian falta las capturas. Circulo cerrado. Aqui las dos capturas se
        toman en el momento, que es lo unico que de verdad hacia falta.

        Y se usa LA MISMA unidad a proposito, no dos distintas: entre las dos
        fotos cambian los tornillos y nada mas -misma pieza, misma luz, misma
        posicion en el fixture-, asi que la separacion que mida el sistema es
        la del tornillo y no la de dos unidades que ademas eran distintas entre
        si. Es el par mas limpio que se puede dar, y sale de una unidad y un
        destornillador."""
        receta = self.cmb.currentText()
        if not receta:
            return
        I = self.m["inspector"]
        I.set_receta(receta)
        try:
            cfg = I.cargar_config()
        except SystemExit as e:
            QMessageBox.critical(self, "Faltan las posiciones", str(e)); return
        esperado = tuple(cfg["image_size"])

        if QMessageBox.information(
                self, "Paso 1 de 2",
                "Coloca en el fixture una unidad CON todos los tornillos.\n\n"
                "Cuando estén los 4 marcadores a la vista, pulsa Capturar.",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        d1 = DialogoCaptura(self.m, self.spin.value(), self,
                            titulo="1 de 2 · unidad CON tornillos",
                            tam=esperado)
        if d1.exec() != QDialog.Accepted or d1.imagen is None:
            return

        if QMessageBox.information(
                self, "Paso 2 de 2",
                "Ahora quítale los tornillos a ESA MISMA unidad y vuelve a "
                "ponerla en el fixture, en la misma posición.\n\n"
                "Usar la misma unidad es lo que hace buena la calibración: "
                "entre las dos fotos solo deben cambiar los tornillos.",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        d2 = DialogoCaptura(self.m, self.spin.value(), self,
                            titulo="2 de 2 · la misma unidad SIN tornillos",
                            tam=esperado)
        if d2.exec() != QDialog.Accepted or d2.imagen is None:
            return

        for nombre, img in (("con tornillos", d1.imagen), ("sin tornillos", d2.imagen)):
            if (img.shape[1], img.shape[0]) != esperado:
                QMessageBox.critical(
                    self, "Tamaño distinto",
                    f"La captura «{nombre}» salió {img.shape[1]}×{img.shape[0]} "
                    f"y las posiciones están marcadas sobre {esperado[0]}×"
                    f"{esperado[1]}.\n\nLos recortes caerían en otro sitio. "
                    f"Vuelve a capturar la referencia y a marcar las posiciones.")
                return

        # Se guardan en la receta, no en un temporal: son la prueba de con que
        # se calibro esta estacion, y sirven para repetir la calibracion mas
        # adelante sin volver a desmontar una unidad.
        p1 = os.path.join(I.dir_receta(), "calib_presente.png")
        p2 = os.path.join(I.dir_receta(), "calib_vacia.png")
        cv2.imwrite(p1, d1.imagen)
        cv2.imwrite(p2, d2.imagen)
        try:
            I.modo_calibrar_fotos(SimpleNamespace(config=None,
                                                  calibrar_fotos=[p1, p2]))
        except SystemExit as e:
            QMessageBox.critical(self, "No se pudo calibrar", str(e)); return
        except Exception as e:
            QMessageBox.critical(self, "No se pudo calibrar",
                                 f"{e}\n\n{traceback.format_exc(limit=3)}")
            return
        self._decir("Calibrada con 1 unidad con tornillos y 1 sin. "
                    "Capturas guardadas en la receta.")
        self._decir("Agrega 2 o 3 unidades más con tornillos para afinarla: "
                    "cada una separa mejor el umbral.")
        self._estado_calibracion()

    def _calibrar_fotos(self):
        receta = self.cmb.currentText()
        if not receta:
            return
        f1, _ = QFileDialog.getOpenFileName(
            self, "Foto de la unidad COMPLETA (con tornillos)", "",
            "Imágenes (*.png *.jpg *.jpeg)")
        if not f1:
            return
        f2, _ = QFileDialog.getOpenFileName(
            self, "Foto de la unidad VACÍA (sin tornillos)", "",
            "Imágenes (*.png *.jpg *.jpeg)")
        if not f2:
            return
        I = self.m["inspector"]
        I.set_receta(receta)
        try:
            I.modo_calibrar_fotos(SimpleNamespace(
                config=None, calibrar_fotos=[f1, f2]))
        except SystemExit as e:
            QMessageBox.critical(self, "No se pudo calibrar", str(e)); return
        except Exception as e:
            QMessageBox.critical(self, "No se pudo calibrar",
                                 f"{e}\n\n{traceback.format_exc(limit=3)}")
            return
        self._decir("Calibración hecha con 1 unidad y 1 vacía. "
                    "Agrega más unidades para afinarla.")
        self._estado_calibracion()

    def _agregar(self):
        """Abre la pantalla que mete varias unidades y guarda UNA vez al final.

        No se llama a modo_agregar_unidad: esa version pregunta por consola y
        dibuja con cv2.imshow. Aqui se usan las mismas piezas de abajo -los
        recortes guardados, la extraccion y el guardado de la calibracion- asi
        que el resultado es identico al del modo terminal."""
        receta = self.cmb.currentText()
        if not receta:
            return
        I = self.m["inspector"]
        I.set_receta(receta)
        try:
            cfg = I.cargar_config()
        except SystemExit as e:
            QMessageBox.critical(self, "Receta incompleta", str(e)); return
        previo = I._cargar_crops_guardados(cfg)
        if previo is None:
            QMessageBox.warning(
                self, "Falta la calibración base",
                "Esta receta todavía no está calibrada, así que no hay nada a "
                "lo que agregarle unidades.\n\n"
                "Empieza por «Calibrar con la cámara (2 capturas)».")
            return
        crops_p, crops_a, n_u, n_v = previo

        d = DialogoAgregar(self.m, self.spin.value(), cfg,
                           crops_p, crops_a, n_u, n_v, self)
        if d.exec() != QDialog.Accepted or not d.guardar:
            self._decir("Agregado cancelado: la calibración quedó como estaba.")
            return
        try:
            I._guardar_calibracion(cfg, d.crops_p, d.crops_a, d.n_u, d.n_v)
        except Exception as e:
            QMessageBox.critical(self, "No se pudo guardar", str(e)); return
        self._decir(f"Agregadas {d.n_u - d.ini_u} con tornillos y "
                    f"{d.n_v - d.ini_v} sin. Ahora: {d.n_u} / {d.n_v}.")
        self._estado_calibracion()

    # -- clave -------------------------------------------------------
    def _cambiar_clave(self):
        a, ok = QInputDialog.getText(self, "Cambiar clave", "Clave actual:",
                                     QLineEdit.Password)
        if not ok:
            return
        n, ok = QInputDialog.getText(self, "Cambiar clave",
                                     "Clave nueva (mínimo 10 caracteres):",
                                     QLineEdit.Password)
        if not ok:
            return
        n2, ok = QInputDialog.getText(self, "Cambiar clave", "Repítela:",
                                      QLineEdit.Password)
        if not ok:
            return
        if n != n2:
            QMessageBox.warning(self, "No coinciden",
                                "Las dos claves nuevas no son iguales.")
            return
        bien, msg = seguridad.cambiar(a, n)
        (QMessageBox.information if bien else QMessageBox.warning)(
            self, "Cambiar clave", msg)


def abrir(parent=None, indice_camara=0):
    """Pide la clave y, si entra, abre el panel. Devuelve True si se abrio."""
    if DialogoAcceso(parent).exec() != QDialog.Accepted:
        return False
    PanelAdmin(parent, indice_camara).exec()
    return True
