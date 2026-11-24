# Scripts SQL — AIRLINK

**Las estaciones nunca ejecutan DDL** (decisión 6). Estos scripts los corre Jaime
con un usuario con permisos; `AlAppUser` no puede crear tablas ni procedimientos.

DDL y procedimientos van **en archivos separados** (decisión 5): SQL Server valida
nombres de columna al compilar y `GO` lo interpreta el cliente.

## Orden

1. `03a_columnas.sql` — `LCD_Serial`, `LCD_SerialOrigen`, `LCD_SerialFecha`, `Estacion`
2. `04a_etapas_columnas.sql` — `Etapa`, `Parte`
3. `03a_verificar.sql` (4 filas) + `04a_verificar.sql` (2 filas)
4. `04b_procedimiento.sql` — `SP_Set_PRD_ClosingEvidence_LCDSerial` (6 parámetros)

## Consultas

- `00_cobertura.sql` — ¿aparece el serial por su cuenta?
- `04c_lados_pendientes.sql` — unidades con un solo lado cerrado

## Otros

- `03b_procedimientos.sql` — sustituido por `04b`, salvo su procedimiento de
  conciliación, pendiente de que haya datos para decidir si sirve.

## `obsoleto/` — NO EJECUTAR

Diseño anterior, conservado solo como historia:

- `01_estructura.sql` crea **índices únicos** sobre la evidencia. Eso contradice la
  decisión 2: una unidad se cierra varias veces a propósito y con dos lados tiene
  dos filas.
- `02_procedimientos.sql` — procedimientos de ese diseño.
- `03_lcd_serial.sql` — versión que mezclaba DDL y procedimiento; sustituida por `03a` + `04b`.
