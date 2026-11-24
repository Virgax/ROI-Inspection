#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ver_plantillas.py — Genera hojas de contacto con las plantillas guardadas en la
calibracion, para poder revisarlas a ojo.

Para que sirve:
  - Comprobar que la calibracion capturo lo que debia (y no una mano, un reflejo
    o la pieza mal asentada).
  - Encontrar la unidad ATIPICA cuando una posicion sale con poco margen: basta
    con que una de las unidades se capturara mal para hundir el umbral de esa
    posicion, y en la tabla de calibracion eso solo se ve como un numero bajo.
    Aqui se ve cual es.
  - Obtener imagenes reales para documentacion o presentaciones.

Uso:
    python3 ver_plantillas.py                      # menu de procesos
    python3 ver_plantillas.py --receta "Ensamble 2"
    python3 ver_plantillas.py --receta "Ensamble 2" --roi T17
    python3 ver_plantillas.py --crudos             # usa los recortes de ROI completa

Salidas en procesos/<receta>/plantillas_vista/:
    <ROI>.png       una hoja por posicion (presentes arriba, vacias abajo)
    _resumen.png    todas las posiciones juntas, una fila cada una
"""

import argparse
import json
import os
import sys

import cv2
import numpy as np

DIR_RECETAS = "procesos"
LADO = 110          # px por miniatura en la hoja de contacto
MARGEN = 12
VERDE = (110, 205, 62)
ROJO = (77, 86, 226)
GRIS = (150, 150, 150)
FONDO = (28, 30, 34)


def _recetas():
    if not os.path.isdir(DIR_RECETAS):
        return []
    return sorted(x for x in os.listdir(DIR_RECETAS)
                  if os.path.isdir(os.path.join(DIR_RECETAS, x)) and not x.startswith("."))


def _elegir(arg):
    if arg:
        return arg
    rs = _recetas()
    if not rs:
        sys.exit(f"[ERROR] No hay procesos en '{DIR_RECETAS}/'.")
    if len(rs) == 1:
        print(f"[i] Proceso: {rs[0]}")
        return rs[0]
    print("Procesos disponibles:")
    for i, r in enumerate(rs, 1):
        print(f"  {i}) {r}")
    sel = input("Numero: ").strip()
    if sel.isdigit() and 1 <= int(sel) <= len(rs):
        return rs[int(sel) - 1]
    sys.exit("Opcion invalida.")


def _claves(cfg):
    """Misma logica de clave interna que inspector.py: la etiqueta si es unica, o
    la etiqueta con sufijo del uid si esta repetida."""
    conteo = {}
    for p in cfg["puntos"]:
        conteo[p.get("id")] = conteo.get(p.get("id"), 0) + 1
    for i, p in enumerate(cfg["puntos"]):
        rid = p.get("id")
        if conteo.get(rid, 0) > 1:
            uid = str(p.get("uid") or "")
            p["_clave"] = f"{rid}#{uid[-4:]}" if len(uid) >= 4 else f"{rid}#{i:02d}"
        else:
            p["_clave"] = rid
    return [p["_clave"] for p in cfg["puntos"]]


def _muestras(banco):
    if banco is None:
        return []
    if banco.ndim == 2:
        return [banco.astype(np.uint8)]
    return [banco[i].astype(np.uint8) for i in range(banco.shape[0])]


def _mini(img, lado=LADO):
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = cv2.resize(g.astype(np.uint8), (lado, lado), interpolation=cv2.INTER_NEAREST)
    return cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)


def _atipica(muestras):
    """Indice de la muestra que MENOS se parece a las demas.

    Si una unidad se capturo mal, sera la que mas se aleje del resto. Se compara
    cada muestra contra las otras y se devuelve la de menor parecido medio, junto
    con los parecidos de todas para poder ver si destaca o si todas son parecidas."""
    n = len(muestras)
    if n < 3:
        return None, []
    lado = min(min(m.shape[:2]) for m in muestras)
    norm = [cv2.resize(m, (lado, lado)) for m in muestras]
    medias = []
    for i in range(n):
        vals = []
        for j in range(n):
            if i == j:
                continue
            a = norm[i].astype(np.float32)
            b = norm[j].astype(np.float32)
            r = cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)
            vals.append(float(r.max()))
        medias.append(sum(vals) / len(vals))
    return int(np.argmin(medias)), medias


def hoja(rid, pres, vac, marcar_atipica=True):
    """Hoja de contacto de una posicion: presentes arriba, vacias abajo."""
    ncols = max(len(pres), len(vac), 1)
    ancho = MARGEN + ncols * (LADO + MARGEN)
    alto = 34 + 2 * (LADO + 26 + MARGEN) + 8
    img = np.full((alto, max(ancho, 320), 3), FONDO, np.uint8)

    cv2.putText(img, rid, (MARGEN, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (235, 235, 235), 2, cv2.LINE_AA)

    idx_at, medias = _atipica(pres) if marcar_atipica else (None, [])
    # si todas se parecen mucho entre si, no hay atipica que senalar
    if medias and (max(medias) - min(medias)) < 0.08:
        idx_at = None

    for fila, (muestras, color, etq) in enumerate(
            [(pres, VERDE, "CON TORNILLO"), (vac, ROJO, "SIN TORNILLO")]):
        y0 = 34 + fila * (LADO + 26 + MARGEN)
        cv2.putText(img, etq, (MARGEN, y0 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    color, 1, cv2.LINE_AA)
        for i, m in enumerate(muestras):
            x = MARGEN + i * (LADO + MARGEN)
            y = y0 + 22
            img[y:y + LADO, x:x + LADO] = _mini(m)
            es_at = (fila == 0 and idx_at is not None and i == idx_at)
            cv2.rectangle(img, (x - 1, y - 1), (x + LADO, y + LADO),
                          (60, 165, 235) if es_at else color, 2 if es_at else 1)
            txt = f"u{i + 1}"
            if es_at:
                txt += "  <-- la mas distinta"
            cv2.putText(img, txt, (x, y + LADO + 15), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (60, 165, 235) if es_at else GRIS, 1, cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser(description="Visualiza las plantillas de la calibracion")
    ap.add_argument("--receta", default=None)
    ap.add_argument("--roi", default=None, help="ver solo una posicion")
    ap.add_argument("--crudos", action="store_true",
                    help="usar los recortes de ROI completa (_pc/_ac) en vez de las plantillas")
    args = ap.parse_args()

    receta = _elegir(args.receta)
    d = os.path.join(DIR_RECETAS, receta)
    r_cfg = os.path.join(d, "config_rois.json")
    r_tpl = os.path.join(d, "plantillas.npz")
    for r in (r_cfg, r_tpl):
        if not os.path.exists(r):
            sys.exit(f"[ERROR] No existe {r}. Calibra el proceso primero.")

    cfg = json.load(open(r_cfg, encoding="utf-8"))
    claves = _claves(cfg)
    data = np.load(r_tpl)
    salida = os.path.join(d, "plantillas_vista")
    os.makedirs(salida, exist_ok=True)

    sufijos = ("_pc", "_ac") if args.crudos else ("_p", "_a")
    if args.crudos and (claves[0] + "_pc") not in data:
        sys.exit("[ERROR] Esta calibracion no guarda los recortes crudos.\n"
                 "        Se guardan desde la version nueva del inspector; recalibra\n"
                 "        o quita --crudos para ver las plantillas normales.")

    objetivo = [k for k in claves if (args.roi is None or k == args.roi
                                      or k.split("#")[0] == args.roi)]
    if not objetivo:
        sys.exit(f"[ERROR] No existe la posicion '{args.roi}'. "
                 f"Disponibles: {', '.join(claves)}")

    print(f"\n=== Plantillas de '{receta}' "
          f"({'recortes crudos' if args.crudos else 'plantillas'}) ===\n")
    print(f"{'ROI':10s} | {'con tornillo':13s} | {'sin tornillo':13s} | unidad mas distinta")
    print("-" * 68)

    filas_resumen = []
    for k in objetivo:
        pres = _muestras(data.get(k + sufijos[0]))
        vac = _muestras(data.get(k + sufijos[1]))
        if not pres and not vac:
            print(f"{k:10s} |  (sin datos)")
            continue
        h = hoja(k, pres, vac)
        cv2.imwrite(os.path.join(salida, f"{k.replace('#','_')}.png"), h)
        idx, medias = _atipica(pres)
        if medias and (max(medias) - min(medias)) >= 0.08:
            nota = f"u{idx + 1}  (parecido {medias[idx]:.2f} vs {max(medias):.2f})"
        else:
            nota = "todas parecidas"
        print(f"{k:10s} | {len(pres):^13d} | {len(vac):^13d} | {nota}")
        filas_resumen.append(h)

    if len(filas_resumen) > 1:
        ancho = max(f.shape[1] for f in filas_resumen)
        pegadas = []
        for f in filas_resumen:
            if f.shape[1] < ancho:
                pad = np.full((f.shape[0], ancho - f.shape[1], 3), FONDO, np.uint8)
                f = np.hstack([f, pad])
            pegadas.append(f)
            pegadas.append(np.full((3, ancho, 3), (60, 64, 70), np.uint8))
        cv2.imwrite(os.path.join(salida, "_resumen.png"), np.vstack(pegadas[:-1]))

    print(f"\n[OK] Imagenes en: {salida}/")
    print("     Una por posicion, mas _resumen.png con todas.")
    print("\nQue mirar: que todas las miniaturas 'con tornillo' muestren un tornillo")
    print("nitido y centrado, y que las 'sin tornillo' muestren el alojamiento vacio.")
    print("Si una esta borrosa, tapada o mal encuadrada, esa unidad esta hundiendo el")
    print("margen de esa posicion: recalibrala o agrega mas unidades buenas.\n")


if __name__ == "__main__":
    main()
