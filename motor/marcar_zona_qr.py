#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marcar_zona_qr.py — Define en que parte del rectificado esta el QR del giftcard.

POR QUE HACE FALTA ACOTAR LA ZONA
Dos motivos, y el segundo es el importante:

1. Velocidad y acierto. Buscar el simbolo en una zona de 300x300 px tarda unos
   8 ms; buscarlo en el rectificado completo de 3200x1659 tarda unos 180 ms, y
   ademas FALLA con simbolos pequenos que en la zona acotada se leen sin problema.

2. Para no leer el codigo equivocado. El propio housing lleva impreso su serial en
   codigo (formato de 18 caracteres, el mismo del PUID). Al buscar en la imagen
   completa el detector puede devolver ESE codigo en lugar del giftcard, y la
   estacion mandaria a la base de datos un identificador que no es el GCN. En este
   punto del ensamble el codigo del housing suele quedar tapado -por eso se usa el
   giftcard-, pero "suele" no es una garantia sobre la que construir.

La zona se guarda en el config_rois.json de la receta, junto a las ROIs, porque es
una propiedad de la geometria de ESE proceso: cambia con la receta, igual que las
posiciones de los tornillos.

USO
    python3 marcar_zona_qr.py --receta "Ensamble 2"
        Marca la zona del GIFTCARD (etapa housing). Se arrastra un recuadro,
        ENTER confirma, ESC cancela. Deja margen generoso: la pieza se mueve
        entre unidades y el giftcard no cae siempre exactamente igual.

    python3 marcar_zona_qr.py --receta "Ensamble 2" --etapa lcd
        Marca la zona del QR del DISPLAY (etapa lcd).

    python3 marcar_zona_qr.py --receta "Ensamble 2" --probar foto.png [--etapa lcd]
        Lee el QR en la zona guardada y dice que serial saca.

DOS ETAPAS, UNA SOLA PLANTILLA POR MODELO
El cierre tiene dos momentos y cada uno mira una zona distinta del mismo lienzo
rectificado: primero el housing con su giftcard, despues el display con su QR.
Los dos comparten camara, tapete y homografia, asi que las coordenadas son
comparables y no hace falta calibrar nada nuevo.

Se guardan en el MISMO config_rois.json de la receta:

    zona_qr                 -> giftcard        (etapa housing)
    etapa_lcd.zona_qr       -> QR del display  (etapa lcd)

Una sola plantilla por modelo con dos secciones, y no dos recetas separadas,
porque las dos etapas son la misma unidad: partirlas obligaria al operario a
cambiar de receta a mitad de pieza y dejaria la relacion entre las dos fotos
fuera del config. La seccion del LCD es mucho mas ligera que la del housing: no
lleva ROIs de tornillos, ni plantillas, ni umbrales. Solo la zona del QR, porque
en esa etapa no se inspecciona nada, se captura evidencia.

Un config sin `etapa_lcd` sigue funcionando igual que antes.
"""

import argparse
import json
import os
import sys

import cv2

import roi_qr

DIR_RECETAS = "procesos"


CLAVES_ETAPA = {"housing": None, "lcd": "etapa_lcd"}


def _leer_zona(cfg, etapa):
    """La etapa housing guarda la zona en la raiz del config y la etapa lcd bajo
    'etapa_lcd'. Asi un config antiguo, que solo tiene 'zona_qr', sigue siendo
    valido sin convertirlo."""
    if CLAVES_ETAPA[etapa] is None:
        return cfg.get("zona_qr")
    return (cfg.get(CLAVES_ETAPA[etapa]) or {}).get("zona_qr")


def _escribir_zona(cfg, etapa, zona):
    clave = CLAVES_ETAPA[etapa]
    if clave is None:
        cfg["zona_qr"] = zona
    else:
        cfg.setdefault(clave, {})["zona_qr"] = zona


def _rutas(receta):
    d = os.path.join(DIR_RECETAS, receta)
    return os.path.join(d, "config_rois.json"), os.path.join(d, "referencia.png")


def _recetas():
    if not os.path.isdir(DIR_RECETAS):
        return []
    return sorted(r for r in os.listdir(DIR_RECETAS)
                  if os.path.isdir(os.path.join(DIR_RECETAS, r)))


def elegir_receta(arg):
    disponibles = _recetas()
    if arg:
        if arg not in disponibles:
            sys.exit(f"[ERROR] No existe la receta '{arg}'. Hay: {', '.join(disponibles)}")
        return arg
    if len(disponibles) == 1:
        return disponibles[0]
    if not disponibles:
        sys.exit(f"[ERROR] No hay recetas en {DIR_RECETAS}/")
    print("Procesos:")
    for i, r in enumerate(disponibles, 1):
        print(f"  {i}) {r}")
    sel = input("Numero: ").strip()
    if not sel.isdigit() or not 1 <= int(sel) <= len(disponibles):
        sys.exit("[i] Cancelado.")
    return disponibles[int(sel) - 1]


def marcar(receta, etapa="housing", ruta_imagen=None):
    ruta_cfg, ruta_ref = _rutas(receta)
    # La etapa del display no se puede marcar sobre la referencia, que es una foto
    # del HOUSING: ahi no hay ningun display y no se ve donde cae su codigo. Con
    # --imagen se marca sobre una captura hecha con la tecla C del inspector, que
    # es el mismo lienzo rectificado y por tanto las mismas coordenadas.
    origen = ruta_imagen or ruta_ref
    if not os.path.exists(origen):
        sys.exit(f"[ERROR] Falta {origen}."
                 + ("" if ruta_imagen else " Corre --capturar-referencia primero."))
    img = cv2.imread(origen)
    if img is None:
        sys.exit(f"[ERROR] No se pudo leer {origen}")
    print(f"[i] Marcando sobre {origen}")
    alto, ancho = img.shape[:2]

    # La referencia no cabe en pantalla: se muestra reducida y las coordenadas se
    # devuelven a la escala real al guardarlas.
    esc = min(1.0, 1500.0 / ancho)
    vista = cv2.resize(img, None, fx=esc, fy=esc, interpolation=cv2.INTER_AREA) \
        if esc < 1.0 else img.copy()

    with open(ruta_cfg, encoding="utf-8") as f:
        cfg = json.load(f)
    previa = _leer_zona(cfg, etapa)
    if previa:
        x0, y0, x1, y1 = (int(v * esc) for v in previa)
        cv2.rectangle(vista, (x0, y0), (x1, y1), (46, 189, 255), 2)
        print(f"[i] Zona actual: {previa} (en ambar)")

    objetivo = "GIFTCARD" if etapa == "housing" else "QR del DISPLAY"
    print(f"[i] Arrastra un recuadro sobre el {objetivo}. ENTER confirma, ESC cancela.")
    print("    Deja margen: el giftcard no cae siempre en el mismo sitio exacto.")
    r = cv2.selectROI(f"Zona del QR [{etapa}] - {receta}", vista,
                      showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if r[2] == 0 or r[3] == 0:
        print("[i] Cancelado. No se guardo nada.")
        return

    x0, y0 = int(r[0] / esc), int(r[1] / esc)
    x1, y1 = int((r[0] + r[2]) / esc), int((r[1] + r[3]) / esc)

    # La zona se GUARDA en coordenadas del lienzo del config, que es el unico
    # sistema en el que trabaja el inspector: `_procesar_frame` rectifica siempre
    # a cfg["image_size"], venga la camara como venga.
    #
    # La imagen sobre la que se marca puede estar en OTRA escala. `referencia.png`
    # la escribe --capturar-referencia con el tamano NATURAL que miden los
    # marcadores en ese momento, y ese tamano cambia si se mueve la camara: el
    # 18-sep dio 3200x1600 donde el config decia 3200x1659, un 3,7% en vertical.
    # Marcar ahi y guardar tal cual desplazaria la zona hasta 55 px en la parte
    # baja, mas que el ancho de una ROI. Las capturas de la tecla C no tienen el
    # problema (ya salen en el lienzo del config), pero eso no se puede dar por
    # hecho segun de donde venga el archivo.
    cw, ch = (cfg.get("image_size") or [ancho, alto])[:2]
    if (int(cw), int(ch)) != (ancho, alto):
        fx, fy = cw / float(ancho), ch / float(alto)
        print(f"[!] La imagen mide {ancho}x{alto} y el lienzo del proceso es "
              f"{int(cw)}x{int(ch)}. La zona se reescala (x{fx:.4f}, x{fy:.4f}) "
              f"para guardarla en coordenadas del lienzo.")
        x0, y0 = int(round(x0 * fx)), int(round(y0 * fy))
        x1, y1 = int(round(x1 * fx)), int(round(y1 * fy))
        ancho, alto = int(cw), int(ch)

    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(ancho, x1), min(alto, y1)
    zona = [x0, y0, x1, y1]

    # Comprobar de una vez si ahi dentro hay un QR legible. Si la referencia se
    # capturo sin giftcard no habra nada, y eso no es un error: solo se avisa.
    # La lectura va sobre la imagen ORIGINAL, asi que si la zona se reescalo hay
    # que devolverla a la escala de esa imagen para mirar donde toca.
    zona_en_img = zona
    if (img.shape[1], img.shape[0]) != (int(cw), int(ch)):
        gx, gy = img.shape[1] / float(cw), img.shape[0] / float(ch)
        zona_en_img = [int(zona[0] * gx), int(zona[1] * gy),
                       int(zona[2] * gx), int(zona[3] * gy)]
    codigos = roi_qr.leer(img, zona_en_img)
    if codigos:
        gcn = roi_qr.gcn_de_giftcard(codigos[0])
        print(f"[OK] En esa zona se lee: {codigos[0]!r}")
        print(f"     GCN extraido: {gcn}" if gcn
              else "     [!] No parece un giftcard: no se reconocio un GCN dentro.")
    else:
        print("[i] No se detecto ningun QR en la zona (normal si la referencia se "
              "capturo sin giftcard). Comprueba con --probar sobre una foto real.")

    _escribir_zona(cfg, etapa, zona)
    with open(ruta_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"[OK] Zona {zona} guardada en {ruta_cfg}")
    print(f"     ({x1-x0}x{y1-y0} px de {ancho}x{alto})")


def probar(receta, ruta_foto, etapa="housing"):
    ruta_cfg, _ = _rutas(receta)
    with open(ruta_cfg, encoding="utf-8") as f:
        cfg = json.load(f)
    zona = _leer_zona(cfg, etapa)
    if not zona:
        sys.exit(f"[ERROR] Esta receta no tiene zona de QR para la etapa "
                 f"'{etapa}'. Marcala primero.")
    img = cv2.imread(ruta_foto)
    if img is None:
        sys.exit(f"[ERROR] No se pudo leer {ruta_foto}")
    print(f"  zona   : {zona}")
    if etapa == "lcd":
        # En esta etapa el QR es el serial del display, NO un GCN: no se le
        # aplica el parseo del giftcard ni se valida contra el patron de GCN.
        codigos = roi_qr.leer(img, zona)
        print(f"  leido  : {codigos[0]!r}" if codigos else "  leido  : nada")
        print(f"  serial : {codigos[0].strip().upper()}" if codigos
              else "  serial : no se detecto codigo")
    else:
        gcn, texto = roi_qr.gcn_en(img, zona)
        print(f"  leido  : {texto!r}")
        print(f"  GCN    : {gcn}" if gcn else "  GCN    : no se reconocio")


def main():
    ap = argparse.ArgumentParser(description="Marca la zona del QR del giftcard")
    ap.add_argument("--receta", default=None)
    ap.add_argument("--etapa", choices=sorted(CLAVES_ETAPA), default="housing",
                    help="housing = giftcard (default) | lcd = QR del display")
    ap.add_argument("--imagen", metavar="FOTO", default=None,
                    help="marcar sobre esta foto en vez de la referencia "
                         "(necesario para la etapa lcd: usa una captura hecha "
                         "con la tecla C con el display puesto)")
    ap.add_argument("--probar", metavar="FOTO", default=None,
                    help="prueba la zona guardada sobre una foto rectificada")
    args = ap.parse_args()
    receta = elegir_receta(args.receta)
    if args.probar:
        probar(receta, args.probar, args.etapa)
    else:
        marcar(receta, args.etapa, args.imagen)


if __name__ == "__main__":
    main()
