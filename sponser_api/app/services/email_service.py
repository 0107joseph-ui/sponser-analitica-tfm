"""Envío de correo para el login en dos pasos (OTP) y el restablecimiento de
contraseña. SMTP genérico por variables de entorno (ver config.py) -- si
SPONSER_API_SMTP_HOST no está seteado, cae en "modo consola": loguea el
correo completo (incluyendo el código/enlace) en vez de enviarlo, para poder
desarrollar y probar localmente sin credenciales reales.
"""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from .. import config

logger = logging.getLogger("sponser_api.email")


class EmailSendError(RuntimeError):
    """El correo no se pudo enviar (SMTP real configurado pero falló)."""


def _modo_consola() -> bool:
    return not config.SMTP_HOST


def enviar_correo(destinatario: str, asunto: str, cuerpo_texto: str) -> None:
    if _modo_consola():
        # print(), no logger.info(): esto ES el mecanismo real de entrega en
        # modo consola (sin esto no hay forma de completar el login/reset en
        # desarrollo), no solo una línea de log que el nivel por defecto
        # (WARNING) filtraría en silencio.
        print(
            f"\n===== CORREO (modo consola -- SPONSER_API_SMTP_HOST no configurado) =====\n"
            f"Para: {destinatario}\nAsunto: {asunto}\n{cuerpo_texto}"
            f"===========================================================================\n",
            flush=True,
        )
        return

    mensaje = MIMEText(cuerpo_texto, "plain", "utf-8")
    mensaje["Subject"] = asunto
    mensaje["From"] = config.SMTP_FROM
    mensaje["To"] = destinatario

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.SMTP_TIMEOUT_SEGUNDOS) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD or "")
            smtp.sendmail(config.SMTP_FROM, [destinatario], mensaje.as_string())
    except (smtplib.SMTPException, OSError) as e:
        logger.error("Fallo enviando correo a %s: %s", destinatario, e)
        raise EmailSendError(str(e)) from e


def enviar_otp(destinatario: str, nombre: str | None, codigo: str, expira_minutos: int) -> None:
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    cuerpo = (
        f"{saludo}\n\n"
        f"Tu código de verificación para ingresar a Sponser Analítica es:\n\n"
        f"    {codigo}\n\n"
        f"Vence en {expira_minutos} minutos y es de un solo uso. "
        f"Si no intentaste iniciar sesión, ignorá este correo.\n"
    )
    enviar_correo(destinatario, "Tu código de verificación - Sponser Analítica", cuerpo)


def enviar_invitacion(destinatario: str, nombre: str | None, enlace: str, rol: str, expira_minutos: int) -> None:
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    rol_label = "administrador" if rol == "admin" else "de solo lectura"
    cuerpo = (
        f"{saludo}\n\n"
        f"Te dieron acceso a Sponser Analítica con permisos de {rol_label}. Entrá a este "
        f"enlace para elegir tu contraseña y empezar a usarlo (válido por {expira_minutos} "
        f"minutos, un solo uso):\n\n"
        f"    {enlace}\n\n"
        f"Si no esperabas este correo, ignoralo.\n"
    )
    enviar_correo(destinatario, "Te invitaron a Sponser Analítica", cuerpo)


def enviar_reset_password(destinatario: str, nombre: str | None, enlace: str, expira_minutos: int) -> None:
    saludo = f"Hola {nombre}," if nombre else "Hola,"
    cuerpo = (
        f"{saludo}\n\n"
        f"Pediste restablecer tu contraseña de Sponser Analítica. Entrá a este enlace "
        f"para elegir una nueva (válido por {expira_minutos} minutos, un solo uso):\n\n"
        f"    {enlace}\n\n"
        f"Si no pediste esto, ignorá este correo -- tu contraseña actual sigue funcionando.\n"
    )
    enviar_correo(destinatario, "Restablecer tu contraseña - Sponser Analítica", cuerpo)
