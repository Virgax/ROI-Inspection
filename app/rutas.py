#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rutas.py — Donde vive cada cosa cuando el sistema corre como aplicacion.

EL PROBLEMA QUE RESUELVE
Dentro de un .app de macOS, el codigo va en un paquete que se REEMPLAZA entero
al actualizar. Si las recetas, las calibraciones o el banco vivieran ahi dentro,
mandar una version nueva significaria borrar la calibracion de ese banco de
trabajo, que cuesta media hora de planta y cinco unidades. Y encima el contenido
del paquete es de solo lectura en muchas instalaciones.

Asi que los DATOS viven fuera, en la carpeta del usuario, y el paquete solo lleva
codigo. Actualizar es arrastrar el .app encima; las calibraciones ni se enteran.

    ~/Documents/ROI/
    ├── procesos/              recetas: config, plantillas, umbrales, referencia
    ├── banco_evidencia/       una pieza por foto + fichas.jsonl
    ├── dataset/               capturas manuales (tecla C)
    ├── inspecciones_log.csv   log de inspecciones
    └── conexion.json          credenciales de SQL (permisos 600)

Se eligio ~/Documents y no ~/Library/Application Support -que es la convencion de
macOS- porque estas carpetas no son estado interno de una app: son el activo del
banco de trabajo. Alguien las va a querer respaldar, copiar a otra Mac o mirar
desde el Finder, y Application Support esta oculta.

CORRIENDO DESDE EL CODIGO FUENTE
Si no esta empaquetado, se respeta la carpeta actual para no romper la forma de
trabajar de hoy: el mismo codigo sirve para las dos cosas sin ramas raras.
"""

import os
import shutil
import sys

NOMBRE = "ROI"


def empaquetado():
    """True si corremos dentro de un .app construido con PyInstaller."""
    return getattr(sys, "frozen", False)


def raiz_datos():
    """Carpeta de datos. Variable de entorno > empaquetado > carpeta actual."""
    forzada = os.environ.get("ROI_DATOS")
    if forzada:
        return os.path.abspath(os.path.expanduser(forzada))
    if empaquetado():
        return os.path.expanduser(os.path.join("~", "Documents", NOMBRE))
    return os.path.abspath(".")


def ruta(*partes):
    return os.path.join(raiz_datos(), *partes)


def dir_procesos():
    return ruta("procesos")


def dir_banco():
    return ruta("banco_evidencia")


def dir_dataset():
    return ruta("dataset")


def archivo_log():
    return ruta("inspecciones_log.csv")


def archivo_conexion():
    """Credenciales de SQL.

    Se mira primero ~/.roi/conexion.json, que es donde las dejo el modo terminal,
    para que quien ya lo tenia configurado no tenga que repetirlo."""
    heredada = os.path.expanduser("~/.roi/conexion.json")
    if os.path.exists(heredada):
        return heredada
    return ruta("conexion.json")


def preparar(verbose=True):
    """Crea la estructura si falta. Devuelve la raiz."""
    r = raiz_datos()
    for d in (r, dir_procesos(), dir_banco(), dir_dataset()):
        os.makedirs(d, exist_ok=True)
    if verbose:
        print(f"[rutas] Datos en {r}")
    return r


def hay_recetas():
    d = dir_procesos()
    if not os.path.isdir(d):
        return []
    # Se saltan las que empiezan por "." o "_": la calibracion guarda ahi sus
    # carpetas de trabajo (_reemplazadas_...), y ofrecerlas como si fueran un
    # proceso invita a arrancar produccion contra plantillas viejas.
    return sorted(x for x in os.listdir(d)
                  if os.path.isdir(os.path.join(d, x))
                  and not x.startswith(".") and not x.startswith("_"))


def importar_desde(origen, verbose=True):
    """Copia recetas, log y dataset de una instalacion previa a la carpeta de datos.

    Para la primera vez que se abre la app en una Mac que ya venia trabajando con
    el modo terminal. NO sobrescribe lo que ya exista: una receta ya presente en
    el destino se respeta, porque puede estar recien calibrada en esta estacion y
    perderla seria mucho peor que no importar nada."""
    origen = os.path.abspath(os.path.expanduser(origen))
    if not os.path.isdir(origen):
        raise FileNotFoundError(origen)
    preparar(verbose=False)
    copiadas, saltadas = [], []
    orig_proc = os.path.join(origen, "procesos")
    if os.path.isdir(orig_proc):
        for receta in sorted(os.listdir(orig_proc)):
            o = os.path.join(orig_proc, receta)
            d = os.path.join(dir_procesos(), receta)
            if not os.path.isdir(o) or receta.startswith("."):
                continue
            if os.path.exists(d):
                saltadas.append(receta)
                continue
            shutil.copytree(o, d)
            copiadas.append(receta)
    log_o = os.path.join(origen, "inspecciones_log.csv")
    if os.path.exists(log_o) and not os.path.exists(archivo_log()):
        shutil.copy2(log_o, archivo_log())
        copiadas.append("inspecciones_log.csv")
    if verbose:
        print(f"[rutas] Importadas: {copiadas or 'ninguna'}")
        if saltadas:
            print(f"[rutas] Ya existian, NO se tocaron: {saltadas}")
    return copiadas, saltadas
