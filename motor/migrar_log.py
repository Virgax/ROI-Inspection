#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrar_log.py — Normaliza inspecciones_log.csv a un solo esquema de 10 columnas.

POR QUE EXISTE
El log se fue ampliando con el sistema y hoy conviven cuatro formatos en el mismo
archivo, pero la cabecera sigue siendo la de la primera version (6 columnas):

    6 col  fecha,hora,resultado,faltantes,n_faltantes,n_total
    7 col  + proceso
    8 col  + detalle
   10 col  + dudosos,n_dudosos        <- el que escribe el inspector hoy

`analizar_log.py` lo tolera porque distingue los formatos por numero de columnas.
Cualquier otro lector (pandas, Excel, Power BI, un DictReader) se guia por la
cabecera y alinea mal TODAS las filas nuevas: lee el proceso donde dice resultado.
Este script deja un unico esquema y una cabecera que lo describe.

QUE HACE CON LOS DATOS QUE NO EXISTIAN
No inventa nada. Las columnas que una generacion no registraba quedan VACIAS, que
es distinto de cero:
  - `proceso` vacio en las filas de 6 columnas (son anteriores a los procesos).
  - `dudosos`/`n_dudosos` vacios en las de 6 y 7 columnas (el estado "dudoso" aun
    no existia).
  - En las de 8 columnas SI se reconstruyen: el `detalle` lista el estado de cada
    posicion, asi que un 0 ahi es un cero medido, no una suposicion.

USO
    python3 migrar_log.py              # ensayo en seco: dice que haria
    python3 migrar_log.py --aplicar    # reescribe, dejando respaldo con fecha
"""

import argparse
import csv
import os
import shutil
import sys
from collections import Counter
from datetime import datetime

ARCHIVO = "inspecciones_log.csv"

CAMPOS = ["fecha", "hora", "proceso", "resultado", "faltantes", "n_faltantes",
          "dudosos", "n_dudosos", "n_total", "detalle"]


def dudosos_desde_detalle(detalle):
    """Reconstruye (lista, conteo) de posiciones dudosas leyendo el detalle.

    Cada token es `rid:puntaje/umbral:similitud:estado`, con sufijo '+J' cuando
    el Juez 2 rescato la posicion. Solo se usa cuando hay detalle: sin el, el
    dato no se conoce y se deja vacio en vez de escribir un cero falso."""
    if not detalle:
        return "", ""
    dud = []
    for tok in detalle.split(";"):
        p = tok.split(":")
        if len(p) < 4:
            continue
        est = p[3][:-2] if p[3].endswith("+J") else p[3]
        if est == "dudoso":
            dud.append(p[0])
    return ";".join(dud), len(dud)


def normalizar(row):
    """Lleva una fila de cualquier generacion al esquema de 10 columnas."""
    n = len(row)
    if n >= 10:
        return row[:10]
    if n == 8:
        f, h, proc, res, falt, nfalt, ntot, det = row
        dud, ndud = dudosos_desde_detalle(det)
        return [f, h, proc, res, falt, nfalt, dud, ndud, ntot, det]
    if n == 7:
        f, h, proc, res, falt, nfalt, ntot = row
        return [f, h, proc, res, falt, nfalt, "", "", ntot, ""]
    if n == 6:
        f, h, res, falt, nfalt, ntot = row
        return [f, h, "", res, falt, nfalt, "", "", ntot, ""]
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Normaliza el log de inspecciones a un solo esquema")
    ap.add_argument("--archivo", default=ARCHIVO)
    ap.add_argument("--aplicar", action="store_true",
                    help="reescribe el archivo (sin esto solo informa)")
    args = ap.parse_args()

    if not os.path.exists(args.archivo):
        sys.exit(f"[ERROR] No existe {args.archivo}")

    with open(args.archivo, newline="", encoding="utf-8") as f:
        filas = list(csv.reader(f))

    anchos = Counter()
    salida, descartadas = [], []
    for i, row in enumerate(filas):
        if not row or row[0] == "fecha":       # cabeceras viejas, en cualquier punto
            continue
        anchos[len(row)] += 1
        nueva = normalizar(row)
        if nueva is None:
            descartadas.append((i + 1, row))
            continue
        salida.append(nueva)

    print(f"\n=== {args.archivo} ===")
    print(f"filas de datos: {len(salida)}")
    print("formatos encontrados:", dict(sorted(anchos.items())))
    if descartadas:
        print(f"[!] {len(descartadas)} filas con un ancho no reconocido; se "
              f"DESCARTAN. Primeras:")
        for ln, r in descartadas[:5]:
            print(f"    linea {ln}: {r[:6]}")

    recuperados = sum(1 for r in salida if r[6] or r[7] != "")
    con_detalle = sum(1 for r in salida if r[9])
    sin_proceso = sum(1 for r in salida if not r[2])
    print(f"con detalle por posicion: {con_detalle}")
    print(f"con columna dudosos poblada: {recuperados}")
    print(f"sin proceso (anteriores a los procesos): {sin_proceso}")

    if len(anchos) == 1 and 10 in anchos and filas and filas[0] == CAMPOS:
        print("\n[OK] El archivo ya esta normalizado. No hay nada que hacer.")
        return

    if not args.aplicar:
        print("\n[i] Ensayo en seco. Nada se ha escrito.")
        print("    Corre 'python3 migrar_log.py --aplicar' para reescribir.")
        return

    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    respaldo = f"{os.path.splitext(args.archivo)[0]}_pre_migracion_{sello}.csv"
    shutil.copy2(args.archivo, respaldo)

    tmp = args.archivo + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CAMPOS)
        w.writerows(salida)
    os.replace(tmp, args.archivo)             # atomico: o queda el viejo o el nuevo

    print(f"\n[OK] Respaldo: {respaldo}")
    print(f"[OK] {args.archivo} reescrito con {len(salida)} filas y cabecera de "
          f"{len(CAMPOS)} columnas.")


if __name__ == "__main__":
    main()
