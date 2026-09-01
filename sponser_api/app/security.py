from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, models_db
from .db import get_db


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def autenticar(db: Session, email: str, password: str) -> models_db.Usuario:
    """Valida credenciales y aplica el bloqueo tras intentos fallidos (igual regla
    que ya simulaba la UI: 5 intentos, bloqueo de 15 minutos). Lanza HTTPException
    con el mensaje adecuado en cada caso de rechazo."""
    usuario = db.query(models_db.Usuario).filter(models_db.Usuario.email == email).first()
    ahora = dt.datetime.utcnow()

    if usuario is None or not usuario.activo:
        raise HTTPException(status_code=401, detail="El correo o la contraseña no coinciden.")

    if usuario.bloqueado_hasta and usuario.bloqueado_hasta > ahora:
        raise HTTPException(
            status_code=423,
            detail="Cuenta bloqueada por intentos fallidos. Restablecé tu contraseña para volver a entrar.",
        )

    if not verify_password(password, usuario.password_hash):
        usuario.intentos_fallidos += 1
        if usuario.intentos_fallidos >= config.MAX_INTENTOS_LOGIN:
            usuario.bloqueado_hasta = ahora + dt.timedelta(minutes=config.BLOQUEO_MINUTOS)
        db.commit()
        detalle = (
            "Superaste el número de intentos permitidos. Restablecé tu contraseña para volver a entrar."
            if usuario.intentos_fallidos >= config.MAX_INTENTOS_LOGIN
            else "El correo o la contraseña no coinciden."
        )
        raise HTTPException(status_code=401, detail=detalle)

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_login = ahora
    db.commit()
    return usuario


def usuario_actual(request: Request, db: Session = Depends(get_db)) -> models_db.Usuario:
    usuario_id = request.session.get("usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=401, detail="No hay sesión activa.")
    usuario = db.get(models_db.Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(status_code=401, detail="No hay sesión activa.")
    return usuario


def requiere_admin(usuario: models_db.Usuario = Depends(usuario_actual)) -> models_db.Usuario:
    """Para endpoints que cambian datos (subir archivos, correr el pipeline,
    metas, pedidos, patrocinios, gestión de usuarios) -- rol "viewer" solo
    puede leer (dashboards, bitácora, reportes)."""
    if usuario.rol != "admin":
        raise HTTPException(status_code=403, detail="Esta acción requiere permisos de administrador.")
    return usuario


def crear_usuario_invitado(db: Session, email: str, nombre: str | None, rol: str) -> tuple[models_db.Usuario, str]:
    """Crea la cuenta con una contraseña aleatoria que nadie conoce -- el
    colaborador entra por primera vez con el mismo enlace de un solo uso que
    "olvidé mi contraseña" (generar_reset_token/restablecer_password), así
    que nunca circula una contraseña temporal por correo."""
    usuario = models_db.Usuario(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        nombre=nombre,
        rol=rol,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    token = generar_reset_token(db, usuario)
    return usuario, token


# --- Verificación en dos pasos (OTP por correo) y restablecimiento de contraseña ---


def generar_codigo_otp() -> str:
    """Código numérico de OTP_LARGO dígitos. secrets.randbelow es
    criptográficamente seguro (a diferencia de random)."""
    return str(secrets.randbelow(10 ** config.OTP_LARGO)).zfill(config.OTP_LARGO)


def _hash_secreto(valor: str) -> str:
    """HMAC-SHA256 con SESSION_SECRET_KEY como clave. Para códigos OTP y
    tokens de reset (secretos cortos y efímeros, no contraseñas) -- no hace
    falta un hash lento tipo werkzeug/bcrypt, y al ser determinístico permite
    buscar por igualdad directa en la base (ver usuario_por_reset_token)."""
    clave = config.SESSION_SECRET_KEY.encode("utf-8")
    return hmac.new(clave, valor.encode("utf-8"), hashlib.sha256).hexdigest()


def _comparar_hash(valor: str, hash_esperado: str | None) -> bool:
    if not hash_esperado:
        return False
    return hmac.compare_digest(_hash_secreto(valor), hash_esperado)


def _limpiar_otp(usuario: models_db.Usuario) -> None:
    usuario.otp_hash = None
    usuario.otp_expira_en = None
    usuario.otp_enviado_en = None
    usuario.otp_reenvios = 0


def registrar_intento_fallido(db: Session, usuario: models_db.Usuario) -> bool:
    """Incrementa el contador unificado de tanteos fallidos (mismo campo que
    usa autenticar() para la contraseña) y aplica el mismo bloqueo si se
    agota. Devuelve True si esto dejó la cuenta bloqueada."""
    usuario.intentos_fallidos += 1
    bloqueada = usuario.intentos_fallidos >= config.MAX_INTENTOS_LOGIN
    if bloqueada:
        usuario.bloqueado_hasta = dt.datetime.utcnow() + dt.timedelta(minutes=config.BLOQUEO_MINUTOS)
        _limpiar_otp(usuario)
    db.commit()
    return bloqueada


def persistir_otp(db: Session, usuario: models_db.Usuario, codigo: str, reenvio: bool) -> None:
    """Se llama DESPUÉS de que el correo se envió con éxito -- si el envío
    falla, no debe quedar en la base un código que el usuario nunca recibió."""
    ahora = dt.datetime.utcnow()
    usuario.otp_hash = _hash_secreto(codigo)
    usuario.otp_expira_en = ahora + dt.timedelta(minutes=config.OTP_EXPIRA_MINUTOS)
    usuario.otp_enviado_en = ahora
    usuario.otp_reenvios = usuario.otp_reenvios + 1 if reenvio else 0
    db.commit()


def puede_reenviar_otp(usuario: models_db.Usuario) -> tuple[bool, int]:
    """(puede_reenviar, segundos_restantes_de_cooldown)."""
    if usuario.otp_reenvios >= config.OTP_MAX_REENVIOS:
        return False, 0
    if usuario.otp_enviado_en:
        transcurrido = (dt.datetime.utcnow() - usuario.otp_enviado_en).total_seconds()
        restante = config.OTP_REENVIO_COOLDOWN_SEGUNDOS - transcurrido
        if restante > 0:
            return False, int(restante) + 1
    return True, 0


def verificar_otp(db: Session, usuario: models_db.Usuario, codigo: str) -> None:
    """Lanza HTTPException: 400 si el código venció (caducidad, no cuenta
    como tanteo), 401 si no coincide (sí cuenta), 423 si esto agota el
    presupuesto de intentos."""
    ahora = dt.datetime.utcnow()
    if not usuario.otp_hash or not usuario.otp_expira_en or usuario.otp_expira_en <= ahora:
        raise HTTPException(status_code=400, detail="El código venció. Pedí uno nuevo.")

    if not _comparar_hash(codigo, usuario.otp_hash):
        if registrar_intento_fallido(db, usuario):
            raise HTTPException(
                status_code=423,
                detail="Cuenta bloqueada por intentos fallidos. Restablecé tu contraseña para volver a entrar.",
            )
        raise HTTPException(status_code=401, detail="Ese código no es válido. Revisá los 6 dígitos e intentá de nuevo.")

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_login = ahora
    _limpiar_otp(usuario)
    db.commit()


def usuario_pendiente_otp(request: Request, db: Session = Depends(get_db)) -> models_db.Usuario:
    """Como usuario_actual, pero para la sesión de menor privilegio que deja
    /api/auth/login mientras falta verificar el OTP -- una clave de sesión
    distinta a usuario_id, así ningún endpoint de negocio (todos dependen de
    usuario_actual) queda accesible hasta pasar el segundo factor."""
    usuario_id = request.session.get("otp_usuario_id")
    if not usuario_id:
        raise HTTPException(status_code=401, detail="No hay una verificación pendiente.")
    usuario = db.get(models_db.Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise HTTPException(status_code=401, detail="No hay una verificación pendiente.")
    return usuario


def generar_reset_token(db: Session, usuario: models_db.Usuario) -> str:
    token = secrets.token_urlsafe(32)
    usuario.reset_token_hash = _hash_secreto(token)
    usuario.reset_token_expira_en = dt.datetime.utcnow() + dt.timedelta(minutes=config.RESET_TOKEN_EXPIRA_MINUTOS)
    db.commit()
    return token


def usuario_por_reset_token(db: Session, token: str) -> models_db.Usuario | None:
    hash_token = _hash_secreto(token)
    usuario = db.query(models_db.Usuario).filter(models_db.Usuario.reset_token_hash == hash_token).first()
    if usuario and usuario.reset_token_expira_en and usuario.reset_token_expira_en > dt.datetime.utcnow():
        return usuario
    return None


def restablecer_password(db: Session, usuario: models_db.Usuario, nueva_password: str) -> None:
    usuario.password_hash = hash_password(nueva_password)
    usuario.reset_token_hash = None
    usuario.reset_token_expira_en = None
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    _limpiar_otp(usuario)
    db.commit()
