-- =====================================================================
-- 04a_etapas_columnas.sql - PRIMERO ESTE. Solo columnas.
--
-- POR QUE
-- En 14, 14 Plus, 15 y 15 Plus el housing tiene DOS lados con tornillos
-- propios, y cada lado se cierra con una parte distinta: uno con el display y
-- otro con el backglass. Son dos vistas fisicas, con geometrias y
-- calibraciones distintas, asi que son dos recetas y DOS FILAS de evidencia
-- por unidad.
--
-- Dos filas con el mismo GCN ya eran posibles -una unidad puede cerrarse con
-- otro display en otro momento-, asi que no hace falta tocar nada de eso. Lo
-- que hace falta es poder DISTINGUIRLAS: que lado y que parte es cada una.
--
-- Va separado de los procedimientos por lo mismo de la vez pasada: SQL Server
-- valida los nombres de columna al compilar, y GO lo interpreta el cliente, no
-- el servidor.
-- =====================================================================

-- Etapa: el nombre de la receta que produjo esta fila ("IP14 - BACKGLASS").
--        No se inventa una convencion aparte: dos lados son dos recetas, y sus
--        nombres ya las distinguen.
IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'Etapa') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD Etapa VARCHAR(30) NULL;

-- Parte: DISPLAY | BACKGLASS. Que pieza se fotografio en LCD_PhotoBase64.
--        La columna de foto conserva su nombre historico aunque ahora pueda
--        traer un backglass: renombrarla romperia el procedimiento que ya
--        corre en produccion, y un nombre heredado molesta menos que una
--        migracion a medias.
IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'Parte') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD Parte VARCHAR(20) NULL;
