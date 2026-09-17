"""Simulación FIFO de consumo de inventario y asignación de ofertas de proveedor,
priorizando siempre lo que vence más pronto, para minimizar pérdida por vencimiento.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def ajustar_a_moq_y_multiple(qty: float, moq: float, multiple: float) -> int:
    if qty <= 0:
        return 0
    qty = max(int(qty), int(moq or 0))
    m = int(multiple or 0)
    if m > 0 and qty % m != 0:
        qty += (m - qty % m)
    return int(qty)


def rango_fechas(inicio: date, fin: date):
    dias = (fin - inicio).days
    for d in range(dias + 1):
        yield inicio + timedelta(days=d)


def construir_horizonte(sku_inv: pd.DataFrame, sku_ofertas: pd.DataFrame, lt: int, cov: int, saf: int, hoy: date):
    base_fin = hoy + timedelta(days=max(0, int(lt) + int(cov) + int(saf)))
    max_exp_inv = max(sku_inv["expiry_date"]) if not sku_inv.empty else base_fin
    max_exp_of = max(sku_ofertas["expiry_date"]) if not sku_ofertas.empty else base_fin
    fin = max(base_fin, max_exp_inv, max_exp_of)
    fin = min(fin, hoy + timedelta(days=540))  # tope 18 meses
    return hoy, fin


def consumir_fifo_solo_inventario(demanda_diaria: pd.Series, sku_inv: pd.DataFrame, start_date: date, end_date: date):
    """demanda_diaria: serie indexada por fecha con la curva día a día del
    pronóstico (no un promedio aplanado, para no perder picos por evento que
    puedan definir si un lote vence antes de venderse). Ver run.py."""
    fechas = list(rango_fechas(start_date, end_date))
    residual = pd.Series([float(demanda_diaria.get(f, 0.0)) for f in fechas], index=fechas, dtype=float)
    servido = pd.Series([0] * len(fechas), index=fechas, dtype=float)

    if sku_inv.empty or residual.sum() <= 0:
        return residual, 0, servido

    lots = sku_inv.sort_values("expiry_date").copy()
    lots_state = [{"expiry": r.expiry_date, "qty": float(r.qty)} for _, r in lots.iterrows()]

    for i, fecha in enumerate(fechas):
        demanda = residual.iloc[i]
        if demanda <= 0:
            continue
        for lot in lots_state:
            if demanda <= 0:
                break
            if lot["qty"] <= 0:
                continue
            if lot["expiry"] < fecha:
                continue
            usar = min(lot["qty"], demanda)
            lot["qty"] -= usar
            demanda -= usar
            servido.iloc[i] += usar
        residual.iloc[i] = demanda

    expirado = 0
    fin_h = end_date
    for lot in lots_state:
        if lot["qty"] > 0 and lot["expiry"] <= fin_h:
            expirado += lot["qty"]
            lot["qty"] = 0

    return residual, float(expirado), servido


def asignar_ofertas_a_residual(residual_series: pd.Series, opciones: pd.DataFrame):
    """Cubre la demanda residual (no cubierta por inventario actual) con ofertas
    de proveedor ya negociadas, priorizando la que vence más pronto (igual criterio
    que consumir_fifo_solo_inventario), y solo desde su fecha de llegada.

    Devuelve: (asignado, residual_restante, riesgo)
      - asignado: dict option_id -> unidades consumidas por la demanda.
      - residual_restante: pd.Series, demanda que sigue sin cubrir después de aplicar ofertas.
      - riesgo: dict option_id -> {"at_risk": unidades de la oferta que quedaron sin usar
        al llegar su vencimiento (desperdicio potencial)}.
    """
    residual = residual_series.copy()
    asignado = {opt: 0.0 for opt in opciones["option_id"]}
    riesgo = {opt: {"at_risk": 0.0} for opt in opciones["option_id"]}

    if opciones.empty:
        return asignado, residual, riesgo

    opciones_sorted = opciones.sort_values("expiry_date").copy()

    for _, row in opciones_sorted.iterrows():
        opt_id = row["option_id"]
        arr = row["arrival_date"]
        exp = row["expiry_date"]
        disponible = float(row["available_qty"])

        for fecha in residual.index:
            if disponible <= 0:
                break
            if fecha < arr or fecha > exp:
                continue
            demanda = residual.loc[fecha]
            if demanda <= 0:
                continue
            usar = min(disponible, demanda)
            residual.loc[fecha] -= usar
            disponible -= usar
            asignado[opt_id] += usar

        if disponible > 0:
            riesgo[opt_id]["at_risk"] = disponible

    return asignado, residual, riesgo
