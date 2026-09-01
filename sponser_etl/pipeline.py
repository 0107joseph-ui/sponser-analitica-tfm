"""Orquestador del pipeline ETL de ventas Sponser.

Uso: python pipeline.py
Lee los .xls/.xlsx crudos de RAW_DIR, limpia y agrega los datos, y escribe
las tablas resultantes en data/processed/. Se puede volver a correr tal
cual cuando llegue el archivo de un nuevo año/periodo.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

import extract
import transform
import load

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data", "raw")
OUT_DIR = os.path.join(BASE_DIR, "data", "processed")


def main() -> int:
    print(f"Leyendo archivos crudos desde: {RAW_DIR}")
    resultado = extract.extract_ventas(RAW_DIR)

    for err in resultado.errores:
        print(f"  [warning] {err}", file=sys.stderr)

    if resultado.ventas.empty:
        print("No se extrajo ninguna fila de ventas. Revisa RAW_DIR y los nombres de archivo.")
        return 1

    ventas_todo = transform.limpiar_ventas(resultado.ventas)
    ventas, patrocinios = transform.separar_patrocinios(ventas_todo)
    ventas_diarias = transform.construir_ventas_diarias_producto(ventas)
    clientes_rfm = transform.construir_clientes_rfm(ventas)

    try:
        inventario_raw = extract.extract_inventario(RAW_DIR)
        inventario_lotes = transform.construir_inventario_lotes(inventario_raw)
    except FileNotFoundError as e:
        print(f"  [warning] {e}", file=sys.stderr)
        inventario_lotes = transform.construir_inventario_lotes(pd.DataFrame())

    tablas = {
        "ventas_detalle": ventas,
        "ventas_diarias_producto": ventas_diarias,
        "clientes_rfm": clientes_rfm,
        "inventario_lotes": inventario_lotes,
        "patrocinios": patrocinios,
    }
    load.guardar_tablas(tablas, OUT_DIR)

    print("\nResumen:")
    for nombre, df in tablas.items():
        print(f"  {nombre}: {len(df):,} filas -> {os.path.join(OUT_DIR, nombre + '.csv')}")

    if not ventas.empty:
        print(f"\n  Rango de fechas en ventas: {ventas['fecha'].min().date()} -> {ventas['fecha'].max().date()}")
        print(f"  Facturas distintas: {ventas['factura_id'].nunique():,}")
        print(f"  Clientes distintos: {ventas['id_cliente'].nunique():,}")
        monedas_no_usd = (ventas['moneda'] != 'USD').sum()
        print(f"  Filas con moneda distinta de USD tras conversión: {monedas_no_usd}")
    if not patrocinios.empty:
        print(f"\n  Patrocinios separados: {len(patrocinios):,} filas, "
              f"{patrocinios['id_cliente'].nunique():,} clientes, "
              f"${patrocinios['monto_total'].sum():,.2f} netos (excluidos de ventas)")

    if resultado.errores:
        print(f"\n{len(resultado.errores)} advertencia(s) durante la extracción (ver arriba).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
