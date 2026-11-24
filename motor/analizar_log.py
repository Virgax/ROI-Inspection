#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analizar_log.py — Lee el log de inspecciones y resume, por posicion, los puntajes
y las SIMILITUDES reales de produccion.

Para que sirve: el 'piso_similitud' de la calibracion sale siempre 0.55 por
construccion (cada muestra se compara contra el banco que la contiene, dando
1.0), asi que no se sabe donde cae la similitud de unidades LEGITIMAS en la linea.
Este script lo mide con datos reales. Con eso el piso se fija con evidencia:
un piso sano debe quedar por DEBAJO de la similitud minima observada en
posiciones que estaban correctas (estado ok), con algo de margen.

POR QUE SE COLAPSAN RACHAS
Hasta el arreglo de FRAMES_ESTABLE_CAMBIO, el inspector volvia a registrar la
misma pieza cada vez que el veredicto dejaba de ser uniforme. Una posicion que
oscilaba entre 'falta' y 'dudoso' bastaba: el 8-sep-2026 hay 2403 filas que
contienen solo 14 veredictos distintos. Sin corregirlo, los percentiles de este
informe los dominan unas pocas escenas repetidas miles de veces, y las tasas de
PASA no significan nada (una pieza buena se estabiliza y registra UNA vez; una
inestable registra cien).

Por eso, por defecto, las filas consecutivas del mismo proceso con el MISMO
veredicto y separadas por menos de --ventana-dedup segundos cuentan como un solo
evento. Es una heuristica, no una verdad: si la linea produce dos piezas
identicas en menos de esa ventana, se cuentan como una. El informe dice siempre
cuantas filas colapso, y --crudo lo desactiva.

Uso:
    python3 analizar_log.py                      # todo el log
    python3 analizar_log.py --proceso "Ensamble 2"
    python3 analizar_log.py --desde 2026-08-07
    python3 analizar_log.py --crudo              # sin colapsar rachas
    python3 analizar_log.py --ventana-dedup 60
"""

import argparse
import csv
import datetime
import os
import sys
from collections import defaultdict

ARCHIVO = "inspecciones_log.csv"


def _pct(vals, p):
    """Percentil simple sin numpy."""
    if not vals:
        return None
    v = sorted(vals)
    k = (len(v) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _colapsar(filas, ventana):
    """Agrupa las filas en SESIONES de pieza y deja un evento por veredicto.

    `filas` es una lista de (fecha, hora, proceso, resultado, faltantes, dudosos,
    detalle) en el orden en que se escribieron.

    Una sesion es una tanda de registros sin huecos: mientras dos registros
    consecutivos disten menos de `ventana` segundos, se asume que el fixture no
    se vacio y que se trata de la misma pieza. Un hueco mayor (o un cambio de
    proceso, o de dia) abre una sesion nueva.

    Dentro de una sesion se conserva la PRIMERA aparicion de cada veredicto
    distinto y se descartan las repeticiones. Esa es exactamente la semantica del
    inspector corregido: una pieza se registra una vez por veredicto, y volver a
    un veredicto ya registrado no escribe nada.

    No colapsar por rachas CONSECUTIVAS es deliberado: el parpadeo que motivo
    esto alterna entre varias firmas (A,B,A,B...), asi que dos filas seguidas
    casi nunca son iguales y una comparacion con la anterior no lo detectaria.

    Limite conocido: dos piezas realmente distintas con el mismo veredicto y
    menos de `ventana` segundos entre ellas se cuentan como una. Con el default
    de 30 s eso exige un ciclo de pieza mas rapido que el cambio manual. Sube
    --ventana-dedup si la linea es mas lenta, bajala si es mas rapida.

    Devuelve (filas_conservadas, n_colapsadas)."""
    if ventana <= 0 or not filas:
        return filas, 0

    def ts(f):
        try:
            return datetime.datetime.strptime(f"{f[0]} {f[1]}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    salida = []
    vistos = set()          # veredictos ya registrados en la sesion en curso
    t_ant, proc_ant = None, None
    for f in filas:
        t = ts(f)
        hueco = (t is None or t_ant is None
                 or not (0 <= (t - t_ant).total_seconds() < ventana))
        if hueco or f[2] != proc_ant:
            vistos.clear()                      # empieza una sesion nueva
        veredicto = (f[4], f[5])
        if veredicto not in vistos:
            vistos.add(veredicto)
            salida.append(f)
        t_ant, proc_ant = t, f[2]
    return salida, len(filas) - len(salida)


def main():
    ap = argparse.ArgumentParser(description="Resumen del log de inspecciones")
    ap.add_argument("--archivo", default=ARCHIVO)
    ap.add_argument("--proceso", default=None, help="filtrar por proceso")
    ap.add_argument("--desde", default=None, help="fecha minima YYYY-MM-DD")
    ap.add_argument("--ventana-dedup", type=float, default=30.0, metavar="SEG",
                    help="segundos bajo los cuales dos filas consecutivas con el "
                         "mismo veredicto se cuentan como una sola pieza "
                         "(default 30; 0 desactiva)")
    ap.add_argument("--crudo", action="store_true",
                    help="no colapsar rachas: cuenta todas las filas del log")
    args = ap.parse_args()

    if not os.path.exists(args.archivo):
        sys.exit(f"[ERROR] No existe {args.archivo}")

    # acumuladores por posicion
    punt_ok = defaultdict(list)     # puntajes de posiciones correctas
    punt_falta = defaultdict(list)  # puntajes de posiciones faltantes
    umbral_de = {}                  # umbral vigente por posicion
    sim_ok = defaultdict(list)      # similitudes de posiciones correctas
    sim_falta = defaultdict(list)   # similitudes de posiciones faltantes
    n_ok = defaultdict(int)
    n_falta = defaultdict(int)
    n_dudoso = defaultdict(int)
    n_rescate = defaultdict(int)
    filas = con_detalle = 0
    resultados = defaultdict(int)

    # --- fase 1: leer y normalizar --------------------------------------
    # El CSV ha tenido cuatro formatos; se distinguen por numero de columnas.
    # El 'detalle' es siempre la ULTIMA columna cuando existe, pero su indice
    # cambio al separar dudosos, asi que no se puede fijar en row[7].
    #  6: fecha,hora,resultado,faltantes,n_faltantes,n_total
    #  7: fecha,hora,proceso,resultado,faltantes,n_faltantes,n_total
    #  8: ...,n_total,detalle
    # 10: ...,faltantes,n_faltantes,dudosos,n_dudosos,n_total,detalle
    # (migrar_log.py deja todo en el de 10; esto sigue aqui para poder leer
    # respaldos anteriores a la migracion.)
    crudas = []
    with open(args.archivo, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0] == "fecha":
                continue
            if len(row) >= 10:
                fecha, hora, proc, res = row[0], row[1], row[2], row[3]
                falt, dud, detalle = row[4], row[6], row[9]
            elif len(row) == 8:
                fecha, hora, proc, res = row[0], row[1], row[2], row[3]
                falt, dud, detalle = row[4], "", row[7]
            elif len(row) == 7:
                fecha, hora, proc, res = row[0], row[1], row[2], row[3]
                falt, dud, detalle = row[4], "", ""
            else:
                fecha, hora, proc, res = row[0], row[1], "", row[2]
                falt, dud, detalle = row[3], "", ""
            if args.proceso and proc != args.proceso:
                continue
            if args.desde and fecha < args.desde:
                continue
            crudas.append((fecha, hora, proc, res, falt, dud, detalle))

    # --- fase 2: colapsar rachas del mismo veredicto ---------------------
    if args.crudo:
        usadas, colapsadas = crudas, 0
    else:
        usadas, colapsadas = _colapsar(crudas, args.ventana_dedup)

    # --- fase 3: acumular por posicion -----------------------------------
    for fecha, hora, proc, res, falt, dud, detalle in usadas:
        filas += 1
        resultados[res] += 1
        if not detalle:
            continue
        con_detalle += 1
        for tok in detalle.split(";"):
            p = tok.split(":")
            if len(p) < 4:
                continue
            rid, pu, sm, est = p[0], p[1], p[2], p[3]
            try:
                partes_pu = pu.split("/")
                punt = float(partes_pu[0])
                if len(partes_pu) > 1:
                    umbral_de[rid] = float(partes_pu[1])
            except ValueError:
                continue
            sim = None
            if sm not in ("-", ""):
                try:
                    sim = float(sm)
                except ValueError:
                    sim = None
            if est.endswith("+J"):
                n_rescate[rid] += 1
                est = est[:-2]
            if est == "ok":
                n_ok[rid] += 1
                punt_ok[rid].append(punt)
                if sim is not None:
                    sim_ok[rid].append(sim)
            elif est == "falta":
                n_falta[rid] += 1
                punt_falta[rid].append(punt)
                if sim is not None:
                    sim_falta[rid].append(sim)
            elif est == "dudoso":
                n_dudoso[rid] += 1

    print(f"\n=== Log: {filas} inspecciones "
          f"({con_detalle} con detalle por posicion) ===")
    if colapsadas:
        print(f"[i] Se colapsaron {colapsadas} filas de {len(crudas)} "
              f"({100.0 * colapsadas / len(crudas):.0f}%): eran re-registros de "
              f"una misma pieza con el mismo veredicto, a menos de "
              f"{args.ventana_dedup:.0f} s. Usa --crudo para contarlas todas.")
    elif args.crudo:
        print(f"[i] Modo crudo: {len(crudas)} filas sin colapsar. Las escenas "
              f"inestables anteriores al arreglo estan sobrerrepresentadas.")
    print("resultados:", dict(resultados))
    if con_detalle == 0:
        print("\n[i] Ninguna fila tiene detalle por posicion todavia. Las filas con\n"
              "    puntajes y similitudes se generan con la version nueva del\n"
              "    inspector; corre unas piezas y vuelve a ejecutar esto.\n")
        return

    todos = sorted(set(list(n_ok) + list(n_falta) + list(n_dudoso)))
    print(f"\n{'ROI':6s} | {'ok':5s} {'falta':6s} {'dud':4s} {'resc':5s} "
          f"| {'umbr':5s} | {'ok min/med':13s} | {'falta max/med':13s} | {'margen':7s} "
          f"| sim ok min")
    print("-" * 108)
    for rid in todos:
        po, pf = punt_ok[rid], punt_falta[rid]
        so = sim_ok[rid]
        umb = umbral_de.get(rid)
        pmin = f"{min(po):.1f}" if po else "-"
        pmed = f"{sum(po)/len(po):.1f}" if po else "-"
        fmax = f"{max(pf):.1f}" if pf else "-"
        fmed = f"{sum(pf)/len(pf):.1f}" if pf else "-"
        # HUECO real: cuanto separa el peor 'ok' del mejor 'falta'
        if po and pf:
            hueco = min(po) - max(pf)
            htxt = f"{hueco:+.1f}"
        else:
            hueco, htxt = None, "-"
        smin = f"{min(so):.2f}" if so else "-"
        alerta = ""
        if hueco is not None and hueco < 3:
            alerta = " <-- sin holgura"
        if n_rescate[rid] > 0 and n_ok[rid] > 0 and n_rescate[rid] / n_ok[rid] > 0.5:
            alerta = " <-- depende del JUEZ"
        print(f"{rid:6s} | {n_ok[rid]:5d} {n_falta[rid]:6d} {n_dudoso[rid]:4d} "
              f"{n_rescate[rid]:5d} | {umb if umb else 0:5.1f} | {pmin:>5s} / {pmed:5s} "
              f"| {fmax:>5s} / {fmed:5s} | {htxt:>7s} | {smin:>4s}{alerta}")
    print("\n'margen' = peor puntaje correcto menos mejor puntaje faltante, con datos")
    print("reales de linea. Es la holgura VERDADERA, distinta de la de calibracion.")

    # recomendacion global de piso, basada en unidades correctas reales
    todas_ok = [v for l in sim_ok.values() for v in l]
    todas_falta = [v for l in sim_falta.values() for v in l]
    if todas_ok:
        print("\n--- Piso de similitud sugerido (con datos reales) ---")
        mn = min(todas_ok)
        p1 = _pct(todas_ok, 1)
        print(f"similitud de posiciones CORRECTAS: min {mn:.3f}  "
              f"p1 {p1:.3f}  med {sum(todas_ok)/len(todas_ok):.3f}")
        if todas_falta:
            print(f"similitud de posiciones FALTANTES: "
                  f"med {sum(todas_falta)/len(todas_falta):.3f}  "
                  f"max {max(todas_falta):.3f}")
        # el piso NUNCA debe quedar por encima de la similitud minima observada en
        # posiciones correctas: marcaria amarillas piezas legitimas. Se toma el
        # minimo real menos un margen, no el percentil (que puede superar al minimo).
        sug = max(0.0, round(mn - 0.08, 2))
        print(f"\npiso sugerido: {sug:.2f}   (minimo observado {mn:.3f} menos 0.08 "
              f"de margen)")
        print("El piso actual de la calibracion es 0.55 fijo. Si el sugerido queda")
        print("MUY por debajo, el 0.55 puede estar marcando amarillos de mas; si")
        print("queda por encima, el 0.55 nunca disparara.")
        if mn < 0.55:
            print(f"\n[!] Hay posiciones CORRECTAS con similitud {mn:.3f} < 0.55:")
            print("    el piso actual de 0.55 las marca 'dudoso' sin razon. Bajalo.")
        else:
            print(f"\n[OK] El piso actual de 0.55 queda {mn-0.55:.3f} por debajo del")
            print("     minimo observado en posiciones correctas: no genera falsas")
            print("     alarmas. No hace falta cambiarlo.")
    print()


if __name__ == "__main__":
    main()
