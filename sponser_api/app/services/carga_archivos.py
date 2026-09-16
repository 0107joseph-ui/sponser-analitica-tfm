"""Validación y guardado de los archivos crudos que el usuario sube en
"Carga de datos" (ventas COM/Control Sponser e inventario).

Los nombres reconocidos son exactamente los que sponser_etl/extract.py sabe
leer -- ver `_PATRON_NOMBRE` (ventas) y el glob "Inventario*.xlsx"
(inventario) en ese archivo. No se valida el contenido aquí: si el archivo
no es en realidad HTML GridView (ventas) o un .xlsx legítimo (inventario),
el pipeline lo reporta como error en la bitácora al correr.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .. import config

_PATRON_VENTAS = re.compile(r"^(com|control) sponser (\d{4})\.xls$", re.IGNORECASE)
# El sistema fuente exporta con este otro nombre ("Ventas COM ene - sep
# 2026.xls", "Ventas CON ene - sep 2026.xls" -- "CON" es como abrevia
# "Control" ahí, no "Control" completo). Se acepta también y se normaliza al
# nombre canónico de arriba, que es el que espera el glob de extract.py.
_PATRON_VENTAS_EXPORT = re.compile(r"^ventas\s+(com|con)\b.*?(\d{4})\.xls$", re.IGNORECASE)
_FUENTE_POR_ABREVIATURA = {"com": "COM", "con": "Control", "control": "Control"}
_PATRON_INVENTARIO = re.compile(r"^inventario.*\.xlsx$", re.IGNORECASE)

# Nombre fijo de guardado para inventario: extract.extract_inventario() toma
# el primer archivo que encuentre con glob("Inventario*.xlsx"), así que si
# se dejaran acumular nombres distintos, una subida más nueva podría quedar
# ignorada en silencio. El inventario es una foto del momento (no una serie
# histórica como las ventas por año), así que cada subida reemplaza a la
# anterior bajo este mismo nombre.
_NOMBRE_INVENTARIO = "Inventario.xlsx"


@dataclass
class ResultadoCarga:
    nombre_original: str
    guardado_como: str | None
    tipo: str | None  # "ventas" | "inventario"
    ok: bool
    error: str | None = None


def _clasificar(nombre_archivo: str) -> tuple[str, str] | None:
    """(tipo, nombre_de_guardado) si el nombre calza con un patrón conocido,
    o None si no se reconoce. `nombre_de_guardado` nunca depende del
    directorio del archivo original (siempre `os.path.basename`), así que no
    hace falta sanitizar path traversal aparte."""
    base = os.path.basename(nombre_archivo)
    m = _PATRON_VENTAS.match(base)
    if m:
        return "ventas", base
    m = _PATRON_VENTAS_EXPORT.match(base)
    if m:
        fuente = _FUENTE_POR_ABREVIATURA[m.group(1).lower()]
        anio = m.group(2)
        return "ventas", f"{fuente} Sponser {anio}.xls"
    if _PATRON_INVENTARIO.match(base):
        return "inventario", _NOMBRE_INVENTARIO
    return None


def guardar(nombre_original: str, contenido: bytes) -> ResultadoCarga:
    if not contenido:
        return ResultadoCarga(nombre_original, None, None, False, "El archivo está vacío.")
    if len(contenido) > config.CARGA_ARCHIVO_MAX_BYTES:
        limite_mb = config.CARGA_ARCHIVO_MAX_BYTES // (1024 * 1024)
        return ResultadoCarga(nombre_original, None, None, False, f"Supera el límite de {limite_mb} MB.")

    clasificado = _clasificar(nombre_original)
    if clasificado is None:
        return ResultadoCarga(
            nombre_original, None, None, False,
            'Nombre no reconocido. Debe ser "COM Sponser AAAA.xls", '
            '"Control Sponser AAAA.xls", el export directo del sistema '
            '("Ventas COM ... AAAA.xls" / "Ventas CON ... AAAA.xls") o '
            '"Inventario*.xlsx".',
        )

    tipo, nombre_guardado = clasificado
    os.makedirs(config.RAW_DIR, exist_ok=True)
    destino = os.path.join(config.RAW_DIR, nombre_guardado)
    with open(destino, "wb") as f:
        f.write(contenido)
    return ResultadoCarga(nombre_original, nombre_guardado, tipo, True)
