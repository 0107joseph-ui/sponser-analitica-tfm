"""Utilidades para manejar versiones del modelo entrenado.

Cada corrida de train.py crea una carpeta nueva versions/vN/ (nunca sobreescribe
una anterior), con su modelo, encoders, configuración y métricas. versions/LATEST.txt
apunta a la más reciente, para que forecast.py y backtest.py la usen por defecto.
"""

from __future__ import annotations

import os
import re

import pandas as pd

VERSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "versions")
LATEST_PATH = os.path.join(VERSIONS_DIR, "LATEST.txt")
SUMMARY_PATH = os.path.join(VERSIONS_DIR, "summary.csv")


def listar_versiones() -> list[str]:
    if not os.path.isdir(VERSIONS_DIR):
        return []
    vs = [
        d for d in os.listdir(VERSIONS_DIR)
        if re.fullmatch(r"v\d+", d) and os.path.isdir(os.path.join(VERSIONS_DIR, d))
    ]
    return sorted(vs, key=lambda v: int(v[1:]))


def siguiente_version() -> str:
    vs = listar_versiones()
    if not vs:
        return "v1"
    return f"v{int(vs[-1][1:]) + 1}"


def version_dir(version: str) -> str:
    d = os.path.join(VERSIONS_DIR, version)
    os.makedirs(d, exist_ok=True)
    return d


def marcar_latest(version: str) -> None:
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        f.write(version)


def resolver_version(version: str | None = None) -> str:
    """Si version es None, devuelve la última entrenada (versions/LATEST.txt)."""
    if version:
        return version
    if not os.path.exists(LATEST_PATH):
        raise FileNotFoundError("No hay ninguna versión entrenada todavía. Corre train.py primero.")
    with open(LATEST_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def actualizar_resumen(version: str, columnas: dict) -> None:
    """Agrega o actualiza (upsert) la fila de `version` en versions/summary.csv."""
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    if os.path.exists(SUMMARY_PATH):
        df = pd.read_csv(SUMMARY_PATH)
    else:
        df = pd.DataFrame(columns=["version"])

    if "version" not in df.columns:
        df["version"] = []

    fila = {"version": version, **columnas}
    if version in df["version"].astype(str).values:
        idx = df.index[df["version"].astype(str) == version][0]
        for k, v in fila.items():
            df.loc[idx, k] = v
    else:
        df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)

    df.to_csv(SUMMARY_PATH, index=False)
