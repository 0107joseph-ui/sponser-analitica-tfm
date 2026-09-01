# Pipeline de datos

## 1. Extracción (`extract.py`)

Los reportes de venta que exporta el sistema de Sponser
("COM Sponser AAAA.xls", "Control Sponser AAAA.xls") **no son binarios
Excel**: son una tabla `GridView` de ASP.NET renderizada como HTML y
guardada con extensión `.xls`. Abrirlos con `pandas.read_excel` falla
silenciosamente o lanza error de formato. Se parsean en cambio con
`html.parser` de la librería estándar (`_GridViewTableParser`, una
subclase de `HTMLParser` que solo lee la primera `<table>` del documento),
deliberadamente sin `lxml`/`beautifulsoup4` para no agregar una dependencia
externa a algo que la librería estándar ya resuelve.

El inventario (`Inventario*.xlsx`) sí es un `.xlsx` genuino, leído
directamente con `pandas.read_excel`.

Cada archivo de ventas se identifica por nombre (`COM|Control Sponser
AAAA.xls`) para etiquetar la fuente y el año; un archivo con encabezados
distintos a los esperados se omite con una advertencia en vez de tumbar
todo el proceso (`ResultadoExtraccion.errores`).

## 2. Transformación (`transform.py`)

- **Conversión de moneda**: las columnas monetarias se normalizan a USD
  (`TIPO_CAMBIO_USD = 530.0`); solo un puñado de filas del dataset original
  están en colones, así que el impacto de usar un tipo de cambio fijo en
  vez de uno histórico día a día es mínimo.
- **Separación de patrocinios**: cualquier venta a un cliente cuyo nombre
  contenga "patrocinio" se separa a su propio conjunto
  (`separar_patrocinios`) -- es salida de inventario real, pero no una
  venta comercial. Mezclarla infla la facturación reportada y, más
  importante, sesgaría el modelo de demanda si se entrenara sobre ella (un
  patrocinio de 500 unidades en un solo día no es una señal de demanda
  orgánica repetible).
- **Tablas agregadas de salida**: ventas diarias por producto (insumo
  directo del modelo), RFM de clientes, inventario por lote con fecha de
  vencimiento.

## 3. Carga (`load.py`)

Escribe cada tabla a `data/processed/*.csv`. Sin transformación adicional:
el objetivo es que cualquier consumidor (el backend, un notebook de
análisis exploratorio, este mismo README) pueda leer directamente un CSV
plano sin conocer la lógica de extracción/transformación previa.

## Orquestación (`pipeline.py`)

```
extract.extract_ventas(RAW_DIR)
    -> transform.limpiar_ventas(...)
    -> transform.separar_patrocinios(...)
    -> transform.construir_ventas_diarias_producto(...)
    -> transform.construir_clientes_rfm(...)
extract.extract_inventario(RAW_DIR)
    -> transform.construir_inventario_lotes(...)
load.guardar_tablas({...}, OUT_DIR)
```

Si no encuentra `Inventario*.xlsx`, no interrumpe el pipeline completo:
continúa con una tabla de inventario vacía y lo reporta como advertencia
(el resto de las tablas sí dependen de que haya ventas -- sin eso, el
pipeline sí termina con código de error).

`RAW_DIR` apunta a `sponser_etl/data/raw/` (relativo al proyecto). Este
repositorio ya incluye ahí los 7 archivos crudos reales de Sponser usados
para producir `data/processed/` y entrenar el modelo -- ver
[Reproducir el pipeline desde cero](../README.md#reproducir-el-pipeline-desde-cero).

## Cómo llegan datos nuevos en producción

Desde la pestaña "Carga de datos" del dashboard, un administrador sube un
archivo nuevo (`POST /api/datos/subir-archivos`). El backend valida el
nombre contra los mismos patrones que espera `extract.py`
(`sponser_api/app/services/carga_archivos.py`) y lo guarda en
`sponser_etl/data/raw/`:

- **Ventas**: cada año es un archivo con nombre fijo
  (`COM Sponser 2026.xls`) -- subir el mismo año otra vez lo reemplaza (es
  una corrección/actualización de ese año), subir un año nuevo se acumula
  junto a los anteriores.
- **Inventario**: siempre se guarda bajo un nombre fijo
  (`Inventario.xlsx`), reemplazando cualquier subida anterior. Es
  deliberado: `extract_inventario` toma el primer archivo que encuentre con
  `glob("Inventario*.xlsx")`, así que dejar acumular nombres distintos
  arriesgaría que una subida más reciente quedara ignorada en silencio.

Ningún archivo se procesa automáticamente al subirse -- un administrador
dispara el pipeline aparte (`POST /api/datos/ejecutar-pipeline`), para
poder subir varios archivos antes de correrlo una sola vez.
