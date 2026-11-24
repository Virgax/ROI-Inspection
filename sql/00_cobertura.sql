-- =====================================================================
-- 00_cobertura.sql - ¿Aparece el serial del display por su cuenta?
--
-- CLOSING es un proceso NUEVO: no hay historico que medir. Asi que esto no se
-- calcula, se observa, y se observa desde PRD_ClosingEvidence -que si va a
-- tener filas desde el primer dia- y no desde el historico de procesos.
--
-- LA PREGUNTA
-- La estacion guarda siempre las dos fotos. El serial del display lo pone la
-- camara cuando puede, el escaner cuando lo haya, y si no, queda vacio para
-- completarlo despues desde donde produccion lo anote.
--
-- Todo eso ultimo depende de UN supuesto sin comprobar: que al escanear el
-- display en el sistema de produccion, el emparejamiento acabe en
-- PRD_LinkMainPartToUnit. Es razonable -es la tabla que empareja partes con
-- unidades- pero es un supuesto, y de el depende que la conciliacion complete
-- el 95% o el 0%.
--
-- CUANDO CORRERLO
-- Al final del primer dia con unidades de verdad, y otra vez al dia siguiente.
-- Dos veces porque importa tanto SI aparece como CUANDO: si el emparejamiento
-- lo escribe produccion despues del cierre, la conciliacion tiene que correr
-- mas tarde, no en el momento de guardar.
-- =====================================================================

-- 1. De lo que hemos registrado, ¿cuanto tiene serial y de donde salio?
SELECT
    COUNT(*) AS Evidencias,
    SUM(CASE WHEN e.LCD_Serial IS NOT NULL THEN 1 ELSE 0 END) AS ConSerial,
    SUM(CASE WHEN e.LCD_SerialOrigen = 'CAMARA' THEN 1 ELSE 0 END) AS PorCamara,
    SUM(CASE WHEN e.LCD_SerialOrigen = 'ESCANER' THEN 1 ELSE 0 END) AS PorEscaner,
    SUM(CASE WHEN e.LCD_SerialOrigen = 'CONCILIADO' THEN 1 ELSE 0 END)
        AS Conciliados,
    SUM(CASE WHEN e.LCD_Serial IS NULL THEN 1 ELSE 0 END) AS SinSerial
FROM dbo.PRD_ClosingEvidence AS e
WHERE e.[Date] >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE));
GO

-- 2. EL NUMERO QUE DECIDE: de las que quedaron sin serial, ¿a cuantas les
--    aparecio el emparejamiento por su cuenta?
--
--    Alto  -> la conciliacion sirve y se programa.
--    Bajo  -> produccion no escribe ahi, y entonces el serial tiene que salir
--             de la estacion: escaner, y no hay vuelta de hoja.
SELECT
    COUNT(*) AS SinSerial,
    SUM(CASE WHEN l.GCN IS NOT NULL THEN 1 ELSE 0 END) AS YaEmparejadas,
    CAST(100.0 * SUM(CASE WHEN l.GCN IS NOT NULL THEN 1 ELSE 0 END)
         / NULLIF(COUNT(*), 0) AS DECIMAL(5, 1)) AS PorcentajeRecuperable
FROM dbo.PRD_ClosingEvidence AS e
LEFT JOIN (SELECT DISTINCT GCN FROM dbo.PRD_LinkMainPartToUnit) AS l
    ON l.GCN = e.GCN
WHERE e.LCD_Serial IS NULL
  AND e.[Date] >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE));
GO

-- 3. Unidad por unidad, para ver el detalle y el retraso con que aparece.
SELECT TOP 50
    e.Id,
    e.GCN,
    e.[Date],
    e.[Time],
    ISNULL(e.LCD_SerialOrigen, '(sin serial)') AS Origen,
    CASE WHEN l.GCN IS NULL THEN 'NO' ELSE 'SI' END AS EmparejadaEnProduccion,
    l.LCD AS SerialSegunProduccion
FROM dbo.PRD_ClosingEvidence AS e
OUTER APPLY (
    SELECT TOP 1 p.GCN, p.LCD
    FROM dbo.PRD_LinkMainPartToUnit AS p
    WHERE p.GCN = e.GCN AND p.LCD IS NOT NULL
) AS l
WHERE e.[Date] >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE))
ORDER BY e.Id DESC;
GO
