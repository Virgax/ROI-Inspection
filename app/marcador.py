#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marcador.py — Marcado de ROIs sobre la foto de referencia, dentro de la app.

QUE SUSTITUYE
Hasta ahora las ROIs se marcaban en calibrador_roi.html, se descargaba un
config_rois.json y habia que moverlo a mano a la carpeta de la receta. Ese paso
manual costo una tarde entera: macOS renombro la descarga a 'config_rois-3.json'
porque ya habia otras, el archivo nunca llego a su sitio, y el inspector siguio
leyendo la configuracion del mes anterior sin que nada avisara. La pieza no
pasaba y el problema no estaba donde lo buscabamos.

Aqui no hay descarga ni copia: se marca sobre la referencia y se escribe directo
en la receta. El paso que fallaba deja de existir.

COORDENADAS
Todo se guarda en coordenadas del CANVAS RECTIFICADO -el mismo sistema en el que
trabajan las plantillas, los umbrales y las zonas-, nunca en pixeles de pantalla.
La imagen se muestra escalada para que quepa; el factor se aplica al leer y al
pintar, y jamas se guarda escalado. Mezclar los dos sistemas es exactamente el
error que hizo que una zona marcada sobre una captura de 1601 px no sirviera
para un canvas de 1659.

EL CENTRO, NO LA ESQUINA
El formato guarda cada ROI por CENTRO (x, y) y tamano (roi_w, roi_h), que es lo
que espera el inspector. El raton da esquinas; la conversion se hace al soltar.
"""

import json
import os
import time
import uuid

from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QImage, QFont
from PySide6.QtWidgets import QWidget

TIPOS = ["Tornillo", "Giftcard", "LCD", "Backglass"]

COLOR = {"Tornillo": QColor(105, 215, 83),
         "Giftcard": QColor(255, 189, 46),
         "LCD": QColor(60, 170, 235),
         "Backglass": QColor(190, 130, 235)}

# Debajo de esto se entiende que fue un clic y no un arrastre: se ignora, para
# que un clic suelto no siembre ROIs de 2 px que luego nadie encuentra.
MINIMO_PX = 8


def _normalizar(p, cfg):
    """Deja cada punto con roi_w y roi_h explicitos.

    El calibrador HTML solo escribe el tamano cuando difiere del global: de las
    25 posiciones de una receta real, 22 no lo traen. El inspector lo resuelve
    al vuelo con `_medidas`, pero aqui hay que pintar, medir solapes y arrastrar
    con el raton, y no tiene sentido repetir esa resolucion en cada sitio.
    Se hace una vez, al cargar, con el mismo orden de preferencia que usa el
    motor -roi_w, roi_h, roi, y por ultimo el global- para que un config leido y
    vuelto a guardar aqui signifique exactamente lo mismo que antes."""
    g = cfg.get("roi_size", 60)
    w = p.get("roi_w") or p.get("roi_h") or p.get("roi") or g
    h = p.get("roi_h") or p.get("roi_w") or p.get("roi") or g
    p["roi_w"], p["roi_h"] = int(w), int(h)
    p.setdefault("tipo", "Tornillo")
    p.setdefault("id", "")
    return p


class Lienzo(QWidget):
    """Muestra la referencia y deja dibujar, mover y borrar recuadros."""

    cambio = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)
        self.pix = None            # QPixmap de la referencia, tamano original
        self.rois = []             # dicts en coordenadas del canvas
        self.tipo = "Tornillo"
        self.tam = 70              # lado por defecto de un tornillo
        self.sel = -1
        self._arrastre = None      # (inicio, actual) en pixeles de pantalla
        self._mover = None         # (indice, dx, dy) en canvas

    # -- imagen ------------------------------------------------------
    def cargar_imagen(self, ruta):
        pix = QPixmap(ruta)
        if pix.isNull():
            return False
        self.pix = pix
        self.update()
        return True

    def tam_canvas(self):
        return (self.pix.width(), self.pix.height()) if self.pix else (0, 0)

    def _escala(self):
        """Factor canvas -> pantalla, y el desplazamiento para centrar."""
        if not self.pix:
            return (1.0, 0, 0)
        w, h = self.pix.width(), self.pix.height()
        f = min(self.width() / w, self.height() / h)
        return (f, int((self.width() - w * f) / 2), int((self.height() - h * f) / 2))

    def a_canvas(self, p):
        f, ox, oy = self._escala()
        return ((p.x() - ox) / f, (p.y() - oy) / f)

    def a_pantalla(self, x, y):
        f, ox, oy = self._escala()
        return QPoint(int(x * f + ox), int(y * f + oy))

    # -- dibujo ------------------------------------------------------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 17, 16))
        if not self.pix:
            p.setPen(QColor(150, 145, 140))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Sin referencia. Captúrala primero con la cámara.")
            return
        f, ox, oy = self._escala()
        p.drawPixmap(QRect(ox, oy, int(self.pix.width() * f),
                           int(self.pix.height() * f)), self.pix)
        fuente = QFont(); fuente.setPointSize(9); fuente.setBold(True)
        p.setFont(fuente)
        for i, r in enumerate(self.rois):
            c = COLOR.get(r.get("tipo"), QColor(200, 200, 200))
            ancho = 3 if i == self.sel else 2
            p.setPen(QPen(c, ancho))
            tl = self.a_pantalla(r["x"] - r["roi_w"] / 2, r["y"] - r["roi_h"] / 2)
            br = self.a_pantalla(r["x"] + r["roi_w"] / 2, r["y"] + r["roi_h"] / 2)
            p.drawRect(QRect(tl, br))
            p.drawText(tl.x(), tl.y() - 4, str(r.get("id", "")))
        if self._arrastre:
            a, b = self._arrastre
            p.setPen(QPen(COLOR.get(self.tipo, QColor(255, 255, 255)), 2, Qt.DashLine))
            p.drawRect(QRect(a, b).normalized())

    # -- raton -------------------------------------------------------
    def mousePressEvent(self, ev):
        if not self.pix:
            return
        x, y = self.a_canvas(ev.position().toPoint())
        i = self._en(x, y)
        if ev.button() == Qt.RightButton:
            if i >= 0:
                del self.rois[i]
                self.sel = -1
                self._renumerar()
                self.cambio.emit()
                self.update()
            return
        if i >= 0:
            # Sobre una ROI existente: se mueve, no se crea otra encima.
            self.sel = i
            self._mover = (i, self.rois[i]["x"] - x, self.rois[i]["y"] - y)
            self.cambio.emit()
            self.update()
            return
        self._arrastre = (ev.position().toPoint(), ev.position().toPoint())

    def mouseMoveEvent(self, ev):
        if self._mover:
            i, dx, dy = self._mover
            x, y = self.a_canvas(ev.position().toPoint())
            self.rois[i]["x"] = round(x + dx, 1)
            self.rois[i]["y"] = round(y + dy, 1)
            self.update()
        elif self._arrastre:
            self._arrastre = (self._arrastre[0], ev.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._mover:
            self._mover = None
            self.cambio.emit()
            return
        if not self._arrastre:
            return
        a, b = self._arrastre
        self._arrastre = None
        r = QRect(a, b).normalized()
        if r.width() < MINIMO_PX or r.height() < MINIMO_PX:
            # Clic suelto: cuadrado del tamano por defecto, centrado donde pincho.
            x, y = self.a_canvas(a)
            w = h = self.tam
        else:
            x0, y0 = self.a_canvas(r.topLeft())
            x1, y1 = self.a_canvas(r.bottomRight())
            x, y = (x0 + x1) / 2, (y0 + y1) / 2
            w, h = abs(x1 - x0), abs(y1 - y0)
        cw, ch = self.tam_canvas()
        if not (0 <= x <= cw and 0 <= y <= ch):
            return
        self.rois.append({"uid": uuid.uuid4().hex[:14], "id": "", "tipo": self.tipo,
                          "x": round(x, 1), "y": round(y, 1),
                          "roi_w": int(round(w)), "roi_h": int(round(h)),
                          "phillips": self.tipo == "Tornillo"})
        self._renumerar()
        self.sel = len(self.rois) - 1
        self.cambio.emit()
        self.update()

    def _en(self, x, y):
        """Indice de la ROI bajo el punto, de la ultima a la primera: si dos se
        solapan, gana la de encima, que es la que el usuario esta viendo."""
        for i in range(len(self.rois) - 1, -1, -1):
            r = self.rois[i]
            if (abs(x - r["x"]) <= r["roi_w"] / 2
                    and abs(y - r["y"]) <= r["roi_h"] / 2):
                return i
        return -1

    # -- ids ---------------------------------------------------------
    def _renumerar(self):
        """T01, T02... por posicion en la pieza: primero arriba, luego izquierda.

        Numerar por orden de marcado daria una lista que solo entiende quien la
        marco. Asi el T07 del log se busca en la pieza mirando, sin abrir nada.
        Las zonas llevan su propia letra porque no son tornillos y mezclarlas en
        la misma serie confunde al leer el detalle de una inspeccion."""
        prefijo = {"Tornillo": "T", "Giftcard": "G", "LCD": "L",
                   "Backglass": "B"}
        cuenta = {}
        for r in sorted(self.rois, key=lambda r: (round(r["y"] / 60), r["x"])):
            t = r.get("tipo", "Tornillo")
            cuenta[t] = cuenta.get(t, 0) + 1
            r["id"] = f"{prefijo.get(t, 'X')}{cuenta[t]:02d}"

    # -- carga y guardado --------------------------------------------
    def cargar_config(self, ruta):
        if not os.path.exists(ruta):
            self.rois = []
            return None
        with open(ruta, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.rois = [_normalizar(dict(p), cfg) for p in cfg.get("puntos", [])]
        self.update()
        return cfg

    def guardar_config(self, ruta, proceso, modelo, previo=None):
        """Escribe config_rois.json conservando lo que no se marca aqui.

        `previo` son las claves del config anterior -zona_qr, etapa_lcd- que no
        salen de este lienzo. Reescribir el archivo sin ellas borraria las zonas
        del giftcard y del display, que costaron su propia tarde de ajuste."""
        cw, ch = self.tam_canvas()
        cfg = dict(previo or {})
        cfg.update({"version": 2, "proceso": proceso, "modelo": modelo,
                    "creado": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "image_size": [cw, ch],
                    "roi_size": cfg.get("roi_size", 60),
                    "puntos": self.rois})
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ruta)
        return cfg

    def avisos(self):
        """Lo que conviene saber antes de calibrar. No bloquea: avisa."""
        av = []
        cw, ch = self.tam_canvas()
        tor = [r for r in self.rois if r.get("tipo") == "Tornillo"]
        if not tor:
            av.append("No hay ninguna posición de tornillo marcada.")
        for r in self.rois:
            if (r["x"] - r["roi_w"] / 2 < 0 or r["y"] - r["roi_h"] / 2 < 0
                    or r["x"] + r["roi_w"] / 2 > cw or r["y"] + r["roi_h"] / 2 > ch):
                av.append(f"{r['id']} se sale de la imagen.")
        # Solape entre tornillos: si una ROI puede capturar el tornillo vecino,
        # un desplazamiento minimo de la pieza cambia el veredicto.
        for i in range(len(tor)):
            for j in range(i + 1, len(tor)):
                a, b = tor[i], tor[j]
                dx = abs(a["x"] - b["x"]) - (a["roi_w"] + b["roi_w"]) / 2
                dy = abs(a["y"] - b["y"]) - (a["roi_h"] + b["roi_h"]) / 2
                if dx < 0 and dy < 0:
                    av.append(f"{a['id']} y {b['id']} SE SOLAPAN.")
                elif max(dx, dy) < 6:
                    av.append(f"{a['id']} y {b['id']} están a {max(dx, dy):.1f} px.")
        return av
