from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config, models_db, schemas, security
from ..db import get_db
from ..services import email_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginOtpRequired)
def login(datos: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    usuario = security.autenticar(db, datos.email.strip().lower(), datos.password)

    codigo = security.generar_codigo_otp()
    try:
        email_service.enviar_otp(usuario.email, usuario.nombre, codigo, config.OTP_EXPIRA_MINUTOS)
    except email_service.EmailSendError:
        raise HTTPException(status_code=502, detail="No pudimos enviar el código de verificación. Intentá de nuevo en unos minutos.")
    security.persistir_otp(db, usuario, codigo, reenvio=False)

    request.session.clear()
    request.session["otp_usuario_id"] = usuario.id
    return schemas.LoginOtpRequired(email=usuario.email)


@router.post("/otp/verify", response_model=schemas.UsuarioOut)
def verificar_otp(
    datos: schemas.OtpVerifyRequest,
    request: Request,
    usuario=Depends(security.usuario_pendiente_otp),
    db: Session = Depends(get_db),
):
    security.verificar_otp(db, usuario, datos.code)
    request.session.clear()
    request.session["usuario_id"] = usuario.id
    return schemas.UsuarioOut(email=usuario.email, nombre=usuario.nombre, rol=usuario.rol)


@router.post("/otp/resend", response_model=schemas.OtpResendResponse)
def reenviar_otp(usuario=Depends(security.usuario_pendiente_otp), db: Session = Depends(get_db)):
    puede, restante = security.puede_reenviar_otp(usuario)
    if not puede:
        detalle = "Ya reenviamos el código el máximo de veces permitido. Volvé a iniciar sesión." if restante == 0 else "Esperá un momento antes de pedir otro código."
        raise HTTPException(status_code=429, detail=detalle, headers={"Retry-After": str(restante)} if restante else None)

    codigo = security.generar_codigo_otp()
    try:
        email_service.enviar_otp(usuario.email, usuario.nombre, codigo, config.OTP_EXPIRA_MINUTOS)
    except email_service.EmailSendError:
        raise HTTPException(status_code=502, detail="No pudimos enviar el código de verificación. Intentá de nuevo en unos minutos.")
    security.persistir_otp(db, usuario, codigo, reenvio=True)
    return schemas.OtpResendResponse(cooldownSegundos=config.OTP_REENVIO_COOLDOWN_SEGUNDOS)


@router.post("/forgot", response_model=schemas.ForgotPasswordResponse)
def forgot(datos: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Siempre responde 200 igual, exista o no la cuenta -- evita que este
    endpoint sirva para enumerar correos válidos. Cualquier falla de envío se
    loguea del lado del servicio de correo, nunca se refleja en la respuesta."""
    email = datos.email.strip().lower()
    usuario = db.query(models_db.Usuario).filter(models_db.Usuario.email == email).first()
    if usuario and usuario.activo:
        token = security.generar_reset_token(db, usuario)
        enlace = f"{config.FRONTEND_BASE_URL}?resetToken={token}"
        try:
            email_service.enviar_reset_password(usuario.email, usuario.nombre, enlace, config.RESET_TOKEN_EXPIRA_MINUTOS)
        except email_service.EmailSendError:
            pass
    return schemas.ForgotPasswordResponse()


@router.post("/reset")
def reset(datos: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    usuario = security.usuario_por_reset_token(db, datos.token)
    if usuario is None:
        raise HTTPException(status_code=400, detail="El enlace no es válido o ya venció. Pedí uno nuevo.")
    security.restablecer_password(db, usuario, datos.password)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=schemas.UsuarioOut)
def me(request: Request, db: Session = Depends(get_db)):
    usuario = security.usuario_actual(request, db)
    return schemas.UsuarioOut(email=usuario.email, nombre=usuario.nombre, rol=usuario.rol)
