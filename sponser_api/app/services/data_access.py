"""Capa de acceso a datos: lee los CSVs procesados por sponser_etl con cache en
memoria (invalidado por mtime del archivo) y arma el contrato de datos que
espera el frontend (`products`/`clients`/`lineasComerciales`, ver plan sección 3).

Nunca importa código de sponser_etl -- solo lee sus CSVs de salida. Cuando el
pipeline corre de nuevo (vía pipeline_runner.py) los mtimes cambian y el
próximo request relee automáticamente.
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta

import pandas as pd

from .. import config
from . import images as images_service
from . import lineas_comerciales

_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_COMPUTED_CACHE: dict[tuple, object] = {}


def invalidar_cache() -> None:
    """Llamar después de correr el pipeline para forzar relectura en el próximo request."""
    _CACHE.clear()
    _COMPUTED_CACHE.clear()


def _leer_csv_cacheado(path: str, **kwargs) -> pd.DataFrame:
    mtime = os.path.getmtime(path)
    entry = _CACHE.get(path)
    if entry is not None and entry[0] == mtime:
        return entry[1]
    df = pd.read_csv(path, **kwargs)
    _CACHE[path] = (mtime, df)
    return df


def _memo(clave: tuple, builder):
    """Segunda capa de cache, arriba de _leer_csv_cacheado(): esa evita releer
    el CSV del disco, pero cada build_*() de este módulo repetía el mismo
    groupby/agregación de pandas en CADA request aunque nada hubiera cambiado
    (medido: /api/bootstrap ~200ms por llamada, y bajo unas pocas peticiones
    concurrentes -por el GIL, todo el trabajo de pandas es CPU-bound en el
    mismo proceso- degradaba a >2s por request). Se invalida junto con _CACHE
    en invalidar_cache(), que ya se llama tras cada corrida del pipeline --
    ningún consumidor debe mutar el resultado devuelto (ver bootstrap.py,
    que copia los dicts en vez de mutarlos) porque acá se comparte la misma
    instancia entre requests concurrentes."""
    if clave in _COMPUTED_CACHE:
        return _COMPUTED_CACHE[clave]
    resultado = builder()
    _COMPUTED_CACHE[clave] = resultado
    return resultado


def _ventas_detalle() -> pd.DataFrame:
    return _leer_csv_cacheado(
        config.VENTAS_DETALLE_PATH,
        dtype={"codigo_producto": str, "id_cliente": str},
        parse_dates=["fecha"],
    )


def _ventas_diarias() -> pd.DataFrame:
    return _leer_csv_cacheado(
        config.VENTAS_DIARIAS_PATH, dtype={"codigo_producto": str}, parse_dates=["fecha"]
    )


def _inventario() -> pd.DataFrame:
    return _leer_csv_cacheado(
        config.INVENTARIO_LOTES_PATH, dtype={"codigo_producto": str}, parse_dates=["fecha_vencimiento"]
    )


def _forecast_diario() -> pd.DataFrame:
    return _leer_csv_cacheado(
        config.FORECAST_DIARIO_PATH, dtype={"codigo_producto": str}, parse_dates=["fecha"]
    )


def _recomendacion() -> pd.DataFrame:
    return _leer_csv_cacheado(config.RECOMENDACION_COMPRA_PATH, dtype={"codigo_producto": str})


def _clientes_rfm() -> pd.DataFrame:
    return _leer_csv_cacheado(config.CLIENTES_RFM_PATH, dtype={"id_cliente": str})


def get_as_of_date() -> date:
    """Última fecha con venta real registrada -- se trata como el 'hoy' del
    dashboard en vez de la fecha real del sistema (ver plan, sección de contexto).
    Si el pipeline todavía no corrió (despliegue nuevo, sin datos), no hay
    ninguna fecha real de la cual partir: se usa la fecha real del sistema."""
    if not os.path.exists(config.VENTAS_DIARIAS_PATH):
        return date.today()
    return _ventas_diarias()["fecha"].max().date()


def _meses_cerrados(as_of: date) -> int:
    """Cuántos meses del año de `as_of` cuentan como 'cerrados' (Ene..mes de as_of)."""
    return as_of.month


def _descripciones_por_sku() -> pd.Series:
    df = _ventas_detalle()[["codigo_producto", "item_descripcion"]].copy()
    df["descripcion"] = (
        df["item_descripcion"].astype(str).str.replace(r"^\s*\S+\s*", "", regex=True).str.strip()
    )
    return df.groupby("codigo_producto")["descripcion"].agg(lambda s: s.mode().iat[0])


def _precios_por_sku() -> pd.Series:
    """Precio unitario representativo por SKU: mediana de monto_unitario en todo
    el historial (robusta a descuentos/promos puntuales), en vez de un precio
    fijo -- la moneda ya viene normalizada a USD desde el ETL."""
    return _ventas_detalle().groupby("codigo_producto")["monto_unitario"].median()


CHANNEL_RULES: list[tuple[str, re.Pattern]] = [
    ("Patrocinio", re.compile(r"patrocinio", re.I)),
    ("Ventas internas", re.compile(r"\bventas\b|\brebate\b", re.I)),
    ("Farmacia", re.compile(r"farmaci", re.I)),
    ("Bicicletas / ciclismo", re.compile(r"\bbike\b|\bciclo\b|ciclismo|\bmtb\b", re.I)),
    ("Distribuidor / mayorista", re.compile(r"distribui|importaciones|consorcio|sociedad an[oó]nima", re.I)),
    ("Cliente de contado", re.compile(r"cliente contado", re.I)),
]


def _canal_heuristico(nombre: str) -> str:
    """Heurística de canal a partir del nombre del cliente -- no hay un campo de
    canal real en los datos (ver plan sección 5, mismo espíritu que líneas
    comerciales: aproximación declarada, nunca oculta)."""
    for canal, patron in CHANNEL_RULES:
        if patron.search(nombre or ""):
            return canal
    return "Comercio general"


def build_products(as_of: date) -> list[dict]:
    return _memo(("products", as_of), lambda: _build_products_impl(as_of))


def _build_products_impl(as_of: date) -> list[dict]:
    n = _meses_cerrados(as_of)
    year = as_of.year

    diarias = _ventas_diarias()
    diarias_anio = diarias[diarias["fecha"].dt.year == year].copy()
    diarias_anio["mes"] = diarias_anio["fecha"].dt.month
    ventas_mes = (
        diarias_anio.groupby(["codigo_producto", "mes"])["unidades_vendidas"].sum().unstack(fill_value=0.0)
    )

    inv_total = _inventario().groupby("codigo_producto")["cantidad"].sum()
    prox_vencimiento = _inventario().groupby("codigo_producto")["fecha_vencimiento"].min()

    forecast = _forecast_diario()
    meses_objetivo = [(year + (n + i - 1) // 12, (n + i - 1) % 12 + 1) for i in range(1, 4)]
    forecast_por_mes: dict[tuple[str, int], float] = {}
    for offset, (anio_obj, mes_obj) in enumerate(meses_objetivo):
        mask = (forecast["fecha"].dt.year == anio_obj) & (forecast["fecha"].dt.month == mes_obj)
        suma = forecast[mask].groupby("codigo_producto")["unidades_pronosticadas"].sum()
        for codigo, valor in suma.items():
            forecast_por_mes[(codigo, offset)] = float(valor)

    descripciones = _descripciones_por_sku()
    precios = _precios_por_sku()
    recomendacion_idx = _recomendacion().set_index("codigo_producto")
    sugeridos = recomendacion_idx["unidades_a_pedir"]
    # Ya calculado por la simulación FIFO día-a-lote de replenishment/fifo.py
    # (consumir_fifo_solo_inventario): unidades que, dado el ritmo de venta
    # pronosticado, van a caducar sin venderse -- antes se descartaba acá y
    # nunca llegaba al frontend. unidades_a_pedir YA es consciente de esto
    # (un lote que caduca deja de poder cubrir demanda futura en la
    # simulación), así que esto es una alerta informativa, no una resta
    # adicional sobre la sugerencia.
    riesgo_vencimiento = recomendacion_idx["unidades_en_riesgo_vencimiento"]
    categorias = descripciones.apply(lineas_comerciales.clasificar)

    codigos = sorted(descripciones.index)
    productos = []
    for codigo in codigos:
        sales = [float(ventas_mes.at[codigo, m]) if codigo in ventas_mes.index and m in ventas_mes.columns else 0.0
                 for m in range(1, 13)]
        for m in range(n, 12):
            sales[m] = 0.0  # meses aún no cerrados del año en curso: sin dato real todavía

        forecast_months = [forecast_por_mes.get((codigo, i), 0.0) for i in range(3)]

        vencimiento = prox_vencimiento.get(codigo)
        if pd.isna(vencimiento):
            expiry_months = 99.0
        else:
            expiry_months = max(0.0, (vencimiento.date() - as_of).days / 30.44)

        productos.append({
            "id": codigo,
            "name": descripciones.get(codigo, codigo),
            "category": categorias.get(codigo, lineas_comerciales.SIN_CLASIFICAR),
            "image": images_service.url_para(codigo),
            "price": round(float(precios.get(codigo, 0.0)), 2),
            "sales": sales,
            "forecastMonths": forecast_months,
            "inventory": float(inv_total.get(codigo, 0.0)),
            "expiryMonths": round(expiry_months, 1),
            "suggested": float(sugeridos.get(codigo, 0.0)),
            "expiryRiskUnits": round(float(riesgo_vencimiento.get(codigo, 0.0)), 1),
        })
    return productos


def build_clients(as_of: date) -> list[dict]:
    return build_clients_para_anio(as_of.year, _meses_cerrados(as_of))


def build_clients_para_anio(year: int, n: int) -> list[dict]:
    """Misma lógica de build_clients(), parametrizada por año -- reusada por
    Análisis comercial (filtro de año) para años distintos al actual, donde
    los 12 meses ya están cerrados."""
    return _memo(("clients", year, n), lambda: _build_clients_para_anio_impl(year, n))


def _build_clients_para_anio_impl(year: int, n: int) -> list[dict]:
    detalle = _ventas_detalle()
    detalle_anio = detalle[detalle["fecha"].dt.year == year].copy()
    detalle_anio["mes"] = detalle_anio["fecha"].dt.month

    # Ventas sin cliente identificado (ej. "Cliente Contado", venta de
    # mostrador sin No. Expediente registrado) quedan con id_cliente NaN --
    # se excluyen de la lista por cliente (no hay a quién atribuírselas ni
    # nada válido que mostrar como "id" en la tarjeta), pero su monto sigue
    # contando en cualquier agregado que no dependa de agrupar por cliente
    # (KPIs, ventas por producto, etc., que leen `detalle` sin este filtro).
    detalle_anio = detalle_anio[detalle_anio["id_cliente"].notna()]

    activos = detalle_anio.drop_duplicates("id_cliente")[["id_cliente", "nombre_cliente"]]

    monthly_por_cliente = (
        detalle_anio.groupby(["id_cliente", "mes"])["cantidad"].sum().unstack(fill_value=0.0)
    )
    monthly_monto_por_cliente = (
        detalle_anio.groupby(["id_cliente", "mes"])["monto_total"].sum().unstack(fill_value=0.0)
    )
    mix_por_cliente = (
        detalle_anio.groupby(["id_cliente", "codigo_producto"])["cantidad"].sum()
    )

    clientes = []
    for _, row in activos.iterrows():
        cid = row["id_cliente"]
        monthly = [float(monthly_por_cliente.at[cid, m]) if cid in monthly_por_cliente.index and m in monthly_por_cliente.columns else 0.0
                   for m in range(1, 13)]
        for m in range(n, 12):
            monthly[m] = 0.0

        monthly_revenue = [round(float(monthly_monto_por_cliente.at[cid, m]), 2)
                            if cid in monthly_monto_por_cliente.index and m in monthly_monto_por_cliente.columns else 0.0
                            for m in range(1, 13)]
        for m in range(n, 12):
            monthly_revenue[m] = 0.0

        mix = {}
        if cid in mix_por_cliente.index.get_level_values(0):
            for codigo, unidades in mix_por_cliente.loc[cid].items():
                if unidades > 0:
                    mix[codigo] = float(unidades)

        clientes.append({
            "id": cid,
            "name": row["nombre_cliente"],
            "channel": _canal_heuristico(row["nombre_cliente"]),
            "monthly": monthly,
            "monthlyRevenue": monthly_revenue,
            "mix": mix,
        })
    return clientes


def get_available_years() -> list[int]:
    """Años con ventas reales en el histórico -- alimenta el selector de año
    de Análisis comercial (el resto del dashboard solo trabaja con el año en
    curso, ver as_of)."""
    return _memo(("available_years",), lambda: sorted(_ventas_detalle()["fecha"].dt.year.unique().tolist()))


def build_products_comercial(year: int, n: int) -> list[dict]:
    """Versión liviana de build_products() para un año distinto al actual --
    Análisis comercial (filtro de año) solo necesita precio/categoría/ventas
    mensuales para armar Pareto y ventas por familia, nunca pronóstico,
    inventario o riesgo de vencimiento (eso es estado de HOY, no aplica a un
    año histórico) -- por eso no toca esos CSVs y esos campos van en cero."""
    return _memo(("products_comercial", year, n), lambda: _build_products_comercial_impl(year, n))


def _build_products_comercial_impl(year: int, n: int) -> list[dict]:
    diarias = _ventas_diarias()
    diarias_anio = diarias[diarias["fecha"].dt.year == year].copy()
    diarias_anio["mes"] = diarias_anio["fecha"].dt.month
    ventas_mes = (
        diarias_anio.groupby(["codigo_producto", "mes"])["unidades_vendidas"].sum().unstack(fill_value=0.0)
    )

    descripciones = _descripciones_por_sku()
    precios = _precios_por_sku()
    categorias = descripciones.apply(lineas_comerciales.clasificar)

    codigos = sorted(descripciones.index)
    productos = []
    for codigo in codigos:
        sales = [float(ventas_mes.at[codigo, m]) if codigo in ventas_mes.index and m in ventas_mes.columns else 0.0
                 for m in range(1, 13)]
        for m in range(n, 12):
            sales[m] = 0.0

        productos.append({
            "id": codigo,
            "name": descripciones.get(codigo, codigo),
            "category": categorias.get(codigo, lineas_comerciales.SIN_CLASIFICAR),
            "image": images_service.url_para(codigo),
            "price": round(float(precios.get(codigo, 0.0)), 2),
            "sales": sales,
            "forecastMonths": [0.0, 0.0, 0.0],
            "inventory": 0.0,
            "expiryMonths": 99.0,
            "suggested": 0.0,
            "expiryRiskUnits": 0.0,
        })
    return productos


DIAS_INACTIVIDAD_MIN = 60  # ~2 meses (mismo criterio dias/30.44 que expiryMonths)
DIAS_INACTIVIDAD_MAX = 90  # ~3 meses -- ventana, no umbral abierto

# El reporte es una ventana (60-90 días), no "60 días o más": la idea es una
# lista corta y accionable de clientes que ACABAN de cruzar el umbral de
# inactividad (para dar seguimiento a tiempo), no todo el historial de
# clientes dormidos -- eso mezclaría cuentas prácticamente perdidas hace años
# con las que recién se están yendo, diluyendo la lista.


def build_clientes_inactivos(as_of: date) -> list[dict]:
    """Clientes que cruzaron el umbral de ~2 meses sin comprar hace poco
    (entre 60 y 90 días sin compra). A diferencia de build_clients() (solo
    año en curso, para el ranking/Pareto de clientes activos), usa
    clientes_rfm.csv que cubre todo el histórico -- un cliente que compró por
    última vez el año pasado no aparecería en build_clients() pero igual debe
    poder salir acá como recién inactivo."""
    return _memo(("clientes_inactivos", as_of), lambda: _build_clientes_inactivos_impl(as_of))


def _build_clientes_inactivos_impl(as_of: date) -> list[dict]:
    rfm = _clientes_rfm()
    en_ventana = (rfm["recencia_dias"] >= DIAS_INACTIVIDAD_MIN) & (rfm["recencia_dias"] < DIAS_INACTIVIDAD_MAX)
    inactivos = rfm[en_ventana].sort_values("recencia_dias", ascending=False)

    salida = []
    for _, r in inactivos.iterrows():
        dias = int(r["recencia_dias"])
        salida.append({
            "id": r["id_cliente"],
            "name": r["nombre_cliente"],
            "channel": _canal_heuristico(r["nombre_cliente"]),
            "lastPurchase": as_of - timedelta(days=dias),
            "daysInactive": dias,
            "monthsInactive": round(dias / 30.44, 1),
            "historicRevenue": round(float(r["monetario_total"]), 2),
            "historicOrders": int(r["frecuencia"]),
        })
    return salida


def build_lineas_comerciales_resumen(as_of: date) -> list[dict]:
    """Agregado de ventas por línea comercial real, para la tarjeta 'Ventas de
    producto por familia' (placeholder explícito en el diseño, ver plan sección 5)."""
    return _memo(("lineas_comerciales", as_of), lambda: _build_lineas_comerciales_resumen_impl(as_of))


def _build_lineas_comerciales_resumen_impl(as_of: date) -> list[dict]:
    year = as_of.year
    descripciones = _descripciones_por_sku()
    categorias = descripciones.apply(lineas_comerciales.clasificar)

    detalle = _ventas_detalle()
    detalle_anio = detalle[detalle["fecha"].dt.year == year].copy()
    detalle_anio["linea"] = detalle_anio["codigo_producto"].map(categorias).fillna(lineas_comerciales.SIN_CLASIFICAR)

    agg = detalle_anio.groupby("linea").agg(unidades=("cantidad", "sum"), monto=("monto_total", "sum"))
    agg = agg.sort_values("monto", ascending=False)
    return [
        {"nombre": nombre, "unidades": float(r["unidades"]), "monto": float(r["monto"])}
        for nombre, r in agg.iterrows()
    ]


def _patrocinios() -> pd.DataFrame:
    return _leer_csv_cacheado(
        config.PATROCINIOS_PATH, dtype={"codigo_producto": str, "id_cliente": str}, parse_dates=["fecha"]
    )


def build_patrocinios_resumen(as_of: date) -> list[dict]:
    """Registro histórico de salidas por patrocinio -- separadas de las ventas
    en el ETL (ver transform.separar_patrocinios: no son venta, no deben
    inflar la demanda del modelo). El comercial necesita verlas igual, para
    llevar el registro de a quién/qué evento se le entregó producto."""
    return _memo(("patrocinios_resumen", as_of), _build_patrocinios_resumen_impl)


def _build_patrocinios_resumen_impl() -> list[dict]:
    patrocinios = _patrocinios()
    if patrocinios.empty:
        return []

    agg = patrocinios.groupby("nombre_cliente").agg(
        unidades=("cantidad", "sum"),
        monto=("monto_total", "sum"),
        facturas=("num_factura", "nunique"),
        ultima_salida=("fecha", "max"),
    )
    agg = agg.sort_values("monto", ascending=False)
    return [
        {
            "nombre": nombre,
            "unidades": float(r["unidades"]),
            "monto": float(r["monto"]),
            "facturas": int(r["facturas"]),
            "ultimaSalida": r["ultima_salida"].date(),
        }
        for nombre, r in agg.iterrows()
    ]


def build_patrocinios_mensual(as_of: date) -> list[dict]:
    """Monto de patrocinio entregado mes a mes en el año en curso (valor de
    venta/lista, neto de IVA -- mismo monto_total que build_patrocinios_resumen,
    solo que agregado por mes en vez de por cuenta) -- para el gráfico de
    tendencia de la pestaña Patrocinios. Meses aún no cerrados quedan en 0,
    mismo criterio que build_products()."""
    return _memo(("patrocinios_mensual", as_of), lambda: _build_patrocinios_mensual_impl(as_of))


def _build_patrocinios_mensual_impl(as_of: date) -> list[dict]:
    n = _meses_cerrados(as_of)
    year = as_of.year
    patrocinios = _patrocinios()

    if not patrocinios.empty:
        anio = patrocinios[patrocinios["fecha"].dt.year == year].copy()
        anio["mes"] = anio["fecha"].dt.month
        agg = anio.groupby("mes").agg(monto=("monto_total", "sum"), unidades=("cantidad", "sum"))
    else:
        agg = pd.DataFrame(columns=["monto", "unidades"])

    salida = []
    for mes in range(1, 13):
        cerrado = mes <= n
        monto = float(agg.at[mes, "monto"]) if cerrado and mes in agg.index else 0.0
        unidades = float(agg.at[mes, "unidades"]) if cerrado and mes in agg.index else 0.0
        salida.append({"mes": mes, "monto": round(monto, 2), "unidades": unidades})
    return salida


def _pareto_calc(entries: list[dict]) -> list[dict]:
    """Ordena por monto descendente y agrega rank/participación/acumulado/
    clasificación 80/20 -- mismo criterio que paretoOf() en el frontend
    (ver commercialVals() en el .dc.html), calculado acá para el export."""
    ordenados = sorted(entries, key=lambda e: e["monto"], reverse=True)
    total = sum(e["monto"] for e in ordenados) or 1.0
    cum = 0.0
    salida = []
    for i, e in enumerate(ordenados):
        share = e["monto"] / total * 100
        cum += share
        salida.append({
            **e,
            "rank": i + 1,
            "participacion_pct": round(share, 2),
            "acumulado_pct": round(cum, 2),
            "clasificacion": "Vital (80%)" if (cum - share) < 80 else "Cola larga",
        })
    return salida


def build_pareto_comercial(as_of: date) -> tuple[list[dict], list[dict]]:
    """Reporte de concentración 80/20 para exportar -- misma base de cálculo
    que el gráfico 'Pareto comercial' y la tarjeta 'Concentración 80/20' del
    dashboard (unidades vendidas en el año * precio unitario mediano por SKU,
    ver commercialVals() en el .dc.html), para que el Excel coincida
    exactamente con lo que el usuario ve en pantalla al desplegar el detalle.
    A propósito NO usa monto_total real de las transacciones -- esa cifra
    incluye descuentos/promos puntuales que harían el export inconsistente
    con la pantalla."""
    return _memo(("pareto_comercial", as_of), lambda: _build_pareto_comercial_impl(as_of))


def _build_pareto_comercial_impl(as_of: date) -> tuple[list[dict], list[dict]]:
    n = _meses_cerrados(as_of)
    productos = build_products(as_of)

    por_sku_entries = [
        {
            "codigo_producto": p["id"],
            "producto": p["name"],
            "linea_comercial": p["category"],
            "unidades": sum(p["sales"][:n]),
            "monto": sum(p["sales"][:n]) * p["price"],
        }
        for p in productos
    ]
    por_sku = _pareto_calc(por_sku_entries)

    por_linea_map: dict[str, dict] = {}
    for e in por_sku_entries:
        cur = por_linea_map.setdefault(
            e["linea_comercial"], {"linea_comercial": e["linea_comercial"], "unidades": 0.0, "monto": 0.0}
        )
        cur["unidades"] += e["unidades"]
        cur["monto"] += e["monto"]
    por_linea = _pareto_calc(list(por_linea_map.values()))

    return por_linea, por_sku


def _motivo_rezago(share_pct: float, tendencia_pct: float, clientes_con: int, total_clientes: int) -> str:
    razones = []
    if share_pct < 8:
        razones.append("participación baja en la mezcla")
    if tendencia_pct < 1:
        razones.append("tendencia débil")
    if total_clientes and clientes_con < total_clientes * 0.7:
        razones.append(f"presente en solo {clientes_con} de {total_clientes} clientes")
    return ", ".join(razones) if razones else "volumen por debajo del corte 80/20"


def build_articulos_rezagados(as_of: date) -> list[dict]:
    """SKUs fuera del corte 80/20 (cola larga), con el mismo criterio de
    negocio que la tarjeta 'Artículos rezagados' del dashboard (ver laggards
    en commercialVals() del .dc.html): participación, tendencia mes a mes y
    cobertura de clientes, calculados con la misma fórmula que el frontend
    para que el Excel coincida con lo que se ve en pantalla."""
    return _memo(("articulos_rezagados", as_of), lambda: _build_articulos_rezagados_impl(as_of))


def _build_articulos_rezagados_impl(as_of: date) -> list[dict]:
    n = _meses_cerrados(as_of)
    year = as_of.year
    productos = build_products(as_of)

    detalle = _ventas_detalle()
    detalle_anio = detalle[(detalle["fecha"].dt.year == year) & (detalle["cantidad"] > 0)]
    clientes_por_sku = detalle_anio.groupby("codigo_producto")["id_cliente"].nunique()
    total_clientes = int(detalle_anio["id_cliente"].nunique())

    entries = []
    growth_por_sku: dict[str, float] = {}
    clientes_con_por_sku: dict[str, int] = {}
    for p in productos:
        sales = p["sales"][:n]
        crecimientos = [(sales[k] - sales[k - 1]) / sales[k - 1] for k in range(1, n) if sales[k - 1] > 0]
        growth_por_sku[p["id"]] = (sum(crecimientos) / len(crecimientos) * 100) if crecimientos else 0.0
        clientes_con_por_sku[p["id"]] = int(clientes_por_sku.get(p["id"], 0))
        entries.append({
            "codigo_producto": p["id"],
            "producto": p["name"],
            "linea_comercial": p["category"],
            "unidades": sum(sales),
            "monto": sum(sales) * p["price"],
        })

    pareto = _pareto_calc(entries)
    rezagados = []
    for e in pareto:
        if e["clasificacion"] != "Cola larga":
            continue
        codigo = e["codigo_producto"]
        clientes_con = clientes_con_por_sku[codigo]
        tendencia = round(growth_por_sku[codigo], 2)
        rezagados.append({
            **e,
            "tendencia_pct": tendencia,
            "clientes_con_compra": clientes_con,
            "total_clientes": total_clientes,
            "motivo": _motivo_rezago(e["participacion_pct"], tendencia, clientes_con, total_clientes),
        })
    return rezagados


def build_model_accuracy() -> dict:
    """Precisión real del modelo vigente (LATEST.txt), del backtest, no de una
    simulación.

    `pct_total_real_backtest` de versions/summary.csv es qué tan cerca quedó el
    pronóstico total de la venta real agregada en el backtest (la métrica que sí
    tiene sentido mostrar como '% de precisión' en un KPI compacto); el WMAPE
    crudo está dominado por SKUs de bajo volumen con error relativo alto (ver
    ADVERTENCIA en replenishment/run.py) y mostrarlo como '100 - WMAPE' daría un
    número negativo engañoso."""
    return _memo(("model_accuracy",), _build_model_accuracy_impl)


def _build_model_accuracy_impl() -> dict:
    with open(config.MODEL_LATEST_PATH, encoding="utf-8") as f:
        version = f.read().strip()

    summary = pd.read_csv(config.MODEL_SUMMARY_PATH)
    fila = summary[summary["version"] == version].iloc[0]
    pct_total = float(fila["pct_total_real_backtest"]) * 100
    accuracy_pct = round(100 - abs(100 - pct_total), 1)

    backtest_path = os.path.join(config.MODEL_VERSIONS_DIR, version, "backtest_resultados.csv")
    backtest = pd.read_csv(backtest_path, dtype={"codigo_producto": str})
    descripciones = _descripciones_por_sku()

    top = backtest.sort_values("real", ascending=False).head(6)
    filas = [
        {
            "producto": descripciones.get(r["codigo_producto"], r["codigo_producto"]),
            "real": float(r["real"]),
            "pronosticado": float(r["pronosticado"]),
            "errorPct": float(r["error_pct"]) * 100,
        }
        for _, r in top.iterrows()
    ]

    return {"version": version, "accuracyPct": accuracy_pct, "topSkus": filas}


def _prioridad_por_dias(dias: int) -> str:
    if dias < 0:
        return "Vencido"
    if dias <= 30:
        return "Urgente"
    if dias <= 90:
        return "Alta"
    if dias <= 180:
        return "Media"
    return "Baja"


def build_priorizacion_salida(as_of: date) -> pd.DataFrame:
    """Reporte a nivel de LOTE (no agregado por SKU) para priorizar salida de
    inventario por vencimiento. build_products() agrega todos los lotes de un
    mismo código a un solo inventory/expiryMonths (el vencimiento más próximo
    aplicado al total) para el KPI compacto del dashboard -- eso esconde que un
    SKU puede tener, por ejemplo, 2 unidades vencen en 20 días y 200 unidades
    vencen en 8 meses. Este reporte detalla cada lote por separado."""
    return _memo(("priorizacion_salida", as_of), lambda: _build_priorizacion_salida_impl(as_of))


def _build_priorizacion_salida_impl(as_of: date) -> pd.DataFrame:
    lotes = _inventario().copy()
    descripciones = _descripciones_por_sku()
    precios = _precios_por_sku()
    categorias = descripciones.apply(lineas_comerciales.clasificar)

    lotes["producto"] = lotes["codigo_producto"].map(descripciones).fillna(lotes["descripcion"])
    lotes["linea_comercial"] = lotes["codigo_producto"].map(categorias).fillna(lineas_comerciales.SIN_CLASIFICAR)
    lotes["precio_unitario_usd"] = lotes["codigo_producto"].map(precios).fillna(0.0).round(2)
    lotes["valor_lote_usd"] = (lotes["cantidad"] * lotes["precio_unitario_usd"]).round(2)
    lotes["inventario_total_sku_todos_lotes"] = lotes.groupby("codigo_producto")["cantidad"].transform("sum")

    as_of_ts = pd.Timestamp(as_of)
    lotes["dias_para_vencer"] = (lotes["fecha_vencimiento"] - as_of_ts).dt.days
    lotes["meses_para_vencer"] = (lotes["dias_para_vencer"] / 30.44).round(1)
    lotes["prioridad_salida"] = lotes["dias_para_vencer"].apply(_prioridad_por_dias)

    lotes = lotes.sort_values(["dias_para_vencer", "valor_lote_usd"], ascending=[True, False])
    lotes["fecha_vencimiento"] = lotes["fecha_vencimiento"].dt.date.astype(str)

    columnas = {
        "codigo_producto": "codigo_producto",
        "producto": "producto",
        "linea_comercial": "linea_comercial",
        "lote_id": "lote",
        "cantidad": "cantidad_lote",
        "inventario_total_sku_todos_lotes": "inventario_total_sku_todos_lotes",
        "fecha_vencimiento": "fecha_vencimiento",
        "dias_para_vencer": "dias_para_vencer",
        "meses_para_vencer": "meses_para_vencer",
        "prioridad_salida": "prioridad_salida",
        "precio_unitario_usd": "precio_unitario_usd",
        "valor_lote_usd": "valor_inventario_lote_usd",
    }
    return lotes[list(columnas)].rename(columns=columnas).reset_index(drop=True)
