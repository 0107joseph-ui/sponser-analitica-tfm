"""Punto de entrada de la app: instancia FastAPI, middlewares (sesión, CORS)
y registro de routers. Arrancar con `uvicorn app.main:app`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from . import config
from .db import init_db
from .routers import auth, bootstrap, datos, pedido, reportes, usuarios

app = FastAPI(title="Sponser Analítica API")

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET_KEY,
    same_site="lax",
    https_only=config.SESSION_HTTPS_ONLY,
)
app.add_middleware(
    CORSMiddleware,
    # allow_credentials + wildcard origin no es válido (los navegadores lo rechazan);
    # se permite cualquier puerto local (server estático de prueba) y los orígenes
    # de Claude Design, por si el iframe del canvas sí puede llamar a localhost.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://([a-z0-9-]+\.)?claude\.ai",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static/product_images", StaticFiles(directory=config.IMAGES_DIR), name="product_images")

app.include_router(auth.router)
app.include_router(bootstrap.router)
app.include_router(pedido.router)
app.include_router(datos.router)
app.include_router(reportes.router)
app.include_router(usuarios.router)


@app.on_event("startup")
def on_startup() -> None:
    if config.SESSION_SECRET_KEY == config.SESSION_SECRET_KEY_DEFAULT:
        print(
            "[ADVERTENCIA] SPONSER_API_SECRET_KEY no está seteada -- usando la "
            "clave de desarrollo del código fuente. Las sesiones, códigos OTP y "
            "enlaces de restablecimiento quedan firmados con una clave pública. "
            "Fijar esa variable de entorno antes de exponer este backend fuera de localhost.",
            flush=True,
        )
    init_db()


@app.get("/api/health")
def health():
    return {"ok": True}
