/* ============================================================================
   ROI — Inspección de tornillos · estructura de evidencia
   Airlink Distribution DR
   ----------------------------------------------------------------------------
   Guarda la evidencia del cierre de una unidad: la foto rectificada del housing
   con todos sus tornillos validados, y la foto del LCD ya emparejado.

   PRINCIPIO: la tabla es SOLO DE INSERCIÓN. Una unidad no se puede reescribir.
   Si ya existe, el procedimiento lo dice y manda a llamar a soporte. Eso no se
   deja a la aplicación: abajo hay índices únicos y un disparador que rechazan
   UPDATE y DELETE aunque alguien los intente desde SSMS.

   POR QUÉ UNA FILA POR ETAPA Y NO UNA POR UNIDAD
   El housing se registra primero y el LCD después. Si fueran la misma fila, la
   segunda etapa tendría que hacer UPDATE, y entonces "solo inserciones" dejaría
   de ser cierto justo donde importa. Con una fila por etapa el registro es un
   diario: se añade, nunca se corrige. La cadena de custodia es la suma de sus
   filas, y una fila borrada o alterada se nota.
   ============================================================================ */

/* ---------------------------------------------------------------------------
   CONFIRMAR ANTES DE EJECUTAR — nombres reales del entorno
   ---------------------------------------------------------------------------
   Estos cuatro nombres están puestos como los recordabas en la grabación, con
   la duda de si la base es AirlinkDR o Airlink. Corrígelos aquí y en 02 y 03.

     Base de datos .............. AirlinkDR
     Esquema .................... DVO
     Tabla de unidades .......... DVO.unit
     Tabla de emparejamiento .... DVO.parts_married

   También hacen falta los NOMBRES DE COLUMNA reales de esas dos tablas; los de
   abajo son suposiciones y están marcados con  -- [CONFIRMAR]
   --------------------------------------------------------------------------- */

USE AirlinkDR;   -- [CONFIRMAR]
GO

/* ---------------------------------------------------------------------------
   Tabla de evidencia
   --------------------------------------------------------------------------- */
IF OBJECT_ID('DVO.roi_evidencia', 'U') IS NOT NULL
    PRINT 'DVO.roi_evidencia ya existe: no se toca.';
GO

IF OBJECT_ID('DVO.roi_evidencia', 'U') IS NULL
BEGIN
    CREATE TABLE DVO.roi_evidencia
    (
        id_evidencia      BIGINT IDENTITY(1,1) NOT NULL,

        /* etapa del cierre. HOUSING = tornillos validados; LCD = display
           emparejado y colocado. */
        etapa             VARCHAR(10)    NOT NULL,

        /* GCN de la UNIDAD (el teléfono). En la etapa LCD sigue siendo el de la
           unidad: es lo que amarra las dos filas. */
        gcn_unidad        VARCHAR(50)    NOT NULL,

        /* GCN del DISPLAY. Solo en la etapa LCD; NULL en HOUSING. */
        gcn_lcd           VARCHAR(50)        NULL,

        /* Copia de los datos de la unidad TAL COMO ESTABAN al cerrar. No es
           redundancia: si mañana alguien corrige el modelo o el IMEI en la
           tabla de unidades, la evidencia debe seguir diciendo qué se validó
           aquel día. Una cadena de custodia que cambia sola no sirve de nada. */
        imei              VARCHAR(20)        NULL,
        modelo            NVARCHAR(100)      NULL,
        color             NVARCHAR(50)       NULL,
        capacidad         NVARCHAR(50)       NULL,

        /* Veredicto del inspector. Hoy solo se registra el cierre, así que
           siempre es PASS; la columna existe para no tener que alterar la tabla
           el día que se quiera registrar también lo que no pasó. */
        resultado         VARCHAR(20)    NOT NULL,

        /* Contexto de captura: qué receta y qué banco. Con varias estaciones
           corriendo, sin esto no se puede reconstruir dónde pasó cada cosa. */
        proceso           NVARCHAR(50)       NULL,
        estacion          NVARCHAR(100)      NULL,
        version_inspector NVARCHAR(30)       NULL,

        /* Imagen rectificada, en base64.

           NOTA DE TAMAÑO: base64 ocupa 33% MÁS que el binario. A JPEG q95 son
           2.41 MB por imagen aquí, contra 1.81 MB si la columna fuera
           VARBINARY(MAX). Con dos imágenes por unidad son 4.8 MB frente a
           3.6 MB. Se deja en base64 por decisión de proyecto: es lo que el
           inspector ya produce y lo que se sabe que funciona de punta a punta.

           Para cambiarlo a binario más adelante: la columna pasa a
           VARBINARY(MAX) y el procedimiento decodifica en el servidor con
               CAST('' AS XML).value('xs:base64Binary(sql:variable("@imagen_base64"))',
                                     'VARBINARY(MAX)')
           sin que la estación tenga que cambiar nada de lo que envía. */
        imagen_base64     VARCHAR(MAX)   NOT NULL,
        imagen_formato    VARCHAR(10)        NULL,   -- jpg / png
        imagen_bytes      INT                NULL,   -- tamaño del binario, antes de base64
        imagen_sha256     CHAR(64)           NULL,   -- huella del BINARIO, no del base64

        /* Dos relojes distintos a propósito: el de la estación (cuándo se vio
           la pieza) y el del servidor (cuándo quedó registrada). Si divergen,
           hubo cola, red lenta o el reloj del banco está mal, y conviene verlo. */
        fecha_captura     DATETIME2(0)       NULL,
        fecha_registro    DATETIME2(0)   NOT NULL
                          CONSTRAINT DF_roi_evidencia_registro DEFAULT (SYSDATETIME()),

        /* Quién estaba en el banco, si el día de mañana se añade login. */
        usuario           NVARCHAR(100)      NULL,

        CONSTRAINT PK_roi_evidencia PRIMARY KEY CLUSTERED (id_evidencia),
        CONSTRAINT CK_roi_evidencia_etapa
            CHECK (etapa IN ('HOUSING', 'LCD')),
        /* El GCN del LCD solo tiene sentido en la etapa LCD, y ahí es obligatorio. */
        CONSTRAINT CK_roi_evidencia_gcn_lcd
            CHECK ((etapa = 'LCD'     AND gcn_lcd IS NOT NULL)
                OR (etapa = 'HOUSING' AND gcn_lcd IS NULL))
    );

    PRINT 'DVO.roi_evidencia creada.';
END
GO

/* ---------------------------------------------------------------------------
   Índices únicos: aquí es donde de verdad se impide reescribir una unidad.

   El procedimiento también lo comprueba, pero una comprobación en el
   procedimiento es una carrera: dos estaciones que escanean el mismo GCN en el
   mismo instante pasan las dos el SELECT y las dos insertan. El índice único no
   se puede burlar, y convierte esa carrera en un error limpio que el
   procedimiento traduce a "llame a soporte".
   --------------------------------------------------------------------------- */

-- una unidad se cierra UNA vez por etapa
IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE name = 'UX_roi_evidencia_unidad_housing'
                AND object_id = OBJECT_ID('DVO.roi_evidencia'))
CREATE UNIQUE NONCLUSTERED INDEX UX_roi_evidencia_unidad_housing
    ON DVO.roi_evidencia (gcn_unidad)
    WHERE etapa = 'HOUSING';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE name = 'UX_roi_evidencia_unidad_lcd'
                AND object_id = OBJECT_ID('DVO.roi_evidencia'))
CREATE UNIQUE NONCLUSTERED INDEX UX_roi_evidencia_unidad_lcd
    ON DVO.roi_evidencia (gcn_unidad)
    WHERE etapa = 'LCD';
GO

-- un display no se puede montar en dos unidades
IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE name = 'UX_roi_evidencia_lcd'
                AND object_id = OBJECT_ID('DVO.roi_evidencia'))
CREATE UNIQUE NONCLUSTERED INDEX UX_roi_evidencia_lcd
    ON DVO.roi_evidencia (gcn_lcd)
    WHERE etapa = 'LCD';
GO

-- el IMEI es la otra llave por la que se busca un duplicado
IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE name = 'IX_roi_evidencia_imei'
                AND object_id = OBJECT_ID('DVO.roi_evidencia'))
CREATE NONCLUSTERED INDEX IX_roi_evidencia_imei
    ON DVO.roi_evidencia (imei)
    INCLUDE (etapa, gcn_unidad);
GO

-- consultas de soporte por fecha y estación
IF NOT EXISTS (SELECT 1 FROM sys.indexes
              WHERE name = 'IX_roi_evidencia_fecha'
                AND object_id = OBJECT_ID('DVO.roi_evidencia'))
CREATE NONCLUSTERED INDEX IX_roi_evidencia_fecha
    ON DVO.roi_evidencia (fecha_registro)
    INCLUDE (etapa, gcn_unidad, resultado, estacion);
GO

/* ---------------------------------------------------------------------------
   Disparador que hace la tabla realmente de solo inserción.

   Sin esto, "no permitimos updates" es una promesa del procedimiento, y
   cualquiera con permiso de escritura puede alterar una evidencia desde SSMS
   sin dejar rastro. Con esto, la única forma de cambiar el historial es quitar
   el disparador, que sí queda registrado como un cambio de esquema.
   --------------------------------------------------------------------------- */
IF OBJECT_ID('DVO.TR_roi_evidencia_solo_insercion', 'TR') IS NOT NULL
    DROP TRIGGER DVO.TR_roi_evidencia_solo_insercion;
GO

CREATE TRIGGER DVO.TR_roi_evidencia_solo_insercion
ON DVO.roi_evidencia
INSTEAD OF UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    THROW 51000,
        'DVO.roi_evidencia es de solo inserción: la evidencia de una unidad no se modifica ni se borra. Si hay que corregir algo, registre el motivo y contacte a soporte.',
        1;
END
GO

/* ---------------------------------------------------------------------------
   Permisos sugeridos para la cuenta de las estaciones.
   Que la estación NO tenga INSERT directo es lo que obliga a que toda escritura
   pase por las validaciones de los procedimientos.
   --------------------------------------------------------------------------- */
-- CREATE USER roi_estacion FOR LOGIN roi_estacion;   -- [CONFIRMAR nombre del login]
-- GRANT EXECUTE ON DVO.sp_roi_registrar_housing TO roi_estacion;
-- GRANT EXECUTE ON DVO.sp_roi_registrar_lcd     TO roi_estacion;
-- GRANT EXECUTE ON DVO.sp_roi_consultar_unidad  TO roi_estacion;
-- DENY INSERT, UPDATE, DELETE ON DVO.roi_evidencia TO roi_estacion;
GO
