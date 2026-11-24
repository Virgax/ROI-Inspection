
**Esquema de cierre — verificado contra el servidor · 18 sep 2026**  
Corregido el 24-nov-2026. Este documento recomendaba un índice único sobre GCN para impedir duplicados. **Eso era incorrecto y no debe aplicarse.** Una misma unidad se cierra varias veces a propósito (otra pieza, otro momento), y en los modelos de dos lados tiene una fila por lado. Un índice único rompería ambas cosas. Ver `integracion_sql_probada_2026-09-18.md` y la decisión 2 de `HANDOFF_CLAUDE_CODE.md`, que mandan sobre este punto.  
También quedó superado: la columna `LCD_Serial` ya existe (script `03a`), y la alarma sobre IMEI largos se refería a laptops, tablets y anillos, no a teléfonos (99,25% de los teléfonos tienen 15 dígitos).  
Servidor `192.168.181.248,13999`, base `AIRLINK`, SQL Server 2022, usuario `ALAppUser`. Todo lo de abajo está comprobado con consultas reales. **Solo lecturas; no se escribió nada.**  
  
**Cadena de datos confirmada**  
1. Giftcard → GCN  
`A02 SM312|REC|PH|CX666666` → el GCN es el último campo: `CX666666`.  
**2. GCN → IMEI**  
  
sql  
`SELECT Imei FROM dbo.SPC_Receiving WHERE GCN = 'CX666666';`  
La columna se llama **`Imei`**, no `imei` ni `IMEI`.  
El GCN **identifica una sola unidad**: 337,085 filas y 337,085 GCN distintos, cero duplicados, cero GCN con más de un IMEI. La consulta nunca devuelve dos filas.  
**3. GCN → LCD emparejado**  
La tabla de «parts married» es **`dbo.PRD_LinkMainPartToUnit`** (724 filas): `GCN, imei, LCD, Housing, BackGlass, Battery, UserCode, Comment`.  
El LCD se guarda como serial de parte, no como GCN. Los formatos varían: `AK5521361891WMW96A12U0120H` (26 car.) y `G9P5264R0AE1PHPAL` (17 car.).  
**4. GCN → IdHistoryUnitProcess**  
El puente existe, aunque no es directo. `PRD_HousingHistoryUnitProcess` se indexa por **`PUID`, que NO es el GCN** — es el serial del housing. La cadena es:  
  
`GCN `→` PRD_LinkMainPartToUnit.Housing `→` PRD_HousingHistoryUnitProcess.PUID `→` .Id`  
Verificado con filas reales, incluidas del 17-sep:

|          |                    |      |         |        |
|----------|--------------------|------|---------|--------|
| **GCN**  | Housing (=PUID)    | Id   | Proceso | Modelo |
| CX413940 | FM7H1C00REQ00006C4 | 3638 | WPT-B   | IP15PM |
| CX413688 | FM7GZ9001HZ0000BVV | 2800 | WPT-D   | IP15P  |

Solo empatan 52 de 723 filas de emparejamiento, porque el histórico de procesos (3,820 filas) cubre menos tiempo que la tabla de emparejamiento.  
Nota posterior: `IdHistoryUnitProcess` resultó salir de otra base, `[AirlinkDR].[dbo].[HistoryUnitProcess]`. Ver `integracion_sql_probada`.  
  
**Cuatro problemas que rompen el insert**  
1. `IMEI varchar(15)` se queda corto — 6,517 unidades (1.9%)  
Superado: los IMEI largos son de laptops, tablets y anillos, que no pasan por el inspector. En teléfonos, el 99,25% tiene exactamente 15 dígitos.  
`SPC_Receiving.Imei` es `varchar(50)` y hay IMEI reales de hasta **32 caracteres** (`89049032005008882600030789785130`, que parece ICCID o IMEI dual concatenado). Al pasar a `PRD_ClosingEvidence.IMEI varchar(15)` esos inserts fallan por truncamiento.  
**2. `IMEI` es NOT NULL, pero 2,258 unidades no tienen IMEI**  
Esas unidades no se pueden cerrar hasta decidir qué se escribe ahí.  
**3. `GCN varchar(10)` se queda corto — 7 unidades**  
Hay GCN de hasta 22 caracteres: `CX390750SM-F956UZSAVZW`, `PNG353239100286514`, `CX-6367-duplicate`. Son pocos, pero fallan.  
**4. `IdHistoryUnitProcess` apunta a un Id que no existe**  
Las dos filas de prueba traen 853086 y 853087. `PRD_HousingHistoryUnitProcess` va del Id 1 al 3838. **Esos valores no existen en esa tabla.** O son de relleno, o referencian otro sistema.  
Además, `ProcessName = 'CLOSING'` tampoco existe todavía en el histórico: los procesos registrados son WPT-A (1,646), WPT-B (1,006), AQC (542), REWORK (348), FQC (171), LASER (49), WPT-C (28), DSM (12), WPT-D (12), ASM (6).  
  
**Tres riesgos de diseño**  
~~La tabla no impide el duplicado que se quiere impedir~~ — RETIRADO  
No aplicar. Los duplicados por GCN son parte del proceso. Ver la nota al inicio del documento.  
`PRD_ClosingEvidence` **no tiene ningún índice, ni siquiera clave primaria**. Eso sigue siendo cierto y una clave primaria sobre `Id` no estorbaría; lo que **no** debe existir es una restricción única sobre GCN.  
**No hay dónde guardar el identificador del LCD**  
Resuelto: `LCD_Serial`, `LCD_SerialOrigen` y `LCD_SerialFecha` (script `03a`).  
**`text` es un tipo retirado**  
`Housing_PhotoBase64` y `LCD_PhotoBase64` son `text`, deprecado desde SQL Server 2005 y marcado para eliminación. No admite la mayoría de funciones de cadena ni comparaciones. Debería ser `VARCHAR(MAX)`.  
  
**Dos detalles de rendimiento**

- `SPC_Receiving.GCN` no tiene índice. Único índice: la PK sobre `ID`. Cada consulta por GCN escanea 337,085 filas: medido en 38–45 ms. Un índice sobre `GCN` lo baja a menos de 1 ms. A volumen de banco se nota.

- **Tamaño de evidencia ya en uso:** las dos filas existentes tienen 0.93 y 0.99 MB de base64 en el housing, equivalente a JPEG de calidad ~80–85 a 3200×1659. Sirve como referencia de lo que ya se consideró aceptable.  
    
  **Preguntas abiertas (estado a 24-nov)**

1.  ¿De dónde sale `IdHistoryUnitProcess`? Resuelto: `[AirlinkDR].[dbo].[HistoryUnitProcess]`.

2.  **¿Qué codifica el QR del LCD?** Resuelto: el Data Matrix de arriba, junto a la manzana, es el serial de parte (26 caracteres con `WMW`). El de la etiqueta blanca es otra cosa.

3.  **¿La LCD se fotografía sola?** Sí: en esa etapa no hay validación de tornillos.

4.  **¿Una fila o dos escrituras?** Una inserción al final con las dos fotos, más una actualización posterior de los datos extra (serial, estación, etapa, parte) que nunca pone en riesgo la fila.  
      
    **Nota de seguridad**  
    La contraseña de `AlAppUser` se compartió por chat y queda en ese historial. Conviene rotarla antes de producción. No está escrita en ningún archivo del proyecto.
