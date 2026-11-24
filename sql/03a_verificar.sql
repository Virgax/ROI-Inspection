-- Comprobacion. Tienen que salir las CUATRO filas antes de correr 03b.
SELECT
    c.name AS Columna,
    t.name AS Tipo,
    c.max_length AS Largo,
    c.is_nullable AS AdmiteNulo
FROM sys.columns AS c
INNER JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('dbo.PRD_ClosingEvidence')
  AND c.name IN ('LCD_Serial', 'LCD_SerialOrigen', 'LCD_SerialFecha',
                 'Estacion')
ORDER BY c.name;
