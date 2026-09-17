"""Endpoints de "Pedido a fábrica": pedidos en tránsito, patrocinios
previstos, cantidad final por línea, y confirmación del pedido."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models_db, schemas, security
from ..db import get_db

router = APIRouter(prefix="/api/pedido", tags=["pedido"], dependencies=[Depends(security.usuario_actual)])


@router.get("/transito", response_model=list[schemas.TransitoOut])
def listar_transito(db: Session = Depends(get_db)):
    filas = db.query(models_db.PedidoTransito).order_by(models_db.PedidoTransito.eta).all()
    return [schemas.TransitoOut(id=f.id, sku=f.codigo_producto, unidades=f.unidades, eta=f.eta) for f in filas]


@router.post("/transito", response_model=schemas.TransitoOut, dependencies=[Depends(security.requiere_admin)])
def agregar_transito(datos: schemas.TransitoIn, db: Session = Depends(get_db)):
    if datos.unidades <= 0:
        raise HTTPException(status_code=422, detail="Ingresá una cantidad de unidades mayor a cero.")
    fila = models_db.PedidoTransito(codigo_producto=datos.sku, unidades=datos.unidades, eta=datos.eta)
    db.add(fila)
    db.commit()
    db.refresh(fila)
    return schemas.TransitoOut(id=fila.id, sku=fila.codigo_producto, unidades=fila.unidades, eta=fila.eta)


@router.delete("/transito/{transito_id}", dependencies=[Depends(security.requiere_admin)])
def quitar_transito(transito_id: int, db: Session = Depends(get_db)):
    fila = db.get(models_db.PedidoTransito, transito_id)
    if fila is None:
        raise HTTPException(status_code=404, detail="No existe ese pedido en tránsito.")
    db.delete(fila)
    db.commit()
    return {"ok": True}


@router.get("/patrocinios", response_model=list[schemas.PatrocinioOut])
def listar_patrocinios(db: Session = Depends(get_db)):
    filas = db.query(models_db.PatrocinioPrevisto).order_by(models_db.PatrocinioPrevisto.fecha_evento).all()
    return [
        schemas.PatrocinioOut(
            id=f.id, nombreEvento=f.nombre_evento, sku=f.codigo_producto, unidades=f.unidades, fechaEvento=f.fecha_evento
        )
        for f in filas
    ]


@router.post("/patrocinios", response_model=schemas.PatrocinioOut, dependencies=[Depends(security.requiere_admin)])
def agregar_patrocinio(datos: schemas.PatrocinioIn, db: Session = Depends(get_db)):
    if datos.unidades <= 0:
        raise HTTPException(status_code=422, detail="Ingresá una cantidad de unidades mayor a cero.")
    if not datos.nombreEvento.strip():
        raise HTTPException(status_code=422, detail="Ingresá el nombre del evento o patrocinio.")
    fila = models_db.PatrocinioPrevisto(
        nombre_evento=datos.nombreEvento.strip(),
        codigo_producto=datos.sku,
        unidades=datos.unidades,
        fecha_evento=datos.fechaEvento,
    )
    db.add(fila)
    db.commit()
    db.refresh(fila)
    return schemas.PatrocinioOut(
        id=fila.id, nombreEvento=fila.nombre_evento, sku=fila.codigo_producto, unidades=fila.unidades, fechaEvento=fila.fecha_evento
    )


@router.delete("/patrocinios/{patrocinio_id}", dependencies=[Depends(security.requiere_admin)])
def quitar_patrocinio(patrocinio_id: int, db: Session = Depends(get_db)):
    fila = db.get(models_db.PatrocinioPrevisto, patrocinio_id)
    if fila is None:
        raise HTTPException(status_code=404, detail="No existe ese patrocinio previsto.")
    db.delete(fila)
    db.commit()
    return {"ok": True}


@router.put("/lineas/{codigo_producto}", dependencies=[Depends(security.requiere_admin)])
def actualizar_linea_borrador(codigo_producto: str, datos: schemas.BorradorLineaIn, db: Session = Depends(get_db)):
    fila = db.get(models_db.PedidoBorradorLinea, codigo_producto)
    if fila is None:
        fila = models_db.PedidoBorradorLinea(codigo_producto=codigo_producto, cantidad_final=datos.cantidadFinal)
        db.add(fila)
    else:
        fila.cantidad_final = datos.cantidadFinal
    db.commit()
    return {"ok": True}


@router.get("/lineas", response_model=dict[str, int])
def listar_lineas_borrador(db: Session = Depends(get_db)):
    filas = db.query(models_db.PedidoBorradorLinea).all()
    return {f.codigo_producto: f.cantidad_final for f in filas}


@router.post("/confirmar", dependencies=[Depends(security.requiere_admin)])
def confirmar_pedido(datos: schemas.ConfirmarPedidoIn, db: Session = Depends(get_db)):
    if not datos.lineas:
        raise HTTPException(status_code=422, detail="El pedido no tiene líneas.")
    total_unidades = sum(l.cantidad for l in datos.lineas)
    total_costo = sum(l.cantidad * l.precio_unitario for l in datos.lineas)
    pedido = models_db.PedidoConfirmado(fecha=dt.date.today(), total_unidades=total_unidades, total_costo=total_costo)
    pedido.lineas = [
        models_db.PedidoConfirmadoLinea(
            codigo_producto=l.codigo_producto,
            cantidad=l.cantidad,
            precio_unitario=l.precio_unitario,
            subtotal=l.cantidad * l.precio_unitario,
        )
        for l in datos.lineas
    ]
    db.add(pedido)
    db.commit()
    db.refresh(pedido)
    return {"id": pedido.id, "fecha": pedido.fecha, "totalUnidades": total_unidades, "totalCosto": total_costo}
