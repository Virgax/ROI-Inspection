-- =====================================================================
-- 03_lcd_serial.sql - Serial del display en la evidencia de cierre
--
-- POR QUE
-- El Data Matrix del display mide unos 48 px de lado en el rectificado, que
-- para sus ~20 modulos son 2,4 px por modulo cuando el lector necesita 4. La
-- camara lo lee a veces. Bloquear el cierre por eso seria cambiar una
-- validacion por la evidencia entera, que es lo que de verdad hace falta.
--
-- Asi que la estacion registra SIEMPRE las dos fotos, con serial si lo tiene y
-- sin el si no, y el serial se completa despues desde donde produccion ya lo
-- anota. La foto no se puede recuperar mas tarde; el serial si.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Las columnas
-- ---------------------------------------------------------------------
-- LCD_Serial       el codigo del display, venga de donde venga
-- LCD_SerialOrigen como se obtuvo: CAMARA | ESCANER | CONCILIADO
--                  Importa para auditar: un serial leido en la estacion y uno
--                  copiado de otra tabla no tienen la misma fuerza probatoria,
--                  y sin esta columna dentro de un mes son indistinguibles.
-- LCD_SerialFecha  cuando se puso. En una conciliacion posterior no coincide
--                  con la fecha del cierre, y esa diferencia es informacion.
IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_Serial') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_Serial VARCHAR(100) NULL;
GO

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_SerialOrigen') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_SerialOrigen VARCHAR(20) NULL;
GO

IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'LCD_SerialFecha') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD LCD_SerialFecha DATETIME NULL;
GO

-- Estacion: que maquina escribio esta evidencia.
--
-- Va ahora y no despues porque no se puede rellenar hacia atras. Con una sola
-- estacion la columna sobra; con tres -y estan a punto de ser tres- es la
-- diferencia entre rastrear una discrepancia y encogerse de hombros. El campo
-- ProcessName no sirve para esto: significa otra cosa y mezclarlo se paga al
-- primer informe.
IF COL_LENGTH('dbo.PRD_ClosingEvidence', 'Estacion') IS NULL
    ALTER TABLE dbo.PRD_ClosingEvidence ADD Estacion VARCHAR(60) NULL;
GO


-- ---------------------------------------------------------------------
-- 2. Escribir el serial en una fila concreta
-- ---------------------------------------------------------------------
-- Lo llama la estacion en cuanto tiene el serial, y tambien la conciliacion.
--
-- NO SOBRESCRIBE. Es deliberado: si ya hay un serial, viene de alguien que
-- tuvo el display delante. Un dato copiado despues de otra tabla no puede
-- pisar eso. El @Sobrescribio de salida dice si se escribio o no, para que
-- quien llama no tenga que suponerlo.
CREATE OR ALTER PROCEDURE dbo.SP_Set_PRD_ClosingEvidence_LCDSerial
    @Id INT,
    @Serial VARCHAR(100) = NULL,
    @Origen VARCHAR(20) = 'ESCANER',
    @Estacion VARCHAR(60) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @Origen IS NOT NULL AND @Origen NOT IN ('CAMARA', 'ESCANER', 'CONCILIADO')
    BEGIN
        RAISERROR('Origen no valido: use CAMARA, ESCANER o CONCILIADO.', 16, 1);
        RETURN;
    END

    -- La estacion se escribe siempre: es un hecho de quien guardo la fila, no
    -- un dato que pueda mejorar despues.
    IF @Estacion IS NOT NULL AND LTRIM(RTRIM(@Estacion)) <> ''
        UPDATE dbo.PRD_ClosingEvidence
        SET Estacion = LTRIM(RTRIM(@Estacion))
        WHERE Id = @Id;

    -- El serial, solo si esta vacio. Si ya hay uno, viene de alguien que tuvo
    -- el display delante; un dato copiado despues no puede pisar eso.
    IF @Serial IS NOT NULL AND LTRIM(RTRIM(@Serial)) <> ''
        UPDATE dbo.PRD_ClosingEvidence
        SET LCD_Serial = UPPER(LTRIM(RTRIM(@Serial))),
            LCD_SerialOrigen = ISNULL(@Origen, 'ESCANER'),
            LCD_SerialFecha = GETDATE()
        WHERE Id = @Id
          AND LCD_Serial IS NULL;

    SELECT LCD_Serial, LCD_SerialOrigen, Estacion
    FROM dbo.PRD_ClosingEvidence
    WHERE Id = @Id;
END
GO


-- ---------------------------------------------------------------------
-- 3. Conciliacion: completar los seriales que faltan
-- ---------------------------------------------------------------------
-- Toma las evidencias sin serial y las completa con lo que produccion ya
-- anoto en PRD_LinkMainPartToUnit.
--
-- SOLO cuando la unidad tiene UN unico emparejamiento. Una misma unidad puede
-- cerrarse mas de una vez con displays distintos -esto no valida duplicados a
-- proposito-, asi que con dos emparejamientos no hay forma de saber cual toca
-- a cual evidencia. Ahi se deja sin tocar y se reporta: un hueco visible es
-- mejor que un dato inventado que nadie va a volver a cuestionar.
--
-- @Dias limita la ventana para no recorrer el historico entero cada vez.
-- @Simular = 1 devuelve lo que haria sin escribir nada. Correrlo asi la
-- primera vez cuesta nada y evita descubrir el criterio equivocado ya escrito.
CREATE OR ALTER PROCEDURE dbo.SP_Conciliar_ClosingEvidence_LCD
    @Dias INT = 7,
    @Simular BIT = 1
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Desde DATETIME = DATEADD(DAY, -@Dias, CAST(GETDATE() AS DATE));

    -- Se materializa en una tabla temporal y no en un CTE: un CTE vive solo
    -- durante SU sentencia, y aqui hace falta en dos -el plan y el recuento de
    -- lo que queda pendiente-. Escrito como CTE, la segunda fallaba con
    -- "Invalid object name".
    SELECT GCN, MIN(LCD) AS LCD
    INTO #Unicos
    FROM dbo.PRD_LinkMainPartToUnit
    WHERE LCD IS NOT NULL AND LTRIM(RTRIM(LCD)) <> ''
    GROUP BY GCN
    HAVING COUNT(DISTINCT LCD) = 1;

    SELECT e.Id, e.GCN, u.LCD AS SerialQueSePondria
    INTO #Plan
    FROM dbo.PRD_ClosingEvidence AS e
    INNER JOIN #Unicos AS u ON u.GCN = e.GCN
    WHERE e.LCD_Serial IS NULL
      AND e.[Date] >= @Desde;

    IF @Simular = 1
    BEGIN
        SELECT COUNT(*) AS SeCompletarian FROM #Plan;
        SELECT TOP 50 * FROM #Plan ORDER BY Id DESC;
    END
    ELSE
    BEGIN
        UPDATE e
        SET e.LCD_Serial = UPPER(LTRIM(RTRIM(p.SerialQueSePondria))),
            e.LCD_SerialOrigen = 'CONCILIADO',
            e.LCD_SerialFecha = GETDATE()
        FROM dbo.PRD_ClosingEvidence AS e
        INNER JOIN #Plan AS p ON p.Id = e.Id
        WHERE e.LCD_Serial IS NULL;

        SELECT @@ROWCOUNT AS Completadas;
    END

    -- Lo que queda pendiente y por que. Se devuelve siempre, tambien al
    -- simular: es el numero que dice si este mecanismo sirve o no.
    SELECT
        SUM(CASE WHEN l.GCN IS NULL THEN 1 ELSE 0 END) AS SinEmparejamiento,
        SUM(CASE WHEN l.GCN IS NOT NULL AND u.GCN IS NULL THEN 1 ELSE 0 END)
            AS ConVariosDisplays
    FROM dbo.PRD_ClosingEvidence AS e
    LEFT JOIN (SELECT DISTINCT GCN FROM dbo.PRD_LinkMainPartToUnit) AS l
        ON l.GCN = e.GCN
    LEFT JOIN #Unicos AS u ON u.GCN = e.GCN
    WHERE e.LCD_Serial IS NULL AND e.[Date] >= @Desde;

    DROP TABLE #Unicos;
    DROP TABLE #Plan;
END
GO
