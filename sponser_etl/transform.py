"""Limpieza, unificación y construcción de tablas agregadas.

Todas las tablas de salida quedan expresadas en dólares (USD): las pocas
filas en colones se convierten con TIPO_CAMBIO_USD antes de agregar nada.
"""

from __future__ import annotations

import pandas as pd

# Colones por dólar. Ajustar aquí si se necesita mayor precisión histórica;
# el impacto es mínimo (solo un puñado de filas del dataset están en colones).
TIPO_CAMBIO_USD = 530.0

COLUMNAS_MONETARIAS = [
    "saldo_factura", "monto_unitario", "monto_cobrado",
    "descuento_unitario", "descuento_linea", "monto_total", "iva",
]

_RENAME_VENTAS = {
    "Tipo Documento": "tipo_documento",
    "nofactura": "num_factura",
    "Fecha": "fecha",
    "Entidad": "entidad",
    "Cliente": "nombre_cliente",
    "No. Expediente": "id_cliente",
    "telcel": "telefono",
    "saldofactura": "saldo_factura",
    "Código": "codigo_interno_factura",
    "item": "item_descripcion",
    "Proveedor": "proveedor",
    "Cantidad": "cantidad",
    "montounitario": "monto_unitario",
    "montocobrado": "monto_cobrado",
    "descuentounitario": "descuento_unitario",
    "descuentolinea": "descuento_linea",
    "Detalle": "detalle",
    "montototal": "monto_total",
    "iv": "iva",
    "Usuario": "usuario",
    "Clave": "clave_hacienda",
    "Moneda": "moneda",
    "tipopago": "tipo_pago",
}

_COLUMNAS_DESCARTABLES = ["TipoReferencia", "tipocliente", "codigobarras", "noreferencia"]

# Líneas que no son venta de producto (p.ej. alquiler de inmueble/consultorio)
# y distorsionan el modelo de demanda/inventario: se excluyen del dataset.
_PATRONES_NO_PRODUCTO = [
    r"^\s*Servicios de alquiler",
    r"^\s*BUNDLE",
]

# Códigos de producto que se excluyen explícitamente de todo el pipeline (a
# pedido del negocio) -- no deben aparecer en ningún reporte ni alimentar el
# modelo de demanda/inventario.
_SKUS_EXCLUIDOS = {
    "1015",  # Electrolytes Fruitmix 10 Tabs
    "1295",  # Activator 200 Cola-Lemon UNIDAD 25ml
}


def _a_float(serie: pd.Series) -> pd.Series:
    limpia = serie.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(limpia, errors="coerce")


def limpiar_ventas(ventas_raw: pd.DataFrame) -> pd.DataFrame:
    if ventas_raw.empty:
        return ventas_raw

    df = ventas_raw.drop(columns=_COLUMNAS_DESCARTABLES, errors="ignore").rename(columns=_RENAME_VENTAS)

    for patron in _PATRONES_NO_PRODUCTO:
        excluir = df["item_descripcion"].astype(str).str.contains(patron, case=False, regex=True, na=False)
        df = df[~excluir]
    df = df.reset_index(drop=True)

    df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce")

    for col in ["cantidad", *COLUMNAS_MONETARIAS]:
        df[col] = _a_float(df[col])

    df["num_factura"] = pd.to_numeric(df["num_factura"], errors="coerce").astype("Int64")
    df["id_cliente"] = pd.to_numeric(df["id_cliente"], errors="coerce").astype("Int64")
    df["codigo_interno_factura"] = df["codigo_interno_factura"].astype(str).str.strip()
    df["usuario"] = df["usuario"].astype(str).str.upper().str.strip()
    df["nombre_cliente"] = df["nombre_cliente"].astype(str).str.strip()

    # El código de producto real va embebido al inicio de "item"
    # (p.ej. "17504 Activator 200 Fruit Boost..."), no en "Código"
    # (ID interno de facturación, no cruza con inventario).
    df["codigo_producto"] = df["item_descripcion"].astype(str).str.extract(r"^\s*(\d+)")[0]
    df["codigo_producto"] = df["codigo_producto"].fillna(df["codigo_interno_factura"])

    df = df[~df["codigo_producto"].isin(_SKUS_EXCLUIDOS)].reset_index(drop=True)

    # Identificador único de factura: el correlativo se reinicia entre fuentes.
    df["factura_id"] = df["fuente"] + "-" + df["num_factura"].astype(str)

    # Conversión a USD: se normaliza moneda para que todo el análisis quede en una sola divisa.
    df["moneda_original"] = df["moneda"].astype(str).str.strip()
    es_colones = df["moneda_original"].str.upper().str.contains("COLON", na=False)
    df.loc[es_colones, COLUMNAS_MONETARIAS] = df.loc[es_colones, COLUMNAS_MONETARIAS].div(TIPO_CAMBIO_USD)
    df["moneda"] = "USD"

    # "monto_total" incluye IVA; el pipeline trabaja en venta neta, así que
    # se resta acá una sola vez. El bruto queda aparte para conciliación.
    df["monto_total_bruto"] = df["monto_total"]
    df["monto_total"] = (df["monto_total"] - df["iva"].fillna(0)).round(2)

    return df


def separar_patrocinios(ventas: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Todo cliente cuyo nombre contenga 'patrocinio' recibe producto como
    patrocinio deportivo -- no es una venta comercial y no debe contarse como
    tal (infla facturación/unidades y sesga el modelo de demanda). Se separa
    a su propio apartado en vez de simplemente descartarse."""
    if ventas.empty:
        return ventas, ventas
    es_patrocinio = ventas["nombre_cliente"].astype(str).str.contains("patrocinio", case=False, na=False)
    comerciales = ventas[~es_patrocinio].reset_index(drop=True)
    patrocinios = ventas[es_patrocinio].reset_index(drop=True)
    return comerciales, patrocinios


def construir_ventas_diarias_producto(ventas: pd.DataFrame) -> pd.DataFrame:
    if ventas.empty:
        return pd.DataFrame(columns=["fecha", "codigo_producto", "unidades_vendidas", "monto_total", "num_facturas"])

    agg = ventas.groupby(["fecha", "codigo_producto"]).agg(
        unidades_vendidas=("cantidad", "sum"),
        monto_total=("monto_total", "sum"),
        num_facturas=("factura_id", "nunique"),
    ).reset_index()
    return agg.sort_values(["fecha", "codigo_producto"]).reset_index(drop=True)


def construir_clientes_rfm(ventas: pd.DataFrame) -> pd.DataFrame:
    if ventas.empty:
        return pd.DataFrame(columns=[
            "id_cliente", "nombre_cliente", "recencia_dias", "frecuencia",
            "monetario_total", "ticket_promedio", "productos_distintos",
            "antiguedad_dias", "fuente_principal",
        ])

    fecha_max_global = ventas["fecha"].max()

    nombres = (
        ventas.groupby("id_cliente")["nombre_cliente"]
        .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
    )
    fuente_principal = (
        ventas.groupby("id_cliente")["fuente"]
        .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
    )

    base = ventas.groupby("id_cliente").agg(
        fecha_min=("fecha", "min"),
        fecha_max=("fecha", "max"),
        frecuencia=("factura_id", "nunique"),
        monetario_total=("monto_total", "sum"),
        productos_distintos=("codigo_producto", "nunique"),
    )

    base["nombre_cliente"] = nombres
    base["fuente_principal"] = fuente_principal
    base["recencia_dias"] = (fecha_max_global - base["fecha_max"]).dt.days
    base["antiguedad_dias"] = (base["fecha_max"] - base["fecha_min"]).dt.days
    base["ticket_promedio"] = base["monetario_total"] / base["frecuencia"]

    resultado = base.reset_index()[[
        "id_cliente", "nombre_cliente", "recencia_dias", "frecuencia",
        "monetario_total", "ticket_promedio", "productos_distintos",
        "antiguedad_dias", "fuente_principal",
    ]]
    return resultado.sort_values("monetario_total", ascending=False).reset_index(drop=True)


def construir_inventario_lotes(inventario_raw: pd.DataFrame) -> pd.DataFrame:
    """Reshape wide->long: una fila por lote (Lote1 y Lote2 separados en el original)."""
    if inventario_raw.empty:
        return pd.DataFrame(columns=["codigo_producto", "descripcion", "lote_id", "cantidad", "fecha_vencimiento"])

    cols = list(inventario_raw.columns)
    # Posiciones fijas según el layout confirmado del reporte de inventario.
    col_codigo, col_desc = cols[0], cols[1]
    col_lote1_id, col_lote1_qty, col_lote1_venc = cols[4], cols[5], cols[6]
    col_lote2_id, col_lote2_qty, col_lote2_venc = cols[7], cols[8], cols[9]

    base = inventario_raw[[col_codigo, col_desc]].rename(
        columns={col_codigo: "codigo_producto", col_desc: "descripcion"}
    )
    base["codigo_producto"] = base["codigo_producto"].astype(str).str.strip()
    base["descripcion"] = base["descripcion"].astype(str).str.strip()

    lote1 = base.copy()
    lote1["lote_id"] = inventario_raw[col_lote1_id]
    lote1["cantidad"] = pd.to_numeric(inventario_raw[col_lote1_qty], errors="coerce")
    lote1["fecha_vencimiento"] = pd.to_datetime(inventario_raw[col_lote1_venc], errors="coerce")

    lote2 = base.copy()
    lote2["lote_id"] = inventario_raw[col_lote2_id]
    lote2["cantidad"] = pd.to_numeric(inventario_raw[col_lote2_qty], errors="coerce")
    lote2["fecha_vencimiento"] = pd.to_datetime(inventario_raw[col_lote2_venc], errors="coerce")

    lotes = pd.concat([lote1, lote2], ignore_index=True)
    lotes = lotes.dropna(subset=["cantidad"])
    lotes = lotes[lotes["cantidad"] > 0].reset_index(drop=True)
    return lotes[["codigo_producto", "descripcion", "lote_id", "cantidad", "fecha_vencimiento"]]
