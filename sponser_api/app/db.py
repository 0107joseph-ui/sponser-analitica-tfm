"""Motor y sesión de SQLAlchemy (SQLite), y arranque/migración de la base."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
engine = create_engine(config.DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models_db  # noqa: F401 (registra las tablas en Base.metadata)

    Base.metadata.create_all(bind=engine)
    _migrar_columnas_usuarios()
    _crear_usuarios_iniciales()


def _migrar_columnas_usuarios() -> None:
    """No hay Alembic (DB de desarrollo, SQLite) -- create_all() no agrega
    columnas a tablas ya existentes. Se agregan a mano, de forma idempotente
    (corre en cada arranque, solo actúa si falta la columna), sin tocar filas
    existentes.

    PRAGMA table_info es sintaxis de SQLite -- en producción (Postgres, ver
    config.DB_URL) esta función no aplica: create_all() ya crea la tabla
    completa con todas las columnas actuales porque ahí la tabla no existe
    todavía en el primer arranque."""
    if engine.dialect.name != "sqlite":
        return
    columnas_nuevas = {
        "otp_hash": "VARCHAR",
        "otp_expira_en": "DATETIME",
        "otp_enviado_en": "DATETIME",
        "otp_reenvios": "INTEGER DEFAULT 0",
        "reset_token_hash": "VARCHAR",
        "reset_token_expira_en": "DATETIME",
        "rol": "VARCHAR DEFAULT 'admin'",
    }
    with engine.connect() as conn:
        existentes = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(usuarios)")}
        for nombre, tipo in columnas_nuevas.items():
            if nombre not in existentes:
                conn.exec_driver_sql(f"ALTER TABLE usuarios ADD COLUMN {nombre} {tipo}")
        conn.commit()


def _crear_usuarios_iniciales() -> None:
    from . import models_db
    from .security import hash_password

    db = SessionLocal()
    try:
        if db.query(models_db.Usuario).count() > 0:
            return
        if config.ADMIN_PASSWORD == config.ADMIN_PASSWORD_DEFAULT:
            print(
                "[ADVERTENCIA] Creando usuario inicial con la contraseña de ejemplo "
                "(SPONSER_API_ADMIN_PASSWORD no está seteada). No usar así en producción.",
                flush=True,
            )
        db.add(
            models_db.Usuario(
                email=config.ADMIN_EMAIL,
                password_hash=hash_password(config.ADMIN_PASSWORD),
                nombre=config.ADMIN_NOMBRE,
            )
        )
        db.commit()
    finally:
        db.close()
