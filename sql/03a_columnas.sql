-- =====================================================================
-- 03a_columnas.sql - PRIMERO ESTE. Solo las columnas.
--
-- POR QUE VA SEPARADO DEL RESTO
-- SQL Server valida los nombres de columna de una tabla QUE YA EXISTE al
-- compilar el procedimiento. Si el ALTER TABLE y el CREATE PROCEDURE llegan en
-- el mismo lote, el procedimiento se compila contra la tabla vieja y falla con
-- "Invalid column name", aunque el ALTER este tres lineas mas arriba.
--
-- El 'GO' deberia separarlos, pero GO no es T-SQL: es una marca que interpreta
-- el cliente, y no todos la parten igual. Dos archivos no dependen de eso.
--
-- Corre este, comprueba la salida, y solo entonces corre 03b.
-- =====================================================================

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_Serial') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_Serial VARCHAR(100) NULL;

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_SerialOrigen') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_SerialOrigen VARCHAR(20) NULL;

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_SerialFecha') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_SerialFecha DATETIME NULL;

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'Estacion') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD Estacion VARCHAR(60) NULL;
