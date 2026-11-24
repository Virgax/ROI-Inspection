#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roi_app.py — Estacion de inspeccion. Ventana principal.

QUE ES Y QUE NO ES
Es la cara visible del sistema: video, veredicto y secuencia de cierre, con
botones en vez de teclas y terminal. NO reimplementa nada de la inspeccion:
llama al motor que ya existe (`inspector.py`) y a la maquina de votacion
compartida (`veredicto.py`). Si esta pantalla y el modo terminal dieran
veredictos distintos seria un error grave, asi que no hay dos copias de esa
logica: hay una, y las dos la usan.

EL HILO
La captura y el analisis viven en un hilo aparte. En el hilo de la interfaz Qt
dejaria la ventana congelada varias veces por segundo -y con el registro en SQL,
tres segundos seguidos- justo cuando el operario necesita ver que pasa.

El hilo NO toca widgets. Emite senales con el fotograma ya compuesto y un dict
de estado; la ventana pinta. Es la unica forma segura de trabajar con Qt.
"""

import os
import platform
import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings, QEvent
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
                               QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
                               QCheckBox, QListWidget, QListWidgetItem, QFrame,
                               QMessageBox, QSpinBox, QFileDialog)

import rutas
import motor

# Igual que en motor.py: se resuelve antes de que nadie cambie el directorio de
# trabajo.
_AQUI = os.path.dirname(os.path.abspath(__file__))

# Paleta: los mismos colores del overlay de siempre, para que quien ya conoce el
# sistema no tenga que reaprender nada.
VERDE = "#69d753"
ROJO = "#e24b4a"
AMBAR = "#ffbd2e"
AZUL = "#3caaeb"

# Pausa entre teclas que separa una lectura de la siguiente. Un lector escribe
# el codigo entero en milisegundos; una persona tecleando nunca baja de esto.
PAUSA_ESCANER = 0.5
FONDO = "#1b1817"
PANEL = "#241f1c"
TEXTO = "#ebebe8"


class HiloInspeccion(QThread):
    """Captura, analiza y compone. No toca la interfaz."""

    fotograma = Signal(QImage)
    estado = Signal(dict)
    fallo = Signal(str)
    aviso = Signal(str)

    def __init__(self, receta, indice_camara=0, resolucion="auto", cierre=False,
                 contaminacion=False):
        super().__init__()
        self.receta = receta
        self.indice_camara = indice_camara
        self.resolucion = resolucion
        self.usar_cierre = cierre
        self.usar_contaminacion = contaminacion
        self._parar = False
        self._capturar_limpia = False
        self._escaneado = None
        self.m = motor.cargar()

    def detener(self):
        self._parar = True

    def escanear(self, texto):
        """Deja un serial escaneado para que el bucle lo entregue.

        No se llama a la secuencia desde aqui: esto corre en el hilo de la
        interfaz y la secuencia la maneja el hilo de video. Se deja en un
        atributo y se recoge en el frame siguiente, que es como se pasa todo lo
        demas entre los dos hilos."""
        self._escaneado = texto

    def capturar_limpia(self):
        """Equivalente a la tecla C: guarda el rectificado sin overlay."""
        self._capturar_limpia = True

    # ------------------------------------------------------------------
    def run(self):
        I = self.m["inspector"]
        V = self.m["veredicto"]
        try:
            I.set_receta(self.receta)
            cfg = I.cargar_config()
            umbrales = I.cargar_umbrales()
            plantillas = I.cargar_plantillas(cfg)
            if umbrales is None or plantillas is None:
                self.fallo.emit(f"La receta '{self.receta}' no esta calibrada.")
                return
            I.verificar_huella(cfg, umbrales)
        except SystemExit as e:
            self.fallo.emit(str(e))
            return
        except Exception as e:
            self.fallo.emit(f"No se pudo cargar la receta: {e}")
            return

        try:
            cap = I.abrir_camara(self.indice_camara, self.resolucion)
        except SystemExit as e:
            self.fallo.emit(str(e))
            return

        claves = [I.clave(p) for p in cfg["puntos"]]
        ver = V.Veredicto(claves)
        secuencia = self.m["cierre"].Cierre(cfg, self.receta,
                                            activo=self.usar_cierre, verbose=False)
        # En modo terminal, cuando el cierre no puede arrancar lo dice por
        # consola. Aqui no hay consola que mirar: si el operario marco la
        # casilla y el cierre no va a funcionar, tiene que verlo en pantalla o
        # se pasara el turno esperando un giftcard que nadie esta leyendo.
        if self.usar_cierre and secuencia.disponible:
            # Decir que etapa y que parte se dedujeron del nombre de la receta.
            # Deducir del nombre es comodo -no hay un segundo sitio que ajustar-
            # pero solo es honesto si se dice: si alguien nombra una receta de
            # otra forma y el programa decide algo distinto de lo que esperaba,
            # tiene que enterarse al arrancar y no tres turnos despues.
            self.aviso.emit(f"Cierre activo · etapa «{secuencia.etapa}» · "
                            f"se pedirá el {secuencia.parte}")
        if self.usar_cierre and not secuencia.disponible:
            self.aviso.emit("El cierre de unidad no esta disponible: "
                            + (secuencia.motivo_no_disponible or "motivo desconocido"))
        banco_ev = self.m["banco"].Banco(rutas.dir_banco(), self.receta, verbose=False)
        detector = self.m["inspector"].contaminacion.Detector(
            verbose=False, activo=self.usar_contaminacion,
            carpeta=I.dir_receta())

        pieza_id = 0
        sin_marcadores = 0
        t_fps = time.time()
        n_fps = 0
        fps = 0.0

        while not self._parar:
            ok, frame = cap.read()
            if not ok:
                continue
            warped, scores, presencia_frame, centros, sims = I._procesar_frame(
                frame, cfg, umbrales, plantillas)

            n_fps += 1
            if time.time() - t_fps >= 1.0:
                fps = n_fps / (time.time() - t_fps)
                t_fps, n_fps = time.time(), 0

            if warped is None:
                sin_marcadores += 1
                if sin_marcadores > I.FRAMES_SIN_MARCADOR_RESET:
                    ver.reiniciar()
                    # `sin_tapete` y no `cancelar`: solo la secuencia sabe si
                    # perder el tapete significa que abandonaron o que hicieron
                    # exactamente lo que les pedimos. Cancelar aqui hacia que
                    # sacar el housing -el paso siguiente del propio proceso-
                    # tirase la secuencia y volviese a inspeccionar tornillos.
                    secuencia.sin_tapete()
                    detector.reset()
                    pieza_id += 1
                vista = frame
                n = sum(i in centros for i in I.IDS_ESQUINAS)
                self.estado.emit({"banner": f"Buscando el tapete — {n}/4 marcadores",
                                  "color": AMBAR, "posiciones": [], "fps": fps,
                                  "cierre": secuencia.mensaje if secuencia.activa() else ""})
                self._emitir(vista)
                continue

            sin_marcadores = 0
            r = ver.observar(presencia_frame, scores, sims)

            if r["registrar"] and not secuencia.suprime_registro():
                I.registrar_inspeccion(r["resultado"], list(r["faltantes"]),
                                       len(cfg["puntos"]), scores, r["presencia"],
                                       umbrales, sims, dudosos=list(r["dudosos"]),
                                       hist=ver.hist)
                banco_ev.guardar(warped, r["resultado"], r["faltantes"], r["dudosos"],
                                 scores, umbrales, sims,
                                 gcn=getattr(secuencia, "gcn", None),
                                 estacion=platform.node())
                # Marcar DESPUES de escribir: si el registro fallara, el veredicto
                # no queda como hecho y se reintenta en el frame siguiente.
                ver.marcar_registrado(r["veredicto"])
                I.beep(r["resultado"] == "PASA")

            if not r["hay_pieza"]:
                ver.nueva_pieza()
                if detector.zonas(pieza_id):
                    detector.reset()
                    pieza_id += 1
            elif r["estable"]:
                detector.actualizar(warped, pieza_id)

            if self._escaneado:
                texto, self._escaneado = self._escaneado, None
                if not secuencia.escanear(texto):
                    self.aviso.emit(f"Código «{texto[:30]}» ignorado: aquí no "
                                    f"toca escanear nada ahora mismo.")

            secuencia.procesar(warped, r["estable"],
                               not r["faltantes"] and not r["dudosos"],
                               hay_pieza=r["hay_pieza"],
                               frame=frame, centros=centros)

            if self._capturar_limpia:
                self._capturar_limpia = False
                d = os.path.join(rutas.dir_dataset(), self.receta)
                os.makedirs(d, exist_ok=True)
                from datetime import datetime
                n = os.path.join(d, f"limpia_{datetime.now():%Y%m%d_%H%M%S}.png")
                cv2.imwrite(n, warped)

            vista = warped.copy()
            I.dibujar_overlay(vista, cfg, r["presencia"], "")
            detector.dibujar(vista, pieza_id, escala=1.0)
            secuencia.dibujar(vista, escala=1.0, grande=False)

            banner, color = self._banner(r, detector, pieza_id)
            self.estado.emit({
                "banner": banner, "color": color, "fps": fps,
                "cierre": secuencia.mensaje if secuencia.activa() else "",
                "color_cierre": secuencia.color if secuencia.activa() else None,
                "posiciones": self._posiciones(cfg, r, scores, umbrales, I),
                # El cartel del centro viaja como texto y lo pinta la ventana.
                # Dibujarlo sobre el frame lo dejaria a merced del reescalado
                # del video: en una pantalla pequena acabaria ilegible justo
                # cuando mas falta hace.
                "grande": secuencia.grande(),
            })
            self._emitir(vista)

        cap.release()
        detector.cerrar()
        banco_ev.cerrar()
        secuencia.cerrar()

    # ------------------------------------------------------------------
    def _banner(self, r, detector, pieza_id):
        if not r["listo"]:
            return "Analizando…", AZUL
        if not r["hay_pieza"]:
            return "Esperando pieza", TEXTO
        extra = ""
        _t, n_cont = detector.estado(pieza_id)
        if n_cont > 0:
            extra = f"   ·   CONTAMINACIÓN: {n_cont}"
        elif n_cont == -1:
            extra = "   ·   analizando contaminación…"
        if r["faltantes"]:
            return f"FALTAN {len(r['faltantes'])}: {', '.join(r['faltantes'])}{extra}", ROJO
        if r["dudosos"]:
            return f"VERIFICAR {len(r['dudosos'])}: {', '.join(r['dudosos'])}{extra}", AMBAR
        return f"PASA{extra}", VERDE

    def _posiciones(self, cfg, r, scores, umbrales, I):
        glob = umbrales.get("umbral_global", 50.0)
        por_roi = umbrales.get("por_roi", {})
        salida = []
        for p in cfg["puntos"]:
            rid = I.clave(p)
            est = r["presencia"].get(rid)
            umb = por_roi.get(rid, {}).get("umbral", glob)
            sc = scores.get(rid, 0.0)
            salida.append({"id": rid, "estado": est, "puntaje": sc,
                           "umbral": umb, "margen": sc - umb})
        return salida

    def _emitir(self, bgr):
        alto, ancho = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, ancho, alto, 3 * ancho, QImage.Format_RGB888)
        self.fotograma.emit(img.copy())


class VentanaPrincipal(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Inspección ROI — Airlink Distribution")
        self.resize(1440, 900)
        self.hilo = None
        self._borde = FONDO
        self._buffer = ""
        self._ultima_tecla = 0.0
        # Cada banco de trabajo tiene su camara, su receta y su modo de
        # operar, y no cambian de un dia para otro. Preguntarselo al operario
        # en cada arranque es pedirle que acierte cuatro veces al dia en algo
        # que ya estaba decidido; y el dia que falle, el fallo es silencioso:
        # una estacion inspeccionando sin cierre, o con la receta del otro
        # modelo. Se recuerda.
        self.ajustes = QSettings("Airlink Distribution", "Inspeccion ROI")
        rutas.preparar(verbose=False)
        motor.preparar_entorno(rutas.raiz_datos())
        self._construir()
        self._cargar_recetas()
        QApplication.instance().installEventFilter(self)
        self._recordar(leer=True)

    # ------------------------------------------------------------------
    def _construir(self):
        central = QWidget()
        self.setCentralWidget(central)
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)
        central.setStyleSheet(f"background:{FONDO}; color:{TEXTO};")

        # --- barra superior ---
        barra = QFrame()
        barra.setStyleSheet(f"background:{PANEL};")
        hb = QHBoxLayout(barra)
        hb.setContentsMargins(14, 10, 14, 10)
        hb.addWidget(QLabel("Proceso"))
        self.cmb_receta = QComboBox()
        self.cmb_receta.setMinimumWidth(220)
        hb.addWidget(self.cmb_receta)
        hb.addSpacing(18)
        hb.addWidget(QLabel("Cámara"))
        self.spin_cam = QSpinBox()
        self.spin_cam.setRange(0, 8)
        hb.addWidget(self.spin_cam)
        hb.addSpacing(18)
        self.chk_cierre = QCheckBox("Cierre de unidad")
        self.chk_cierre.setToolTip(
            "Lee el giftcard, valida la unidad contra la base y registra la "
            "evidencia del housing y el display.")
        hb.addWidget(self.chk_cierre)
        hb.addSpacing(18)
        # Apagada de salida. La deteccion de contaminacion dibuja recuadros
        # magenta con VERIFICAR encima de la pieza, y mientras su linea base no
        # este verificada contra el canvas de ESTA receta esos avisos son ruido
        # sobre la imagen que el operario necesita mirar. Quien la quiera, la
        # enciende.
        self.chk_cont = QCheckBox("Detectar contaminación")
        self.chk_cont.setToolTip(
            "Marca en magenta zonas con posible suciedad o piezas extrañas.\n"
            "Necesita la línea base (base82_prom.npz) del mismo tamaño de "
            "canvas que esta receta.")
        hb.addWidget(self.chk_cont)
        hb.addStretch(1)
        self.btn_captura = QPushButton("Captura limpia")
        self.btn_captura.setEnabled(False)
        self.btn_captura.clicked.connect(self._captura)
        hb.addWidget(self.btn_captura)
        self.btn_importar = QPushButton("Importar recetas…")
        self.btn_importar.setToolTip(
            "Copia las recetas de otra instalación a esta Mac. "
            "Nunca sobrescribe lo que ya exista aquí.")
        self.btn_importar.clicked.connect(self._importar)
        hb.addWidget(self.btn_importar)
        self.btn_admin = QPushButton("Administración…")
        self.btn_admin.setToolTip("Crear recetas, marcar posiciones y calibrar. "
                                  "Pide clave.")
        self.btn_admin.clicked.connect(self._admin)
        hb.addWidget(self.btn_admin)
        self.btn_inicio = QPushButton("Iniciar")
        self.btn_inicio.setMinimumWidth(130)
        self.btn_inicio.clicked.connect(self._alternar)
        hb.addWidget(self.btn_inicio)
        raiz.addWidget(barra)

        # --- cuerpo: video + panel ---
        cuerpo = QHBoxLayout()
        cuerpo.setContentsMargins(0, 0, 0, 0)
        cuerpo.setSpacing(0)
        self.lbl_video = QLabel("Pulse Iniciar")
        self.lbl_video.setAlignment(Qt.AlignCenter)
        self.lbl_video.setMinimumSize(900, 520)
        self.lbl_video.setObjectName("video")
        # Selector por nombre a proposito: una hoja de estilo sin selector se
        # aplica tambien a los hijos, y el cartel del centro salia con el mismo
        # borde verde alrededor.
        self.lbl_video.setStyleSheet(
            f"#video {{background:#000; border:6px solid {FONDO};}}")
        cuerpo.addWidget(self.lbl_video, 1)

        # Cartel del centro. Es hijo del video para quedar por encima de la
        # imagen, y se coloca a mano porque un layout lo pondria AL LADO del
        # video en vez de encima.
        self.lbl_grande = QLabel("", self.lbl_video)
        self.lbl_grande.setAlignment(Qt.AlignCenter)
        fg = QFont(); fg.setPointSize(40); fg.setBold(True)
        self.lbl_grande.setFont(fg)
        self.lbl_grande.hide()

        lateral = QFrame()
        lateral.setFixedWidth(300)
        lateral.setStyleSheet(f"background:{PANEL};")
        vl = QVBoxLayout(lateral)
        vl.setContentsMargins(12, 12, 12, 12)
        t = QLabel("Posiciones")
        t.setStyleSheet("font-weight:600; font-size:13px;")
        vl.addWidget(t)
        self.lista = QListWidget()
        self.lista.setStyleSheet(
            f"QListWidget{{background:{FONDO}; border:none; font-family:monospace;}}")
        vl.addWidget(self.lista, 1)
        self.lbl_cierre = QLabel("")
        self.lbl_cierre.setWordWrap(True)
        self.lbl_cierre.setStyleSheet("font-size:12px; padding:8px 0;")
        vl.addWidget(self.lbl_cierre)
        cuerpo.addWidget(lateral)
        raiz.addLayout(cuerpo, 1)

        # --- banner inferior ---
        self.lbl_banner = QLabel("")
        self.lbl_banner.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setPointSize(30); f.setBold(True)
        self.lbl_banner.setFont(f)
        self.lbl_banner.setMinimumHeight(104)
        self._pintar_banner("Detenido", TEXTO)
        raiz.addWidget(self.lbl_banner)

        self.statusBar().setStyleSheet(f"background:{PANEL}; color:#9a918c;")
        self.statusBar().showMessage(f"Datos en {rutas.raiz_datos()}")

    def _cargar_recetas(self):
        rs = rutas.hay_recetas()
        self.cmb_receta.clear()
        self.cmb_receta.addItems(rs)
        self.btn_inicio.setEnabled(bool(rs))
        if not rs:
            self.statusBar().showMessage(
                f"No hay procesos en {rutas.dir_procesos()}. "
                f"Impórtalos de otra instalación o créalos en Administración.")
            self._pintar_banner("Sin procesos", AMBAR)

    def _importar(self):
        """Trae recetas de una instalacion anterior.

        Es lo primero que hace falta en una Mac nueva: la app recien instalada
        tiene el codigo pero ninguna calibracion, y calibrar de cero cuesta
        media hora de planta y varias unidades. Apuntando a la carpeta de otra
        estacion se copian las recetas enteras -config, plantillas, umbrales y
        referencia- y esta queda lista.

        No sobrescribe: una receta que ya exista aqui se respeta, porque puede
        estar recien calibrada para ESTA camara y perderla seria mucho peor que
        no importar nada."""
        d = QFileDialog.getExistingDirectory(
            self, "Carpeta de la instalación de origen "
                  "(la que contiene «procesos»)")
        if not d:
            return
        try:
            copiadas, saltadas = rutas.importar_desde(d, verbose=False)
        except Exception as e:
            QMessageBox.critical(self, "No se pudo importar", str(e))
            return
        txt = (f"Importadas: {', '.join(copiadas)}" if copiadas
               else "No había nada nuevo que importar.")
        if saltadas:
            txt += f"\n\nYa existían aquí y NO se tocaron: {', '.join(saltadas)}"
        QMessageBox.information(self, "Importar", txt)
        self._cargar_recetas()

    # ------------------------------------------------------------------
    def _alternar(self):
        if self.hilo and self.hilo.isRunning():
            self.hilo.detener()
            self.hilo.wait(3000)
            self.hilo = None
            self.btn_inicio.setText("Iniciar")
            self.btn_captura.setEnabled(False)
            self._pintar_banner("Detenido", TEXTO)
            self._pintar_grande(None)
            self.cmb_receta.setEnabled(True)
            self.chk_cierre.setEnabled(True)
            self.chk_cont.setEnabled(True)
            self.spin_cam.setEnabled(True)
            return
        self.hilo = HiloInspeccion(self.cmb_receta.currentText(),
                                   self.spin_cam.value(),
                                   cierre=self.chk_cierre.isChecked(),
                                   contaminacion=self.chk_cont.isChecked())
        self.hilo.fotograma.connect(self._pintar_video)
        self.hilo.estado.connect(self._pintar_estado)
        self.hilo.fallo.connect(self._error)
        self.hilo.aviso.connect(self._aviso)
        self.hilo.start()
        self._recordar()
        self.btn_inicio.setText("Detener")
        self.btn_captura.setEnabled(True)
        self.cmb_receta.setEnabled(False)
        self.chk_cierre.setEnabled(False)
        self.chk_cont.setEnabled(False)
        # Tambien el selector de camara: cambiarlo a mitad de una inspeccion no
        # hace nada, y deshabilitado deja de poder robar el foco y tragarse las
        # cifras de un codigo escaneado.
        self.spin_cam.setEnabled(False)

    def _admin(self):
        # Con la inspeccion corriendo, la camara esta ocupada y el panel no
        # podria capturar nada. Se detiene antes en vez de dar un error raro de
        # camara a medio camino.
        if self.hilo and self.hilo.isRunning():
            QMessageBox.information(
                self, "Detén la inspección",
                "Para entrar a administración hay que detener la inspección: "
                "el panel necesita la cámara.")
            return
        import admin
        admin.abrir(self, self.spin_cam.value())
        self._cargar_recetas()

    def _captura(self):
        if self.hilo:
            self.hilo.capturar_limpia()
            self.statusBar().showMessage("Captura limpia guardada", 3000)

    def _aviso(self, msg):
        """Algo que el operario debe saber pero que no impide inspeccionar."""
        self.lbl_cierre.setText(msg)
        self.lbl_cierre.setStyleSheet("font-size:12px; padding:8px 0; color:#ffbd2e;")
        self.statusBar().showMessage(msg, 15000)

    def _error(self, msg):
        # El motor nacio en la terminal y sus mensajes suponen que hay una.
        # Aqui no la hay: el operario esta delante de una ventana. Cuando el
        # motivo es de calibracion, se le dice donde se arregla EN LA APP, que
        # es la pregunta que va a hacer a continuacion.
        if any(t in msg for t in ("RECALIBRA", "no esta calibrada",
                                  "no está calibrada", "calibracion",
                                  "calibración")):
            msg += ("\n\nSe arregla aquí mismo: botón «Administración…» → "
                    "«Calibrar con la cámara (2 capturas)».\n"
                    "Si además cambiaste de cámara o de fixture, captura primero "
                    "la referencia y vuelve a marcar las posiciones.")
        QMessageBox.critical(self, "No se pudo iniciar", msg)
        if self.hilo:
            self.hilo.detener()
            self.hilo = None
        self.btn_inicio.setText("Iniciar")
        self.cmb_receta.setEnabled(True)
        self.chk_cierre.setEnabled(True)
        self.chk_cont.setEnabled(True)

    def _colocar_grande(self):
        """El cartel ocupa la franja central del video, no todo el alto: tapar
        la pieza entera es peor que no avisar."""
        r = self.lbl_video.rect()
        alto = max(140, int(r.height() * 0.34))
        self.lbl_grande.setGeometry(r.x(), r.y() + (r.height() - alto) // 2,
                                    r.width(), alto)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._colocar_grande()

    def _pintar_grande(self, g):
        """g es (texto, color BGR) o None."""
        if not g:
            self.lbl_grande.hide()
            return
        texto, (b, gr, rr) = g
        self.lbl_grande.setText(texto.replace("\n", "<br>"))
        self.lbl_grande.setStyleSheet(
            f"background:rgba(17,14,13,205); color:rgb({rr},{gr},{b}); "
            f"border:none;")
        self._colocar_grande()
        self.lbl_grande.show()
        self.lbl_grande.raise_()

    def _pintar_video(self, img):
        self.lbl_video.setPixmap(QPixmap.fromImage(img).scaled(
            self.lbl_video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _pintar_banner(self, texto, color):
        self.lbl_banner.setText(texto)
        self.lbl_banner.setStyleSheet(
            f"background:{color}; color:#141110; padding:10px;")

    def _pintar_estado(self, e):
        self._pintar_banner(e["banner"], e["color"])
        # El borde del video del color del veredicto. El operario mira la pieza,
        # no la banda de abajo: que el marco entero se ponga verde o rojo se ve
        # sin apartar la vista.
        col = e["color"]
        if col != self._borde:
            self._borde = col
            self.lbl_video.setStyleSheet(
                f"#video {{background:#000; border:6px solid {col};}}")
        self._pintar_grande(e.get("grande"))
        self.lbl_cierre.setText(e.get("cierre") or "")
        if e.get("color_cierre"):
            b, g, r = e["color_cierre"]
            self.lbl_cierre.setStyleSheet(
                f"font-size:12px; padding:8px 0; color:rgb({r},{g},{b});")
        self.statusBar().showMessage(
            f"{e['fps']:.0f} fps   ·   {platform.node()}   ·   {rutas.raiz_datos()}")
        pos = e.get("posiciones") or []
        if self.lista.count() != len(pos):
            self.lista.clear()
            for _ in pos:
                self.lista.addItem(QListWidgetItem(""))
        for i, p in enumerate(pos):
            est = p["estado"]
            col = {True: VERDE, False: ROJO, "dudoso": AMBAR}.get(est, "#6d6560")
            it = self.lista.item(i)
            it.setText(f"{p['id']:<6}{p['puntaje']:5.1f} / {p['umbral']:.1f}"
                       f"{p['margen']:+7.1f}")
            it.setForeground(QColor(col))

    def _recordar(self, leer=False):
        """Lee o escribe la configuracion de esta estacion."""
        a = self.ajustes
        if leer:
            r = a.value("receta", "")
            if r and self.cmb_receta.findText(r) >= 0:
                self.cmb_receta.setCurrentText(r)
            self.spin_cam.setValue(int(a.value("camara", 0)))
            self.chk_cierre.setChecked(a.value("cierre", "false") == "true")
            self.chk_cont.setChecked(a.value("contaminacion", "false") == "true")
            return
        a.setValue("receta", self.cmb_receta.currentText())
        a.setValue("camara", self.spin_cam.value())
        a.setValue("cierre", "true" if self.chk_cierre.isChecked() else "false")
        a.setValue("contaminacion", "true" if self.chk_cont.isChecked() else "false")

    def eventFilter(self, obj, ev):
        """Escucha el lector de codigos en TODA la aplicacion.

        POR QUE UN FILTRO Y NO keyPressEvent DE LA VENTANA
        Un lector USB se presenta como teclado, asi que sus pulsaciones van al
        widget que tenga el foco. Con keyPressEvent de la ventana, bastaba con
        que el foco estuviera en el selector de camara para que el codigo se
        metiera ahi -y un serial que empieza por cifras convertia el numero de
        camara en cualquier cosa- o en el combo de recetas, que hace busqueda
        por letras y se las traga.

        Un filtro a nivel de aplicacion ve las pulsaciones antes que nadie, asi
        que da igual donde este el foco y el operario no tiene que pinchar en
        ningun sitio antes de escanear. Es lo que hace que esto funcione con la
        pieza en las manos.

        Se distingue del tecleo humano por la VELOCIDAD: un lector escribe el
        codigo entero en milisegundos. Si entre dos teclas pasan mas de
        PAUSA_ESCANER segundos, se entiende que empieza una lectura nueva y el
        buffer se descarta. Sin eso, teclear en cualquier campo iria sumando
        letras sueltas hasta formar basura."""
        if ev.type() == QEvent.KeyPress:
            ahora = time.time()
            if ahora - self._ultima_tecla > PAUSA_ESCANER:
                self._buffer = ""
            self._ultima_tecla = ahora
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                codigo, self._buffer = self._buffer.strip(), ""
                if len(codigo) >= 6:
                    self._escaneado(codigo)
                    return True          # no dejar que el Enter active un boton
            else:
                t = ev.text()
                if t and t.isprintable():
                    self._buffer = (self._buffer + t)[-120:]
        return super().eventFilter(obj, ev)

    def _escaneado(self, codigo):
        """Un codigo entro. Decir SIEMPRE que paso con el.

        Antes, escanear fuera de momento no producia ninguna senal: ni el
        codigo se usaba ni nadie lo decia. El operario no puede distinguir
        'no lo lei' de 'lo lei y no tocaba', y lo unico que puede hacer es
        volver a escanear y esperar."""
        corto = codigo[:34]
        if not (self.hilo and self.hilo.isRunning()):
            self._pintar_banner(f"Escaneado {corto} — la inspección no está "
                                f"corriendo", AMBAR)
            self.statusBar().showMessage(
                "Pulsa Iniciar antes de escanear.", 8000)
            return
        self.hilo.escanear(codigo)
        self.statusBar().showMessage(f"Escaneado: {corto}", 8000)
        # Confirmacion inmediata y visible, sin esperar al hilo de video: lo
        # que el operario necesita saber en ese instante es que el pitido del
        # lector llego al programa.
        self.lbl_cierre.setText(f"Código recibido: {corto}")
        self.lbl_cierre.setStyleSheet(f"font-size:12px; padding:8px 0; "
                                      f"color:{AZUL};")

    def closeEvent(self, ev):
        self._recordar()
        if self.hilo and self.hilo.isRunning():
            self.hilo.detener()
            self.hilo.wait(3000)
        ev.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Inspección ROI")
    icono = os.path.join(_AQUI, "ROI.icns")
    if os.path.exists(icono):
        app.setWindowIcon(QIcon(icono))
    v = VentanaPrincipal()
    v.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
