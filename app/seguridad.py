#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seguridad.py — Acceso al modulo de administracion.

POR QUE LA CLAVE NO ESTA EN EL CODIGO
La carpeta del programa se respalda y se copia entre MacBooks. Una clave escrita
aqui la ve cualquiera que abra el archivo con un editor, y ademas viaja en cada
copia y en cada respaldo. Asi que aqui solo vive un HASH, del que no se puede
volver a la clave, y el hash EN USO se guarda fuera del programa, en ~/.roi/, con
permisos 600 -el mismo sitio y el mismo trato que las credenciales de SQL.

LA SEMILLA
La primera vez que se abre no hay archivo todavia, asi que se siembra con la
clave inicial acordada. Tampoco esa esta en texto: van su sal y su hash,
calculados una sola vez. Entras con la clave de siempre, pero el texto no
aparece en ningun archivo.

Conviene cambiarla desde el panel en cuanto esto entre en planta: la clave
inicial la conoce todo el que haya visto como se monto el sistema.

POR QUE PBKDF2 Y NO UN SHA A SECAS
Un sha256 pelado de una clave corta se rompe probando palabras a millones por
segundo. PBKDF2 con 240.000 iteraciones hace que cada intento cueste, que es
justo lo que se necesita cuando el archivo del hash puede acabar en un respaldo.
Es de la libreria estandar: no anade ninguna dependencia a la flota.
"""

import hashlib
import hmac
import json
import os
import time

USUARIO = "admin"
ITERACIONES = 240000

# Semilla: sal y hash de la clave inicial. NO es la clave, y no se puede volver
# a ella desde aqui. Solo se usa mientras no exista el archivo de ~/.roi.
_SAL_INICIAL = "e1fdbfae6a4156bf60ad1b70d071b682"
_HASH_INICIAL = "c9412d53a125713ac40708f151b5e9bc28429fb53f929020a6e7318eeb33ee0d"

# Freno a la fuerza bruta. No pretende ser una defensa seria -esto es una Mac en
# una mesa de produccion, no un servidor expuesto- pero evita que alguien pruebe
# claves a mano sin limite mientras el operario no mira.
INTENTOS_MAX = 5
ESPERA_SEG = 30

_fallos = 0
_bloqueado_hasta = 0.0


def ruta():
    return os.path.expanduser("~/.roi/admin.json")


def _derivar(clave, sal_hex):
    return hashlib.pbkdf2_hmac(
        "sha256", clave.encode("utf-8"),
        bytes.fromhex(sal_hex), ITERACIONES).hex()


def _leer():
    """Devuelve (usuario, sal_hex, hash_hex). Cae a la semilla si no hay archivo."""
    try:
        with open(ruta(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("usuario", USUARIO), d["sal"], d["hash"])
    except Exception:
        return (USUARIO, _SAL_INICIAL, _HASH_INICIAL)


def _escribir(usuario, sal_hex, hash_hex):
    d = os.path.dirname(ruta())
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = ruta() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "usuario": usuario, "algoritmo": "pbkdf2_sha256",
                   "iteraciones": ITERACIONES, "sal": sal_hex, "hash": hash_hex,
                   "actualizado": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=2)
    os.chmod(tmp, 0o600)
    # replace y no un open directo: si algo falla a mitad de la escritura, el
    # archivo viejo sigue intacto y no te quedas sin poder entrar al panel.
    os.replace(tmp, ruta())


def sembrado():
    """True si ya existe el archivo propio (es decir, ya no se usa la semilla)."""
    return os.path.exists(ruta())


def bloqueado():
    """Segundos que faltan para poder reintentar, o 0."""
    return max(0, int(_bloqueado_hasta - time.time()))


def verificar(usuario, clave):
    """True si las credenciales valen. Cuenta los fallos y bloquea al quinto."""
    global _fallos, _bloqueado_hasta
    if bloqueado():
        return False
    u, sal, h = _leer()
    # compare_digest y no '==': una comparacion normal se corta en el primer
    # byte distinto y el tiempo que tarda filtra informacion del hash.
    ok = (str(usuario).strip() == u
          and hmac.compare_digest(_derivar(clave, sal), h))
    if ok:
        _fallos = 0
        if not sembrado():
            # Primer acceso: se pasa de la semilla a un archivo propio, con sal
            # nueva. A partir de aqui esta maquina tiene su propio hash.
            sal2 = os.urandom(16).hex()
            _escribir(u, sal2, _derivar(clave, sal2))
    else:
        _fallos += 1
        if _fallos >= INTENTOS_MAX:
            _fallos = 0
            _bloqueado_hasta = time.time() + ESPERA_SEG
    return ok


def cambiar(clave_actual, clave_nueva):
    """Cambia la clave. Devuelve (ok, mensaje)."""
    if not verificar(USUARIO, clave_actual):
        return (False, "La clave actual no es correcta.")
    n = (clave_nueva or "")
    if len(n) < 10:
        return (False, "La clave nueva tiene que tener al menos 10 caracteres.")
    if n == clave_actual:
        return (False, "La clave nueva es igual a la actual.")
    u, _, _ = _leer()
    sal = os.urandom(16).hex()
    _escribir(u, sal, _derivar(n, sal))
    return (True, f"Clave cambiada. Se guardo en {ruta()} (solo tu usuario puede leerlo).")
