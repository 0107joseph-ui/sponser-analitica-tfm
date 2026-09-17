"""Endpoints de "Carga de datos": meta anual, bitácora, subida de archivos
de ventas/inventario, y disparo del pipeline completo."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from .. import models_db, schemas, security
from ..services import data_access
from ..db import get_db
from ..services import carga_archivos, pipeline_runner

router = APIRouter(prefix="/api/datos", tags=["datos"], dependencies=[Depends(security.usuario_actual)])

META_ANUAL_DEFAULT = 1_100_000.0


@router.get("/meta-anual", response_model=schemas.MetaVentasOut)
def obtener_meta_anual(db: Session = Depends(get_db)):
    anio = data_access.get_as_of_date().year
    meta = db.get(models_db.MetaVentas, anio)
    if meta is None:
        return schemas.MetaVentasOut(anio=anio, montoAnual=META_ANUAL_DEFAULT, modo="estacional")
    return schemas.MetaVentasOut(anio=meta.anio, montoAnual=float(meta.monto_anual), modo=meta.modo)


@router.put("/meta-anual", response_model=schemas.MetaVentasOut, dependencies=[Depends(security.requiere_admin)])
def actualizar_meta_anual(body: schemas.MetaVentasIn, db: Session = Depends(get_db)):
    anio = data_access.get_as_of_date().year
    meta = db.get(models_db.MetaVentas, anio)
    if meta is None:
        meta = models_db.MetaVentas(anio=anio, monto_anual=body.montoAnual, modo=body.modo)
        db.add(meta)
    else:
        meta.monto_anual = body.montoAnual
        meta.modo = body.modo

    modo_label = "estacional" if body.modo == "estacional" else "partes iguales"
    db.add(models_db.CorridaPipeline(
        tipo="meta_ventas",
        estado="ok",
        mensaje=f"Meta anual {anio} actualizada a ${body.montoAnual:,.0f} (segregación: {modo_label}).",
        finalizado_en=dt.datetime.utcnow(),
    ))
    db.commit()
    return schemas.MetaVentasOut(anio=anio, montoAnual=body.montoAnual, modo=body.modo)


@router.get("/bitacora", response_model=list[schemas.BitacoraEntradaOut])
def bitacora(db: Session = Depends(get_db)):
    filas = (
        db.query(models_db.CorridaPipeline)
        .order_by(models_db.CorridaPipeline.iniciado_en.desc())
        .limit(20)
        .all()
    )
    return [
        schemas.BitacoraEntradaOut(
            id=f.id, tipo=f.tipo, estado=f.estado, mensaje=f.mensaje,
            iniciado_en=f.iniciado_en, finalizado_en=f.finalizado_en,
        )
        for f in filas
    ]


@router.post("/subir-archivos", response_model=schemas.CargaArchivosOut, dependencies=[Depends(security.requiere_admin)])
async def subir_archivos(
    archivos: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    resultados = []
    for archivo in archivos:
        contenido = await archivo.read()
        resultado = carga_archivos.guardar(archivo.filename or "", contenido)
        resultados.append(resultado)

        mensaje = (
            f'Archivo "{resultado.nombre_original}" guardado como {resultado.guardado_como} '
            f"({resultado.tipo})."
            if resultado.ok
            else f'Archivo "{resultado.nombre_original}" rechazado: {resultado.error}'
        )
        db.add(models_db.CorridaPipeline(
            tipo="carga_archivo",
            estado="ok" if resultado.ok else "error",
            mensaje=mensaje,
            finalizado_en=dt.datetime.utcnow(),
        ))
    db.commit()

    return schemas.CargaArchivosOut(resultados=[
        schemas.ArchivoCargaResultado(
            nombreOriginal=r.nombre_original,
            guardadoComo=r.guardado_como,
            tipo=r.tipo,
            ok=r.ok,
            error=r.error,
        )
        for r in resultados
    ])


@router.post("/ejecutar-pipeline", dependencies=[Depends(security.requiere_admin)])
def ejecutar_pipeline():
    if pipeline_runner.hay_corrida_en_curso():
        return {"ok": False, "detail": "Ya hay una actualización en curso."}
    corrida_id = pipeline_runner.iniciar_actualizacion("actualizacion_completa")
    return {"ok": True, "corridaId": corrida_id}


@router.get("/estado")
def estado():
    return {"enCurso": pipeline_runner.hay_corrida_en_curso()}
