"""Entrena el modelo de demanda diaria por producto.

Uso: python train.py (desde sponser_etl/modeling/). Cada corrida crea una
versión nueva en versions/vN/ y actualiza versions/LATEST.txt. Split temporal:
últimos DIAS_VALIDACION días como validación, el resto entrenamiento.

TIPO_MODELO: "tweedie_directo" (un LightGBM prediciendo unidades directo,
usado en v1-v4) o "hurdle" (clasificador P(hay venta) + regresor Poisson de
cantidad dado que hubo venta; predicción final = P(venta) x E[cantidad|venta]).
"""

from __future__ import annotations

import datetime
import json
import os

import lightgbm as lgb
import numpy as np
import pandas as pd

import features
import forecast
import versioning

TIPO_MODELO = "hurdle"
DIAS_VALIDACION = 150  # debe coincidir con DIAS_BACKTEST en backtest.py
DIAS_ACTIVIDAD_RECIENTE = 90  # sin venta en esta ventana antes del corte -> se considera inactivo

HYPERPARAMS_COMUNES = dict(
    n_estimators=1500,
    learning_rate=0.03,
    num_leaves=15,
    min_child_samples=50,
    reg_lambda=1.0,
    random_state=42,
)
HYPERPARAMS_TWEEDIE = dict(objective="tweedie", tweedie_variance_power=1.1, **HYPERPARAMS_COMUNES)
HYPERPARAMS_CLASIFICACION = dict(objective="binary", **HYPERPARAMS_COMUNES)
HYPERPARAMS_REGRESION_POSITIVOS = dict(objective="poisson", **HYPERPARAMS_COMUNES)


def wmape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.abs(y_true - y_pred).sum() / max(np.abs(y_true).sum(), 1e-9))


def calibrar_via_backtest_recursivo(
    modelo_crudo: dict,
    encoders: dict,
    ventas: pd.DataFrame,
    cutoff: pd.Timestamp,
    fecha_max: pd.Timestamp,
    dias: int,
    dias_actividad_reciente: int = DIAS_ACTIVIDAD_RECIENTE,
    n_terciles: int = 3,
) -> dict:
    """Calibración por producto, corriendo el mismo pronóstico recursivo de
    forecast.py/backtest.py sobre la ventana de validación (modelo sin
    calibrar) en vez de usar métricas de un solo paso."""
    historial_train = ventas[ventas["fecha"] < cutoff].copy()
    print(f"\nCalculando calibración con un backtest recursivo interno "
          f"({dias} días, {cutoff.date()} -> {fecha_max.date()}, modelo sin calibrar)...")
    pred_df = forecast.forecast_recursivo(modelo_crudo, encoders, dias=dias, historial_inicial=historial_train)

    productos = historial_train["codigo_producto"].unique()
    fechas_val = pd.date_range(cutoff, fecha_max, freq="D")
    idx = pd.MultiIndex.from_product([productos, fechas_val], names=["codigo_producto", "fecha"])
    real_df = pd.DataFrame(index=idx).reset_index().merge(ventas, on=["codigo_producto", "fecha"], how="left")
    real_df["unidades_vendidas"] = real_df["unidades_vendidas"].fillna(0.0)
    comp = pred_df.merge(real_df, on=["fecha", "codigo_producto"], how="inner")

    por_producto = comp.groupby("codigo_producto").agg(
        real=("unidades_vendidas", "sum"),
        pronosticado=("unidades_pronosticadas", "sum"),
    )

    venta_reciente = historial_train[historial_train["fecha"] >= cutoff - pd.Timedelta(days=dias_actividad_reciente)]
    venta_reciente_por_producto = venta_reciente.groupby("codigo_producto")["unidades_vendidas"].sum()
    por_producto["venta_reciente"] = por_producto.index.map(venta_reciente_por_producto).fillna(0.0)

    productos_inactivos = por_producto.index[por_producto["venta_reciente"] <= 0].tolist()

    activos = por_producto[por_producto["venta_reciente"] > 0].copy()
    promedio_historico = historial_train.groupby("codigo_producto")["unidades_vendidas"].mean()
    activos["promedio_historico"] = activos.index.map(promedio_historico).fillna(0.0)

    tercil_por_producto: dict[str, str] = {}
    factores_por_tercil: dict[str, float] = {}
    if len(activos) >= n_terciles:
        terciles = pd.qcut(activos["promedio_historico"], n_terciles, duplicates="drop")
        for etiqueta, grupo in activos.groupby(terciles, observed=True):
            etiqueta = str(etiqueta)
            real_sum = grupo["real"].sum()
            pred_sum = grupo["pronosticado"].sum()
            factores_por_tercil[etiqueta] = float(real_sum / pred_sum) if pred_sum > 1e-9 else 1.0
            for cod in grupo.index:
                tercil_por_producto[cod] = etiqueta

    return {
        "productos_inactivos": productos_inactivos,
        "tercil_por_producto": tercil_por_producto,
        "factores_por_tercil": factores_por_tercil,
        "dias_actividad_reciente": dias_actividad_reciente,
        "n_productos_inactivos": len(productos_inactivos),
    }


def entrenar_tweedie_directo(X_train, y_train, X_val, y_val):
    modelo = lgb.LGBMRegressor(**HYPERPARAMS_TWEEDIE)
    modelo.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric="mae",
        categorical_feature=features.CATEGORICAL_ENC_FEATURES,
        callbacks=[lgb.early_stopping(stopping_rounds=80), lgb.log_evaluation(period=100)],
    )
    pred_val = np.clip(modelo.predict(X_val), 0, None)
    return {
        "tipo": "tweedie_directo",
        "boosters": {"regresion": modelo.booster_},
        "hyperparams": {"regresion": HYPERPARAMS_TWEEDIE},
        "pred_val": pred_val,
    }


def entrenar_hurdle(X_train, y_train, X_val, y_val):
    y_train_bin = (y_train > 0).astype(int)
    y_val_bin = (y_val > 0).astype(int)

    print("\n--- Etapa 1: clasificación (¿hay venta ese producto ese día?) ---")
    clasificador = lgb.LGBMClassifier(**HYPERPARAMS_CLASIFICACION)
    clasificador.fit(
        X_train, y_train_bin,
        eval_set=[(X_val, y_val_bin)],
        eval_metric="auc",
        categorical_feature=features.CATEGORICAL_ENC_FEATURES,
        callbacks=[lgb.early_stopping(stopping_rounds=80), lgb.log_evaluation(period=100)],
    )
    p_val = clasificador.predict_proba(X_val)[:, 1]

    print("\n--- Etapa 2: regresión (cuánto se vende, dado que sí hubo venta) ---")
    mask_train_pos = y_train > 0
    mask_val_pos = y_val > 0
    regresor = lgb.LGBMRegressor(**HYPERPARAMS_REGRESION_POSITIVOS)
    regresor.fit(
        X_train[mask_train_pos], y_train[mask_train_pos],
        eval_set=[(X_val[mask_val_pos], y_val[mask_val_pos])],
        eval_metric="mae",
        categorical_feature=features.CATEGORICAL_ENC_FEATURES,
        callbacks=[lgb.early_stopping(stopping_rounds=80), lgb.log_evaluation(period=100)],
    )
    q_val = regresor.predict(X_val)

    pred_val = np.clip(p_val * q_val, 0, None)

    return {
        "tipo": "hurdle",
        "boosters": {"clasificacion": clasificador.booster_, "regresion": regresor.booster_},
        "hyperparams": {"clasificacion": HYPERPARAMS_CLASIFICACION, "regresion": HYPERPARAMS_REGRESION_POSITIVOS},
        "pred_val": pred_val,
        "diagnostico": {
            "auc_clasificacion": float(clasificador.booster_.best_score["valid_0"]["auc"]),
            "tasa_venta_real_val": float(y_val_bin.mean()),
        },
    }


def main() -> int:
    version = versioning.siguiente_version()
    vdir = versioning.version_dir(version)
    print(f"Entrenando versión {version} (tipo: {TIPO_MODELO}) -> {vdir}")

    print("Construyendo panel histórico (producto x fecha, calendario, eventos, rezagos)...")
    panel = features.construir_panel()

    encoders = features.construir_encoders(panel)
    panel = features.aplicar_encoders(panel, encoders)
    features.guardar_encoders(encoders, os.path.join(vdir, "encoders.json"))

    fecha_max = panel["fecha"].max()
    cutoff = fecha_max - pd.Timedelta(days=DIAS_VALIDACION - 1)
    train_df = panel[panel["fecha"] < cutoff]
    val_df = panel[panel["fecha"] >= cutoff]

    print(f"Entrenamiento: {len(train_df):,} filas ({panel['fecha'].min().date()} -> {(cutoff - pd.Timedelta(days=1)).date()})")
    print(f"Validación:    {len(val_df):,} filas ({cutoff.date()} -> {fecha_max.date()})")

    X_train, y_train = train_df[features.MODEL_FEATURES], train_df[features.TARGET_COLUMN]
    X_val, y_val = val_df[features.MODEL_FEATURES], val_df[features.TARGET_COLUMN]

    if TIPO_MODELO == "hurdle":
        resultado = entrenar_hurdle(X_train, y_train, X_val, y_val)
    elif TIPO_MODELO == "tweedie_directo":
        resultado = entrenar_tweedie_directo(X_train, y_train, X_val, y_val)
    else:
        raise ValueError(f"TIPO_MODELO desconocido: {TIPO_MODELO}")

    pred_val_crudo = resultado["pred_val"]
    mae_crudo = float(np.mean(np.abs(y_val.to_numpy() - pred_val_crudo)))
    w_crudo = wmape(y_val.to_numpy(), pred_val_crudo)

    modelo_crudo = {"tipo": resultado["tipo"], **resultado["boosters"], "calibracion": None}
    ventas_completas = features.cargar_ventas_diarias()
    calibracion = calibrar_via_backtest_recursivo(
        modelo_crudo, encoders, ventas_completas, cutoff, fecha_max, DIAS_VALIDACION
    )
    features.guardar_calibracion(calibracion, os.path.join(vdir, "calibracion.json"))

    pred_val = features.aplicar_calibracion(pred_val_crudo, val_df, calibracion)
    mae = float(np.mean(np.abs(y_val.to_numpy() - pred_val)))
    rmse = float(np.sqrt(np.mean((y_val.to_numpy() - pred_val) ** 2)))
    w = wmape(y_val.to_numpy(), pred_val)

    print(f"\nFactores de calibración por tercil: {calibracion['factores_por_tercil']}")
    print(f"Productos sin venta en los últimos {DIAS_ACTIVIDAD_RECIENTE} días antes del corte "
          f"(forzados a 0): {calibracion['n_productos_inactivos']}")

    print(f"\nMétricas de validación single-step ({TIPO_MODELO}, con rezagos reales — la calibración "
          f"se calculó recursivamente pero acá se aplica también a este set de un solo paso, solo "
          f"como referencia rápida; la comparación real está en backtest.py):")
    print(f"  Sin calibrar -> MAE: {mae_crudo:.3f}  WMAPE: {w_crudo:.1%}")
    print(f"  Calibrado    -> MAE: {mae:.3f}  RMSE: {rmse:.3f}  WMAPE: {w:.1%}")
    if "diagnostico" in resultado:
        d = resultado["diagnostico"]
        print(f"  AUC clasificación (¿hay venta?): {d['auc_clasificacion']:.3f}")
        print(f"  Tasa real de días con venta en validación: {d['tasa_venta_real_val']:.1%}")

    model_paths = {}
    for nombre, booster in resultado["boosters"].items():
        path = os.path.join(vdir, f"model_{nombre}.txt")
        booster.save_model(path)
        model_paths[nombre] = os.path.basename(path)
        print(f"Modelo '{nombre}' guardado en: {path}")

        importancias = pd.Series(
            booster.feature_importance(importance_type="gain"),
            index=booster.feature_name(),
        ).sort_values(ascending=False)
        print(f"\nTop 10 features por importancia ({nombre}, gain):")
        print(importancias.head(10).to_string())

    config = {
        "version": version,
        "tipo_modelo": TIPO_MODELO,
        "fecha_entrenamiento": datetime.datetime.now().isoformat(timespec="seconds"),
        "dias_validacion": DIAS_VALIDACION,
        "lags": features.LAGS,
        "rolling_windows": features.ROLLING_WINDOWS,
        "model_features": features.MODEL_FEATURES,
        "hyperparams": resultado["hyperparams"],
        "model_files": model_paths,
    }
    with open(os.path.join(vdir, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    metrics = {"mae_validacion": mae, "rmse_validacion": rmse, "wmape_validacion": w}
    if "diagnostico" in resultado:
        metrics.update(resultado["diagnostico"])
    with open(os.path.join(vdir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    versioning.actualizar_resumen(version, {
        "tipo_modelo": TIPO_MODELO,
        "fecha": config["fecha_entrenamiento"],
        "dias_validacion": DIAS_VALIDACION,
        "n_features": len(features.MODEL_FEATURES),
        "mae_validacion": round(mae, 4),
        "wmape_validacion": round(w, 4),
    })

    versioning.marcar_latest(version)
    print(f"\nVersión {version} lista y marcada como LATEST.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
