-- =====================================================================
-- 04c_lados_pendientes.sql - Unidades a las que les falta un lado.
--
-- Como cada etapa es un cierre independiente y pueden hacerse en momentos
-- distintos, una unidad puede quedarse con un solo lado registrado y nadie
-- enterarse. Esta consulta es la que lo ve.
--
-- No distingue los modelos de un solo lado: esos solo tienen DISPLAY y es
-- correcto. Filtra por Etapa si quieres mirar solo los de dos lados.
-- =====================================================================
SELECT
    e.GCN,
    MAX(CASE WHEN e.Parte = 'DISPLAY' THEN 'SI' ELSE NULL END) AS TieneDisplay,
    MAX(CASE WHEN e.Parte = 'BACKGLASS' THEN 'SI' ELSE NULL END) AS TieneBackglass,
    COUNT(*) AS Filas,
    MIN(e.[Date]) AS Primera,
    MAX(e.[Date]) AS Ultima,
    MAX(e.Estacion) AS Estacion
FROM dbo.PRD_ClosingEvidence AS e
WHERE e.[Date] >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE))
GROUP BY e.GCN
HAVING MAX(CASE WHEN e.Parte = 'BACKGLASS' THEN 1 ELSE 0 END)
     <>        MAX(CASE WHEN e.Parte = 'DISPLAY' THEN 1 ELSE 0 END)
ORDER BY MAX(e.[Date]) DESC;
