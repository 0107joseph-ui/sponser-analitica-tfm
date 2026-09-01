"""Backtest: mide qué tan preciso es el pronóstico recursivo comparándolo contra
ventas reales que ya ocurrieron (no contra el futuro).

Usa una versión ya entrenada (por defecto la más reciente) — train.py la entrenó
excluyendo los últimos DIAS_BACKTEST días reales — y corre exactamente el mismo
procedimiento recursivo de forecast.py, pero apuntado a ese período que ya
conocemos, para comparar predicción vs. venta real día por día, mes por mes y
producto por producto. Los resultados quedan guardados dentro de la carpeta de
la versión evaluada (versions/vN/), y el resumen global en versions/summary.csv.

Uso: python backtest.py (después de correr train.py)
"""

from __future__ import annotations

import os

import lightgbm as lgb
import numpy as np
import pandas as pd

import features
import forecast
import versioning

DIAS_BACKTEST = 150  # debe coincidir con DIAS_VALIDACION en train.py


def wmape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.abs(y_true - y_pred).sum() / max(np.abs(y_true).sum(), 1e-9))


def cargar_descripciones() -> pd.Series:
    """codigo_producto -> descripción más frecuente en ventas_detalle.csv."""
    ventas = pd.read_csv(
        os.path.join(features.PROCESSED_DIR, "ventas_detalle.csv"),
        dtype={"codigo_producto": str},
        usecols=["codigo_producto", "item_descripcion"],
    )
    ventas["descripcion"] = ventas["item_descripcion"].astype(str).str.replace(
        r"^\s*\d+\s*", "", regex=True
    ).str.strip()
    return ventas.groupby("codigo_producto")["descripcion"].agg(lambda s: s.mode().iat[0])


def main() -> int:
    version, modelo, encoders = forecast.cargar_modelo()
    vdir = versioning.version_dir(version)
    print(f"Backtesteando versión: {version} (tipo: {modelo['tipo']})")

    ventas = features.cargar_ventas_diarias()
    fecha_max = ventas["fecha"].max()
    cutoff = fecha_max - pd.Timedelta(days=DIAS_BACKTEST - 1)

    historial_train = ventas[ventas["fecha"] < cutoff].copy()
    print(f"Pronosticando recursivamente {DIAS_BACKTEST} días desde {cutoff.date()} "
          f"hasta {fecha_max.date()} (la versión {version} NO vio datos reales de ese período al entrenar)...")

    pred_df = forecast.forecast_recursivo(
        modelo, encoders, dias=DIAS_BACKTEST, historial_inicial=historial_train
    )

    productos = historial_train["codigo_producto"].unique()
    fechas_val = pd.date_range(cutoff, fecha_max, freq="D")
    idx = pd.MultiIndex.from_product([productos, fechas_val], names=["codigo_producto", "fecha"])
    real_df = pd.DataFrame(index=idx).reset_index().merge(ventas, on=["codigo_producto", "fecha"], how="left")
    real_df["unidades_vendidas"] = real_df["unidades_vendidas"].fillna(0.0)

    comp = pred_df.merge(real_df, on=["fecha", "codigo_producto"], how="inner")
    comp["error"] = comp["unidades_pronosticadas"] - comp["unidades_vendidas"]
    comp["dia_horizonte"] = (comp["fecha"] - cutoff).dt.days + 1

    mae = comp["error"].abs().mean()
    rmse = np.sqrt((comp["error"] ** 2).mean())
    w = wmape(comp["unidades_vendidas"].to_numpy(), comp["unidades_pronosticadas"].to_numpy())
    sesgo = comp["error"].mean()
    total_real = comp["unidades_vendidas"].sum()
    total_pred = comp["unidades_pronosticadas"].sum()

    print(f"\n=== Backtest recursivo ({DIAS_BACKTEST} días, comparado contra venta real) ===")
    print(f"  Filas comparadas: {len(comp):,} (producto x día)")
    print(f"  MAE:   {mae:.3f} unidades/producto/día")
    print(f"  RMSE:  {rmse:.3f}")
    print(f"  WMAPE: {w:.1%}")
    print(f"  Sesgo promedio: {sesgo:+.3f} unidades/producto/día ({'SUBESTIMA' if sesgo < 0 else 'SOBRESTIMA'})")
    print(f"  Total real en el período:         {total_real:,.0f} unidades")
    print(f"  Total pronosticado en el período: {total_pred:,.0f} unidades ({total_pred / total_real:.1%} del real)")

    print("\n--- Precisión por tramo del horizonte (agrupado en rangos de días; un solo día suelto puede\n"
          "    tener venta real ~0 entre 175 productos y desestabilizar el WMAPE) ---")
    tramos = [(1, 7), (8, 14), (15, 30), (31, 60), (61, 90), (91, 120), (121, 150)]
    tramos = [t for t in tramos if t[0] <= DIAS_BACKTEST]
    comp["tramo"] = pd.cut(
        comp["dia_horizonte"],
        bins=[t[0] - 1 for t in tramos] + [min(tramos[-1][1], DIAS_BACKTEST)],
        labels=[f"días {t[0]}-{min(t[1], DIAS_BACKTEST)}" for t in tramos],
    )
    por_tramo = comp.groupby("tramo", observed=True).apply(
        lambda g: pd.Series({
            "real": g["unidades_vendidas"].sum(),
            "pronosticado": g["unidades_pronosticadas"].sum(),
            "wmape": wmape(g["unidades_vendidas"].to_numpy(), g["unidades_pronosticadas"].to_numpy()),
        }),
        include_groups=False,
    )
    print(por_tramo.to_string(float_format=lambda x: f"{x:.3f}"))

    print("\n--- Top 10 productos de mayor venta real: real vs pronosticado (total del período) ---")
    por_producto = comp.groupby("codigo_producto").agg(
        real=("unidades_vendidas", "sum"),
        pronosticado=("unidades_pronosticadas", "sum"),
    )
    por_producto["error_pct"] = (
        (por_producto["pronosticado"] - por_producto["real"]) / por_producto["real"].replace(0, np.nan)
    )
    top10 = por_producto.sort_values("real", ascending=False).head(10)
    print(top10.to_string(float_format=lambda x: f"{x:.2f}"))

    por_producto.reset_index().sort_values("real", ascending=False).to_csv(
        os.path.join(vdir, "backtest_resultados.csv"), index=False
    )

    # --- Agregación mensual ---
    comp["mes"] = comp["fecha"].dt.to_period("M").astype(str)
    descripciones = cargar_descripciones()

    mensual_producto = comp.groupby(["codigo_producto", "mes"]).agg(
        real=("unidades_vendidas", "sum"),
        pronosticado=("unidades_pronosticadas", "sum"),
    ).reset_index()
    mensual_producto["descripcion"] = mensual_producto["codigo_producto"].map(descripciones).fillna("")
    mensual_producto["error_pct"] = (
        (mensual_producto["pronosticado"] - mensual_producto["real"]) / mensual_producto["real"].replace(0, np.nan)
    )
    mensual_producto = mensual_producto[
        ["codigo_producto", "descripcion", "mes", "real", "pronosticado", "error_pct"]
    ].sort_values(["mes", "real"], ascending=[True, False])

    mensual_total = comp.groupby("mes").apply(
        lambda g: pd.Series({
            "real": g["unidades_vendidas"].sum(),
            "pronosticado": g["unidades_pronosticadas"].sum(),
            "wmape": wmape(g["unidades_vendidas"].to_numpy(), g["unidades_pronosticadas"].to_numpy()),
        }),
        include_groups=False,
    ).reset_index()
    mensual_total["error_pct"] = (mensual_total["pronosticado"] - mensual_total["real"]) / mensual_total["real"]

    print("\n=== Exactitud mensual, total compañía ===")
    print(mensual_total.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print("\n=== Distribución del error por combinación SKU-mes ===")
    valid = mensual_producto.dropna(subset=["error_pct"]).copy()
    valid["abs_error_pct"] = valid["error_pct"].abs()
    bandas = pd.cut(
        valid["abs_error_pct"],
        bins=[0, 0.20, 0.50, 1.0, np.inf],
        labels=["dentro de ±20%", "±20% a ±50%", "±50% a ±100%", "peor de ±100%"],
    )
    resumen_bandas = bandas.value_counts().sort_index()
    for etiqueta, cuenta in resumen_bandas.items():
        print(f"  {etiqueta}: {cuenta} de {len(valid)} combinaciones SKU-mes ({cuenta / len(valid):.1%})")

    mensual_producto.to_csv(os.path.join(vdir, "backtest_mensual_producto.csv"), index=False)
    mensual_total.to_csv(os.path.join(vdir, "backtest_mensual_total.csv"), index=False)
    print(f"\nResultados del backtest guardados en: {vdir}")

    pct_dentro_20 = float((valid["abs_error_pct"] <= 0.20).mean())
    versioning.actualizar_resumen(version, {
        "dias_backtest": DIAS_BACKTEST,
        "wmape_backtest": round(w, 4),
        "sesgo_backtest": round(sesgo, 4),
        "pct_total_real_backtest": round(total_pred / total_real, 4),
        "pct_sku_mes_dentro_20pct": round(pct_dentro_20, 4),
    })
    print(f"\nResumen de versiones actualizado en: {versioning.SUMMARY_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
