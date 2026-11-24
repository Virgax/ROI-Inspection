/* ============================================================================
   ROI — Procedimientos de registro de evidencia
   Airlink Distribution DR
   ----------------------------------------------------------------------------
   CONTRATO COMÚN
   Los tres procedimientos devuelven SIEMPRE exactamente una fila con las mismas
   columnas, pase lo que pase. Ni RAISERROR ni conjuntos de distinta forma según
   el caso: la estación lee `ok` y `codigo` y ya sabe qué hacer, sin distinguir
   entre "falló la validación" y "se cayó el servidor".

       ok            BIT            1 / 0
       codigo        VARCHAR(30)    OK, GCN_NO_EXISTE, UNIDAD_YA_CERRADA, ...
       mensaje       NVARCHAR(400)  texto para MOSTRAR AL OPERARIO, en español
       id_evidencia  BIGINT         la fila insertada, o NULL
       gcn_unidad / imei / modelo / color / capacidad

   El `mensaje` va en español y redactado para la pantalla del banco, no para un
   log: el operario es quien lo lee y quien decide si llama a soporte.

   NOTA DE ESTRUCTURA
   Las validaciones van encadenadas con IF/ELSE y no con GOTO a una etiqueta de
   salida, que sería más corto de leer: T-SQL prohíbe que un GOTO entre o salga
   de un bloque TRY, y todas estas comprobaciones viven dentro de uno.
   ============================================================================ */

USE AirlinkDR;   -- [CONFIRMAR]
GO

/* ############################################################################
   1) HOUSING — se llama cuando el inspector da PASA y se leyó el QR
   ############################################################################ */
IF OBJECT_ID('DVO.sp_roi_registrar_housing', 'P') IS NOT NULL
    DROP PROCEDURE DVO.sp_roi_registrar_housing;
GO

CREATE PROCEDURE DVO.sp_roi_registrar_housing
    @gcn                VARCHAR(50),
    @imagen_base64      VARCHAR(MAX),
    @imagen_formato     VARCHAR(10)    = 'jpg',
    @imagen_bytes       INT            = NULL,
    @imagen_sha256      CHAR(64)       = NULL,
    @resultado          VARCHAR(20)    = 'PASS',
    @proceso            NVARCHAR(50)   = NULL,
    @estacion           NVARCHAR(100)  = NULL,
    @version_inspector  NVARCHAR(30)   = NULL,
    @fecha_captura      DATETIME2(0)   = NULL,
    @usuario            NVARCHAR(100)  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @ok BIT = 0,
            @codigo VARCHAR(30) = 'ERROR_INTERNO',
            @mensaje NVARCHAR(400) = N'Error no identificado.',
            @id BIGINT = NULL,
            @imei VARCHAR(20) = NULL,
            @modelo NVARCHAR(100) = NULL,
            @color NVARCHAR(50) = NULL,
            @capacidad NVARCHAR(50) = NULL,
            @encontrada INT = 0;

    BEGIN TRY
        SET @gcn = LTRIM(RTRIM(@gcn));

        IF @gcn IS NULL OR @gcn = ''
            SELECT @codigo = 'PARAMETRO_INVALIDO',
                   @mensaje = N'No se recibió el GCN. Vuelva a escanear el código.';

        ELSE IF @imagen_base64 IS NULL OR DATALENGTH(@imagen_base64) = 0
            SELECT @codigo = 'PARAMETRO_INVALIDO',
                   @mensaje = N'No se recibió la imagen de evidencia. No se registró nada.';

        /* Solo se cierra lo que pasó. Si algún día se quiere registrar también
           lo rechazado, se quita esta guarda; la tabla ya lo admite. */
        ELSE IF @resultado <> 'PASS'
            SELECT @codigo = 'PARAMETRO_INVALIDO',
                   @mensaje = N'Solo se registran unidades con resultado PASS.';

        ELSE
        BEGIN
            /* -- 1. la unidad tiene que existir --------------------------- */
            SELECT TOP (1)                            -- [CONFIRMAR] columnas
                   @imei      = u.imei,
                   @modelo    = u.model,
                   @color     = u.color,
                   @capacidad = u.capacity
            FROM DVO.unit AS u                        -- [CONFIRMAR] tabla
            WHERE u.gcn = @gcn;                       -- [CONFIRMAR] columna

            SET @encontrada = @@ROWCOUNT;

            IF @encontrada = 0
                SELECT @codigo = 'GCN_NO_EXISTE',
                       @mensaje = N'El GCN ' + @gcn + N' no existe en el sistema. '
                                + N'Verifique el código escaneado; si es correcto, llame a soporte.';

            /* -- 2. la unidad no puede estar ya cerrada --------------------
               Se busca por GCN y también por IMEI: un mismo teléfono
               reingresado con otro GCN sigue siendo el mismo teléfono, y eso
               es justo lo que interesa detectar. */
            ELSE IF EXISTS (SELECT 1 FROM DVO.roi_evidencia
                            WHERE etapa = 'HOUSING'
                              AND (gcn_unidad = @gcn
                                   OR (@imei IS NOT NULL AND imei = @imei)))
                SELECT @codigo = 'UNIDAD_YA_CERRADA',
                       @mensaje = N'Esta unidad ya fue registrada anteriormente. '
                                + N'No se permite sobrescribir evidencia. Llame a soporte.';

            ELSE
            BEGIN
                INSERT INTO DVO.roi_evidencia
                    (etapa, gcn_unidad, gcn_lcd, imei, modelo, color, capacidad,
                     resultado, proceso, estacion, version_inspector,
                     imagen_base64, imagen_formato, imagen_bytes, imagen_sha256,
                     fecha_captura, usuario)
                VALUES
                    ('HOUSING', @gcn, NULL, @imei, @modelo, @color, @capacidad,
                     @resultado, @proceso, @estacion, @version_inspector,
                     @imagen_base64, @imagen_formato, @imagen_bytes, @imagen_sha256,
                     ISNULL(@fecha_captura, SYSDATETIME()), @usuario);

                SET @id = SCOPE_IDENTITY();
                SELECT @ok = 1, @codigo = 'OK',
                       @mensaje = N'Housing validado y registrado. Coloque el display.';
            END
        END
    END TRY
    BEGIN CATCH
        /* 2601/2627 = violación de índice único. Es la carrera que el índice
           está ahí para atrapar: dos bancos con el mismo GCN a la vez. Se
           traduce al mismo mensaje que la comprobación de arriba, para que el
           operario vea una sola cosa coherente. */
        IF ERROR_NUMBER() IN (2601, 2627)
            SELECT @ok = 0, @codigo = 'UNIDAD_YA_CERRADA',
                   @mensaje = N'Esta unidad ya fue registrada anteriormente. '
                            + N'No se permite sobrescribir evidencia. Llame a soporte.';
        ELSE
            SELECT @ok = 0, @codigo = 'ERROR_INTERNO',
                   @mensaje = N'Error del servidor al registrar: '
                            + LEFT(ERROR_MESSAGE(), 300);
    END CATCH

    SELECT @ok AS ok, @codigo AS codigo, @mensaje AS mensaje,
           @id AS id_evidencia, @gcn AS gcn_unidad,
           @imei AS imei, @modelo AS modelo, @color AS color,
           @capacidad AS capacidad;
END
GO


/* ############################################################################
   2) LCD — se llama tras colocar el display y leer sus QR
   ############################################################################ */
IF OBJECT_ID('DVO.sp_roi_registrar_lcd', 'P') IS NOT NULL
    DROP PROCEDURE DVO.sp_roi_registrar_lcd;
GO

CREATE PROCEDURE DVO.sp_roi_registrar_lcd
    @gcn_unidad         VARCHAR(50),
    @gcn_lcd            VARCHAR(50),
    @imagen_base64      VARCHAR(MAX),
    @imagen_formato     VARCHAR(10)    = 'jpg',
    @imagen_bytes       INT            = NULL,
    @imagen_sha256      CHAR(64)       = NULL,
    @proceso            NVARCHAR(50)   = NULL,
    @estacion           NVARCHAR(100)  = NULL,
    @version_inspector  NVARCHAR(30)   = NULL,
    @fecha_captura      DATETIME2(0)   = NULL,
    @usuario            NVARCHAR(100)  = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @ok BIT = 0,
            @codigo VARCHAR(30) = 'ERROR_INTERNO',
            @mensaje NVARCHAR(400) = N'Error no identificado.',
            @id BIGINT = NULL,
            @imei VARCHAR(20) = NULL,
            @modelo NVARCHAR(100) = NULL,
            @color NVARCHAR(50) = NULL,
            @capacidad NVARCHAR(50) = NULL,
            @hay_housing INT = 0;

    BEGIN TRY
        SET @gcn_unidad = LTRIM(RTRIM(@gcn_unidad));
        SET @gcn_lcd    = LTRIM(RTRIM(@gcn_lcd));

        IF @gcn_unidad IS NULL OR @gcn_unidad = ''
           OR @gcn_lcd IS NULL OR @gcn_lcd = ''
            SELECT @codigo = 'PARAMETRO_INVALIDO',
                   @mensaje = N'Falta el GCN de la unidad o el del display. Vuelva a escanear.';

        ELSE IF @imagen_base64 IS NULL OR DATALENGTH(@imagen_base64) = 0
            SELECT @codigo = 'PARAMETRO_INVALIDO',
                   @mensaje = N'No se recibió la imagen del display. No se registró nada.';

        ELSE
        BEGIN
            /* -- 1. el housing tiene que haberse registrado antes -----------
               El orden importa: la evidencia del display solo significa algo
               si consta que esa misma unidad pasó la validación de tornillos. */
            SELECT TOP (1) @imei = imei, @modelo = modelo,
                           @color = color, @capacidad = capacidad
            FROM DVO.roi_evidencia
            WHERE etapa = 'HOUSING' AND gcn_unidad = @gcn_unidad;

            SET @hay_housing = @@ROWCOUNT;

            IF @hay_housing = 0
                SELECT @codigo = 'SIN_HOUSING',
                       @mensaje = N'Esta unidad no tiene registrada la validación del housing. '
                                + N'Valide primero el housing antes de colocar el display.';

            /* -- 2. no repetir el display de esta unidad ------------------- */
            ELSE IF EXISTS (SELECT 1 FROM DVO.roi_evidencia
                            WHERE etapa = 'LCD' AND gcn_unidad = @gcn_unidad)
                SELECT @codigo = 'LCD_YA_REGISTRADO',
                       @mensaje = N'Esta unidad ya tiene un display registrado. '
                                + N'No se permite sobrescribir evidencia. Llame a soporte.';

            /* -- 3. el display no puede venir de otra unidad --------------- */
            ELSE IF EXISTS (SELECT 1 FROM DVO.roi_evidencia
                            WHERE etapa = 'LCD' AND gcn_lcd = @gcn_lcd)
                SELECT @codigo = 'LCD_EN_OTRA_UNIDAD',
                       @mensaje = N'El display ' + @gcn_lcd
                                + N' ya fue registrado en otra unidad. Llame a soporte.';

            /* -- 4. el emparejamiento tiene que existir y coincidir ---------
               Esta es la validación que da sentido a todo: el display que se
               está montando debe ser EL que se emparejó con este teléfono. Se
               distinguen dos fallos porque piden acciones distintas: no haber
               emparejado todavía se resuelve en el proceso previo; montar el
               display de otro teléfono es un error en la mesa, ahora mismo. */
            ELSE IF NOT EXISTS (SELECT 1 FROM DVO.parts_married AS pm   -- [CONFIRMAR]
                                WHERE pm.gcn_unidad = @gcn_unidad)      -- [CONFIRMAR]
                SELECT @codigo = 'PAREJA_NO_DEFINIDA',
                       @mensaje = N'Esta unidad no tiene display emparejado en el sistema. '
                                + N'El emparejamiento debe hacerse antes del cierre.';

            ELSE IF NOT EXISTS (SELECT 1 FROM DVO.parts_married AS pm   -- [CONFIRMAR]
                                WHERE pm.gcn_unidad = @gcn_unidad
                                  AND pm.gcn_lcd    = @gcn_lcd)         -- [CONFIRMAR]
                SELECT @codigo = 'PAREJA_NO_COINCIDE',
                       @mensaje = N'El display ' + @gcn_lcd + N' NO corresponde a esta unidad. '
                                + N'Use el display emparejado con este teléfono.';

            ELSE
            BEGIN
                INSERT INTO DVO.roi_evidencia
                    (etapa, gcn_unidad, gcn_lcd, imei, modelo, color, capacidad,
                     resultado, proceso, estacion, version_inspector,
                     imagen_base64, imagen_formato, imagen_bytes, imagen_sha256,
                     fecha_captura, usuario)
                VALUES
                    ('LCD', @gcn_unidad, @gcn_lcd, @imei, @modelo, @color, @capacidad,
                     'PASS', @proceso, @estacion, @version_inspector,
                     @imagen_base64, @imagen_formato, @imagen_bytes, @imagen_sha256,
                     ISNULL(@fecha_captura, SYSDATETIME()), @usuario);

                SET @id = SCOPE_IDENTITY();
                SELECT @ok = 1, @codigo = 'OK',
                       @mensaje = N'Display validado y registrado. Unidad cerrada.';
            END
        END
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() IN (2601, 2627)
            SELECT @ok = 0, @codigo = 'LCD_YA_REGISTRADO',
                   @mensaje = N'Ya existe evidencia de display para esta unidad o para este display. Llame a soporte.';
        ELSE
            SELECT @ok = 0, @codigo = 'ERROR_INTERNO',
                   @mensaje = N'Error del servidor al registrar: '
                            + LEFT(ERROR_MESSAGE(), 300);
    END CATCH

    SELECT @ok AS ok, @codigo AS codigo, @mensaje AS mensaje,
           @id AS id_evidencia, @gcn_unidad AS gcn_unidad,
           @imei AS imei, @modelo AS modelo, @color AS color,
           @capacidad AS capacidad;
END
GO


/* ############################################################################
   3) CONSULTA — estado de una unidad, sin traer las imágenes

   La usa la estación ANTES de pedirle nada al operario: si la unidad ya está
   cerrada, es mejor decirlo al escanear que después de haberle hecho colocar el
   display. Y la usa soporte para responder "¿qué pasó con esta unidad?".

   Deliberadamente NO devuelve imagen_base64: son megabytes que casi nunca se
   necesitan, y traerlos por costumbre haría lenta una consulta que debe ser
   instantánea. Para la imagen está 03_soporte.sql.
   ############################################################################ */
IF OBJECT_ID('DVO.sp_roi_consultar_unidad', 'P') IS NOT NULL
    DROP PROCEDURE DVO.sp_roi_consultar_unidad;
GO

CREATE PROCEDURE DVO.sp_roi_consultar_unidad
    @gcn VARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    SET @gcn = LTRIM(RTRIM(@gcn));

    SELECT id_evidencia, etapa, gcn_unidad, gcn_lcd, imei, modelo, color,
           capacidad, resultado, proceso, estacion, version_inspector,
           imagen_formato, imagen_bytes, imagen_sha256,
           fecha_captura, fecha_registro, usuario
    FROM DVO.roi_evidencia
    WHERE gcn_unidad = @gcn
    ORDER BY id_evidencia;
END
GO
