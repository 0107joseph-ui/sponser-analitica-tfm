"""Escritura de las tablas procesadas a CSV."""

from __future__ import annotations

import os

import pandas as pd


def guardar_tablas(tablas: dict[str, pd.DataFrame], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for nombre, df in tablas.items():
        ruta = os.path.join(out_dir, f"{nombre}.csv")
        df.to_csv(ruta, index=False, encoding="utf-8")
