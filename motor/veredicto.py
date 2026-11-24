#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
veredicto.py — Votacion temporal, estabilidad y decision de registro.

POR QUE ESTE MODULO EXISTE
Esta logica vivia dentro de `modo_vivo`, enredada con el bucle de video de
OpenCV. Al montar la aplicacion Qt hacia falta la misma decision en otro bucle, y
copiarla habria creado DOS implementaciones de lo mismo. En unas semanas una de
las dos recibe un arreglo que la otra no, y el sistema juzga distinto segun por
donde se lance. El arreglo del re-armado por parpadeo -que costo encontrar- se
perderia justo asi.

Aqui esta una sola vez. El modo terminal y la app Qt la usan igual.

QUE DECIDE, Y QUE NO
Decide CUANDO un veredicto esta lo bastante asentado como para registrarse. NO
decide si un tornillo esta o no: eso lo hace `clasificar()` por frame, y este
modulo solo cuenta votos. Cambiar algo aqui cambia cuando se escribe una fila,
nunca si una pieza pasa.

LAS TRES REGLAS
1. VOTACION. Cada posicion acumula su estado de los ultimos FRAMES_VOTO frames.
   Con tres estados posibles (presente / falta / dudoso) se toma la mayoria, y
   "presente" exige mayoria clara (VOTOS_PRESENTE). Absorbe manos de paso y
   parpadeos.
2. ESTABILIDAD EN DOS NIVELES. El veredicto de la pieza entra con FRAMES_ESTABLE
   frames identicos. Pero un veredicto DISTINTO sobre la misma pieza -una
   correccion del operario- necesita FRAMES_ESTABLE_CAMBIO, bastante mas. Asi
   una correccion real se registra y una oscilacion no.
3. UNA VEZ POR VEREDICTO. Se lleva el conjunto de veredictos ya registrados de
   la pieza actual. Volver a uno ya visto no escribe nada.

   Esto ultimo es el arreglo de fondo. Antes bastaba con que el veredicto dejara
   de ser uniforme para re-armar el registro, asi que una posicion oscilando
   entre 'falta' y 'dudoso' hacia que la MISMA pieza se registrara cada pocos
   segundos: el 8-sep-2026 hay 2403 filas que contienen solo 14 veredictos
   distintos. El log dejaba de contar piezas y pasaba a contar parpadeos.
"""

from collections import deque

FRAMES_VOTO = 15              # ventana de votacion
VOTOS_PRESENTE = 8            # mayoria para declarar presente
FRAMES_ESTABLE = 10           # frames identicos para el primer veredicto
FRAMES_ESTABLE_CAMBIO = 45    # ... y para uno distinto sobre la misma pieza


class Veredicto:
    """Estado de la pieza que hay ahora en el fixture."""

    def __init__(self, claves):
        self.claves = list(claves)
        self.votos = {k: deque(maxlen=FRAMES_VOTO) for k in self.claves}
        # historial numerico de la MISMA ventana: (puntaje, similitud) por frame.
        # Sirve para registrar la MEDIANA de la ventana en vez del ultimo frame,
        # que puede contradecir al veredicto votado.
        self.hist = {k: deque(maxlen=FRAMES_VOTO) for k in self.claves}
        self.historial = deque(maxlen=FRAMES_ESTABLE_CAMBIO)
        self.registrados = set()
        self.rescates = {}

    # ------------------------------------------------------------------
    def nueva_pieza(self):
        """La pieza salio del fixture: empieza otra.

        Es el unico re-armado valido, junto con la perdida de marcadores. Solo
        en ese momento sabemos que lo que viene no es la misma pieza otra vez."""
        self.registrados.clear()
        self.rescates.clear()

    def reiniciar(self):
        """Reinicio completo (se perdieron los marcadores, o cambio la receta)."""
        for d in self.votos.values():
            d.clear()
        for d in self.hist.values():
            d.clear()
        self.historial.clear()
        self.nueva_pieza()

    # ------------------------------------------------------------------
    def observar(self, presencia_frame, scores=None, sims=None):
        """Incorpora un frame y devuelve el estado de la pieza.

        Devuelve un dict con:
          presencia   {clave: True/False/'dudoso'/None}   None = aun votando
          listo       bool, la ventana de votacion esta llena en todas
          faltantes   tupla ordenada
          dudosos     tupla ordenada
          n_presentes int
          hay_pieza   bool, hay algo en el fixture que juzgar
          estable     bool, nivel corto
          veredicto   (faltantes, dudosos)
          resultado   'PASA' / 'NO_PASA' / 'VERIFICAR'
          registrar   bool, ESTA es la llamada en que hay que escribir
        """
        for rid, pres in presencia_frame.items():
            if rid in self.votos:
                self.votos[rid].append(pres)
                self.hist[rid].append(((scores or {}).get(rid, 0.0),
                                       (sims or {}).get(rid)))

        presencia = {}
        for rid, d in self.votos.items():
            if len(d) < FRAMES_VOTO:
                presencia[rid] = None
                continue
            n_true = sum(1 for v in d if v is True)
            n_dud = sum(1 for v in d if v == "dudoso")
            n_false = sum(1 for v in d if v is False)
            if n_true >= VOTOS_PRESENTE:
                presencia[rid] = True
            elif n_dud >= n_false:
                presencia[rid] = "dudoso" if n_dud > 0 else False
            else:
                presencia[rid] = False

        listo = all(v is not None for v in presencia.values())
        faltantes = tuple(sorted(r for r, v in presencia.items() if v is False))
        dudosos = tuple(sorted(r for r, v in presencia.items() if v == "dudoso"))
        n_pres = sum(1 for v in presencia.values() if v is True)

        if listo:
            self.historial.append((faltantes, dudosos))
        ult = list(self.historial)
        estable = (listo and len(ult) >= FRAMES_ESTABLE
                   and len(set(ult[-FRAMES_ESTABLE:])) == 1)
        estable_cambio = (listo and len(ult) == FRAMES_ESTABLE_CAMBIO
                          and len(set(ult)) == 1)

        veredicto = (faltantes, dudosos)
        hay_pieza = bool(n_pres > 0 or dudosos)
        primero = not self.registrados
        registrar = (hay_pieza
                     and veredicto not in self.registrados
                     and (estable if primero else estable_cambio))

        if faltantes:
            resultado = "NO_PASA"
        elif dudosos:
            resultado = "VERIFICAR"
        else:
            resultado = "PASA"

        return {"presencia": presencia, "listo": listo, "faltantes": faltantes,
                "dudosos": dudosos, "n_presentes": n_pres, "hay_pieza": hay_pieza,
                "estable": estable, "estable_cambio": estable_cambio,
                "veredicto": veredicto, "resultado": resultado,
                "registrar": registrar}

    def marcar_registrado(self, veredicto):
        """Confirmar que ese veredicto ya quedo escrito.

        Va aparte de `observar` a proposito: si el registro falla -disco lleno,
        base caida- el veredicto NO se marca y se reintenta en el frame
        siguiente, en vez de darse por escrito algo que no lo esta."""
        self.registrados.add(veredicto)

    def mediana_ventana(self, rid):
        """(puntaje, similitud) medianos de la ventana de esta posicion.

        El veredicto sale de VOTAR una ventana, asi que el puntaje del ultimo
        frame puede contradecirlo -estado 'ok' con un puntaje bajo el umbral-.
        La mediana si representa la decision que se tomo."""
        datos = list(self.hist.get(rid) or [])
        if not datos:
            return None, None
        vals = sorted(v for v, _ in datos)
        pu = vals[len(vals) // 2]
        sv = sorted(t for _, t in datos if t is not None)
        sm = sv[len(sv) // 2] if sv else None
        return pu, sm
