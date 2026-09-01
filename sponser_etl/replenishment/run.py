"""Orquestador: demanda pronosticada + inventario real + parámetros de compra
-> recomendación de compra por producto (FIFO anti-vencimiento).

Uso: python run.py (desde sponser_etl/replenishment/)
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # sponser_etl/ (para load.py)

import load
import fifo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # sponser_etl/
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")

ADVERTENCIA = """
============================================================
ADVERTENCIA - revisar antes de usar para decisiones de compra:
  1. El pronóstico (modelo hurdle, calibrado por tercil de
     volumen) tiene un margen de error real a nivel de SKU
     individual, mayor cuanto más chico es el volumen del
     producto — ver modeling/versions/summary.csv y
     versions/LATEST/backtest_resultados.csv (LATEST.txt indica
     cuál es la versión vigente) antes de confiar en un número
     exacto por SKU chico.
  2. sku_config.csv arrancó con valores PLACEHOLDER de lead
     time / MOQ / múltiplo (no son datos reales del proveedor
     Sponser) — corrígelos ahí antes de confiar en el resultado.
  3. ofertas_proveedor.csv está vacío: se asume que no hay
     ningún pedido ya en camino: toda la demanda no cubierta
     por el inventario actual aparece como "a pedir".
============================================================
"""


def cargar_as_of_date():
    """Última fecha con venta real registrada -- misma ancla temporal que usa
    sponser_api (data_access.get_as_of_date()) para todo el resto del tablero.
    No usar la fecha real del sistema aquí: dejaría el horizonte de compra
    (y el riesgo de vencimiento) desfasado varios meses respecto a lo que
    muestran el catálogo y el detalle de producto."""
    ventas = pd.read_csv(
        os.path.join(PROCESSED_DIR, "ventas_diarias_producto.csv"), parse_dates=["fecha"]
    )
    return ventas["fecha"].max().date()


def cargar_inputs():
    demanda = pd.read_csv(
        os.path.join(PROCESSED_DIR, "demanda_promedio_sku.csv"), dtype={"codigo_producto": str}
    )

    inv = pd.read_csv(
        os.path.join(PROCESSED_DIR, "inventario_lotes.csv"), dtype={"codigo_producto": str}
    )
    inv = inv.rename(columns={"codigo_producto": "sku", "lote_id": "lot_id", "cantidad": "qty"})
    inv["expiry_date"] = pd.to_datetime(inv["fecha_vencimiento"], errors="coerce")
    fecha_lejana = pd.Timestamp.today() + pd.Timedelta(days=3650)
    inv["expiry_date"] = inv["expiry_date"].fillna(fecha_lejana).dt.date

    cfg = pd.read_csv(os.path.join(BASE_DIR, "sku_config.csv"), dtype={"codigo_producto": str})

    ofertas = pd.read_csv(os.path.join(BASE_DIR, "ofertas_proveedor.csv"), dtype={"sku": str})
    if not ofertas.empty:
        ofertas["arrival_date"] = pd.to_datetime(ofertas["arrival_date"]).dt.date
        ofertas["expiry_date"] = pd.to_datetime(ofertas["expiry_date"]).dt.date

    return demanda, inv, cfg, ofertas


def cargar_descripciones() -> pd.Series:
    """codigo_producto -> descripción más frecuente en ventas_detalle.csv (cubre
    los 175 productos con demanda, a diferencia de inventario_lotes.csv que solo
    tiene descripción para los que además tienen stock registrado)."""
    ventas = pd.read_csv(
        os.path.join(PROCESSED_DIR, "ventas_detalle.csv"),
        dtype={"codigo_producto": str},
        usecols=["codigo_producto", "item_descripcion"],
    )
    ventas["descripcion"] = ventas["item_descripcion"].astype(str).str.replace(
        r"^\s*\d+\s*", "", regex=True
    ).str.strip()
    return ventas.groupby("codigo_producto")["descripcion"].agg(lambda s: s.mode().iat[0])


def calcular_recomendacion(demanda: pd.DataFrame, inv: pd.DataFrame, cfg: pd.DataFrame, ofertas: pd.DataFrame, hoy) -> pd.DataFrame:
    cfg_idx = cfg.set_index("codigo_producto")
    filas = []

    for _, row in demanda.iterrows():
        codigo = row["codigo_producto"]
        daily_demand = float(row["daily_demand"])

        if codigo in cfg_idx.index:
            params = cfg_idx.loc[codigo]
            lt, cov, saf = params["lead_time_days"], params["coverage_days"], params["safety_days"]
            moq, multiple = params["moq"], params["multiple"]
        else:
            lt, cov, saf, moq, multiple = 21, 30, 7, 1, 1

        sku_inv = inv[inv["sku"] == codigo][["lot_id", "qty", "expiry_date"]]
        sku_ofertas = ofertas[ofertas["sku"] == codigo] if not ofertas.empty else ofertas

        start, end = fifo.construir_horizonte(sku_inv, sku_ofertas, lt, cov, saf, hoy)
        residual, expirado, _servido = fifo.consumir_fifo_solo_inventario(daily_demand, sku_inv, start, end)
        asignado, residual_final, riesgo = fifo.asignar_ofertas_a_residual(residual, sku_ofertas)

        demanda_no_cubierta = float(residual_final.clip(lower=0).sum())
        unidades_a_pedir = fifo.ajustar_a_moq_y_multiple(demanda_no_cubierta, moq, multiple)
        riesgo_ofertas = sum(r["at_risk"] for r in riesgo.values())

        filas.append({
            "codigo_producto": codigo,
            "daily_demand": daily_demand,
            "dias_horizonte": (end - start).days + 1,
            "unidades_en_inventario": float(sku_inv["qty"].sum()) if not sku_inv.empty else 0.0,
            "unidades_en_riesgo_vencimiento": float(expirado) + float(riesgo_ofertas),
            "unidades_cubiertas_por_ofertas": float(sum(asignado.values())),
            "unidades_a_pedir": unidades_a_pedir,
        })

    return pd.DataFrame(filas).sort_values("unidades_a_pedir", ascending=False).reset_index(drop=True)


def main() -> int:
    print(ADVERTENCIA)

    demanda, inv, cfg, ofertas = cargar_inputs()
    hoy = cargar_as_of_date()
    print(f"Calculando recomendación de compra para {len(demanda)} productos (referencia: hoy = {hoy}, última venta real registrada)...")

    recomendacion = calcular_recomendacion(demanda, inv, cfg, ofertas, hoy)

    descripciones = cargar_descripciones()
    recomendacion.insert(1, "descripcion", recomendacion["codigo_producto"].map(descripciones).fillna(""))

    load.guardar_tablas({"recomendacion_compra": recomendacion}, PROCESSED_DIR)

    print(f"\nrecomendacion_compra.csv: {len(recomendacion):,} filas -> {PROCESSED_DIR}")
    print(f"Productos con unidades_a_pedir > 0: {(recomendacion['unidades_a_pedir'] > 0).sum()}")
    print(f"Total unidades a pedir (todos los productos): {recomendacion['unidades_a_pedir'].sum():,.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
