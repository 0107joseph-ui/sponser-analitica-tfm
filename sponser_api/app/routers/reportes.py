from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Depends, Response

from .. import security
from ..services import data_access

router = APIRouter(prefix="/api/reportes", tags=["reportes"], dependencies=[Depends(security.usuario_actual)])

BOM = "﻿"  # acentos legibles al abrir el CSV en Excel

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

COLUMNAS_LINEA = {
    "rank": "#", "linea_comercial": "Línea comercial", "unidades": "Unidades",
    "monto": "Facturación (US$)", "participacion_pct": "Participación (%)",
    "acumulado_pct": "Acumulado (%)", "clasificacion": "Clasificación",
}
COLUMNAS_SKU = {
    "rank": "#", "codigo_producto": "Código", "producto": "Producto",
    "linea_comercial": "Línea comercial", "unidades": "Unidades",
    "monto": "Facturación (US$)", "participacion_pct": "Participación (%)",
    "acumulado_pct": "Acumulado (%)", "clasificacion": "Clasificación",
}
COLUMNAS_REZAGADOS = {
    "rank": "#", "codigo_producto": "Código", "producto": "Producto",
    "linea_comercial": "Línea comercial", "unidades": "Unidades",
    "monto": "Facturación (US$)", "participacion_pct": "Participación (%)",
    "tendencia_pct": "Tendencia mensual (%)", "clientes_con_compra": "Clientes que lo compran",
    "total_clientes": "Clientes activos totales", "motivo": "Motivo",
}
COLUMNAS_INACTIVOS = {
    "id": "Código", "name": "Cliente", "channel": "Canal", "lastPurchase": "Última compra",
    "daysInactive": "Días sin comprar", "monthsInactive": "Meses sin comprar",
    "historicRevenue": "Facturación histórica (US$)", "historicOrders": "Facturas históricas",
}


@router.get("/priorizacion-salida")
def priorizacion_salida():
    as_of = data_access.get_as_of_date()
    df = data_access.build_priorizacion_salida(as_of)
    csv_text = BOM + df.to_csv(index=False)
    filename = f"priorizacion_salida_{as_of.isoformat()}.csv"
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/concentracion-80-20")
def concentracion_80_20():
    as_of = data_access.get_as_of_date()
    por_linea, por_sku = data_access.build_pareto_comercial(as_of)

    df_linea = pd.DataFrame(por_linea)[list(COLUMNAS_LINEA)].rename(columns=COLUMNAS_LINEA)
    df_sku = pd.DataFrame(por_sku)[list(COLUMNAS_SKU)].rename(columns=COLUMNAS_SKU)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_linea.to_excel(writer, sheet_name="Por línea comercial", index=False)
        df_sku.to_excel(writer, sheet_name="Detalle por SKU", index=False)
    buffer.seek(0)

    filename = f"concentracion_80_20_{as_of.isoformat()}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/articulos-rezagados")
def articulos_rezagados():
    as_of = data_access.get_as_of_date()
    rezagados = data_access.build_articulos_rezagados(as_of)

    df = pd.DataFrame(rezagados)[list(COLUMNAS_REZAGADOS)].rename(columns=COLUMNAS_REZAGADOS)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Artículos rezagados", index=False)
    buffer.seek(0)

    filename = f"articulos_rezagados_{as_of.isoformat()}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/clientes-inactivos")
def clientes_inactivos():
    as_of = data_access.get_as_of_date()
    inactivos = data_access.build_clientes_inactivos(as_of)

    df = pd.DataFrame(inactivos)[list(COLUMNAS_INACTIVOS)].rename(columns=COLUMNAS_INACTIVOS)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Clientes inactivos", index=False)
    buffer.seek(0)

    filename = f"clientes_inactivos_{as_of.isoformat()}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
