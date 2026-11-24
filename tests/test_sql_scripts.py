# -*- coding: utf-8 -*-
"""Decisiones 2 y 5 sobre los scripts de sql/ (los vigentes, no obsoleto/)."""

import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL = os.path.join(RAIZ, "sql")


def _vigentes():
    for n in sorted(os.listdir(SQL)):
        if n.endswith(".sql"):
            with open(os.path.join(SQL, n), encoding="utf-8", errors="replace") as f:
                yield n, f.read()


def _sin_comentarios(t):
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"--[^\n]*", "", t)


def test_ningun_indice_unico():
    for n, t in _vigentes():
        assert not re.search(r"\bCREATE\s+UNIQUE\b", _sin_comentarios(t), re.I), n


def test_ddl_y_procedimientos_separados():
    """SQL Server valida columnas al compilar: un ALTER TABLE y el procedimiento
    que usa esas columnas no pueden ir en el mismo archivo."""
    for n, t in _vigentes():
        c = _sin_comentarios(t)
        ddl = re.search(r"\bALTER\s+TABLE\b|\bCREATE\s+TABLE\b", c, re.I)
        proc = re.search(r"\b(CREATE|ALTER)\s+(OR\s+ALTER\s+)?PROC(EDURE)?\b", c, re.I)
        assert not (ddl and proc), n
