"""Dispara los scripts de sponser_etl como subprocess (nunca los importa) y
registra el resultado en corridas_pipeline. Corre en un hilo de background
para no bloquear el request que lo dispara -- el frontend hace polling a
GET /api/datos/estado mientras tanto.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import threading

from .. import config
from ..db import SessionLocal
from .. import models_db
from . import data_access

_lock = threading.Lock()
_en_curso = False


def hay_corrida_en_curso() -> bool:
    return _en_curso


def iniciar_actualizacion(tipo: str) -> int:
    """Crea el registro de bitácora y lanza el pipeline completo en background.
    Devuelve el id de la corrida para que el llamador lo pueda reportar."""
    global _en_curso
    db = SessionLocal()
    try:
        corrida = models_db.CorridaPipeline(tipo=tipo, estado="en_curso")
        db.add(corrida)
        db.commit()
        db.refresh(corrida)
        corrida_id = corrida.id
    finally:
        db.close()

    hilo = threading.Thread(target=_ejecutar, args=(corrida_id,), daemon=True)
    with _lock:
        _en_curso = True
    hilo.start()
    return corrida_id


def _correr_script(path: str, cwd: str) -> tuple[bool, str]:
    resultado = subprocess.run(
        [sys.executable, path], cwd=cwd, capture_output=True, text=True, timeout=1800
    )
    ok = resultado.returncode == 0
    salida = resultado.stdout[-4000:] if ok else (resultado.stderr or resultado.stdout)[-4000:]
    return ok, salida


def _ejecutar(corrida_id: int) -> None:
    global _en_curso
    db = SessionLocal()
    try:
        corrida = db.get(models_db.CorridaPipeline, corrida_id)
        pasos = [
            ("ETL (extract/transform/load)", config.PIPELINE_SCRIPT),
            ("Pronóstico de demanda", config.FORECAST_SCRIPT),
            ("Recomendación de compra", config.REPLENISHMENT_SCRIPT),
        ]
        for nombre, script in pasos:
            ok, salida = _correr_script(script, os.path.dirname(script))
            if not ok:
                corrida.estado = "error"
                corrida.mensaje = f"Falló en '{nombre}': {salida}"
                db.commit()
                return
        corrida.estado = "ok"
        corrida.mensaje = "Pipeline completo: ETL + pronóstico + recomendación de compra actualizados."
        db.commit()
        data_access.invalidar_cache()
    except Exception as exc:  # noqa: BLE001 - se reporta en la bitácora, no se re-lanza en el hilo
        corrida = db.get(models_db.CorridaPipeline, corrida_id)
        if corrida is not None:
            corrida.estado = "error"
            corrida.mensaje = str(exc)
            db.commit()
    finally:
        corrida = db.get(models_db.CorridaPipeline, corrida_id)
        if corrida is not None:
            corrida.finalizado_en = dt.datetime.utcnow()
            db.commit()
        db.close()
        with _lock:
            _en_curso = False
