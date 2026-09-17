from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from . import config


class LoginRequest(BaseModel):
    email: str
    password: str


class UsuarioOut(BaseModel):
    email: str
    nombre: str | None = None
    rol: Literal["admin", "viewer"] = "admin"


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordResponse(BaseModel):
    ok: bool = True


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=config.PASSWORD_MIN_LARGO)


class ProductoOut(BaseModel):
    id: str
    name: str
    category: str
    image: str
    price: float
    sales: list[float]
    forecastMonths: list[float]
    inventory: float
    expiryMonths: float
    suggested: float
    expiryRiskUnits: float
    sponsorshipUnits: float = 0


class ClienteOut(BaseModel):
    id: str
    name: str
    channel: str
    monthly: list[float]
    monthlyRevenue: list[float]
    mix: dict[str, float]


class LineaComercialOut(BaseModel):
    nombre: str
    unidades: float
    monto: float


class BacktestSkuOut(BaseModel):
    producto: str
    real: float
    pronosticado: float
    errorPct: float


class ModeloAccuracyOut(BaseModel):
    version: str
    accuracyPct: float
    topSkus: list[BacktestSkuOut]


class ClienteInactivoOut(BaseModel):
    id: str
    name: str
    channel: str
    lastPurchase: dt.date
    daysInactive: int
    monthsInactive: float
    historicRevenue: float
    historicOrders: int


class PatrocinioClienteOut(BaseModel):
    nombre: str
    unidades: float
    monto: float
    facturas: int
    ultimaSalida: dt.date


class PatrocinioMensualOut(BaseModel):
    mes: int
    monto: float
    unidades: float


class BootstrapOut(BaseModel):
    asOfDate: dt.date
    products: list[ProductoOut]
    clients: list[ClienteOut]
    lineasComerciales: list[LineaComercialOut]
    modelAccuracy: ModeloAccuracyOut
    clientesInactivos: list[ClienteInactivoOut]
    patrocinioClientes: list[PatrocinioClienteOut]
    patrociniosMensual: list[PatrocinioMensualOut]
    availableYears: list[int]


class AnalisisComercialOut(BaseModel):
    year: int
    products: list[ProductoOut]
    clients: list[ClienteOut]


class TransitoIn(BaseModel):
    sku: str
    unidades: int
    eta: dt.date


class TransitoOut(BaseModel):
    id: int
    sku: str
    unidades: int
    eta: dt.date


class BorradorLineaIn(BaseModel):
    cantidadFinal: int


class PedidoLineaIn(BaseModel):
    codigo_producto: str
    cantidad: int
    precio_unitario: float


class ConfirmarPedidoIn(BaseModel):
    lineas: list[PedidoLineaIn]


class PatrocinioIn(BaseModel):
    nombreEvento: str
    sku: str
    unidades: int
    fechaEvento: dt.date


class PatrocinioOut(BaseModel):
    id: int
    nombreEvento: str
    sku: str
    unidades: int
    fechaEvento: dt.date


class MetaVentasIn(BaseModel):
    montoAnual: float = Field(gt=0)
    modo: Literal["estacional", "lineal"]


class MetaVentasOut(BaseModel):
    anio: int
    montoAnual: float
    modo: Literal["estacional", "lineal"]


class BitacoraEntradaOut(BaseModel):
    id: int
    tipo: str
    estado: str
    mensaje: str | None
    iniciado_en: dt.datetime
    finalizado_en: dt.datetime | None


class ArchivoCargaResultado(BaseModel):
    nombreOriginal: str
    guardadoComo: str | None
    tipo: Literal["ventas", "inventario"] | None
    ok: bool
    error: str | None = None


class CargaArchivosOut(BaseModel):
    resultados: list[ArchivoCargaResultado]


class UsuarioCrearIn(BaseModel):
    email: str
    nombre: str | None = None
    rol: Literal["admin", "viewer"] = "viewer"


class UsuarioAdminOut(BaseModel):
    id: int
    email: str
    nombre: str | None
    rol: Literal["admin", "viewer"]
    activo: bool
    ultimoLogin: dt.datetime | None
