# -*- coding: utf-8 -*-
"""Decision 13 (patron de GCN) y deduccion de la parte por el nombre."""

import pytest

import cierre
import roi_qr


@pytest.mark.parametrize("gcn", ["CX666666", "R10172", "CX413940", "ABC1234",
                                 "A1234", "XYZ12345678"])
def test_patron_acepta(gcn):
    assert roi_qr.PATRON_GCN.match(gcn)


@pytest.mark.parametrize("gcn", ["cx666666", "CX123", "CX123456789", "ABCD1234",
                                 "CX-6367", "1234567", "CX666666A", ""])
def test_patron_rechaza(gcn):
    assert not roi_qr.PATRON_GCN.match(gcn)


def test_patron_exacto():
    assert roi_qr.PATRON_GCN.pattern == r"^[A-Z]{1,3}\d{4,8}$"


@pytest.mark.parametrize("texto,esperado", [
    ("A02 SM312|REC|PH|CX666666", "CX666666"),
    ("A02 SM312|REC|PH|R10172", "R10172"),
    ("CX413945", "CX413945"),
    ("052|DSM2|LCD|cx413945", "CX413945"),
    ("A02|CX666666|EXTRA-NO-GCN", "CX666666"),   # respaldo: campo extra al final
    ("https://example.com/otra-cosa", None),
    ("", None),
    (None, None),
])
def test_gcn_de_giftcard(texto, esperado):
    assert roi_qr.gcn_de_giftcard(texto) == esperado


# --- La parte se deduce del nombre de la receta ------------------------------

@pytest.mark.parametrize("nombre,parte", [
    ("IP14 - BACKGLASS", "BACKGLASS"),
    ("IP14 - BACK GLASS", "BACKGLASS"),
    ("IP14 - BACK-GLASS", "BACKGLASS"),
    ("ip15 plus - backglass", "BACKGLASS"),
    ("IP14 - DISPLAY", "DISPLAY"),
    ("IP15PM - LCD", "DISPLAY"),
    ("CLOSING - 15PM", "DISPLAY"),      # no dice nada: DISPLAY por defecto
])
def test_parte_por_nombre(nombre, parte):
    etapa, p = cierre._etapa_de_cfg({}, nombre)
    assert p == parte
    assert etapa == nombre.strip().upper()[:30]


def test_bloque_etapa_sobrescribe_al_nombre():
    cfg = {"etapa": {"nombre": "lado b", "parte": "backglass"}}
    assert cierre._etapa_de_cfg(cfg, "IP14 - DISPLAY") == ("LADO B", "BACKGLASS")


def test_parte_invalida_en_bloque_cae_al_nombre():
    cfg = {"etapa": {"parte": "BATERIA"}}
    assert cierre._etapa_de_cfg(cfg, "IP14 - BACKGLASS")[1] == "BACKGLASS"


def test_etapa_cabe_en_la_columna():
    etapa, _ = cierre._etapa_de_cfg({}, "X" * 80)
    assert len(etapa) == 30
