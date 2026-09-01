"""Gestión de colaboradores -- crear cuentas nuevas (invitación por correo) y
activar/desactivar acceso. Todo el router es solo para administradores."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models_db, schemas, security
from ..db import get_db
from ..services import email_service

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"], dependencies=[Depends(security.requiere_admin)])


def _a_out(u: models_db.Usuario) -> schemas.UsuarioAdminOut:
    return schemas.UsuarioAdminOut(
        id=u.id, email=u.email, nombre=u.nombre, rol=u.rol, activo=u.activo, ultimoLogin=u.ultimo_login,
    )


@router.get("", response_model=list[schemas.UsuarioAdminOut])
def listar_usuarios(db: Session = Depends(get_db)):
    filas = db.query(models_db.Usuario).order_by(models_db.Usuario.creado_en).all()
    return [_a_out(u) for u in filas]


@router.post("", response_model=schemas.UsuarioAdminOut)
def invitar_usuario(datos: schemas.UsuarioCrearIn, db: Session = Depends(get_db)):
    email = datos.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Ingresá un correo válido.")
    if db.query(models_db.Usuario).filter(models_db.Usuario.email == email).first() is not None:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo.")

    nombre = (datos.nombre or "").strip() or None
    usuario, token = security.crear_usuario_invitado(db, email, nombre, datos.rol)
    enlace = f"{config.FRONTEND_BASE_URL}?resetToken={token}"
    try:
        email_service.enviar_invitacion(usuario.email, usuario.nombre, enlace, usuario.rol, config.RESET_TOKEN_EXPIRA_MINUTOS)
    except email_service.EmailSendError:
        raise HTTPException(status_code=502, detail="La cuenta se creó pero no se pudo enviar el correo de invitación.")
    return _a_out(usuario)


@router.patch("/{usuario_id}", response_model=schemas.UsuarioAdminOut)
def cambiar_estado_usuario(
    usuario_id: int,
    activo: bool,
    admin_actual: models_db.Usuario = Depends(security.requiere_admin),
    db: Session = Depends(get_db),
):
    if usuario_id == admin_actual.id:
        raise HTTPException(status_code=422, detail="No podés desactivar tu propia cuenta.")
    usuario = db.get(models_db.Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="No existe ese usuario.")
    usuario.activo = activo
    db.commit()
    return _a_out(usuario)
