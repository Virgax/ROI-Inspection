#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cierre.py — Secuencia de cierre de unidad: housing + display.

QUE HACE
Cuando el inspector declara PASA, arranca esta secuencia:

  1. Busca el giftcard en su zona y saca el GCN.
  2. Valida la unidad contra la base (existe, tiene IMEI, tiene historial de
     proceso, el registro no esta anulado).
  3. Guarda la foto rectificada del housing.
  4. Pide al operario retirar el housing y colocar el display.
  5. Busca el QR del display, valida que sea el emparejado con esa unidad y
     guarda su foto.
  6. Registra las dos fotos con SP_Save_PRD_ClosingEvidence.

POR QUE EN UN HILO
Las consultas de validacion tardan decenas de milisegundos, pero el registro con
las dos imagenes en base64 tarda unos 3 segundos. Hacerlo en el bucle de video
dejaria la imagen congelada todo ese rato justo cuando el operario necesita ver
que esta pasando. El hilo hace una tarea a la vez; el bucle sigue a su ritmo y
solo lee el estado.

POR QUE NO TOCA EL VEREDICTO DE TORNILLOS
Esta secuencia observa, no decide. El inspector sigue dando PASA / NO PASA / 
VERIFICAR con su propia logica; el cierre solo se activa DESPUES de un PASA
estable. Si el cierre falla, la inspeccion de tornillos no cambia de resultado:
lo que falla es el registro, y eso se dice aparte.

USO DESDE EL INSPECTOR
    import cierre
    c = cierre.Cierre(cfg, receta)        # c.disponible dice si se puede usar
    ...
    c.procesar(warped, estable, es_pasa)  # cada frame con rectificado valido
    c.dibujar(vista, escala)
    c.tecla(k)                            # para confirmar o cancelar
    if c.suprime_registro(): ...          # no escribir el CSV de tornillos
"""

import platform
import threading
import time

import cv2

import roi_qr

try:
    import roi_db
except ImportError:
    roi_db = None

# --- estados ---------------------------------------------------------------
INACTIVO = "inactivo"            # esperando una pieza que pase
GIFTCARD = "giftcard"            # PASA: buscando el giftcard
VALIDANDO = "validando"          # consultando la unidad en la base
PIDE_LCD = "pide_lcd"            # housing capturado, pedir el display
QR_LCD = "qr_lcd"                # buscando el QR del display
ESPERA_SCAN = "espera_scan"      # la camara no pudo: escanear o seguir sin serial
VALIDA_LCD = "valida_lcd"        # comprobando el emparejamiento
REGISTRANDO = "registrando"      # enviando las dos fotos
HECHO = "hecho"                  # cerrado, esperando confirmacion
ERROR = "error"                  # algo fallo, esperando confirmacion

VERDE = (105, 215, 83)
ROJO = (74, 75, 226)
AMARILLO = (46, 189, 255)
AZUL = (235, 170, 60)
BLANCO = (235, 235, 232)

# Frames que debe llevar la etapa del display antes de dar por buena la lectura.
# Sin esto, el QR se lee mientras el operario todavia mueve la mano y la foto de
# evidencia sale movida.
FRAMES_QUIETO_LCD = 8

# Frames que un final -cerrado u error- se queda en pantalla como MINIMO antes
# de poder limpiarse. Un mensaje que aparece y desaparece en dos frames es un
# mensaje que nadie leyo.
FRAMES_MINIMOS_HECHO = 15        # ~1 s
FRAMES_MINIMOS_ERROR = 45        # ~3 s: si algo fallo, que se lea entero

# Y el limite DURO, pasado el cual el final se limpia pase lo que pase.
#
# Hace falta porque `hay_pieza` NO sirve como senal de "ya la retiraron": mira
# las ROIs de tornillo, y con el display puesto esas ROIs ven superficie lisa y
# votan 'dudoso', asi que se queda en True. Es exactamente el mismo hueco que
# obligo a poner el segundo camino en PIDE_LCD. Confiar solo en esa senal dejo
# un ERROR clavado en pantalla para siempre: la secuencia no volvia a arrancar
# y la unidad no se cerraba nunca.
FRAMES_LIMITE_HECHO = 120        # ~8 s
FRAMES_LIMITE_ERROR = 260        # ~17 s

# Lo mismo en PIDE_LCD: cuanto se espera antes de pasar a buscar el codigo del
# display de todas formas. Es el TERCER camino, y existe porque los otros dos
# pueden fallar A LA VEZ, que es justo lo que pasa en la mesa:
#   - `hay_pieza` se queda en True con el display puesto, porque mira las ROIs
#     de tornillo y esas ven superficie lisa y votan 'dudoso';
#   - y la lectura del codigo no sale si la zona del display quedo mal marcada.
# Con solo esos dos, la secuencia se queda pidiendo un display que YA esta
# puesto, para siempre.
#
# Pasar de largo es seguro: en QR_LCD no se captura NADA hasta que se lee un
# codigo dentro de la zona del display, y en este punto del ensamble el codigo
# del housing esta tapado. Sin lectura no hay foto.
FRAMES_LIMITE_PIDE_LCD = 45      # ~3 s

# Cada cuantos frames sin leer se repite el aviso de que no se ve el codigo.
# Repetido y no una sola vez: el operario necesita saber que el sistema sigue
# mirando y que la zona puede estar mal, no un mensaje que aparecio una vez y
# se perdio.
FRAMES_AVISO_SIN_LEER = 45       # ~3 s

# Estos dos van en SEGUNDOS y no en fotogramas, al contrario que el resto.
#
# Los demas son cuentas internas y da igual que su duracion real dependa de los
# fps. Estos dos no: uno se le ensena al operario como cuenta atras, y contar
# fotogramas suponiendo 15 fps significa que en una maquina que va a 25 el
# cartel diga "quedan 20s" y guarde a los 12. Un numero que se muestra tiene
# que ser el numero de verdad.
#
# Cuanto insiste la camara antes de rendirse y pedir el escaner. Medido sobre
# piezas reales, el simbolo da unos 2,4 px por modulo cuando el lector necesita
# 4: a veces sale y a veces no, y no es cuestion de esperar mas. Diez segundos
# es de sobra para las que van a salir.
SEG_RENDIRSE_CAMARA = 10.0

# Y cuanto se espera en la pantalla del escaner antes de guardar igual. Se
# guarda SIN serial en vez de bloquear la linea porque la foto de la LCD es la
# evidencia; el serial es una validacion adicional, no el requisito. Una unidad
# sin validar y con su foto vale infinitamente mas que una unidad sin registrar.
SEG_ESPERA_SCAN = 20.0

# Frames sin ver el tapete antes de dar por abandonada una secuencia que ya
# paso del giftcard. Generoso a proposito: a partir de PIDE_LCD la pieza SALE
# del fixture porque se lo hemos pedido, y mientras el operario maneja el
# housing y el display con las manos encima, los marcadores van y vienen.
FRAMES_SIN_TAPETE_CANCELA = 300  # ~20 s


# Partes que se pueden pedir en una etapa. La clave es lo que va al config y a
# la base; el valor es como se le dice al operario.
#
# POR QUE ESTO NO ESTABA ANTES
# El cierre nacio para un modelo con un solo lado: housing y display. En 14, 14
# Plus, 15 y 15 Plus el housing tiene DOS lados con tornillos propios, y cada
# lado se cierra con una parte distinta -uno con el display y otro con el
# backglass-. Son dos vistas fisicas, con dos geometrias y dos calibraciones,
# asi que son dos RECETAS, no una receta con dos modos.
#
# Y como cada etapa lee su propio giftcard y pueden hacerse en momentos
# distintos, cada una es un cierre COMPLETO e independiente: no hay estado que
# arrastrar entre ellas, y el enlace entre las dos lo hace el GCN en la base.
# Por eso el orden -top primero o bottom primero- no es asunto del programa:
# es simplemente que receta se corre.
PARTES = {
    "DISPLAY": "display",
    "BACKGLASS": "backglass",
}

PARTE_POR_DEFECTO = "DISPLAY"

# Como se reconoce la parte en el NOMBRE de la receta. Primero la coincidencia
# mas larga, para que "BACKGLASS" no se lea como si llevara "GLASS" suelto.
PALABRAS_PARTE = [
    ("BACKGLASS", "BACKGLASS"),
    ("BACK GLASS", "BACKGLASS"),
    ("BACK-GLASS", "BACKGLASS"),
    ("DISPLAY", "DISPLAY"),
    ("LCD", "DISPLAY"),
]


def _etapa_de_cfg(cfg, receta=None):
    """(etapa, parte) de la receta.

    DE DONDE SALE LA PARTE, por orden:
      1. El bloque `etapa` del config, si alguien lo puso a mano.
      2. El NOMBRE de la receta: «IP14 - BACKGLASS» habla por si solo.
      3. DISPLAY.

    El nombre manda porque es lo que el operario ya tiene que acertar para
    elegir la receta correcta: si acierta ahi, acierta en todo lo demas, y no
    hay un segundo sitio donde el ajuste pueda quedar a medias. Un campo aparte
    puede contradecir al nombre; el nombre no puede contradecirse a si mismo.

    El riesgo de deducir del nombre es que un nombre raro cambie el
    comportamiento en silencio, asi que lo deducido SE IMPRIME al arrancar y
    sale en el panel. Deducir no es adivinar si se dice en voz alta.

    La etapa es el propio nombre de la receta. Dos lados de una unidad son dos
    recetas distintas, asi que sus nombres ya las distinguen en la base sin
    inventar ninguna convencion aparte."""
    e = cfg.get("etapa") or {}
    nombre = str(receta or cfg.get("proceso") or "CLOSING").strip()
    etapa = str(e.get("nombre") or nombre).strip().upper()[:30]

    parte = str(e.get("parte") or "").strip().upper()
    if parte not in PARTES:
        parte = ""
    if not parte:
        texto = f"{nombre} {cfg.get('modelo') or ''}".upper()
        for palabra, cual in PALABRAS_PARTE:
            if palabra in texto:
                parte = cual
                break
    return etapa, parte or PARTE_POR_DEFECTO


def _zona_de_roi(cfg, tipo, margen=1.10):
    """(x0,y0,x1,y1) de la primera ROI marcada con ese tipo, o None.

    El calibrador guarda las cajas por CENTRO y tamano; aqui se pasan a esquinas,
    que es lo que necesita el recorte. Se agranda un 10% a proposito: la caja se
    dibuja ajustada al giftcard de la unidad que estaba en la mesa ese dia, y el
    siguiente no cae exactamente igual. Un margen de sobra no cuesta nada -son
    milisegundos de mas en una zona que ya es pequena- y un giftcard que se sale
    de la zona por tres pixeles cuesta una relectura."""
    for p in (cfg.get("zonas") or {}).get(tipo, []):
        w = float(p.get("roi_w") or p.get("roi_h") or cfg.get("roi_size", 60)) * margen
        h = float(p.get("roi_h") or p.get("roi_w") or cfg.get("roi_size", 60)) * margen
        cx, cy = float(p["x"]), float(p["y"])
        return [int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)]
    return None


def _partir(texto, ancho, maximo=3):
    """Parte el texto en lineas de como mucho `ancho` caracteres, por palabras."""
    palabras, lineas, actual = (texto or "").split(), [], ""
    for p in palabras:
        if len(actual) + len(p) + 1 <= ancho:
            actual = f"{actual} {p}".strip()
        else:
            lineas.append(actual)
            actual = p
            if len(lineas) == maximo:
                break
    if actual and len(lineas) < maximo:
        lineas.append(actual)
    return lineas or [""]


class Cierre:

    def __init__(self, cfg, receta, verbose=True, exigir_emparejamiento=False,
                 activo=True):
        self.cfg = cfg
        self.receta = receta
        self.exigir_emparejamiento = exigir_emparejamiento
        self.estado = INACTIVO
        self.mensaje = ""
        self.color = BLANCO
        self.disponible = False
        self.motivo_no_disponible = ""
        # Con activo=False queda un objeto completo pero inerte: el inspector lo
        # llama igual en cada frame y todos los metodos salen por disponible.
        # Es mas seguro que construirlo a medias y confiar en que nadie toque un
        # atributo que no existe.
        if not activo:
            self.motivo_no_disponible = "no solicitado (--cierre)"
            return

        self.gcn = None
        self.datos_unidad = None
        self.serial_lcd = None
        self.foto_housing = None      # rectificado LIMPIO, sin overlay
        self.foto_lcd = None
        self.resultado = None

        # La zona puede venir de dos sitios, y se prefiere la ROI porque es la
        # que el operario ve y ajusta en el calibrador, junto al resto de la
        # geometria del proceso. `zona_qr` queda como alternativa para quien la
        # marco con marcar_zona_qr.py antes de que existiera el tipo Giftcard.
        self._zona_housing = (_zona_de_roi(cfg, "giftcard")
                              or cfg.get("zona_qr"))
        self.etapa, self.parte = _etapa_de_cfg(cfg, receta)
        # Como se nombra la parte en pantalla. Se calcula una vez: repartir
        # condicionales por los quince mensajes es como se acaba con una
        # pantalla que dice "display" en la etapa del backglass.
        self.parte_txt = PARTES[self.parte]
        # La zona de la parte se busca por su propio tipo primero -una receta de
        # backglass marca una ROI tipo Backglass- y despues por los nombres
        # viejos, para que las recetas que ya existen sigan encontrando la suya.
        self._zona_lcd = (_zona_de_roi(cfg, self.parte_txt)
                          or _zona_de_roi(cfg, "lcd")
                          or _zona_de_roi(cfg, "display")
                          or (cfg.get("etapa_lcd") or {}).get("zona_qr"))
        self._lock = threading.Lock()
        self._tarea = None
        self._ocupado = False
        self._parar = False
        self._quieto = 0
        self._visto = 0
        self._sin_tapete = 0
        self._t_estado = time.time()
        self.sin_serial = False
        self.origen_serial = None
        self.aviso_lcd = None
        self._frame = None
        self._centros = None
        self._tam = tuple(cfg["image_size"])

        if roi_db is None:
            self.motivo_no_disponible = "Falta roi_db.py"
        elif not self._zona_housing:
            self.motivo_no_disponible = (
                f"La receta '{receta}' no tiene marcada la zona del giftcard. "
                f"Corre: python3 marcar_zona_qr.py --receta \"{receta}\"")
        else:
            try:
                roi_db.cargar_config()
                self.disponible = True
            except Exception as e:
                self.motivo_no_disponible = str(e)

        if not self.disponible:
            if verbose:
                print(f"[cierre] Desactivado: {self.motivo_no_disponible}")
            return
        if not self._zona_lcd and verbose:
            print(f"[cierre] AVISO: falta la zona del codigo del "
                  f"{self.parte_txt}. Se podra "
                  f"leer el giftcard y validar la unidad, pero el cierre NO se "
                  f"completara. Marcala con:\n"
                  f"          python3 marcar_zona_qr.py --receta \"{receta}\" "
                  f"--etapa lcd --imagen <foto con el {self.parte_txt}>")

        self._hilo = threading.Thread(target=self._trabajar, daemon=True)
        self._hilo.start()
        if verbose:
            print(f"[cierre] Listo. Etapa {self.etapa} / parte "
                  f"{self.parte}. Zona giftcard {self._zona_housing} | "
                  f"zona {self.parte_txt} {self._zona_lcd}")

    # ------------------------------------------------------------------
    def suprime_registro(self):
        """True mientras la pieza en el fixture YA NO es el housing.

        Durante la etapa del display no hay tornillos que inspeccionar, asi que
        el clasificador veria todas las posiciones vacias y escribiria un
        NO_PASA en el log de tornillos por una pieza que ni siquiera es la que
        se estaba inspeccionando."""
        return self.estado in (PIDE_LCD, QR_LCD, VALIDA_LCD, REGISTRANDO, HECHO)

    def activa(self):
        return self.estado != INACTIVO

    def _poner(self, estado, mensaje, color):
        # `_visto` cuenta frames EN ESTE ESTADO, asi que solo se reinicia cuando
        # el estado cambia de verdad. Reiniciarlo tambien al refrescar el texto
        # dejaba la cuenta atras de ESPERA_SCAN sin avanzar nunca: cada segundo
        # se actualizaba el mensaje y el contador volvia a cero, asi que el
        # guardado sin serial no llegaba jamas. La pantalla decia "quedan 39s"
        # para siempre.
        if estado != self.estado:
            self._visto = 0
            self._t_estado = time.time()
        self.estado, self.mensaje, self.color = estado, mensaje, color

    def cancelar(self, motivo="Cancelado por el operario"):
        with self._lock:
            self._tarea = None
        self.gcn = self.datos_unidad = self.serial_lcd = None
        self.origen_serial = None
        self.aviso_lcd = None
        self._sin_leer = 0
        self._sin_tapete = 0
        self.foto_housing = self.foto_lcd = self.resultado = None
        self._quieto = 0
        self._poner(INACTIVO, "", BLANCO)
        if motivo:
            print(f"[cierre] {motivo}")

    def escanear(self, texto):
        """Serial del display tecleado o leido con un lector de mano.

        Un lector USB se comporta como un teclado: escribe el serial y pulsa
        Enter. Quien llama junta esas pulsaciones y entrega la cadena entera
        aqui, que es donde se sabe si en este momento tiene sentido.

        Se acepta en ESPERA_SCAN -la camara se rindio- y tambien en QR_LCD,
        para que alguien con el lector en la mano no tenga que esperar los diez
        segundos de cortesia."""
        t = (texto or "").strip().upper()
        if not self.disponible or len(t) < 6:
            return False
        if self.estado not in (QR_LCD, ESPERA_SCAN):
            return False
        if self.foto_lcd is None:
            # En QR_LCD todavia no hay foto: no se puede validar un display del
            # que no se guarda evidencia.
            return False
        self.serial_lcd = t
        self.origen_serial = "ESCANER"
        self._poner(VALIDA_LCD, f"Validando {self.parte_txt} {t}...", AZUL)
        self._encolar(("lcd", t))
        return True

    def tecla(self, k):
        """ENTER/ESPACIO confirma un final; X cancela en cualquier momento."""
        if not self.disponible:
            return False
        if k in (ord("x"), ord("X")) and self.activa():
            self.cancelar()
            return True
        if k in (13, 32) and self.estado in (HECHO, ERROR):
            self.cancelar(motivo=None)
            return True
        return False

    # ------------------------------------------------------------------
    def sin_tapete(self):
        """El bucle perdio los 4 marcadores. Decide la secuencia, no el bucle.

        Quien mira el video no puede saber si perder el tapete significa
        'abandonaron' o 'esto es exactamente lo que les pedimos que hicieran',
        porque depende del punto de la secuencia. Aqui si se sabe:

          - En GIFTCARD la pieza deberia seguir puesta: si desaparece, se
            abandono. Se cancela.
          - De PIDE_LCD en adelante, la pieza SALE porque se lo hemos pedido.
            Cancelar ahi tira una secuencia que ya valido la unidad y ya tiene
            la foto del housing, y el operario ve que el programa 'se le
            olvido'. Solo se cancela tras un abandono largo de verdad.

        El modo terminal nunca cancelo por esto; la app si, y esa diferencia
        era un fallo: sacar el housing bastaba para perder la secuencia."""
        if not self.disponible or not self.activa():
            return
        if self.estado == GIFTCARD:
            self.cancelar(motivo=None)
            return
        self._sin_tapete += 1
        if self._sin_tapete >= FRAMES_SIN_TAPETE_CANCELA:
            self.cancelar(motivo=None)

    def _leer_lcd(self, warped):
        """Lee el codigo del display: primero en crudo, luego en el rectificado.

        El orden importa. El rectificado esta remuestreado y, con camara 4K,
        ademas reducido al lienzo canonico; el simbolo del display mide unos
        48 px de lado ahi, que para sus ~20 modulos son 2,4 px por modulo
        cuando el lector necesita 4. El frame crudo trae los pixeles que dio el
        sensor, sin esa perdida.

        No sustituye al rectificado, se le adelanta: si en crudo no sale -o no
        hay frame, o faltan marcadores- se intenta igual sobre el rectificado.
        Son dos oportunidades por fotograma en vez de una."""
        if self._frame is not None:
            r = roi_qr.leer_crudo(self._frame, self._zona_lcd,
                                  self._centros, self._tam)
            if r:
                return r
        return roi_qr.leer(warped, self._zona_lcd)

    def procesar(self, warped, estable, es_pasa, hay_pieza=True,
                 frame=None, centros=None):
        """Llamar en cada frame con rectificado valido.

        `frame` y `centros` son opcionales: con ellos se puede leer el codigo
        del display sobre los pixeles del sensor. Sin ellos todo funciona igual,
        solo que se lee unicamente sobre el rectificado."""
        self._frame = frame
        self._centros = centros
        self._tam = tuple(self.cfg["image_size"])
        if not self.disponible or self._ocupado:
            return
        self._sin_tapete = 0
        e = self.estado

        if e in (HECHO, ERROR):
            # Antes habia que pulsar ENTER. Eso obliga al operario a soltar la
            # pieza, buscar el teclado y volver, en el momento en que ya tiene
            # las dos manos ocupadas. Y en la aplicacion muchas veces ni hay un
            # teclado a mano.
            #
            # El gesto que de verdad confirma que leyo el mensaje es RETIRAR la
            # unidad, que ademas es lo siguiente que tiene que hacer. Asi que
            # ese es el disparador: mensaje grande en el centro, se va cuando la
            # pieza sale. Con un minimo de frames delante para que no se limpie
            # antes de que a nadie le de tiempo de leerlo.
            self._visto += 1
            if e == ERROR:
                minimo, limite = FRAMES_MINIMOS_ERROR, FRAMES_LIMITE_ERROR
            else:
                minimo, limite = FRAMES_MINIMOS_HECHO, FRAMES_LIMITE_HECHO
            # Dos salidas: la rapida, cuando se ve que la pieza salio; y el
            # limite duro, que no depende de ninguna deteccion. La segunda es la
            # que garantiza que esto no se pueda quedar trabado.
            if self._visto >= limite or (self._visto >= minimo and not hay_pieza):
                self.cancelar(motivo=None)
            return

        if e == INACTIVO:
            # Arranca solo con un PASA estable. Un PASA que aun parpadea no es
            # una pieza terminada, es una pieza que el operario esta acomodando.
            if estable and es_pasa:
                self._poner(GIFTCARD, "Acerque el giftcard al area marcada", AMARILLO)
            return

        if e == GIFTCARD:
            gcn, crudo = roi_qr.gcn_en(warped, self._zona_housing)
            if gcn:
                self.gcn = gcn
                self.foto_housing = warped.copy()   # limpio, antes de dibujar nada
                self._poner(VALIDANDO, f"Validando unidad {gcn}...", AZUL)
                self._encolar(("unidad", gcn))
            elif crudo:
                self._poner(GIFTCARD,
                            f"Ese codigo no es un giftcard valido: {crudo[:28]}",
                            AMARILLO)
            return

        if e == PIDE_LCD and not self._zona_lcd:
            # La etapa del display no esta configurada. Se avisa AQUI y no al
            # arrancar: negarse a empezar por esto dejaria sin probar la parte
            # del housing, que puede estar perfectamente lista. Asi se valida la
            # unidad, se captura su foto, y el corte queda donde de verdad falta
            # algo, con la instruccion de que hacer.
            self._poner(ERROR,
                        f"Falta marcar la zona del codigo del "
                        f"{self.parte_txt} para '{self.receta}'. La evidencia "
                        f"del housing NO se registro.",
                        ROJO)
            return

        if e == PIDE_LCD:
            # DOS caminos para avanzar, y el segundo existe porque el primero
            # solo no basta.
            #
            # El primero es el limpio: la pieza sale del fixture. Hace falta
            # porque si no esperaramos a que salga, el mismo housing seguiria en
            # cuadro y se le tomaria la foto como si fuera el display.
            #
            # El problema es que `hay_pieza` mira las ROIs de tornillo, y con el
            # display puesto esas ROIs ven superficie lisa: basta que UNA vote
            # 'dudoso' para que `hay_pieza` siga siendo cierto y la secuencia se
            # quede clavada aqui sin buscar nunca el codigo. Paso en la primera
            # prueba real.
            #
            # El segundo camino cierra ese hueco: si ya se ve un codigo en la
            # zona del display, eso ES la prueba de que el housing salio. Ninguna
            # lectura de esa zona puede venir de un housing.
            self._visto += 1
            if (not hay_pieza
                    or self._leer_lcd(warped)
                    or self._visto >= FRAMES_LIMITE_PIDE_LCD):
                self._quieto = 0
                self._sin_leer = 0
                self._poner(QR_LCD, f"Coloque el {self.parte_txt} y acerque "
                                    f"su codigo", AMARILLO)
            return

        if e == ESPERA_SCAN:
            # La camara ya se rindio, pero se sigue intentando: si el codigo
            # aparece por su cuenta mientras el operario busca el lector, mejor.
            codigos = self._leer_lcd(warped)
            if codigos:
                self.serial_lcd = codigos[0].strip().upper()
                self.origen_serial = "CAMARA"
                self._poner(VALIDA_LCD,
                            f"Validando {self.parte_txt} "
                            f"{self.serial_lcd}...", AZUL)
                self._encolar(("lcd", self.serial_lcd))
                return
            self._visto += 1
            transcurrido = time.time() - self._t_estado
            faltan = max(1, int(SEG_ESPERA_SCAN - transcurrido) + 1)
            if transcurrido >= SEG_ESPERA_SCAN:
                self._poner(REGISTRANDO,
                            f"Guardando SIN serial del {self.parte_txt}...",
                            AZUL)
                self._encolar(("guardar", None))
                return
            if self._visto % 10 == 0:
                self._poner(ESPERA_SCAN,
                            f"No se pudo leer el codigo del {self.parte_txt}. "
                            f"ESCANEALO con el lector, o espera {faltan}s y se "
                            f"guarda sin el.", AMARILLO)
            return

        if e == QR_LCD:
            # Foto del display lista desde el principio: asi un escaneo puede
            # entrar en cualquier momento y ya hay evidencia que guardar. Se
            # refresca mientras no haya serial, para que sea la mas reciente.
            if self.serial_lcd is None:
                self.foto_lcd = warped.copy()
            codigos = self._leer_lcd(warped)
            if codigos:
                self._quieto += 1
                self._sin_leer = 0
                if self._quieto >= FRAMES_QUIETO_LCD:
                    self.serial_lcd = codigos[0].strip().upper()
                    self.origen_serial = "CAMARA"
                    self.foto_lcd = warped.copy()
                    self._poner(VALIDA_LCD,
                                f"Validando {self.parte_txt} "
                                f"{self.serial_lcd}...", AZUL)
                    self._encolar(("lcd", self.serial_lcd))
                else:
                    self._poner(QR_LCD, f"{self.parte_txt.capitalize()} "
                                        f"detectado, no lo mueva...", AMARILLO)
            else:
                self._quieto = 0
                # Contar los intentos permite decir algo util cuando no aparece:
                # sin esto el operario solo ve "coloque el display" para siempre
                # y no sabe si el sistema esta mirando donde debe.
                self._sin_leer = getattr(self, "_sin_leer", 0) + 1
                if time.time() - self._t_estado >= SEG_RENDIRSE_CAMARA:
                    # Se captura la foto AHORA: el display esta puesto y quieto,
                    # que es justo lo que tiene que quedar de evidencia. Si se
                    # esperase al final, el operario ya podria estar moviendolo.
                    self.foto_lcd = warped.copy()
                    self._poner(ESPERA_SCAN,
                                f"No se pudo leer el codigo del "
                                f"{self.parte_txt} con la camara. ESCANEALO con "
                                f"el lector, o espera y se guarda sin el.",
                                AMARILLO)
                    return
                if (self._sin_leer >= FRAMES_AVISO_SIN_LEER
                        and self._sin_leer % FRAMES_AVISO_SIN_LEER == 0):
                    seg = int(self._sin_leer / 15)
                    self._poner(QR_LCD,
                                f"No se ve el codigo del {self.parte_txt} "
                                f"en su zona "
                                f"({seg}s buscando en {self._zona_lcd}). "
                                f"Compruebala con: python3 marcar_zona_qr.py "
                                f"--receta \"{self.receta}\" --etapa lcd --probar"
                                f"   |   X para cancelar",
                                ROJO)
            return

    # ------------------------------------------------------------------
    def _encolar(self, tarea):
        with self._lock:
            self._tarea = tarea
            self._ocupado = True

    def _trabajar(self):
        while not self._parar:
            with self._lock:
                tarea = self._tarea
                self._tarea = None
            if tarea is None:
                time.sleep(0.02)
                continue
            try:
                self._ejecutar(tarea)
            except Exception as e:
                self._poner(ERROR, f"Error inesperado: {e}", ROJO)
            finally:
                self._ocupado = False

    def _ejecutar(self, tarea):
        que, dato = tarea
        try:
            if que == "unidad":
                self.datos_unidad = roi_db.verificar_unidad(dato,
                                                            etapa=self.etapa)
                aviso = ""
                # Repetir un cierre esta PERMITIDO: la misma unidad puede volver
                # con otra pieza. Solo se avisa, y se avisa de lo que de verdad
                # es una repeticion -esta misma etapa-, no del otro lado.
                pe = self.datos_unidad.get("previos_etapa")
                if pe is None:
                    pe = self.datos_unidad.get("registros_previos") or 0
                if pe:
                    aviso = (f" (ya cerrada {pe} vez en esta etapa; se guarda "
                             f"otra)" if pe == 1 else
                             f" (ya cerrada {pe} veces en esta etapa; se guarda "
                             f"otra)")
                self._poner(PIDE_LCD,
                            f"Housing OK - {dato}{aviso}. RETIRE el housing "
                            f"y coloque el {self.parte_txt}",
                            VERDE)

            elif que == "lcd":
                if self.parte != "DISPLAY":
                    # PRD_LinkMainPartToUnit empareja displays: solo el display
                    # tiene emparejamiento que comprobar ahi. Comparar un serial
                    # de backglass contra la columna LCD daria "no coincide"
                    # SIEMPRE, y ese aviso constante acabaria ensenando al
                    # operario a ignorar los avisos -incluido el dia que uno sea
                    # de verdad-.
                    self.aviso_lcd = None
                    self._poner(REGISTRANDO, "Registrando evidencia...", AZUL)
                    self._encolar(("guardar", None))
                    return
                r = roi_db.verificar_lcd(self.gcn, dato,
                                         exigir_emparejamiento=self.exigir_emparejamiento)
                # El aviso se guarda y se ensena al final, junto al veredicto.
                # No corta: en este proceso se captura evidencia antes del
                # cierre definitivo y el emparejamiento no se valida. Que no
                # coincida es informacion util, no un motivo para perder las
                # dos fotos.
                self.aviso_lcd = None if r.get("validado") else r.get("aviso")
                if self.aviso_lcd:
                    print(f"[cierre] {self.aviso_lcd}")
                self._poner(REGISTRANDO, "Registrando evidencia...", AZUL)
                self._encolar(("guardar", None))

            elif que == "guardar":
                b64h, nh, _ = roi_db.a_base64(self.foto_housing, 90)
                b64l, nl, _ = roi_db.a_base64(self.foto_lcd, 90)
                self.resultado = roi_db.guardar_cierre(
                    self.gcn, b64h, b64l, proceso="CLOSING", verificar=False,
                    serial_lcd=self.serial_lcd,
                    origen=self.origen_serial,
                    estacion=platform.node(),
                    etapa=self.etapa, parte=self.parte)
                self.sin_serial = self.serial_lcd is None
                if self.sin_serial:
                    # Se avisa en VERDE, no en rojo: la unidad SI quedo
                    # registrada con sus dos fotos, que es lo que importa. Lo
                    # que falta es la validacion del emparejamiento, y eso se
                    # dice sin dar a entender que hubo un fallo.
                    self._poner(HECHO,
                                f"UNIDAD CERRADA - {self.gcn} "
                                f"(Id {self.resultado['id']}) - SIN serial de "
                                f"{self.parte_txt}: no se pudo leer ni escanear",
                                VERDE)
                    print(f"[cierre] SIN SERIAL DE {self.parte} - {self.gcn} "
                          f"Id {self.resultado['id']}")
                else:
                    extra = f"  -  {self.aviso_lcd}" if self.aviso_lcd else ""
                    self._poner(HECHO,
                                f"UNIDAD CERRADA - {self.gcn} "
                                f"(Id {self.resultado['id']}){extra}",
                                VERDE)
                print(f"[cierre] Registrado: {self.resultado}")

        except Exception as e:
            codigo = getattr(e, "codigo", "ERROR")
            self._poner(ERROR, f"{codigo}: {e}", ROJO)
            print(f"[cierre] {codigo}: {e}")

    # ------------------------------------------------------------------
    def grande(self):
        """(texto, color) para el CENTRO de la pantalla, o None.

        La banda de abajo sirve para leer el detalle, pero hay tres momentos en
        los que el operario no esta leyendo nada: tiene las manos en la pieza y
        lo unico que necesita saber es si sigue o se detiene. Para esos tres, un
        cartel en el medio, en grande, de un solo color.

        El detalle NO se repite aqui. Arriba va el veredicto -verde o rojo- y
        abajo, en la banda, el porque. Meter el texto largo en el centro tapa la
        pieza justo cuando hay que mirarla."""
        if not self.disponible or not self.activa():
            return None
        if self.estado == PIDE_LCD:
            return (f"RETIRE EL HOUSING\nCOLOQUE EL {self.parte}", VERDE)
        if self.estado == ESPERA_SCAN:
            return (f"ESCANEA EL CODIGO\nDEL {self.parte}", AMARILLO)
        if self.estado == HECHO:
            if self.sin_serial:
                return (f"UNIDAD CERRADA\nSIN SERIAL DE {self.parte}", AMARILLO)
            return ("UNIDAD CERRADA\nPUEDE RETIRARLA", VERDE)
        if self.estado == ERROR:
            return ("ALGO ANDA MAL\nRETIRE LA UNIDAD", ROJO)
        return None

    def dibujar(self, vista, escala=1.0, grande=True):
        """Banda de estado del cierre y recuadro de la zona que se esta mirando.

        `grande=False` deja fuera el cartel del centro. Lo usa la aplicacion,
        que lo pinta ella misma con texto de verdad: dibujarlo aqui tambien
        saldria dos veces, uno encima del otro."""
        if not self.disponible or not self.activa():
            return vista
        h, w = vista.shape[:2]

        zona = None
        if self.estado == GIFTCARD:
            zona = self._zona_housing
        elif self.estado in (QR_LCD, VALIDA_LCD):
            zona = self._zona_lcd
        if zona:
            x0, y0, x1, y1 = (int(v * escala) for v in zona)
            cv2.rectangle(vista, (x0, y0), (x1, y1), self.color, 3)

        # Banda inferior: el operario mira la pieza, no la esquina de la pantalla.
        # El mensaje se parte en lineas por PALABRAS y no se recorta: el aviso mas
        # importante -el display que no corresponde- es tambien el mas largo, y
        # cortarlo dejaria fuera justo la parte que dice que hacer.
        # El cartel del centro, para que el modo terminal vea lo mismo que la
        # aplicacion y no haya que arreglar esto dos veces.
        g = self.grande() if grande else None
        if g:
            texto, col = g
            filas = texto.split("\n")
            esc = max(1.2, w / 1400.0 * 2.2)
            gruesa = max(2, int(esc * 2))
            medidas = [cv2.getTextSize(f, cv2.FONT_HERSHEY_SIMPLEX, esc, gruesa)[0]
                       for f in filas]
            alto_f = max(m[1] for m in medidas) + 22
            y0 = h // 2 - (alto_f * len(filas)) // 2
            ancho_c = max(m[0] for m in medidas) + 80
            capa = vista.copy()
            cv2.rectangle(capa, (w // 2 - ancho_c // 2, y0 - 50),
                          (w // 2 + ancho_c // 2, y0 + alto_f * len(filas) + 10),
                          (17, 14, 13), -1)
            cv2.addWeighted(capa, 0.72, vista, 0.28, 0, vista)
            for i, f in enumerate(filas):
                tw = medidas[i][0]
                cv2.putText(vista, f, (w // 2 - tw // 2, y0 + alto_f * (i + 1) - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, esc, col, gruesa, cv2.LINE_AA)

        lineas = _partir(self.mensaje, 74, maximo=3)
        alto = 26 + 34 * len(lineas)
        y = h - alto
        cv2.rectangle(vista, (0, y), (w, h), (17, 14, 13), -1)
        cv2.rectangle(vista, (0, y), (w, y + 5), self.color, -1)
        for i, ln in enumerate(lineas):
            cv2.putText(vista, ln, (18, y + 36 + 34 * i),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, self.color, 2, cv2.LINE_AA)
        return vista

    def cerrar(self):
        self._parar = True
