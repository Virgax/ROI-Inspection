#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contaminacion.py — Deteccion de contaminacion en vivo para el inspector.

QUE HACE
Cuando la pieza lleva N frames con homografia estable, analiza UNA vez el cuadro
rectificado buscando tornillos sueltos y piezas metalicas. Dibuja un recuadro
magenta con la palabra VERIFICAR sobre cada zona sospechosa.

POR QUE EN UN HILO APARTE
El analisis tarda entre 3 y 6 segundos: calcula el mapa de anomalia sobre 5
megapixeles y clasifica varios cientos de recortes. Hacerlo en el bucle de video
congelaria la imagen todo ese tiempo. En un hilo, el video sigue a su ritmo y el
resultado aparece cuando esta listo.

El hilo trabaja sobre una COPIA del frame. Sin copia, el bucle principal podria
estar sobrescribiendo el mismo buffer mientras el hilo lo analiza, y el resultado
seria una mezcla de dos piezas.

POR QUE SE DESCARTA SI LA PIEZA SALE
Un analisis que tarda 5 segundos puede terminar cuando el operario ya retiro el
telefono. Mostrar entonces "VERIFICAR" senalaria contaminacion de una pieza que
ya no esta, y el operario buscaria algo inexistente. Cada analisis lleva el
numero de pieza; si al terminar no coincide con la pieza actual, se tira.

RENDIMIENTO MEDIDO (106 + 27 tornillos reales, dos lotes independientes)
  umbral 2.5 -> 81% de deteccion, 3.7 falsos por imagen
  umbral 3.0 -> 63-92% segun el lote, 0.7-6.1 falsos
La variacion entre lotes viene de que la linea base (82 imagenes de pocos dias)
no cubre toda la variacion normal de produccion. Con mas imagenes buenas de dias
distintos, un mismo umbral rendiria igual en todos los lotes.

USO DESDE EL INSPECTOR
    import contaminacion
    detector = contaminacion.Detector()          # None si falta algo
    ...
    # en el bucle, con warped valido:
    detector.actualizar(warped, pieza_id)
    detector.dibujar(vista)
    # cuando no hay marcadores:
    detector.reset()
"""

import os
import threading
import time

import cv2
import numpy as np

# --- configuracion ---
ARCHIVO_BASE = "base82_prom.npz"
ARCHIVO_MODELO = "modelo_v2.pt"
ARCHIVO_AREA = "area_enclosure.json"
ARCHIVO_ROIS = None              # config_rois.json cuando este recalibrado

UMBRAL = 2.5                     # etapa 1 (medido: 81% con 3.7 falsos/imagen)
CONFIANZA = 0.5                  # etapa 2
RECORTE = 64
FRAMES_PARA_DISPARAR = 15        # ~1 s a 15 fps de homografia estable
LOTE_CLASIFICADOR = 64

MAGENTA = (240, 60, 200)         # BGR


def _resolver(nombre, carpeta):
    """Ruta del archivo: primero dentro de la receta, despues en la raiz.

    La linea base y el contorno del enclosure DEPENDEN DEL MODELO, porque
    dependen del tamano del canvas, que cambia de un modelo a otro. Tenerlos
    solo en la raiz obliga a duplicar el programa entero por modelo, que es
    justo lo que queremos dejar de hacer.

    Mezclarlos ya costo caro una vez: la linea base de 3200x1659 aplicada sobre
    un canvas de 3200x1607 subio el p99 de 4.2 a 40.4 sigmas. Todo sospechoso,
    sin que nada avisara.

    El clasificador (modelo_v2.pt) si se comparte: clasifica tipos de suciedad,
    no geometria. Por eso el respaldo a la raiz, y no un error: lo que es comun
    se deja arriba y lo que es del modelo se pone en su receta.
    """
    if not nombre or os.path.isabs(nombre):
        return nombre
    if carpeta:
        p = os.path.join(carpeta, nombre)
        if os.path.exists(p):
            return p
    return nombre


class Detector:
    """Analiza contaminacion en segundo plano y dibuja el resultado.

    Se construye una vez al arrancar: cargar la linea base y el modelo tarda
    varios segundos y no puede hacerse por cada pieza."""

    def __init__(self, base=ARCHIVO_BASE, modelo=ARCHIVO_MODELO,
                 area=ARCHIVO_AREA, rois=ARCHIVO_ROIS,
                 umbral=UMBRAL, confianza=CONFIANZA, verbose=True,
                 activo=True, carpeta=None):
        self.ok = False
        self.umbral = umbral
        self.confianza = confianza
        self._aviso_tam = False

        # Apagado a peticion. Todos los metodos publicos ya se protegen con
        # `self.ok`, asi que salir aqui deja un objeto completo pero inerte: el
        # bucle de video lo sigue llamando igual y no tiene que preguntar si
        # existe. `cerrar()` solo escribe _parar, asi que tampoco falla.
        if not activo:
            if verbose:
                print("[contaminacion] Desactivada por configuracion.")
            return

        try:
            import detector_anomalias as D
        except ImportError:
            if verbose:
                print("[contaminacion] Falta detector_anomalias.py. Desactivado.")
            return
        self.D = D

        # Lo del modelo, en la receta; lo comun, en la raiz. Ver _resolver.
        base = _resolver(base, carpeta)
        modelo = _resolver(modelo, carpeta)
        area = _resolver(area, carpeta)
        rois = _resolver(rois, carpeta)

        for f in (base, modelo, area):
            if f and not os.path.exists(f):
                if verbose:
                    print(f"[contaminacion] Falta '{f}'. Desactivado.")
                return

        try:
            from ultralytics import YOLO
        except ImportError:
            if verbose:
                print("[contaminacion] Falta ultralytics "
                      "(pip3 install ultralytics). Desactivado.")
            return

        try:
            t0 = time.time()
            self.base = D.cargar_base(base)
            self.modelo = YOLO(modelo)
            self.idx = None
            for k, v in self.modelo.names.items():
                if str(v).lower().startswith("tornillo"):
                    self.idx = k
            if self.idx is None:
                if verbose:
                    print(f"[contaminacion] El modelo no tiene clase 'tornillo': "
                          f"{self.modelo.names}. Desactivado.")
                return
            self.area = D.cargar_area(area, self.base["alto"], self.base["ancho"]) \
                if area else None
            self.rois = D.cargar_rois(rois, self.base["alto"], self.base["ancho"]) \
                if rois else None
            self.forma = {"lado_min": D.LADO_MIN_PX, "lado_max": D.LADO_MAX_PX,
                          "aspecto_max": D.ASPECTO_MAX,
                          "llenado_min": D.LLENADO_MIN}
        except SystemExit as e:
            # cargar_base y compañia hacen sys.exit ante un fallo. Aqui no
            # queremos tumbar el inspector entero: se desactiva y sigue.
            if verbose:
                print(f"[contaminacion] {e}. Desactivado.")
            return
        except Exception as e:
            if verbose:
                print(f"[contaminacion] Error al cargar: {e}. Desactivado.")
            return

        self._lock = threading.Lock()
        self._pendiente = None        # (frame, pieza_id) por analizar
        self._resultado = []          # zonas de la pieza actual
        self._pieza_result = None     # a que pieza pertenece el resultado
        self._analizando = False
        self._pieza_lanzada = None    # ultima pieza que se mando a analizar
        self._estables = 0
        self._ultimo_ms = 0
        self._parar = False

        self._hilo = threading.Thread(target=self._trabajar, daemon=True)
        self._hilo.start()
        self.ok = True
        if verbose:
            print(f"[contaminacion] Listo en {time.time()-t0:.1f}s | "
                  f"umbral {umbral} | confianza {confianza} | "
                  f"dispara a los {FRAMES_PARA_DISPARAR} frames estables")

    # ------------------------------------------------------------------
    def actualizar(self, warped, pieza_id):
        """Llamar en cada frame con homografia valida.

        `pieza_id` debe cambiar cuando entra una pieza nueva. Sirve para dos
        cosas: no analizar dos veces la misma pieza, y descartar un resultado
        que llegue tarde, cuando esa pieza ya no esta."""
        if not self.ok:
            return
        self._estables += 1
        if self._estables < FRAMES_PARA_DISPARAR:
            return
        if self._pieza_lanzada == pieza_id or self._analizando:
            return
        with self._lock:
            # copia: el bucle principal reutiliza el buffer del frame siguiente
            self._pendiente = (warped.copy(), pieza_id)
            self._analizando = True
            self._pieza_lanzada = pieza_id

    def reset(self):
        """Llamar cuando se pierden los marcadores (pieza retirada)."""
        if not self.ok:
            return
        self._estables = 0
        with self._lock:
            self._resultado = []
            self._pieza_result = None
            self._pieza_lanzada = None

    def estado(self, pieza_id):
        """(texto, n_zonas) para el panel. n_zonas es -1 mientras analiza."""
        if not self.ok:
            return ("", 0)
        if self._analizando:
            return ("Analizando contaminacion...", -1)
        if self._pieza_result != pieza_id:
            return ("", 0)
        n = len(self._resultado)
        if n == 0:
            return ("Contaminacion: limpio", 0)
        return (f"Contaminacion: {n} zona{'s' if n > 1 else ''}", n)

    def zonas(self, pieza_id):
        if not self.ok or self._pieza_result != pieza_id:
            return []
        return list(self._resultado)

    def dibujar(self, vista, pieza_id=None, escala=None):
        """Dibuja los recuadros magenta con VERIFICAR sobre `vista`.

        `escala` permite dibujar sobre una vista redimensionada; si es None se
        deduce del ancho de la vista respecto al de la linea base."""
        if not self.ok:
            return vista
        if pieza_id is not None and self._pieza_result != pieza_id:
            return vista
        zonas = self._resultado
        if not zonas:
            return vista
        if escala is None:
            escala = vista.shape[1] / float(self.base["ancho"])
        m = int(RECORTE * escala / 2)
        for z in zonas:
            x, y = int(z["cx"] * escala), int(z["cy"] * escala)
            cv2.rectangle(vista, (x - m, y - m), (x + m, y + m), MAGENTA, 3)
            # etiqueta sobre fondo solido: el interior del housing es oscuro y
            # abigarrado, y el texto plano se pierde
            txt = "VERIFICAR"
            fs = max(0.5, 0.75 * escala) if escala < 1 else 0.75
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
            ex0, ey0 = x - m, y - m - th - 12
            if ey0 < 2:                      # si no cabe arriba, va debajo
                ey0 = y + m + 4
            cv2.rectangle(vista, (ex0, ey0), (ex0 + tw + 14, ey0 + th + 10),
                          MAGENTA, -1)
            cv2.putText(vista, txt, (ex0 + 7, ey0 + th + 3),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 2,
                        cv2.LINE_AA)
        return vista

    def cerrar(self):
        self._parar = True

    # ------------------------------------------------------------------
    def _trabajar(self):
        while not self._parar:
            with self._lock:
                tarea = self._pendiente
                self._pendiente = None
            if tarea is None:
                time.sleep(0.03)
                continue
            frame, pieza = tarea
            try:
                zonas = self._analizar(frame)
            except Exception as e:
                print(f"[contaminacion] Error en el analisis: {e}")
                zonas = []
            with self._lock:
                # si la pieza cambio mientras analizabamos, el resultado ya no
                # corresponde a lo que hay en el fixture: se tira
                if self._pieza_lanzada == pieza:
                    self._resultado = zonas
                    self._pieza_result = pieza
                self._analizando = False

    def _analizar(self, warped):
        D = self.D
        t0 = time.time()
        alto, ancho = self.base["alto"], self.base["ancho"]
        if warped.shape[:2] != (alto, ancho):
            if not self._aviso_tam:
                print(f"[contaminacion] El rectificado mide "
                      f"{warped.shape[1]}x{warped.shape[0]} y la linea base "
                      f"{ancho}x{alto}. Se redimensiona, pero conviene "
                      f"reconstruir la base al tamano real: un reescalado "
                      f"cambia la textura fina que el detector compara.")
                self._aviso_tam = True
            warped = cv2.resize(warped, (ancho, alto),
                                interpolation=cv2.INTER_AREA)

        mapa = D.mapa_anomalia(warped, self.base)
        mapa = D.aplicar_mascaras(mapa, self.base, self.area, self.rois)
        regs = D.regiones(mapa, self.umbral, paso=self.base["paso"],
                          forma=self.forma)

        paso, off = self.base["paso"], self.base["lado"] // 2
        recortes, centros = [], []
        h, w = warped.shape[:2]
        for r in regs:
            cx = off + (r["x"] + r["w"] / 2.0) * paso
            cy = off + (r["y"] + r["h"] / 2.0) * paso
            m = RECORTE // 2
            x0, y0 = int(round(cx)) - m, int(round(cy)) - m
            if x0 < 0 or y0 < 0 or x0 + RECORTE > w or y0 + RECORTE > h:
                continue
            recortes.append(warped[y0:y0 + RECORTE, x0:x0 + RECORTE])
            centros.append((cx, cy, r["sigmas"]))

        zonas = []
        for i in range(0, len(recortes), LOTE_CLASIFICADOR):
            res = self.modelo.predict(recortes[i:i + LOTE_CLASIFICADOR],
                                      verbose=False, imgsz=RECORTE)
            for j, rr in enumerate(res):
                conf = float(rr.probs.data[self.idx])
                if conf >= self.confianza:
                    cx, cy, sg = centros[i + j]
                    zonas.append({"cx": cx, "cy": cy, "conf": conf,
                                  "sigmas": sg})

        self._ultimo_ms = int((time.time() - t0) * 1000)
        print(f"[contaminacion] {len(regs)} candidatos -> {len(zonas)} zonas "
              f"({self._ultimo_ms} ms)")
        return zonas
