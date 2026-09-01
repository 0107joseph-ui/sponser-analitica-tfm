"""codigo_producto -> URL de imagen real de producto (o placeholder si no hay match).

Fuente: sponser_etl/data/match_report.csv (125/175 SKUs con imagen real) +
sponser_etl/data/images/ (archivos servidos como estáticos en /static/product_images).
Cuando un SKU tiene varias imágenes candidatas (separadas por ';'), se usa la primera.
"""

from __future__ import annotations

import os

import pandas as pd

from ..config import MATCH_REPORT_PATH, PUBLIC_BASE_URL

STATIC_PREFIX = f"{PUBLIC_BASE_URL}/static/product_images"
PLACEHOLDER_URL = "assets/isotonic-peach.png"  # ícono neutro ya existente en el diseño

_cache: dict | None = None
_cache_mtime: float | None = None


def _cargar() -> dict[str, str]:
    df = pd.read_csv(MATCH_REPORT_PATH, dtype=str)
    mapa: dict[str, str] = {}
    for _, row in df.iterrows():
        if row.get("tiene_imagen") != "SI":
            continue
        archivos = str(row.get("archivos_imagen") or "").strip()
        if not archivos:
            continue
        primer_archivo = archivos.split(";")[0].strip()
        if primer_archivo:
            mapa[row["codigo_producto"]] = f"{STATIC_PREFIX}/{primer_archivo}"
    return mapa


def _mapa_actual() -> dict[str, str]:
    global _cache, _cache_mtime
    mtime = os.path.getmtime(MATCH_REPORT_PATH)
    if _cache is None or mtime != _cache_mtime:
        _cache = _cargar()
        _cache_mtime = mtime
    return _cache


def url_para(codigo_producto: str) -> str:
    return _mapa_actual().get(codigo_producto, PLACEHOLDER_URL)
