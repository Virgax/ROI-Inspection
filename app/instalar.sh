#!/bin/bash
# instalar.sh — Deja la Mac mini lista, de una sola pasada.
#
# Hace todo lo que se puede hacer sin pedir nada: entorno, paquetes con las
# versiones que corresponden a este Python, construccion de la aplicacion y
# ajuste del Info.plist. Lo unico que pregunta es la conexion a SQL, porque
# lleva credenciales y no puede venir escrita en un archivo que se copia.
#
# Se puede volver a correr cuantas veces haga falta: si algo ya esta hecho,
# lo detecta y sigue.

set -u
CARPETA="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/roi-venv"
ROJO=$'\033[31m'; VERDE=$'\033[32m'; AMBAR=$'\033[33m'; FIN=$'\033[0m'

titulo() { printf "\n%s=== %s%s\n" "$AMBAR" "$1" "$FIN"; }
bien()   { printf "%s  ok  %s%s\n" "$VERDE" "$1" "$FIN"; }
mal()    { printf "%s  !!  %s%s\n" "$ROJO" "$1" "$FIN"; }
morir()  { mal "$1"; printf "\nSe detuvo aqui. Pega esta salida en el chat.\n\n"; exit 1; }

titulo "Esta Mac"
ARQ="$(uname -m)"
echo "  macOS $(sw_vers -productVersion)  ·  $ARQ  ·  Python $(python3 -V 2>&1 | awk '{print $2}')"
[ "$ARQ" = "x86_64" ] || mal "No es Intel. La app que salga de aqui no abrira en las Mac mini."

titulo "Entorno"
if [ -d "$VENV" ]; then
  bien "ya existe $VENV"
else
  python3 -m venv "$VENV" || morir "no se pudo crear el entorno"
  bien "creado $VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate" || morir "no se pudo activar el entorno"
PY="$VENV/bin/python"
"$PY" -m pip install --quiet --upgrade pip || morir "no se pudo actualizar pip"
bien "pip $("$PY" -m pip -V | awk '{print $2}')"

# Las versiones se eligen por la version de Python, no a ojo. En 3.9:
# PySide6 6.11 es cp310 y zxing-cpp 3.1 es cp312; con las nuevas, pip se pone
# a compilar desde fuente y no termina. Esto ya nos costo una tarde.
MENOR="$("$PY" -c 'import sys;print(sys.version_info[1])')"
if [ "$MENOR" -le 9 ]; then
  PAQUETES=("PySide6==6.10.3" "zxing-cpp==2.3.0" "numpy==2.0.2" "opencv-python<5" "pymssql" "pyinstaller")
  echo "  Python 3.$MENOR: se fijan PySide6 6.10.3 y zxing-cpp 2.3.0"
else
  PAQUETES=("PySide6" "zxing-cpp" "numpy" "opencv-python<5" "pymssql" "pyinstaller")
  echo "  Python 3.$MENOR: valen las versiones actuales"
fi

titulo "Paquetes"
# --no-compile: PySide6 trae dentro una plantilla de Jinja con extension .py
# que no es Python valido. pip intenta precompilarla y revienta a mitad de la
# instalacion. Saltarse el .pyc lo esquiva y no afecta a nada.
for p in "${PAQUETES[@]}"; do
  printf "  instalando %-22s" "$p"
  if "$PY" -m pip install --quiet --no-compile "$p" >/tmp/roi_pip.log 2>&1; then
    printf "%sok%s\n" "$VERDE" "$FIN"
  else
    printf "%sfallo%s\n" "$ROJO" "$FIN"; tail -25 /tmp/roi_pip.log
    morir "fallo instalando $p"
  fi
done

titulo "Comprobacion"
"$PY" - <<'PY' || morir "falta algun paquete"
import importlib, sys
faltan = []
for m in ("PySide6.QtWidgets", "cv2", "numpy", "zxingcpp", "pymssql", "PyInstaller"):
    try:
        importlib.import_module(m)
        print(f"  ok  {m}")
    except Exception as e:
        print(f"  !!  {m}: {e}"); faltan.append(m)
sys.exit(1 if faltan else 0)
PY

titulo "Construyendo la aplicacion (unos minutos)"
cd "$CARPETA" || morir "no encuentro $CARPETA"
"$PY" empaquetar.py </dev/null || morir "fallo el empaquetado"

APP="$CARPETA/dist/Inspeccion ROI.app"
[ -d "$APP" ] || morir "no hay .app en dist/"

titulo "Conexion a SQL"
# El motor vive en app/motor/ en el paquete viejo (PARA_MAC_MINI) y en ../motor/
# en el repositorio. Se busca en los dos sitios para que el script valga para ambos.
if [ -f "$CARPETA/motor/roi_db.py" ]; then
  MOTOR="$CARPETA/motor"
else
  MOTOR="$(cd "$CARPETA/../motor" 2>/dev/null && pwd)"
fi
echo "  Sin esto el inspector funciona, pero el CIERRE DE UNIDAD queda apagado:"
echo "  veras PASA y nada mas, sin pedir el giftcard ni el display."
if [ -f "$HOME/.roi/conexion.json" ]; then
  bien "ya configurada en ~/.roi/conexion.json"
else
  printf "\n  ¿Configurarla ahora? [s/N] "
  read -r R
  case "$R" in
    s|S|si|Si|SI|y|Y)
      ( cd "$MOTOR" && "$PY" roi_db.py --crear-config && "$PY" roi_db.py --probar ) \
        || mal "no quedo configurada. Repite luego: cd '$MOTOR' && '$PY' roi_db.py --crear-config"
      ;;
    *) echo "  Saltada. Cuando quieras:"
       echo "    cd '$MOTOR' && '$PY' roi_db.py --crear-config" ;;
  esac
fi

titulo "Listo"
echo "  $APP"
echo
echo "  1. Abrela: clic derecho sobre ella > Abrir > Abrir."
echo "     (Solo la primera vez. No esta firmada y con doble clic macOS la"
echo "      bloquea sin dar opcion.)"
echo "  2. Boton 'Importar recetas...' y apunta a:"
echo "       $(cd "$CARPETA/.." && pwd)/datos"
echo "  3. Marca 'Cierre de unidad' antes de pulsar Iniciar."
echo "     La app recuerda la receta, la camara y esa casilla para la"
echo "     proxima vez, asi que esto se hace una sola vez por estacion."
echo "  4. La primera vez que pulses Iniciar, macOS pedira permiso de camara."
echo
echo "  Para la SEGUNDA Mac mini basta copiar el .app. Nada mas: ni Python,"
echo "  ni este script. Solo la conexion a SQL y el permiso de camara."
echo
