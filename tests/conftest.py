# -*- coding: utf-8 -*-
"""
Infraestructura comun de las pruebas.

Las pruebas fijan las decisiones de la seccion 3 de docs/HANDOFF_CLAUDE_CODE.md.
Cada una se tomo despues de que algo se rompiera en planta, y romperlas no da
error: da una estacion que sigue trabajando y juzga mal. Si una prueba de aqui
falla tras un cambio, lo primero es leer el comentario del codigo que protege,
no ajustar la prueba.

PATRONES QUE YA SE PROBARON A MANO Y FUNCIONAN
- Qt sin pantalla: QT_QPA_PLATFORM=offscreen (se fija antes de importar Qt).
- Base falsa: un cursor que responde segun el TEXTO de la consulta (`BaseFalsa`).
- `Cierre` con un `roi_db` falso que deja `disponible=True` sobre un objeto
  RECIEN creado. Forzar `disponible` sobre uno creado con activo=False, o
  reutilizar uno que ya encolo una tarea, da falsos fallos: paso tres veces.
- Configs pasados por `inspector.cargar_config`, no crudos: el cierre recibe
  `cfg["zonas"]`, que solo existe despues de cargarlo.
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("motor", "app"):
    ruta = os.path.join(RAIZ, sub)
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Base de datos falsa
# ---------------------------------------------------------------------------

class CursorFalso:
    """Cursor que contesta segun el texto de la consulta.

    `reglas` es una lista de (fragmento, respuesta). La primera regla cuyo
    fragmento aparece en la consulta decide. `respuesta` puede ser una fila
    (tupla), None, o una funcion (sql, params) -> fila. Todo lo ejecutado queda
    en `ejecutadas` para poder comprobar QUE se pregunto y en que orden."""

    def __init__(self, base):
        self.base = base
        self._ultima = None

    def execute(self, sql, params=None):
        self.base.ejecutadas.append((sql, params))
        if self.base.fallar_en and self.base.fallar_en in sql:
            raise RuntimeError(f"fallo simulado en: {self.base.fallar_en}")
        self._ultima = None
        for frag, resp in self.base.reglas:
            if frag in sql:
                self._ultima = resp(sql, params) if callable(resp) else resp
                return
        self._ultima = None

    def fetchone(self):
        return self._ultima

    def fetchall(self):
        return []

    def callproc(self, *a, **k):
        # Decision 1: nunca callproc. Si alguien lo usa, la prueba revienta.
        raise AssertionError("cur.callproc() prohibido: pymssql recorta a 8000 caracteres")


class BaseFalsa:
    def __init__(self, reglas=None, fallar_en=None):
        self.reglas = list(reglas or [])
        self.fallar_en = fallar_en
        self.ejecutadas = []
        self.eventos = []          # 'commit' / 'rollback' / 'close' en orden

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.eventos.append("commit")
        self.ejecutadas.append(("<COMMIT>", None))

    def rollback(self):
        self.eventos.append("rollback")

    def close(self):
        self.eventos.append("close")

    def consultas(self, fragmento):
        return [s for s, _ in self.ejecutadas if fragmento in s]


@pytest.fixture
def base_falsa():
    return BaseFalsa


# ---------------------------------------------------------------------------
# Recetas
# ---------------------------------------------------------------------------

def config_minimo(nombre="IP15PM - DISPLAY", con_giftcard=True, con_parte=True,
                  tipo_parte="LCD", image_size=(1600, 900)):
    puntos = [
        {"id": "T01", "x": 200, "y": 200, "tipo": "Tornillo"},
        {"id": "T02", "x": 400, "y": 200, "tipo": "Tornillo"},
        {"id": "T03", "x": 600, "y": 200, "roi_w": 80, "roi_h": 50, "tipo": "Tornillo"},
    ]
    if con_giftcard:
        puntos.append({"id": "G01", "x": 800, "y": 600, "roi_w": 200,
                       "roi_h": 200, "tipo": "Giftcard"})
    if con_parte:
        puntos.append({"id": "L01", "x": 1200, "y": 600, "roi_w": 150,
                       "roi_h": 150, "tipo": tipo_parte})
    return {"version": 2, "proceso": nombre, "modelo": "iPhone",
            "image_size": list(image_size), "roi_size": 60, "puntos": puntos}


@pytest.fixture
def receta(tmp_path, monkeypatch):
    """Crea una receta en una carpeta temporal y devuelve una funcion que la
    carga con `inspector.cargar_config`, como hace el programa de verdad."""
    import inspector
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(inspector, "_RECETA", None)

    def crear(nombre="IP15PM - DISPLAY", **kw):
        cfg = config_minimo(nombre, **kw)
        d = tmp_path / "procesos" / nombre
        d.mkdir(parents=True, exist_ok=True)
        (d / "config_rois.json").write_text(json.dumps(cfg), encoding="utf-8")
        inspector.set_receta(nombre)
        return inspector.cargar_config()

    return crear
