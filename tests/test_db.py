# -*- coding: utf-8 -*-
"""Decisiones 1, 2, 3, 4 y 7: como se habla con SQL Server."""

import pytest

import roi_db


GCN = "CX666666"
IMEI = "353431654144997"


def reglas_unidad(col_etapa=True, previos=0, previos_etapa=0):
    return [
        ("COL_LENGTH('dbo.PRD_ClosingEvidence', 'Etapa')",
         (60,) if col_etapa else (None,)),
        ("FROM dbo.PRD_ClosingEvidence WHERE GCN=%s AND Etapa=%s", (previos_etapa,)),
        ("COUNT(*), MAX(Date) FROM dbo.PRD_ClosingEvidence", (previos, None)),
        ("FROM dbo.SPC_Receiving WHERE GCN=%s", (IMEI,)),
        ("HistoryUnitProcess", (853086,)),
    ]


# --- Decision 2: los previos se cuentan por etapa y nunca bloquean ----------

def test_previos_por_etapa_se_cuentan(base_falsa):
    con = base_falsa(reglas_unidad(previos=2, previos_etapa=1))
    d = roi_db.verificar_unidad(GCN, con=con, etapa="IP14 - BACKGLASS")
    assert d["registros_previos"] == 2
    assert d["previos_etapa"] == 1
    assert d["imei"] == IMEI


def test_previos_no_bloquean(base_falsa):
    """Una unidad con muchos cierres previos se sigue pudiendo cerrar."""
    con = base_falsa(reglas_unidad(previos=7, previos_etapa=5))
    d = roi_db.verificar_unidad(GCN, con=con, etapa="IP14 - DISPLAY")
    assert d["previos_etapa"] == 5


# --- Decision 7: COL_LENGTH antes de consultar una columna que puede faltar --

def test_columna_etapa_ausente_no_es_error(base_falsa):
    con = base_falsa(reglas_unidad(col_etapa=False, previos=1))
    d = roi_db.verificar_unidad(GCN, con=con, etapa="IP14 - DISPLAY")
    assert d["previos_etapa"] is None
    assert d["registros_previos"] == 1
    # Y no se llego a consultar la columna que no existe.
    assert not con.consultas("AND Etapa=%s")


def test_col_length_se_mira_antes_que_la_columna(base_falsa):
    con = base_falsa(reglas_unidad())
    roi_db.verificar_unidad(GCN, con=con, etapa="X")
    sqls = [s for s, _ in con.ejecutadas]
    i_col = next(i for i, s in enumerate(sqls) if "COL_LENGTH" in s)
    i_eta = next(i for i, s in enumerate(sqls) if "AND Etapa=%s" in s)
    assert i_col < i_eta


# --- Decision 3: el emparejamiento nunca bloquea -----------------------------

def test_serial_que_no_coincide_solo_avisa(base_falsa):
    con = base_falsa([
        ("SELECT TOP 1 LCD FROM dbo.PRD_LinkMainPartToUnit WHERE GCN",
         ("AK5521361891WMW96A12U0120H",)),
        ("UPPER(LCD)", ("CX111111",)),
    ])
    r = roi_db.verificar_lcd(GCN, "FYJ341770HF1WMWADA148RU26B", con=con)
    assert r["validado"] is False
    assert "no coincide" in r["aviso"]
    assert "CX111111" in r["aviso"]


def test_sin_emparejamiento_solo_avisa(base_falsa):
    con = base_falsa([("PRD_LinkMainPartToUnit", None)])
    r = roi_db.verificar_lcd(GCN, "FYJ341770HF1WMWADA148RU26B", con=con)
    assert r["validado"] is False
    assert r["aviso"]


def test_etiqueta_de_otra_unidad_solo_avisa():
    # Camino 1: la etiqueta trae el GCN; no hace falta base.
    r = roi_db.verificar_lcd(GCN, "052|DSM2|LCD|CX413945")
    assert r["validado"] is False and "CX413945" in r["aviso"]


def test_serial_que_coincide_valida(base_falsa):
    con = base_falsa([("PRD_LinkMainPartToUnit WHERE GCN",
                       ("FYJ341770HF1WMWADA148RU26B+8+AA",))])
    r = roi_db.verificar_lcd(GCN, "fyj341770hf1wmwada148ru26b", con=con)
    assert r["validado"] is True


def test_exigir_emparejamiento_si_corta(base_falsa):
    """El modo estricto existe; lo que no puede es ser el de por defecto."""
    con = base_falsa([("PRD_LinkMainPartToUnit WHERE GCN", ("OTRO123456",))])
    with pytest.raises(roi_db.ErrorCierre):
        roi_db.verificar_lcd(GCN, "FYJ341770HF1WMWADA148RU26B", con=con,
                             exigir_emparejamiento=True)


def test_por_defecto_no_se_exige():
    import inspect
    import cierre
    assert inspect.signature(roi_db.verificar_lcd).parameters[
        "exigir_emparejamiento"].default is False
    assert inspect.signature(cierre.Cierre.__init__).parameters[
        "exigir_emparejamiento"].default is False


# --- Decisiones 1 y 4: guardado ----------------------------------------------

def reglas_guardado():
    return [("FROM dbo.PRD_ClosingEvidence WHERE GCN=%s ORDER BY Id DESC",
             (4242, 853086, IMEI, 1_500_000, 1_400_000))]


def test_guardado_sin_callproc_y_con_varchar_max(base_falsa):
    con = base_falsa(reglas_guardado())
    grande = "A" * 1_572_932          # el tamano medido que llegaba en 8000
    r = roi_db.guardar_cierre(GCN, grande, grande, con=con, verificar=False)
    assert r["id"] == 4242
    sql, params = con.ejecutadas[0]
    assert "DECLARE @h VARCHAR(MAX)=%s, @l VARCHAR(MAX)=%s" in sql
    assert "EXEC dbo.SP_Save_PRD_ClosingEvidence" in sql
    assert len(params[0]) == 1_572_932 and len(params[1]) == 1_572_932


def test_extras_despues_del_commit(base_falsa):
    con = base_falsa(reglas_guardado())
    roi_db.guardar_cierre(GCN, "h", "l", con=con, verificar=False,
                          serial_lcd="S", origen="CAMARA", estacion="mac",
                          etapa="IP14 - DISPLAY", parte="DISPLAY")
    sqls = [s for s, _ in con.ejecutadas]
    i_commit = sqls.index("<COMMIT>")
    i_extra = next(i for i, s in enumerate(sqls) if "LCDSerial" in s)
    assert i_commit < i_extra


def test_fallo_de_extras_no_tumba_el_cierre(base_falsa, capsys):
    """Si falta el procedimiento de extras, la evidencia ya esta guardada: el
    resultado vuelve normal y solo se avisa por consola."""
    con = base_falsa(reglas_guardado(), fallar_en="SP_Set_PRD_ClosingEvidence_LCDSerial")
    r = roi_db.guardar_cierre(GCN, "h", "l", con=con, verificar=False,
                              serial_lcd="S", origen="ESCANER", estacion="mac")
    assert r["id"] == 4242
    assert r["serial_anotado"] is False
    assert con.eventos[0] == "commit"            # la fila quedo antes del fallo
    assert "se guardo bien" in capsys.readouterr().out


def test_ningun_callproc_en_el_codigo():
    """Red de seguridad estatica: ni una llamada a callproc en todo el codigo."""
    import os
    import re
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    patron = re.compile(r"\.callproc\s*\(")
    culpables = []
    for sub in ("motor", "app"):
        for n in os.listdir(os.path.join(raiz, sub)):
            if n.endswith(".py"):
                for i, linea in enumerate(open(os.path.join(raiz, sub, n),
                                               encoding="utf-8"), 1):
                    if patron.search(linea.split("#")[0]):
                        culpables.append(f"{sub}/{n}:{i}")
    assert not culpables, culpables


# --- Validaciones de unidad que si deben cortar ------------------------------

def test_gcn_inexistente_corta(base_falsa):
    con = base_falsa([("SPC_Receiving", None)])
    with pytest.raises(roi_db.ErrorCierre) as e:
        roi_db.verificar_unidad(GCN, con=con)
    assert e.value.codigo == "GCN_NO_EXISTE"


def test_recepcion_anulada_sugiere_el_gcn_bueno(base_falsa):
    con = base_falsa([
        ("WHERE Imei=%s AND GCN<>%s", ("CX777777", "2026-09-01")),
        ("FROM dbo.SPC_Receiving WHERE GCN=%s", ("-" + IMEI,)),
    ])
    with pytest.raises(roi_db.ErrorCierre) as e:
        roi_db.verificar_unidad(GCN, con=con)
    assert e.value.codigo == "RECEPCION_ANULADA" and "CX777777" in str(e.value)
