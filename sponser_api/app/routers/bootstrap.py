"""Endpoint principal del dashboard: productos, clientes, líneas
comerciales, patrocinios y precisión del modelo, todo en una sola
respuesta que el frontend pide al cargar la sesión."""

from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models_db, schemas, security
from ..db import get_db
from ..services import data_access

router = APIRouter(prefix="/api", tags=["bootstrap"])


def _bootstrap_vacio() -> schemas.BootstrapOut:
    """Respuesta válida cuando el pipeline todavía no corrió ni una vez --
    sin esto, un despliegue nuevo sin datos no puede ni terminar de iniciar
    sesión (el frontend pide /api/bootstrap como parte del login)."""
    return schemas.BootstrapOut(
        asOfDate=date.today(),
        products=[],
        clients=[],
        lineasComerciales=[],
        modelAccuracy=schemas.ModeloAccuracyOut(version="", accuracyPct=0.0, topSkus=[]),
        clientesInactivos=[],
        patrocinioClientes=[],
        patrociniosMensual=[],
        availableYears=[],
    )


def _sponsorship_por_sku(db: Session, as_of) -> dict[str, int]:
    """Solo eventos futuros (>= as_of): un evento ya pasado ya se refleja en el
    inventario actual, sumarlo de nuevo duplicaría la salida."""
    filas = (
        db.query(models_db.PatrocinioPrevisto)
        .filter(models_db.PatrocinioPrevisto.fecha_evento >= as_of)
        .all()
    )
    acumulado: dict[str, int] = {}
    for f in filas:
        acumulado[f.codigo_producto] = acumulado.get(f.codigo_producto, 0) + f.unidades
    return acumulado


@router.get("/bootstrap", response_model=schemas.BootstrapOut)
def bootstrap(_usuario=Depends(security.usuario_actual), db: Session = Depends(get_db)):
    if not os.path.exists(config.VENTAS_DIARIAS_PATH):
        return _bootstrap_vacio()

    as_of = data_access.get_as_of_date()
    sponsorship = _sponsorship_por_sku(db, as_of)
    # build_products() ahora cachea y comparte la misma lista entre requests
    # (ver data_access._memo) -- nunca mutar esos dicts in place, o un request
    # concurrente podría leer el sponsorshipUnits de otro. Se arma una copia
    # nueva por producto en cada llamada.
    products = [
        {**producto, "sponsorshipUnits": sponsorship.get(producto["id"], 0)}
        for producto in data_access.build_products(as_of)
    ]

    return schemas.BootstrapOut(
        asOfDate=as_of,
        products=products,
        clients=data_access.build_clients(as_of),
        lineasComerciales=data_access.build_lineas_comerciales_resumen(as_of),
        modelAccuracy=data_access.build_model_accuracy(),
        clientesInactivos=data_access.build_clientes_inactivos(as_of),
        patrocinioClientes=data_access.build_patrocinios_resumen(as_of),
        patrociniosMensual=data_access.build_patrocinios_mensual(as_of),
        availableYears=data_access.get_available_years(),
    )


@router.get("/analisis-comercial/{year}", response_model=schemas.AnalisisComercialOut)
def analisis_comercial(year: int, _usuario=Depends(security.usuario_actual)):
    """Datos de Análisis comercial para un año distinto al actual (ver filtro
    de año) -- el año en curso ya viene completo en /api/bootstrap."""
    as_of = data_access.get_as_of_date()
    if year == as_of.year:
        raise HTTPException(status_code=400, detail="El año actual ya viene en /api/bootstrap.")
    if year not in data_access.get_available_years():
        raise HTTPException(status_code=404, detail="No hay datos de ventas para ese año.")

    return schemas.AnalisisComercialOut(
        year=year,
        products=data_access.build_products_comercial(year, 12),
        clients=data_access.build_clients_para_anio(year, 12),
    )
