"""Rutas a los datos de sponser_etl y configuración del backend.

sponser_api es solo un consumidor de sponser_etl/ (lectura de datos y
disparo de sus scripts como subprocess) -- nunca importa su código como
librería para no acoplar dos proyectos que evolucionan por separado.
"""

from __future__ import annotations

import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))  # sponser_api/app
API_ROOT = os.path.dirname(APP_DIR)  # sponser_api/
PROJECT_ROOT = os.path.dirname(API_ROOT)  # .py/
SPONSER_ETL_DIR = os.path.join(PROJECT_ROOT, "sponser_etl")
RAW_DIR = os.path.join(SPONSER_ETL_DIR, "data", "raw")
PROCESSED_DIR = os.path.join(SPONSER_ETL_DIR, "data", "processed")
IMAGES_DIR = os.path.join(SPONSER_ETL_DIR, "data", "images")
MATCH_REPORT_PATH = os.path.join(SPONSER_ETL_DIR, "data", "match_report.csv")
SKU_CONFIG_PATH = os.path.join(SPONSER_ETL_DIR, "sku_config.csv")

VENTAS_DETALLE_PATH = os.path.join(PROCESSED_DIR, "ventas_detalle.csv")
VENTAS_DIARIAS_PATH = os.path.join(PROCESSED_DIR, "ventas_diarias_producto.csv")
CLIENTES_RFM_PATH = os.path.join(PROCESSED_DIR, "clientes_rfm.csv")
INVENTARIO_LOTES_PATH = os.path.join(PROCESSED_DIR, "inventario_lotes.csv")
FORECAST_DIARIO_PATH = os.path.join(PROCESSED_DIR, "forecast_demanda_producto.csv")
RECOMENDACION_COMPRA_PATH = os.path.join(PROCESSED_DIR, "recomendacion_compra.csv")
PATROCINIOS_PATH = os.path.join(PROCESSED_DIR, "patrocinios.csv")
MODEL_VERSIONS_DIR = os.path.join(SPONSER_ETL_DIR, "modeling", "versions")
MODEL_SUMMARY_PATH = os.path.join(MODEL_VERSIONS_DIR, "summary.csv")
MODEL_LATEST_PATH = os.path.join(MODEL_VERSIONS_DIR, "LATEST.txt")

DB_PATH = os.path.join(API_ROOT, "data", "sponser_api.db")
DB_URL = f"sqlite:///{DB_PATH}"

SESSION_SECRET_KEY_DEFAULT = "dev-secret-cambiar-en-produccion"
SESSION_SECRET_KEY = os.environ.get("SPONSER_API_SECRET_KEY", SESSION_SECRET_KEY_DEFAULT)

# Cookie de sesión: en producción (HTTPS real) debe ir en true para que el
# navegador nunca la mande por HTTP plano. En false por defecto porque el
# desarrollo local (http://localhost) no tiene TLS -- con true ahí el
# navegador descarta la cookie silenciosamente y el login parece "no hacer nada".
SESSION_HTTPS_ONLY = os.environ.get("SPONSER_API_SESSION_HTTPS_ONLY", "false").lower() == "true"

# Usuario inicial (se crea solo si la tabla usuarios está vacía, ver
# db._crear_usuarios_iniciales). Los defaults son para desarrollo local
# únicamente -- en un despliegue real hay que fijar estas dos variables de
# entorno ANTES del primer arranque, o la cuenta queda con una contraseña
# de ejemplo públicamente conocida (queda documentada en este mismo archivo).
ADMIN_EMAIL_DEFAULT = "m.jimenez@sponser.cr"
ADMIN_PASSWORD_DEFAULT = "sponser2026"
ADMIN_EMAIL = os.environ.get("SPONSER_API_ADMIN_EMAIL", ADMIN_EMAIL_DEFAULT)
ADMIN_PASSWORD = os.environ.get("SPONSER_API_ADMIN_PASSWORD", ADMIN_PASSWORD_DEFAULT)
ADMIN_NOMBRE = os.environ.get("SPONSER_API_ADMIN_NOMBRE", "M. Jiménez")

# El frontend corre en un origen distinto (otro puerto, o el canvas de Claude
# Design) -- las URLs de imágenes servidas por este backend deben ser
# absolutas o el navegador las resuelve contra el origen del frontend, no el
# del backend. Mismo host:puerto que API_BASE en el .dc.html.
PUBLIC_BASE_URL = os.environ.get("SPONSER_API_PUBLIC_BASE_URL", "http://localhost:8000")
MAX_INTENTOS_LOGIN = 5
BLOQUEO_MINUTOS = 15

# SMTP genérico (cualquier proveedor SMTP+STARTTLS estándar -- Office 365,
# correo corporativo propio, etc., no uno específico). Si SPONSER_API_SMTP_HOST
# no está seteado, el envío cae en modo consola (loguea el correo en vez de
# mandarlo) -- así se puede desarrollar/probar localmente sin credenciales
# reales; para el cliente final sí hay que setear las variables de entorno.
SMTP_HOST = os.environ.get("SPONSER_API_SMTP_HOST")
SMTP_PORT = int(os.environ.get("SPONSER_API_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SPONSER_API_SMTP_USER")
SMTP_PASSWORD = os.environ.get("SPONSER_API_SMTP_PASSWORD")
SMTP_FROM = os.environ.get("SPONSER_API_SMTP_FROM", SMTP_USER or "no-responder@sponser.cr")
SMTP_USE_TLS = os.environ.get("SPONSER_API_SMTP_USE_TLS", "true").lower() != "false"
SMTP_TIMEOUT_SEGUNDOS = int(os.environ.get("SPONSER_API_SMTP_TIMEOUT", "10"))

# Verificación en dos pasos (OTP por correo, obligatoria en cada login).
OTP_LARGO = 6
OTP_EXPIRA_MINUTOS = 10
OTP_REENVIO_COOLDOWN_SEGUNDOS = 30
OTP_MAX_REENVIOS = 3  # por ciclo de login; se reinicia con cada password OK

# Restablecimiento de contraseña por enlace de un solo uso.
RESET_TOKEN_EXPIRA_MINUTOS = 30
PASSWORD_MIN_LARGO = 8

# No hay un origen fijo del frontend hoy (ver allow_origin_regex de CORS en
# main.py, acepta cualquier puerto local) -- se fija a mano para poder armar
# el enlace del correo de restablecimiento de contraseña.
FRONTEND_BASE_URL = os.environ.get("SPONSER_API_FRONTEND_BASE_URL", "http://localhost:5500")

# Scripts del pipeline (se ejecutan como subprocess, nunca se importan).
PIPELINE_SCRIPT = os.path.join(SPONSER_ETL_DIR, "pipeline.py")
FORECAST_SCRIPT = os.path.join(SPONSER_ETL_DIR, "modeling", "forecast.py")
REPLENISHMENT_SCRIPT = os.path.join(SPONSER_ETL_DIR, "replenishment", "run.py")

# Carga de archivos crudos (ventas/inventario) desde "Carga de datos". Los
# archivos más grandes actuales rondan 2 MB -- 25 MB da margen sin permitir
# que alguien llene el disco con una subida.
CARGA_ARCHIVO_MAX_BYTES = 25 * 1024 * 1024
