#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roi_db.py — Registro del cierre de unidad en SQL Server.

QUE HACE
Envia a [AIRLINK].[dbo].[SP_Save_PRD_ClosingEvidence] la evidencia de una unidad
cerrada: el GCN leido del giftcard y las dos fotos rectificadas (housing y LCD)
en base64.

POR QUE VALIDA ANTES DE LLAMAR AL PROCEDIMIENTO
El procedimiento no comprueba nada: busca el IMEI y el IdHistoryUnitProcess y
hace el INSERT. Si algo falta, el fallo llega como un error de SQL Server con un
texto que no le dice nada al operario, o -peor- no llega como error:

  - GCN inexistente  -> @Imei queda NULL -> el INSERT falla porque IMEI es NOT NULL
  - unidad sin paso por HistoryUnitProcess -> @IdHistoryUnitProcess NULL -> igual
  - IMEI de mas de 15 caracteres -> se asigna a un varchar(15) y SQL Server lo
    TRUNCA EN SILENCIO. Comprobado: '89049032005008882600030789785130' se guarda
    como '890490320050088'. No hay error, no hay aviso, y la evidencia queda
    ligada a un IMEI que no existe. Son 6.517 unidades de 337.085.
Lo que NO se bloquea, a proposito: que una unidad ya tenga evidencia. Esta tabla
es un registro de EVIDENCIA previa al cierre definitivo, no un libro de unidades
cerradas. Una misma unidad puede volver a cerrarse con otra LCD, y una misma LCD
puede terminar en otra unidad. Repetir es parte del proceso, asi que el cliente
informa cuantos registros previos hay y sigue adelante.

Comprobar lo demas aqui cuesta una consulta rapida y convierte cada uno de esos
casos en una frase que el operario entiende y puede accionar.

CREDENCIALES
Nunca en el codigo ni en la carpeta ROI (que se respalda y se copia entre
bancos). Se leen de ~/.roi/conexion.json, que se crea con:

    python3 roi_db.py --crear-config

y luego se edita para poner la clave. Alternativa: variables de entorno
ROI_DB_SERVIDOR, ROI_DB_PUERTO, ROI_DB_USUARIO, ROI_DB_CLAVE, ROI_DB_BASE.

DEPENDENCIA
    pip3 install pymssql --break-system-packages
"""

import argparse
import base64
import hashlib
import json
import os
import socket
import sys

RUTA_CONFIG = os.path.expanduser("~/.roi/conexion.json")

PROCEDIMIENTO = "[AIRLINK].[dbo].[SP_Save_PRD_ClosingEvidence]"

# Limites REALES de las columnas de destino. Estan aqui como constantes y no
# sueltos en el codigo porque son el motivo de la mitad de las validaciones.
# PRD_ClosingEvidence.GCN es varchar(10) y el parametro @GCN tambien. Hoy los GCN
# de telefono caben, pero ya hay 7 en SPC_Receiving que no (hasta 22 caracteres) y
# se espera que crezcan. Cuando la columna y el parametro pasen a varchar(50), que
# es lo que usa SPC_Receiving, esta constante sube y deja de estorbar.
MAX_GCN = 10

# El IMEI de un telefono son 15 digitos SIEMPRE: 50.417 de 50.797 en SPC_Receiving
# (99,25%). Los 90 que se pasan son errores de captura, casi todos un IMEI correcto
# con un guion delante ('-353431654144997'). No es un caso de diseno, es una red de
# seguridad: sin ella el procedimiento asigna a un varchar(15) y trunca en silencio.
MAX_IMEI = 15


class ErrorCierre(Exception):
    """Fallo con un mensaje pensado para la pantalla del banco.

    `codigo` sirve para que el inspector decida (reintentar, pedir otro escaneo,
    parar la linea); `args[0]` es lo que lee el operario."""

    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo


def cargar_config():
    cfg = {"servidor": os.environ.get("ROI_DB_SERVIDOR"),
           "puerto": int(os.environ.get("ROI_DB_PUERTO", 0)) or None,
           "usuario": os.environ.get("ROI_DB_USUARIO"),
           "clave": os.environ.get("ROI_DB_CLAVE"),
           "base": os.environ.get("ROI_DB_BASE", "AIRLINK")}
    if os.path.exists(RUTA_CONFIG):
        with open(RUTA_CONFIG, encoding="utf-8") as f:
            disco = json.load(f)
        for k, v in disco.items():
            if cfg.get(k) in (None, ""):
                cfg[k] = v
    faltan = [k for k in ("servidor", "usuario", "clave") if not cfg.get(k)]
    if faltan:
        raise ErrorCierre("SIN_CONFIG",
                          f"Falta la configuracion de conexion ({', '.join(faltan)}). "
                          f"Cree {RUTA_CONFIG} con --crear-config.")
    cfg["puerto"] = cfg.get("puerto") or 1433
    return cfg


def crear_config():
    """Pregunta los datos y escribe la configuracion, lista para usar.

    Antes dejaba una plantilla con la clave puesta a 'PONER_AQUI' y un aviso de
    que habia que editarla. Ese aviso se pierde entre el resto de la salida del
    instalador, y lo que llega al operario no es 'falta editar un archivo' sino
    'no hay conexion con el servidor': el sintoma no se parece en nada a la
    causa. Preguntarlo aqui cuesta cuatro lineas y ahorra ese rodeo.

    La clave se pide con getpass, asi que no se ve al teclearla ni queda en el
    historial de la terminal."""
    import getpass
    os.makedirs(os.path.dirname(RUTA_CONFIG), exist_ok=True)
    os.chmod(os.path.dirname(RUTA_CONFIG), 0o700)
    if os.path.exists(RUTA_CONFIG):
        print(f"[i] Ya existe {RUTA_CONFIG}. No se toca.")
        return

    def pedir(etiqueta, defecto):
        r = input(f"  {etiqueta} [{defecto}]: ").strip()
        return r or defecto

    print("\n  Conexion a SQL Server. ENTER deja el valor entre corchetes.")
    cfg = {"servidor": pedir("Servidor", "192.168.181.248"),
           "puerto": int(pedir("Puerto", "13999")),
           "usuario": pedir("Usuario", "AlAppUser"),
           "base": pedir("Base de datos", "AIRLINK")}
    clave = ""
    while not clave:
        clave = getpass.getpass(f"  Clave de {cfg['usuario']} (no se muestra): ")
        if not clave:
            print("  La clave no puede quedar vacia.")
    cfg["clave"] = clave

    with open(RUTA_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(RUTA_CONFIG, 0o600)   # solo el dueno puede leerlo
    print(f"[OK] Guardado en {RUTA_CONFIG} (permisos 600).")


def conectar(cfg=None, timeout=20):
    try:
        import pymssql
    except ImportError:
        raise ErrorCierre("SIN_DRIVER",
                          "Falta pymssql. Instale: pip3 install pymssql --break-system-packages")
    cfg = cfg or cargar_config()
    try:
        return pymssql.connect(server=cfg["servidor"], port=cfg["puerto"],
                               user=cfg["usuario"], password=cfg["clave"],
                               database=cfg["base"],
                               timeout=timeout, login_timeout=10)
    except Exception as e:
        # Un fallo de conexion se distingue de un fallo de datos a proposito: el
        # primero lo resuelve soporte o la red, el segundo lo resuelve el operario.
        raise ErrorCierre("SIN_CONEXION",
                          f"No hay conexion con el servidor ({cfg['servidor']}). "
                          f"Avise a soporte. Detalle: {e}")


def a_base64(imagen_bgr, calidad=90):
    """Codifica el rectificado a JPEG y lo devuelve como (base64, bytes, sha256).

    JPEG y no PNG por tamano: a 3200x1659 el PNG comprimido son 7.75 MB (10.33 MB
    ya en base64) frente a 1.18 MB del JPEG q90. Las dos filas que ya existen en
    la tabla rondan 0.95 MB, o sea una calidad equivalente.

    El sha256 se calcula sobre el BINARIO, no sobre el base64: es la huella de la
    imagen, y sigue siendo la misma si algun dia se cambia la codificacion."""
    import cv2
    ok, buf = cv2.imencode(".jpg", imagen_bgr, [cv2.IMWRITE_JPEG_QUALITY, calidad])
    if not ok:
        raise ErrorCierre("IMAGEN", "No se pudo codificar la imagen de evidencia.")
    crudo = buf.tobytes()
    return base64.b64encode(crudo).decode("ascii"), len(crudo), hashlib.sha256(crudo).hexdigest()


def verificar_unidad(gcn, con=None, etapa=None):
    """Comprueba que la unidad se puede cerrar. Devuelve un dict con sus datos.

    Lanza ErrorCierre con un mensaje para pantalla si algo impide el registro.
    Se hace ANTES de codificar las fotos y antes de llamar al procedimiento: no
    tiene sentido mover 2 MB de base64 por la red para que el INSERT falle."""
    propia = con is None
    con = con or conectar()
    try:
        cur = con.cursor()
        gcn = (gcn or "").strip()
        if not gcn:
            raise ErrorCierre("SIN_GCN", "No se leyo el GCN. Vuelva a escanear el giftcard.")
        if len(gcn) > MAX_GCN:
            raise ErrorCierre(
                "GCN_LARGO",
                f"El GCN {gcn} tiene {len(gcn)} caracteres y la tabla admite {MAX_GCN}. "
                f"No se puede registrar sin truncarlo. Avise a soporte.")

        cur.execute("SELECT TOP 1 Imei FROM dbo.SPC_Receiving WHERE GCN=%s ORDER BY ID DESC", (gcn,))
        fila = cur.fetchone()
        if not fila:
            raise ErrorCierre("GCN_NO_EXISTE",
                              f"El GCN {gcn} no existe en el sistema. Verifique el giftcard.")
        imei = (fila[0] or "").strip()
        if not imei:
            raise ErrorCierre("SIN_IMEI",
                              f"La unidad {gcn} no tiene IMEI registrado. No se puede cerrar. "
                              f"Avise a soporte.")
        if imei.startswith("-"):
            # Un guion delante NO es un error de captura: es como se ANULA un
            # registro de recepcion para poder volver a recibir la misma unidad
            # si regresa en otro lote (revendida, por ejemplo). El GCN que se
            # escaneo es entonces el registro VIEJO, y cerrar contra el seria
            # atar la evidencia a una recepcion que ya no vale.
            # Comprobado: 1.434 de 1.440 anulados reaparecen limpios bajo otro
            # GCN, asi que casi siempre se puede decir cual es el bueno, que es
            # mucho mas util para el operario que un simple "no se puede".
            cur.execute("SELECT TOP 1 GCN, Date FROM dbo.SPC_Receiving "
                        "WHERE Imei=%s AND GCN<>%s ORDER BY ID DESC",
                        (imei[1:], gcn))
            nuevo = cur.fetchone()
            if nuevo:
                raise ErrorCierre(
                    "RECEPCION_ANULADA",
                    f"El registro de la unidad {gcn} esta anulado: volvio a "
                    f"recibirse el {nuevo[1]} como {nuevo[0]}. "
                    f"Use el giftcard de {nuevo[0]}.")
            raise ErrorCierre(
                "RECEPCION_ANULADA",
                f"El registro de recepcion de la unidad {gcn} esta anulado y no "
                f"se encontro una recepcion posterior. Avise a soporte.")

        if len(imei) > MAX_IMEI:
            # Este es el caso peligroso: si se deja pasar, el procedimiento lo
            # trunca sin avisar y la evidencia queda con un IMEI que no existe.
            raise ErrorCierre(
                "IMEI_LARGO",
                f"El IMEI de la unidad {gcn} tiene {len(imei)} caracteres y la tabla admite "
                f"{MAX_IMEI}. Registrarlo lo guardaria cortado y la evidencia quedaria "
                f"ligada a un IMEI incorrecto. Avise a soporte.")

        cur.execute("SELECT TOP 1 id FROM [AirlinkDR].[dbo].[HistoryUnitProcess] "
                    "WHERE gcn=%s ORDER BY id DESC", (gcn,))
        fila = cur.fetchone()
        if not fila:
            raise ErrorCierre("SIN_PROCESO",
                              f"La unidad {gcn} no tiene historial de proceso registrado. "
                              f"No se puede cerrar todavia.")
        id_hup = fila[0]

        # Registros previos: se CUENTAN, no se bloquean. La misma unidad puede
        # volver a pasar por aqui con otra LCD, y la misma LCD puede acabar en otra
        # unidad; esto es evidencia previa al cierre definitivo, no un libro de
        # unidades cerradas. El dato se devuelve para que la estacion pueda
        # avisarlo en pantalla ("2 registros previos") sin detener al operario.
        cur.execute("SELECT COUNT(*), MAX(Date) FROM dbo.PRD_ClosingEvidence WHERE GCN=%s",
                    (gcn,))
        n_previos, ultima = cur.fetchone()

        # Y los de ESTA etapa, que es lo que de verdad es una repeticion.
        #
        # Con modelos de dos lados, una unidad tiene una fila por lado. Contar
        # por GCN haria que el segundo lado dijese siempre "1 registro previo"
        # -que es el otro lado, no una repeticion- y un aviso que sale siempre
        # deja de leerse.
        #
        # Se mira antes si la columna existe en vez de lanzar la consulta y
        # capturar el error: si 04a todavia no se ha corrido, esto NO puede
        # convertirse en el motivo de que una unidad no se cierre. Repetir un
        # cierre esta permitido a proposito; un aviso nunca puede impedirlo.
        previos_etapa = None
        if etapa:
            cur.execute("SELECT COL_LENGTH('dbo.PRD_ClosingEvidence', 'Etapa')")
            if cur.fetchone()[0] is not None:
                cur.execute("SELECT COUNT(*) FROM dbo.PRD_ClosingEvidence "
                            "WHERE GCN=%s AND Etapa=%s", (gcn, etapa))
                previos_etapa = cur.fetchone()[0] or 0

        return {"gcn": gcn, "imei": imei, "id_history_unit_process": id_hup,
                "registros_previos": n_previos or 0, "ultimo_registro": ultima,
                "previos_etapa": previos_etapa}
    finally:
        if propia:
            con.close()


def _normalizar_serial(s):
    """Deja un serial de parte listo para comparar.

    Mayusculas y sin espacios, y se queda con el trozo anterior al '+'. Ese '+'
    no es parte del serial: en PRD_LinkMainPartToUnit las columnas de partes
    guardan a veces un valor compuesto (la columna Housing trae cosas como
    'DRCHG80036C0000AY2+8+AA+FY8H70005M2+E02HG8', y 14 de las 724 filas de LCD
    tambien lo traen). Comparar la cadena entera contra lo que dice el QR daria
    'no coincide' en todos esos casos siendo el mismo display."""
    if not s:
        return ""
    return str(s).strip().upper().split("+")[0].strip()


def verificar_lcd(gcn_unidad, texto_lcd, con=None, exigir_emparejamiento=False):
    """Comprueba que el display escaneado corresponde a esta unidad.

    DOS CAMINOS, segun lo que traiga el codigo del display.

    1) La etiqueta lleva el GCN DE LA UNIDAD. Es lo que se ve en produccion: el
       QR dice '052|DSM2|LCD|CX413945', el mismo formato de campos separados por
       '|' del giftcard, y el ultimo campo es el GCN del telefono al que ese
       display pertenece. La etiqueta impresa debajo lo repite en claro
       ('LCD-CX413945'). Entonces la comprobacion es directa y exacta: ese GCN
       tiene que ser el mismo que el del giftcard. No hace falta consultar nada.

    2) El codigo es un SERIAL DE PARTE (formato 'AK5346270151WMW9KA14B2C34D').
       Ahi no dice a que unidad pertenece, asi que hay que preguntarselo a
       PRD_LinkMainPartToUnit, que guarda que display se emparejo con que GCN.

    Se intenta primero el camino 1 porque no cuesta una consulta y porque no
    depende de que el emparejamiento este registrado, que hoy solo cubre el 6,4%
    de las unidades con actividad reciente.

    QUE HACE `exigir_emparejamiento`
    Con False -el modo de este proceso- esta funcion NUNCA detiene un cierre:
    devuelve validado=False y un aviso, y quien llama decide. Con True, un
    desajuste es un error y corta.

    El valor por defecto es False a proposito: aqui se captura evidencia antes
    del cierre definitivo, no se valida el emparejamiento. La foto es lo unico
    que no se puede recuperar mas tarde; el serial si."""
    gcn_unidad = (gcn_unidad or "").strip()
    crudo = (texto_lcd or "").strip()
    if not crudo:
        raise ErrorCierre("SIN_QR_LCD",
                          "No se leyo el codigo del display. Vuelva a escanear.")

    # --- camino 1: la etiqueta trae el GCN de la unidad ---------------------
    try:
        import roi_qr
        gcn_lcd = roi_qr.gcn_de_giftcard(crudo)
    except ImportError:
        gcn_lcd = None

    if gcn_lcd:
        if gcn_lcd == gcn_unidad.upper():
            return {"gcn": gcn_unidad, "lcd": gcn_lcd, "lcd_registrado": crudo,
                    "validado": True, "via": "etiqueta", "aviso": None}
        aviso = (f"Este display figura en la unidad {gcn_lcd} y el telefono "
                 f"es {gcn_unidad}.")
        if exigir_emparejamiento:
            raise ErrorCierre("LCD_NO_COINCIDE",
                              aviso + " Use el display de esta unidad.")
        # SOLO INFORMA. Con exigir_emparejamiento=False -que es el modo de este
        # proceso- un desajuste NO detiene nada: aqui se captura evidencia
        # antes del cierre definitivo, y una misma unidad puede llevar otro
        # display en otro momento. Cortar por esto perderia las dos fotos, que
        # es lo unico irrecuperable, por una comprobacion que ni siquiera se
        # pidio.
        return {"gcn": gcn_unidad, "lcd": gcn_lcd, "lcd_registrado": crudo,
                "validado": False, "via": "etiqueta", "aviso": aviso}

    # --- camino 2: es un serial de parte, hay que consultar el emparejamiento -
    propia = con is None
    con = con or conectar()
    try:
        cur = con.cursor()
        leido = _normalizar_serial(crudo)
        cur.execute("SELECT TOP 1 LCD FROM dbo.PRD_LinkMainPartToUnit WHERE GCN=%s "
                    "ORDER BY Id DESC", (gcn_unidad,))
        fila = cur.fetchone()

        # AUSENCIA de emparejamiento y DISCREPANCIA son cosas distintas. Una
        # discrepancia es un error en la mesa y siempre para. La ausencia es un
        # hueco de datos y hoy es lo normal, asi que por defecto solo se avisa.
        if not fila or not _normalizar_serial(fila[0]):
            if exigir_emparejamiento:
                raise ErrorCierre(
                    "SIN_EMPAREJAMIENTO",
                    f"La unidad {gcn_unidad} no tiene display emparejado en el "
                    f"sistema. El emparejamiento debe hacerse antes del cierre.")
            return {"gcn": gcn_unidad, "lcd": leido, "lcd_registrado": None,
                    "validado": False, "via": "serial",
                    "aviso": f"La unidad {gcn_unidad} no tiene emparejamiento "
                             f"registrado: no se pudo comprobar el display."}

        esperado = _normalizar_serial(fila[0])
        if leido != esperado:
            cur.execute("SELECT TOP 1 GCN FROM dbo.PRD_LinkMainPartToUnit "
                        "WHERE UPPER(LCD)=%s OR UPPER(LCD) LIKE %s",
                        (leido, leido + "+%"))
            otra = cur.fetchone()
            pista = f" Ese display figura en la unidad {otra[0]}." if otra else ""
            aviso = (f"El display {leido} no coincide con el emparejamiento "
                     f"registrado de {gcn_unidad} ({esperado}).{pista}")
            if exigir_emparejamiento:
                raise ErrorCierre("LCD_NO_COINCIDE", aviso)
            # SOLO INFORMA, por lo mismo que arriba.
            return {"gcn": gcn_unidad, "lcd": leido, "lcd_registrado": fila[0],
                    "validado": False, "via": "serial", "aviso": aviso}

        return {"gcn": gcn_unidad, "lcd": leido, "lcd_registrado": fila[0],
                "validado": True, "via": "serial", "aviso": None}
    finally:
        if propia:
            con.close()


def _marcar_extras(con, id_fila, serial_lcd, origen, estacion, etapa, parte):
    """Escribe el serial del display y la estacion, SIN poner en riesgo la fila.

    Va DESPUES del commit y en su propio try a proposito. La evidencia -las dos
    fotos- ya esta guardada en ese punto; esto son dos datos que la acompanan.
    Si el procedimiento no existe todavia porque nadie ha corrido el DDL, o la
    columna falta, lo que NO puede pasar es que el operario vea 'fallo el
    cierre' de una unidad que si quedo registrada. Se avisa por consola y se
    sigue.

    Es la misma leccion de la clave a 'PONER_AQUI': cuando el sintoma que le
    llega al operario no se parece a la causa, se pierde media tarde."""
    try:
        cur = con.cursor()
        cur.execute("EXEC dbo.SP_Set_PRD_ClosingEvidence_LCDSerial "
                    "%s,%s,%s,%s,%s,%s",
                    (id_fila, serial_lcd, origen, estacion, etapa, parte))
        try:
            cur.fetchall()
        except Exception:
            pass
        con.commit()
        return True
    except Exception as e:
        print(f"[cierre] AVISO: la evidencia {id_fila} se guardo bien, pero no "
              f"se pudo anotar el serial/estacion: {e}")
        print(f"[cierre]        Falta correr sql/03_lcd_serial.sql en AIRLINK.")
        try:
            con.rollback()
        except Exception:
            pass
        return False


def guardar_cierre(gcn, housing_b64, lcd_b64, proceso="CLOSING",
                   con=None, verificar=True, _rollback=False,
                   serial_lcd=None, origen=None, estacion=None,
                   etapa=None, parte=None):
    """Llama al procedimiento y registra la evidencia.

    `_rollback=True` ejecuta todo dentro de una transaccion que se deshace al
    final: sirve para probar la cadena completa contra el servidor de produccion
    sin dejar una fila de prueba en la tabla."""
    propia = con is None
    con = con or conectar(timeout=120)     # 2 MB de base64 por parametro
    try:
        datos = verificar_unidad(gcn, con) if verificar else {"gcn": gcn}
        cur = con.cursor()
        if _rollback:
            cur.execute("BEGIN TRANSACTION")
        # NO usar cur.callproc: pymssql liga los parametros de cadena como
        # VARCHAR(8000) y RECORTA el base64 a 8000 caracteres sin dar ningun
        # error. Medido: de 1.572.932 caracteres enviados llegaban 8.000, o sea
        # una imagen destruida guardada como si nada hubiera pasado.
        # Declarar las variables como VARCHAR(MAX) en el mismo lote y pasarlas al
        # procedimiento conserva el contenido completo.
        cur.execute(
            "DECLARE @h VARCHAR(MAX)=%s, @l VARCHAR(MAX)=%s; "
            "EXEC dbo.SP_Save_PRD_ClosingEvidence %s,%s,@h,@l",
            (housing_b64, lcd_b64, datos["gcn"], proceso))
        cur.execute("SELECT TOP 1 Id, IdHistoryUnitProcess, IMEI, "
                    "DATALENGTH(Housing_PhotoBase64), DATALENGTH(LCD_PhotoBase64) "
                    "FROM dbo.PRD_ClosingEvidence WHERE GCN=%s ORDER BY Id DESC",
                    (datos["gcn"],))
        fila = cur.fetchone()
        if not fila:
            raise ErrorCierre("NO_REGISTRO",
                              "El servidor no registro la evidencia. Avise a soporte.")
        resultado = {"id": fila[0], "id_history_unit_process": fila[1], "imei": fila[2],
                     "bytes_housing": fila[3], "bytes_lcd": fila[4]}
        if _rollback:
            cur.execute("ROLLBACK TRANSACTION")
            return resultado
        con.commit()
        resultado["serial_anotado"] = _marcar_extras(
            con, fila[0], serial_lcd, origen, estacion, etapa, parte)
        return resultado
    except ErrorCierre:
        raise
    except Exception as e:
        raise ErrorCierre("ERROR_SQL", f"Error al registrar en la base de datos: {e}")
    finally:
        if propia:
            con.close()


def main():
    ap = argparse.ArgumentParser(description="Utilidades de conexion del cierre ROI")
    ap.add_argument("--crear-config", action="store_true")
    ap.add_argument("--probar", action="store_true", help="prueba de conexion")
    ap.add_argument("--verificar", metavar="GCN", help="valida un GCN sin escribir nada")
    args = ap.parse_args()
    try:
        if args.crear_config:
            crear_config()
        elif args.probar:
            cfg = cargar_config()
            con = conectar(cfg)
            cur = con.cursor()
            cur.execute("SELECT DB_NAME(), SUSER_NAME()")
            print("[OK] Conectado:", cur.fetchone())
            con.close()
        elif args.verificar:
            print("[OK]", verificar_unidad(args.verificar))
        else:
            ap.print_help()
    except ErrorCierre as e:
        sys.exit(f"[{e.codigo}] {e}")


if __name__ == "__main__":
    main()
