from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=False)
    nombre: Mapped[str | None] = mapped_column(nullable=True)
    activo: Mapped[bool] = mapped_column(default=True)
    # "admin": sube archivos, corre el pipeline, cambia metas/pedidos, invita
    # colaboradores. "viewer": solo lee (dashboards, bitácora, reportes).
    rol: Mapped[str] = mapped_column(default="admin")
    intentos_fallidos: Mapped[int] = mapped_column(default=0)
    bloqueado_hasta: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    ultimo_login: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Verificación en dos pasos (OTP por correo, obligatoria en cada login).
    # Los intentos fallidos de OTP reusan intentos_fallidos/bloqueado_hasta de
    # arriba -- un solo concepto de "demasiados tanteos", no dos contadores.
    otp_hash: Mapped[str | None] = mapped_column(nullable=True)
    otp_expira_en: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    otp_enviado_en: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    otp_reenvios: Mapped[int] = mapped_column(default=0)

    # Restablecimiento de contraseña por enlace de un solo uso.
    reset_token_hash: Mapped[str | None] = mapped_column(nullable=True)
    reset_token_expira_en: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class PedidoTransito(Base):
    __tablename__ = "pedidos_transito"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo_producto: Mapped[str] = mapped_column(nullable=False)
    unidades: Mapped[int] = mapped_column(nullable=False)
    eta: Mapped[dt.date] = mapped_column(nullable=False)
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class PedidoBorradorLinea(Base):
    __tablename__ = "pedido_borrador_lineas"

    codigo_producto: Mapped[str] = mapped_column(primary_key=True)
    cantidad_final: Mapped[int] = mapped_column(nullable=False)
    actualizado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)


class PedidoConfirmado(Base):
    __tablename__ = "pedidos_confirmados"

    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[dt.date] = mapped_column(nullable=False)
    total_unidades: Mapped[int] = mapped_column(nullable=False)
    total_costo: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    lineas: Mapped[list["PedidoConfirmadoLinea"]] = relationship(back_populates="pedido", cascade="all, delete-orphan")


class PedidoConfirmadoLinea(Base):
    __tablename__ = "pedidos_confirmados_lineas"

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos_confirmados.id"))
    codigo_producto: Mapped[str] = mapped_column(nullable=False)
    cantidad: Mapped[int] = mapped_column(nullable=False)
    precio_unitario: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    pedido: Mapped[PedidoConfirmado] = relationship(back_populates="lineas")


class PatrocinioPrevisto(Base):
    """Salida de inventario prevista por patrocinio (evento futuro, no es venta).
    Input manual del comercial -- se suma a la demanda al calcular 'Sugerido' en
    Pedido a fábrica, porque el modelo de pronóstico se entrena solo con ventas
    orgánicas (los patrocinios se excluyen de ventas_detalle.csv) y por lo tanto
    no puede anticipar esta salida por sí solo."""

    __tablename__ = "patrocinios_previstos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre_evento: Mapped[str] = mapped_column(nullable=False)
    codigo_producto: Mapped[str] = mapped_column(nullable=False)
    unidades: Mapped[int] = mapped_column(nullable=False)
    fecha_evento: Mapped[dt.date] = mapped_column(nullable=False)
    creado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class MetaVentas(Base):
    """Meta anual de facturación configurada por el comercial en Carga de
    datos (input 'Meta anual' + modo de segregación) -- una fila por año para
    no perder la meta de años anteriores cuando empieza uno nuevo."""

    __tablename__ = "metas_ventas"

    anio: Mapped[int] = mapped_column(primary_key=True)
    monto_anual: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    modo: Mapped[str] = mapped_column(nullable=False)  # 'estacional' | 'lineal'
    actualizado_en: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )


class CorridaPipeline(Base):
    __tablename__ = "corridas_pipeline"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(nullable=False)
    estado: Mapped[str] = mapped_column(nullable=False)  # 'en_curso' | 'ok' | 'error'
    mensaje: Mapped[str | None] = mapped_column(nullable=True)
    iniciado_en: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    finalizado_en: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
