#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
banco.py — Banco de evidencia para afinar el sistema despues.

QUE HACE
Guarda, de cada pieza que el inspector registra, la imagen rectificada LIMPIA y
una ficha con lo que el sistema decidio y con que numeros. Nada mas. No juzga, no
etiqueta y no toca la calibracion.

POR QUE UN BANCO APARTE Y NO ALIMENTAR LA CALIBRACION DIRECTAMENTE
Porque el sistema estaria aprendiendo de sus propios veredictos. Una pieza que
paso siendo mala se convertiria en plantilla de "presente", empujaria esa clase
hacia el estado vacio y REDUCIRIA la separacion; y cada reduccion hace mas
probable el siguiente falso PASA. Es un lazo que se degrada solo, despacio y sin
avisar: el sistema "mejorando" hacia el error. Con la separacion actual en 13.9
el margen para eso es minimo.

Tampoco se le pregunta nada al operario. Anadir una pregunta por pieza es
friccion en la linea, y la respuesta no se puede auditar despues. El banco guarda
HECHOS -la imagen y los numeros- y la decision de que entra a la calibracion la
toma un jurado externo, mirando la evidencia, fuera del momento de produccion.

QUE SE GUARDA POR PIEZA
  banco_evidencia/<receta>/<AAAA-MM-DD>/<hora>_<GCN>_<VEREDICTO>.jpg
  banco_evidencia/<receta>/<AAAA-MM-DD>/fichas.jsonl   (una linea por pieza)

La ficha lleva el veredicto, las posiciones en falta y dudosas, y el puntaje,
umbral y similitud de CADA posicion. Con eso el jurado puede ordenar por lo que
le interese -"ensename los PASA con menos margen", "los fallos de T12"- sin
volver a abrir una sola imagen.

La imagen es el `warped` LIMPIO, antes de dibujar overlay. Una foto con circulos
y banner encima no sirve para calibrar: el sistema aprenderia que esos dibujos
son parte de la pieza.

POR QUE EN UN HILO
Codificar el JPEG son unos 46 ms y escribirlo otro tanto. En el bucle de video
eso es perder un par de frames en el momento justo en que la pieza se declara
estable. En un hilo, el bucle ni se entera.

USO
    import banco
    b = banco.Banco(raiz, receta)        # b.activo dice si quedo operativo
    b.guardar(warped, resultado, faltantes, dudosos, scores, umbrales, sims, gcn)
    b.cerrar()
"""

import json
import os
import queue
import threading
import time
from datetime import datetime

import cv2

# Calidad del JPEG. 90 es el punto que ya usa el resto del sistema: ~0,7 MB por
# pieza a 3200 px. Sin perdida (PNG) serian 8 MB y el banco se volveria
# inmanejable en semanas sin aportar nada que el ojo o el clasificador noten.
CALIDAD = 90

# Cada cuantas piezas se recuerda cuanto espacio lleva ocupado.
AVISO_CADA = 200


class Banco:

    def __init__(self, raiz, receta, activo=True, calidad=CALIDAD, verbose=True):
        self.receta = receta
        self.calidad = calidad
        self.activo = False
        self.motivo = ""
        self.n = 0
        self._cola = queue.Queue(maxsize=64)
        self._parar = False

        if not activo:
            self.motivo = "desactivado con --sin-banco"
            return
        self.dir_receta = os.path.join(raiz, receta)
        try:
            os.makedirs(self.dir_receta, exist_ok=True)
        except OSError as e:
            self.motivo = f"no se pudo crear {self.dir_receta}: {e}"
            if verbose:
                print(f"[banco] Desactivado: {self.motivo}")
            return

        self._hilo = threading.Thread(target=self._trabajar, daemon=True)
        self._hilo.start()
        self.activo = True
        if verbose:
            print(f"[banco] Guardando evidencia en {self.dir_receta} "
                  f"({self._tamano_legible()} acumulados)")

    # ------------------------------------------------------------------
    def guardar(self, warped, resultado, faltantes, dudosos, scores,
                umbrales=None, sims=None, gcn=None, estacion=None):
        """Encola una pieza. Nunca bloquea el bucle de video.

        Si la cola esta llena se DESCARTA esta pieza y se avisa. Es deliberado:
        el banco es un extra, y ninguna pieza del banco vale detener la
        inspeccion. Que se llene significa que el disco no da abasto, y eso hay
        que saberlo, no disimularlo."""
        if not self.activo or warped is None:
            return
        try:
            self._cola.put_nowait((warped.copy(), resultado, list(faltantes),
                                   list(dudosos), dict(scores or {}),
                                   dict(umbrales or {}), dict(sims or {}),
                                   gcn, estacion, datetime.now()))
        except queue.Full:
            print("[banco] Cola llena: esta pieza no se guarda. "
                  "El disco no esta siguiendo el ritmo de la linea.")

    def cerrar(self):
        self._parar = True

    # ------------------------------------------------------------------
    def _tamano_legible(self):
        total = 0
        for raiz, _d, archivos in os.walk(self.dir_receta):
            for a in archivos:
                try:
                    total += os.path.getsize(os.path.join(raiz, a))
                except OSError:
                    pass
        for u in ("B", "KB", "MB", "GB", "TB"):
            if total < 1024 or u == "TB":
                return f"{total:.0f} {u}" if u == "B" else f"{total:.1f} {u}"
            total /= 1024.0

    def _trabajar(self):
        while not self._parar:
            try:
                tarea = self._cola.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self._escribir(*tarea)
            except Exception as e:
                print(f"[banco] No se pudo guardar la pieza: {e}")

    def _escribir(self, warped, resultado, faltantes, dudosos, scores,
                  umbrales, sims, gcn, estacion, cuando):
        dia = cuando.strftime("%Y-%m-%d")
        carpeta = os.path.join(self.dir_receta, dia)
        os.makedirs(carpeta, exist_ok=True)

        etiqueta_gcn = (gcn or "sinGCN").replace(os.sep, "_")
        # Milisegundos en el nombre: dos piezas del mismo GCN en el mismo segundo
        # -una pieza que se re-registra al corregirse, por ejemplo- se pisarian
        # la una a la otra, y justamente ese par (antes y despues de la
        # correccion) es de lo mas util que puede recibir el jurado.
        sello = f"{cuando.strftime('%H%M%S')}{cuando.microsecond // 1000:03d}"
        nombre = f"{sello}_{etiqueta_gcn}_{resultado}.jpg"
        ruta = os.path.join(carpeta, nombre)
        ok, buf = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, self.calidad])
        if not ok:
            raise RuntimeError("no se pudo codificar la imagen")
        with open(ruta, "wb") as f:
            f.write(buf.tobytes())

        # Ficha: el detalle por posicion es lo que permite al jurado filtrar sin
        # abrir imagenes. Se guarda el umbral junto al puntaje porque el umbral
        # cambia con cada recalibracion, y dentro de un mes un puntaje suelto ya
        # no diria si la posicion iba holgada o apurada.
        glob = (umbrales or {}).get("umbral_global", 50.0)
        por_roi = (umbrales or {}).get("por_roi", {})
        detalle = {}
        for rid in sorted(scores):
            info = por_roi.get(rid, {})
            detalle[rid] = {
                "puntaje": round(float(scores[rid]), 1),
                "umbral": round(float(info.get("umbral", glob)), 1),
                "similitud": (round(float(sims[rid]), 3)
                              if sims and sims.get(rid) is not None else None),
                "estado": ("falta" if rid in faltantes else
                           "dudoso" if rid in dudosos else "ok"),
            }
        ficha = {
            "archivo": nombre,
            "fecha": dia,
            "hora": cuando.strftime("%H:%M:%S"),
            "receta": self.receta,
            "estacion": estacion,
            "gcn": gcn,
            "resultado": resultado,
            "faltantes": faltantes,
            "dudosos": dudosos,
            "n_total": len(scores),
            "revisado": False,      # lo pone el jurado externo, nunca la app
            "veredicto_jurado": None,
            "detalle": detalle,
        }
        with open(os.path.join(carpeta, "fichas.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ficha, ensure_ascii=False) + "\n")

        self.n += 1
        if self.n % AVISO_CADA == 0:
            print(f"[banco] {self.n} piezas guardadas en esta sesion "
                  f"({self._tamano_legible()} en total).")
