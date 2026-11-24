#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnostico_juez.py — Valida y AFINA el Juez 2 (simetria de 4 de la cruz Phillips)
sobre tu calibracion ya guardada, SIN tomar fotos nuevas.

IMPORTANTE — mide sobre los RECORTES CRUDOS (claves _pc/_ac), que son exactamente
la misma imagen que el inspector le pasa al juez en produccion (la ROI completa).
Antes leia las PLANTILLAS (_p/_a), que son el 70% central ya recortado: como
cruzness recorre circulos alrededor del centro, el encuadre cambia el valor hasta
un 15%, asi que los umbrales que salian de aqui no eran validos en vivo (p.ej. una
posicion daba 0.20 en el diagnostico y 0.12 en produccion). Las calibraciones
anteriores a esta version no guardan los recortes crudos: hay que recalibrar una
vez para poder usar el diagnostico.

Corre el juez contra los bancos guardados en calibracion (tornillos PRESENTES y
huecos VACIOS reales) y reporta, por posicion Phillips:
  cruzness = fuerza de la simetria de 4 (los brazos de la cruz)  [CRITERIO PRINCIPAL]
  centro   = energia de borde en el centro                        [guardia de respaldo]
Un tornillo da cruzness alto; un hueco, bajo. Propone UMBRAL_CRUZNESS y PISO_CENTRO
con TUS datos y guarda montajes para que veas lo que ve el juez.

Uso:
    python3 diagnostico_juez.py                    # menu de procesos
    python3 diagnostico_juez.py --receta "Ensamble 2"
"""

import argparse, json, os, sys
import cv2, numpy as np
import juez_cruz as J

DIR_RECETAS = "procesos"


def _asignar_claves(cfg):
    """Misma logica de clave interna que inspector.py: la etiqueta si es unica, o
    la etiqueta con sufijo del uid si esta repetida. Debe coincidir con el inspector
    para leer las claves correctas del .npz."""
    conteo = {}
    for p in cfg["puntos"]:
        conteo[p.get("id")] = conteo.get(p.get("id"), 0) + 1
    for i, p in enumerate(cfg["puntos"]):
        rid = p.get("id")
        if conteo.get(rid, 0) > 1:
            uid = str(p.get("uid") or "")
            p["_clave"] = f"{rid}#{uid[-4:] if len(uid) >= 4 else i:02d}" \
                if len(uid) >= 4 else f"{rid}#{i:02d}"
        else:
            p["_clave"] = rid


def clave(p):
    return p.get("_clave") or p["id"]


def _rutas(r):
    d = os.path.join(DIR_RECETAS, r)
    return (os.path.join(d, "config_rois.json"), os.path.join(d, "umbrales.json"),
            os.path.join(d, "plantillas.npz"), os.path.join(d, "diagnostico_juez"))


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
        print(f"[i] Proceso: {rs[0]}"); return rs[0]
    print("Procesos disponibles:")
    for i, r in enumerate(rs, 1):
        print(f"  {i}) {r}")
    sel = input("Numero: ").strip()
    if sel.isdigit() and 1 <= int(sel) <= len(rs):
        return rs[int(sel) - 1]
    sys.exit("Opcion invalida.")


def _muestras(banco):
    if banco is None:
        return []
    if banco.ndim == 2:
        return [banco.astype(np.uint8)]
    return [banco[i].astype(np.uint8) for i in range(banco.shape[0])]


def _montaje(tpl, tor, hue, lado=120):
    def prep(img):
        return cv2.resize(J._gris(img).astype(np.uint8), (lado, lado), interpolation=cv2.INTER_AREA)
    def edges(img):
        b = J._bordes(img, lado)
        return cv2.normalize(b, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    cols = []
    for titulo, img in [("plantilla", tpl), ("tornillo", tor), ("hueco", hue)]:
        if img is None:
            g = e = np.zeros((lado, lado), np.uint8)
        else:
            g, e = prep(img), edges(img)
        col = cv2.cvtColor(np.vstack([g, e]), cv2.COLOR_GRAY2BGR)
        cv2.putText(col, titulo, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 220, 90), 1, cv2.LINE_AA)
        cols += [col, np.full((col.shape[0], 3, 3), 40, np.uint8)]
    return np.hstack(cols[:-1])


def _recomendar(czt, czh, ct, ch):
    """Escanea UMBRAL_CRUZNESS y PISO_CENTRO.

    Los dos errores NO cuestan igual desde que el juez es SOLO-RESCATE:
      - falso rescate  = un hueco pasa como presente -> un tornillo faltante se
                         escapa. GRAVE.
      - falso rechazo  = el juez no rescata un tornillo -> decide el clasificador,
                         exactamente igual que si el juez no existiera. INOCUO.
    Por eso se busca primero una combinacion con CERO falsos rescates, y entre
    esas la que menos tornillos deje sin rescatar. Solo si ninguna lo logra se
    permite el minimo de falsos rescates posible."""
    czt, czh = np.array(czt), np.array(czh)
    ct, ch = np.array(ct), np.array(ch)
    if len(czt) == 0 or len(czh) == 0:
        return None
    cmax = float(max(ct.max(), ch.max()))
    candidatos = []
    for u in np.arange(0.0, 0.45, 0.005):
        for pc in np.arange(0.0, cmax * 1.05 + 1, 5.0):
            pt = (czt >= u) & (ct >= pc)
            ph = (czh >= u) & (ch >= pc)
            fr = int(ph.sum()); frj = int((~pt).sum())
            candidatos.append((fr, frj, round(float(u), 3), round(float(pc), 1)))
    # orden: primero minimo de falsos rescates, luego minimo de falsos rechazos
    fr, frj, u, pc = min(candidatos)
    return (0, fr, frj, u, pc)


def main():
    ap = argparse.ArgumentParser(description="Diagnostico/afinado del Juez 2 (simetria de 4)")
    ap.add_argument("--receta", default=None)
    args = ap.parse_args()
    receta = _elegir(args.receta)
    r_cfg, r_umb, r_tpl, r_out = _rutas(receta)
    for r in (r_cfg, r_tpl):
        if not os.path.exists(r):
            sys.exit(f"[ERROR] No existe {r}. Calibra el proceso primero.")

    cfg = json.load(open(r_cfg, encoding="utf-8"))
    _asignar_claves(cfg)
    umbrales = json.load(open(r_umb, encoding="utf-8")) if os.path.exists(r_umb) else {"por_roi": {}}
    data = np.load(r_tpl)
    por_roi = umbrales.get("por_roi", {})
    os.makedirs(r_out, exist_ok=True)

    def es_phillips(p):
        return bool(p.get("phillips") or por_roi.get(clave(p), {}).get("phillips"))

    phillips = [p for p in cfg["puntos"] if es_phillips(p)]
    if not phillips:
        sys.exit("[i] Esta receta no tiene posiciones Phillips. El Juez 2 no aplica.")

    # verificar que la calibracion tenga los RECORTES CRUDOS (_pc/_ac). Son los
    # que ve el juez en produccion; medir sobre las plantillas daria umbrales
    # sesgados que no aplican en vivo.
    faltan_crudos = [clave(p) for p in phillips
                     if (clave(p) + "_pc") not in data or (clave(p) + "_ac") not in data]
    if faltan_crudos:
        sys.exit("[ERROR] Esta calibracion no guarda los recortes crudos de la ROI\n"
                 "        (se guardan desde la version nueva del inspector).\n"
                 "        Sin ellos el diagnostico mediria sobre las plantillas (70%\n"
                 "        central), que NO es lo que ve el juez en produccion, y los\n"
                 "        umbrales saldrian sesgados.\n\n"
                 "        Recalibra una vez con:  python3 inspector.py --calibrar\n"
                 f"        (posiciones afectadas: {', '.join(faltan_crudos[:6])}"
                 f"{'...' if len(faltan_crudos) > 6 else ''})")

    print(f"\n=== Diagnostico Juez 2 (simetria de 4) — proceso '{receta}' ===")
    print(f"Posiciones Phillips: {len(phillips)}")
    print("Midiendo sobre los recortes crudos de la ROI (igual que en produccion)\n")
    print(f"{'ROI':7s} | {'TORNILLOS cruzness(min/med)':28s} | {'HUECOS cruzness(med/max)':25s} "
          f"| {'centro T/H':13s} | separacion")
    print("-" * 100)

    CZT, CZH, CT, CH = [], [], [], []
    sin_sep = []
    daninas = []

    for p in phillips:
        rid = clave(p)
        tpl = data[rid + "_cruz"] if (rid + "_cruz") in data else None
        # RECORTES CRUDOS: misma imagen que recibe el juez en produccion
        tors = _muestras(data.get(rid + "_pc"))
        hues = _muestras(data.get(rid + "_ac"))
        mt = [J.evaluar(c, tpl) for c in tors]
        mh = [J.evaluar(c, tpl) for c in hues]
        czt = [x["cruzness"] for x in mt]; ct = [x["centro"] for x in mt]
        czh = [x["cruzness"] for x in mh]; ch = [x["centro"] for x in mh]
        CZT += czt; CZH += czh; CT += ct; CH += ch

        czt_min, czt_med = (min(czt), np.mean(czt)) if czt else (0, 0)
        czh_med, czh_max = (np.mean(czh), max(czh)) if czh else (0, 0)
        ctm = np.mean(ct) if ct else 0
        chm = np.mean(ch) if ch else 0
        sep = czt_min - czh_max
        flag = ""
        if sep < 0:
            flag = "  <-- EL JUEZ NO SIRVE AQUI (huecos puntuan mas que tornillos)"
            daninas.append(rid)
        elif sep <= 0.02:
            flag = "  <-- estrecho (revisar encuadre/luz de esta posicion)"
            sin_sep.append(rid)
        print(f"{rid:7s} | {czt_min:.3f} / {czt_med:.3f}{'':14s} | {czh_med:.3f} / {czh_max:.3f}{'':13s} "
              f"| {ctm:4.0f}/{chm:4.0f}{'':2s} | {sep:+.3f}{flag}")
        try:
            cv2.imwrite(os.path.join(r_out, f"{rid}.png"),
                        _montaje(tpl, tors[0] if tors else None, hues[0] if hues else None))
        except Exception:
            pass

    print("\n--- Recomendacion (con TUS datos) ---")
    rec = _recomendar(CZT, CZH, CT, CH)
    if rec:
        _, fr, frj, u, pc = rec
        print(f"UMBRAL_CRUZNESS = {u}")
        print(f"PISO_CENTRO     = {pc}")
        print("Sobre las muestras de calibracion con esos valores:")
        print(f"  falsos rescates (hueco rescatado, GRAVE): {fr}")
        print(f"  falsos rechazos (no rescata, inocuo):     {frj}")
        if fr == 0:
            print("  -> ningun hueco se cuela. Los rechazos no hacen dano: en esos")
            print("     casos simplemente decide el clasificador, como sin juez.")
        else:
            print("  [!] Aun con los umbrales mas exigentes se cuelan huecos. Eso pasa")
            print("      cuando alguna posicion tiene huecos que puntuan como tornillos;")
            print("      revisa la lista de posiciones donde el juez no sirve.")
    else:
        print("No hay muestras suficientes para recomendar.")

    if daninas:
        print(f"\n[!!] El juez NO SIRVE en: {', '.join(daninas)}")
        print("     Ahi los huecos dan mas simetria de cruz que los tornillos, asi que")
        print("     el juez rescataria huecos y no rescataria tornillos. Conviene")
        print("     DESMARCAR 'Phillips' en esas posiciones en el calibrador para que")
        print("     el juez no las toque; el clasificador las resuelve solo.")
    if sin_sep:
        print(f"\n[!] Posiciones con separacion estrecha: {', '.join(sin_sep)}")
    print(f"\n[OK] Montajes en: {r_out}/  (arriba grises, abajo bordes)")
    print("     Pega esta tabla para afinar juntos.\n")


if __name__ == "__main__":
    main()
