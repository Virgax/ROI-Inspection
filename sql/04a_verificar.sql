-- Comprobacion. Las DOS filas tienen que salir antes de correr 04b.
SELECT c.name AS Columna, t.name AS Tipo, c.max_length AS Largo
FROM sys.columns AS c
INNER JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('dbo.PRD_ClosingEvidence')
  AND c.name IN ('Etapa', 'Parte')
ORDER BY c.name;
