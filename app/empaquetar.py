#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
empaquetar.py — Construye «Inspeccion ROI.app» para esta Mac.

COMO SE USA
    cd ~/Desktop/ROI/app
    python3 empaquetar.py

Deja la aplicacion en app/dist/. Se arrastra a /Applications o a la otra Mac y
se abre con doble clic.

UNA ARQUITECTURA POR CONSTRUCCION
PyInstaller no cruza arquitecturas: lo que construyes en un Mac con Apple
Silicon corre en Apple Silicon, y lo de un Intel corre en Intel (y tambien en
Apple Silicon, por Rosetta, mas lento). Con una flota mezclada, lo honesto es
correr esto una vez en cada tipo de maquina. El script dice al final para cual
quedo.

EL MOTOR VA DENTRO, LOS DATOS FUERA
Dentro del paquete van el codigo de la app y el del motor. Fuera, en
~/Documents/ROI, van las recetas, el log y el banco. Esa separacion es
deliberada: actualizar es arrastrar el .app nuevo encima, y las calibraciones
-que cuestan media hora de planta y varias unidades- ni se enteran. Si vivieran
dentro del paquete, cada actualizacion las borraria.

QUE NO VA DENTRO
El modelo de contaminacion (base82_prom.npz, 28 MB, y modelo_v2.pt) y
ultralytics se quedan fuera a proposito: la deteccion viene apagada, arrastrar
torch multiplicaria el tamano por diez, y esos archivos dependen del modelo de
telefono, asi que su sitio es la carpeta de la receta, no el programa.
"""

import os
import platform
import plistlib
import shutil
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
NOMBRE = "Inspeccion ROI"

# Lo que el motor importa y PyInstaller no puede ver: el motor viaja como
# archivos .py sueltos -se importan en tiempo de ejecucion- asi que el analisis
# estatico no llega a sus imports. Sin esto, la app arranca y revienta al
# empezar a inspeccionar, que es el peor momento para enterarse.
OCULTOS = ["cv2", "numpy", "pymssql", "zxingcpp"]

# Fuera a proposito: pesan cientisimo y hoy no se usan.
EXCLUIDOS = ["ultralytics", "torch", "torchvision", "matplotlib",
             "tkinter", "PyQt5", "PyQt6"]


def paso(t):
    print(f"\n=== {t}")


def falta(mod):
    try:
        __import__(mod)
        return False
    except ImportError:
        return True


def main():
    arq = platform.machine()
    paso(f"Arquitectura: {arq}  ·  macOS {platform.mac_ver()[0]}  ·  "
         f"Python {platform.python_version()}")

    # El aviso va ANTES de gastar cinco minutos construyendo, no despues.
    #
    # Rosetta traduce Intel -> Apple Silicon, NO al reves. Asi que:
    #   - construido en Intel  -> corre en Intel Y en Apple Silicon. Sirve para
    #     toda la flota.
    #   - construido en M1/M2  -> corre SOLO en Apple Silicon. En una Intel ni
    #     siquiera abre.
    # Si las maquinas de destino son Intel, hay que construir en una Intel.
    if arq == "arm64":
        print()
        print("   [!] Esta es una Mac Apple Silicon.")
        print("       La aplicacion que salga de aqui NO ABRE en una Mac Intel.")
        print("       Rosetta traduce Intel -> Apple Silicon, no al reves.")
        print()
        print("       Si vas a instalar en Macs Intel, corre este script EN UNA")
        print("       DE ELLAS: lo que sale de una Intel corre en las Intel y")
        print("       tambien aqui, asi que con esa sola construccion cubres")
        print("       toda la flota.")
        print()
        r = input("   ¿Construir igual para esta Mac? [s/N] ").strip().lower()
        if r not in ("s", "si", "sí", "y", "yes"):
            sys.exit("Cancelado.")

    if falta("PySide6"):
        sys.exit("Falta PySide6.\n  pip3 install \"PySide6==6.10.3\" --break-system-packages")
    if falta("cv2"):
        sys.exit("Falta opencv.\n  pip3 install opencv-python --break-system-packages")
    if falta("PyInstaller"):
        paso("Instalando PyInstaller")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "pyinstaller", "--break-system-packages"])

    sys.path.insert(0, AQUI)
    import motor
    try:
        carpeta_motor = motor.localizar()
    except RuntimeError as e:
        sys.exit(f"{e}\n\nDefine ROI_MOTOR con la carpeta del motor y repite.")
    paso(f"Motor: {carpeta_motor}")

    # Solo los .py. Los .npz, .pt y las recetas NO entran: ver la cabecera.
    fuentes = [f for f in sorted(os.listdir(carpeta_motor)) if f.endswith(".py")]
    print("   " + ", ".join(fuentes))

    icono = os.path.join(AQUI, "ROI.icns")
    if not os.path.exists(icono):
        print("   [!] No hay ROI.icns; la app saldra con el icono generico.")
        icono = None

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--windowed", "--name", NOMBRE,
           "--osx-bundle-identifier", "com.airlinkdistribution.roi",
           "--distpath", os.path.join(AQUI, "dist"),
           "--workpath", os.path.join(AQUI, "build"),
           "--specpath", os.path.join(AQUI, "build")]
    if icono:
        cmd += ["--icon", icono]
    for f in fuentes:
        # Destino "motor": es una de las carpetas donde motor.py ya busca, asi
        # que empaquetado y sin empaquetar encuentran el codigo igual, sin una
        # rama especial para cada caso.
        cmd += ["--add-data", f"{os.path.join(carpeta_motor, f)}:motor"]
    for m in OCULTOS:
        cmd += ["--hidden-import", m]
    for m in EXCLUIDOS:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join(AQUI, "roi_app.py"))

    paso("Construyendo (tarda unos minutos)")
    subprocess.check_call(cmd)

    app = os.path.join(AQUI, "dist", f"{NOMBRE}.app")
    if not os.path.isdir(app):
        sys.exit("PyInstaller termino pero no hay .app en dist/.")
    tam = sum(os.path.getsize(os.path.join(r, f))
              for r, _, fs in os.walk(app) for f in fs) / 1e6

    # --- Info.plist: sin esto macOS MATA la app al abrir la camara ---
    #
    # No da un error ni un dialogo: manda SIGKILL. Desde el codigo no pasa
    # porque quien pide la camara es Terminal, que ya tiene el permiso; dentro
    # de un .app el que lo pide es la app, y una app que toca la camara sin
    # declarar para que se cierra en seco. Parece un fallo del programa y no lo
    # es.
    #
    # Se hace despues de construir y no con un .spec para no meter un archivo
    # mas que mantener: PyInstaller no tiene flag para claves sueltas del plist.
    paso("Ajustando Info.plist")
    plist = os.path.join(app, "Contents", "Info.plist")
    with open(plist, "rb") as f:
        pl = plistlib.load(f)
    pl["NSCameraUsageDescription"] = (
        "La inspección usa la cámara del banco de trabajo para ver la pieza.")
    pl["NSHighResolutionCapable"] = True
    with open(plist, "wb") as f:
        plistlib.dump(pl, f)
    print("   NSCameraUsageDescription anadida.")

    # Tocar el plist invalida la firma ad-hoc que pone PyInstaller, y una firma
    # rota es otra forma de que la app no abra. Se vuelve a firmar ad-hoc.
    try:
        subprocess.check_call(["codesign", "--force", "--deep", "--sign", "-",
                               app], stderr=subprocess.DEVNULL)
        print("   Firmada ad-hoc de nuevo.")
    except Exception as e:
        print(f"   [!] No se pudo re-firmar ({e}). Si no abre, es por esto.")

    # Se lee la arquitectura del binario de verdad, no la de la maquina: es el
    # unico dato que responde "¿esto abrira alla?" sin tener que probarlo alla.
    binario = os.path.join(app, "Contents", "MacOS", NOMBRE)
    arqs = "?"
    try:
        arqs = subprocess.check_output(["lipo", "-archs", binario],
                                       text=True).strip()
    except Exception:
        pass

    paso("Listo")
    print(f"   {app}")
    print(f"   {tam:.0f} MB  ·  binario para: {arqs}")
    if arqs == "arm64":
        print("   [!] Solo Apple Silicon. En una Mac Intel no abre.")
    elif "x86_64" in arqs:
        print("   Corre en Macs Intel y tambien en Apple Silicon (por Rosetta).")
    print()
    print("   En la otra Mac:")
    print("     1. Copia la carpeta «Inspeccion ROI.app».")
    print("     2. Clic derecho sobre ella > Abrir > Abrir. (Solo la primera")
    print("        vez: no esta firmada y Gatekeeper pregunta. Con doble clic")
    print("        a secas macOS la bloquea sin dar opcion.)")
    print("     3. Dentro, boton «Importar recetas...» y apunta a la carpeta")
    print("        del motor de esta Mac para traer la calibracion.")
    print()
    print("   Si aun asi se cierra al pulsar Iniciar, corre el binario a mano")
    print("   para ver el error, que dentro del .app no se ve:")
    print(f"     \"{os.path.join(app, 'Contents', 'MacOS', NOMBRE)}\"")
    print()
    print("   Si esa Mac es de otra arquitectura, corre este script alli.")
    print()
    print("   Comprueba antes que la Mac de destino llega a los minimos:")
    print("     python3 -c \"import platform;print(platform.mac_ver()[0], "
          "platform.machine(), platform.python_version())\"")


if __name__ == "__main__":
    main()
