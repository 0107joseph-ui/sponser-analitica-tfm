# Modelado: pronóstico de demanda diaria por producto

## Por qué un modelo "hurdle" y no un solo regresor

El panel producto × día es mayormente ceros: en el set de validación de la
versión vigente (v8), solo el **5.7%** de las combinaciones producto-día
tuvieron venta real. Un solo regresor entrenado directamente sobre esto
tiende a "aprender a predecir cero" y subestimar sistemáticamente los días
en que sí hay demanda -- las primeras versiones del proyecto (v1-v4, ver
tabla abajo) usaron un único LightGBM con objetivo Tweedie (pensado
justamente para variables con exceso de ceros) sobre unidades directamente.

A partir de v5 se cambió a una arquitectura de **dos etapas** ("hurdle" --
"obstáculo": primero hay que "pasar" la clasificación para que el segundo
modelo entre en juego):

1. **Clasificador binario** (LightGBM, `objective="binary"`): ¿hay venta
   ese producto ese día? `AUC = 0.903` en la versión vigente.
2. **Regresor Poisson** (LightGBM, `objective="poisson"`): dado que sí hay
   venta, ¿cuánto se vende? Entrenado solo sobre las filas con venta
   positiva -- una pregunta distinta y más fácil que "cuánto, incluyendo
   todos los ceros".

La predicción final combina ambas: `E[demanda] = P(venta) × E[cantidad | venta]`.
Cada etapa aprende una sola pregunta en vez de intentar las dos a la vez.

## Features (`modeling/features.py`)

Panel producto × fecha compartido entre entrenamiento (`train.py`, sobre
historia real) y pronóstico (`forecast.py`, extendiéndolo día a día hacia
el futuro):

| Grupo | Features |
|---|---|
| Calendario | día del mes, semana del año, trimestre, fin de semana, día de la semana |
| Eventos (`eventos.csv`) | ¿hay evento activo?, días hasta el próximo, días desde el anterior, tipo de evento |
| Rezagos | `lag_1, lag_2, lag_3, lag_7, lag_14, lag_28` |
| Ventanas móviles | media y desviación estándar a 7/28/90 días |
| Histórico | promedio histórico del producto, código de producto codificado |

## Entrenamiento y validación (`train.py`)

**Split temporal, nunca aleatorio**: los últimos 150 días como validación,
el resto como entrenamiento -- un split aleatorio filtraría información del
futuro hacia el pasado a través de los rezagos/rolling, e infla
artificialmente la precisión reportada.

Los productos sin venta en los 90 días previos al corte se marcan como
inactivos y se fuerza su pronóstico a cero, en vez de dejar que el modelo
extrapole sobre un producto que ya no se vende.

Cada corrida crea una versión nueva (`modeling/versions/vN/`, nunca
sobreescribe una anterior) con su modelo, configuración, métricas y
backtest -- este repositorio incluye las 8 versiones entrenadas durante el
desarrollo:

| Versión | Tipo | Features | MAE val. | WMAPE val. | % del total real (backtest) | % SKU-mes dentro de ±20% |
|---|---|---|---|---|---|---|
| v1 | Tweedie directo | 16 | 0.139 | 135.4% | 68.6% | 14.3% |
| v2 | Tweedie directo | 21 | 0.148 | 144.5% | 98.0% | 22.1% |
| v3 | Tweedie directo | 18 | 0.150 | 146.0% | 98.7% | 21.1% |
| v4 | Tweedie directo | 21 | 0.149 | 145.5% | 94.9% | 20.9% |
| v5 | Hurdle | 21 | 0.144 | 140.5% | 85.8% | 20.4% |
| v6 | Hurdle | 21 | 0.153 | 149.2% | 110.2% | 22.6% |
| v7 | Hurdle | 21 | 0.148 | 144.4% | 102.6% | 20.4% |
| **v8 (vigente)** | Hurdle | 21 | **0.119** | **140.5%** | **93.4%** | **24.2%** |

(Tabla generada a partir de `modeling/versions/summary.csv`, incluido en
este repositorio junto con el detalle de cada versión.)

**Cómo leer estas métricas**: el WMAPE a nivel de fila producto-día se
mantiene alto en las ocho versiones (~135-150%) precisamente por la
naturaleza intermitente de la demanda -- un solo día con una venta
inesperada de 1 unidad sobre un pronóstico de 0.1 ya es un error porcentual
enorme, aunque en términos absolutos sea irrelevante para el negocio. Las
métricas que sí importan para la decisión de compra son las agregadas del
backtest: **% del total real** (¿el volumen total pronosticado a 150 días
se parece al que realmente se vendió?) y **% de combinaciones SKU-mes
dentro de ±20%** (¿cuántos productos-mes individuales el modelo acierta
razonablemente?). v8 mejora el MAE de forma notable sobre las versiones
anteriores sin sacrificar la precisión agregada.

## Pronóstico recursivo (`forecast.py`)

A 100 días hacia adelante: cada predicción de un día se encadena como
insumo (rezago, ventana móvil) del día siguiente, porque los rezagos reales
del futuro obviamente no existen todavía. Es el mismo procedimiento que usa
`backtest.py` para poder comparar de forma justa contra ventas ya
ocurridas -- sin esto, el backtest tendría acceso a información que el
modelo nunca tendría en producción.

## Backtest (`backtest.py`)

Corre el mismo pronóstico recursivo apuntado a un período que ya conocemos
(los últimos `DIAS_BACKTEST` días reales, excluidos del entrenamiento), y
compara predicción vs. venta real día por día, mes por mes y producto por
producto. El resumen agregado por versión queda en
`modeling/versions/summary.csv`; el detalle mensual y por producto de cada
versión, en `modeling/versions/vN/backtest_mensual_*.csv`.

## Limitaciones del enfoque

- SKUs nuevos o con muy poca historia dependen más del histórico promedio
  general que de un patrón propio aprendido -- el modelo no tiene forma de
  "saber" que un producto recién lanzado va a tener una curva de adopción
  distinta a la del catálogo general.
- El calendario de eventos (`eventos.csv`) se mantiene a mano; su cobertura
  y exactitud limitan directamente cuánto puede aprender el modelo sobre el
  efecto de eventos deportivos/promociones en la demanda.
- El tipo de cambio fijo (ver `transform.py`) introduce un sesgo pequeño
  pero real en las pocas filas originalmente en colones.
