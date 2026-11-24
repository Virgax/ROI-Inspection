-- =====================================================================
-- 04b_procedimiento.sql - SEGUNDO ESTE, despues de 04a.
--
-- Sustituye al de 03b anadiendo @Etapa y @Parte. La estacion ya llama con los
-- seis parametros, asi que mientras esto no corra, el serial y la estacion NO
-- se anotan: la evidencia se guarda igual -las dos fotos estan a salvo- y la
-- consola avisa de que falta este script.
-- =====================================================================
CREATE OR ALTER PROCEDURE dbo.SP_Set_PRD_ClosingEvidence_LCDSerial
    @Id INT,
    @Serial VARCHAR(100) = NULL,
    @Origen VARCHAR(20) = 'ESCANER',
    @Estacion VARCHAR(60) = NULL,
    @Etapa VARCHAR(30) = NULL,
    @Parte VARCHAR(20) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @Origen IS NOT NULL AND @Origen NOT IN ('CAMARA', 'ESCANER', 'CONCILIADO')
    BEGIN
        RAISERROR('Origen no valido: use CAMARA, ESCANER o CONCILIADO.', 16, 1);
        RETURN;
    END

    IF @Parte IS NOT NULL AND @Parte NOT IN ('DISPLAY', 'BACKGLASS')
    BEGIN
        RAISERROR('Parte no valida: use DISPLAY o BACKGLASS.', 16, 1);
        RETURN;
    END

    -- Estacion, etapa y parte son hechos de quien guardo la fila: se escriben
    -- siempre, no son datos que puedan mejorar despues.
    UPDATE dbo.PRD_ClosingEvidence
    SET Estacion = ISNULL(NULLIF(LTRIM(RTRIM(@Estacion)), ''), Estacion),
        Etapa = ISNULL(NULLIF(LTRIM(RTRIM(@Etapa)), ''), Etapa),
        Parte = ISNULL(@Parte, Parte)
    WHERE Id = @Id;

    -- El serial, solo si esta vacio. Si ya hay uno, viene de alguien que tuvo
    -- la pieza delante; un dato copiado despues no puede pisar eso.
    IF @Serial IS NOT NULL AND LTRIM(RTRIM(@Serial)) <> ''
        UPDATE dbo.PRD_ClosingEvidence
        SET LCD_Serial = UPPER(LTRIM(RTRIM(@Serial))),
            LCD_SerialOrigen = ISNULL(@Origen, 'ESCANER'),
            LCD_SerialFecha = GETDATE()
        WHERE Id = @Id
          AND LCD_Serial IS NULL;

    SELECT LCD_Serial, LCD_SerialOrigen, Estacion, Etapa, Parte
    FROM dbo.PRD_ClosingEvidence
    WHERE Id = @Id;
END
