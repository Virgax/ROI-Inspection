#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
motor.py — Puente entre la aplicacion y el codigo de inspeccion que ya existe.

POR QUE UN PUENTE Y NO REESCRIBIR
`inspector.py` lleva meses en produccion y contiene decisiones que costaron
encontrar: el rescate del Juez 2, el piso de similitud, la huella de geometria,
el manejo de zonas. Reescribir eso para la interfaz seria tirar todo ese
aprendizaje y empezar a acumular diferencias entre lo que juzga la app y lo que
juzga el modo terminal.

Asi que la app NO reimplementa nada: localiza el motor, lo importa y llama a sus
funciones. El unico codigo nuevo es el que dibuja y el que ordena la secuencia.

DONDE ESTA EL MOTOR
Hoy el codigo esta duplicado dentro de cada carpeta de modelo. Se busca en este
orden: la variable ROI_MOTOR, una carpeta `motor/` junto a la app, el propio
paquete cuando esta empaquetada, y por ultimo las carpetas hermanas. Cuando el
codigo pase a vivir una sola vez, esto sigue funcionando sin cambios.
"""

import os
import sys
import glob

_cargado = None

# Carpeta de este archivo, resuelta EN EL IMPORT. Tiene que ser aqui y no dentro
# de las funciones: `preparar_entorno` cambia el directorio de trabajo, y si la
# ruta se resolviera despues, un `__file__` relativo (que es lo que da Python 3.9
# cuando se lanza el script por una ruta relativa) apuntaria al sitio equivocado
# y el localizador no encontraria el motor.
_AQUI = os.path.dirname(os.path.abspath(__file__))


# Modulos que tiene que tener una carpeta para poder actuar de motor. No basta
# con inspector.py: una carpeta incompleta arranca y falla mas tarde, en medio de
# una inspeccion, que es el peor momento para enterarse.
MODULOS = ("inspector.py", "veredicto.py", "banco.py", "cierre.py", "roi_qr.py")

# Nombres que NUNCA deben actuar de motor aunque tengan todos los modulos. Un
# respaldo es codigo viejo a proposito; arrancar desde el significaria operar con
# una version anterior creyendo que es la actual, sin ninguna senal. Paso de
# verdad al probar esto: el localizador eligio el respaldo por orden alfabetico.
EXCLUIDOS = ("respaldo", "backup", "copia", "old", "viejo", "_bak")


def _completa(d):
    return all(os.path.exists(os.path.join(d, m)) for m in MODULOS)


def _excluida(d):
    n = os.path.basename(d).lower()
    return any(x in n for x in EXCLUIDOS)


def _candidatos():
    aqui = _AQUI
    forzado = os.environ.get("ROI_MOTOR")
    if forzado:
        yield os.path.abspath(os.path.expanduser(forzado))
    # Empaquetado: PyInstaller desempaqueta los datos en sys._MEIPASS, que NO
    # tiene por que coincidir con la carpeta de este archivo dentro del paquete.
    # Se mira primero y explicitamente, en vez de confiar en que coincidan: si
    # no coincidieran, la app abriria bien y solo fallaria al empezar a
    # inspeccionar, ya instalada en otra Mac.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        yield os.path.join(base, "motor")
    yield os.path.join(aqui, "motor")
    yield aqui
    # Carpetas hermanas. Se ordenan por fecha de modificacion, la mas reciente
    # primero: si hay varias validas, la que se ha tocado ultimo es la que se
    # esta usando. El orden alfabetico ponia el respaldo por delante.
    padre = os.path.dirname(aqui)
    hermanas = [d for d in glob.glob(os.path.join(padre, "*")) if os.path.isdir(d)]
    for d in sorted(hermanas, key=lambda x: os.path.getmtime(x), reverse=True):
        yield d


def localizar():
    """Devuelve la carpeta del motor, o lanza explicando que se descarto."""
    descartadas = []
    for c in _candidatos():
        if not c or not os.path.isdir(c):
            continue
        if _excluida(c):
            descartadas.append(f"{os.path.basename(c)} (parece un respaldo)")
            continue
        if not _completa(c):
            faltan = [m for m in MODULOS
                      if not os.path.exists(os.path.join(c, m))]
            if os.path.exists(os.path.join(c, "inspector.py")):
                descartadas.append(f"{os.path.basename(c)} (le falta {', '.join(faltan)})")
            continue
        return c
    detalle = ("  Descartadas:\n    - " + "\n    - ".join(descartadas)) if descartadas else ""
    raise RuntimeError(
        "No se encontro una carpeta con el motor de inspeccion completo.\n"
        + detalle +
        "\n  Defina ROI_MOTOR con la carpeta que lo contiene.")


def cargar():
    """Importa el motor una sola vez y devuelve sus modulos."""
    global _cargado
    if _cargado is not None:
        return _cargado
    carpeta = localizar()
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    import inspector, veredicto, banco, cierre, roi_qr
    _cargado = {"carpeta": carpeta, "inspector": inspector,
                "veredicto": veredicto, "banco": banco,
                "cierre": cierre, "roi_qr": roi_qr}
    return _cargado


def preparar_entorno(raiz_datos):
    """Hace que el motor trabaje sobre la carpeta de datos, no sobre la suya.

    `inspector.py` usa rutas relativas ("procesos", "inspecciones_log.csv")
    resueltas contra el directorio de trabajo. En vez de tocar esas constantes
    una por una -y arriesgarse a olvidar alguna- se cambia el directorio de
    trabajo del proceso. Es una linea, no deja rastro en el motor, y cualquier
    ruta relativa que se anada manana cae sola en el sitio correcto."""
    os.makedirs(raiz_datos, exist_ok=True)
    os.chdir(raiz_datos)
    return raiz_datos
