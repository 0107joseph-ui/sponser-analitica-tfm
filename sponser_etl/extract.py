"""Extracción de los archivos crudos de ventas e inventario de Sponser.

Los archivos "COM Sponser *.xls" y "Control Sponser *.xls" no son binarios
Excel: son HTML (una tabla GridView de ASP.NET) exportado con extensión
.xls. Se parsean con html.parser (stdlib) para no depender de lxml/bs4.
"""

from __future__ import annotations

import glob
import html
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

import pandas as pd

# Columnas en el orden exacto en el que aparecen en el reporte
COLUMNAS_VENTAS = [
    "Tipo Documento", "nofactura", "Fecha", "Entidad", "Cliente",
    "No. Expediente", "telcel", "TipoReferencia", "tipocliente",
    "saldofactura", "Código", "item", "Proveedor", "Cantidad",
    "montounitario", "montocobrado", "descuentounitario", "descuentolinea",
    "Detalle", "montototal", "iv", "codigobarras", "Usuario", "Clave",
    "Moneda", "noreferencia", "tipopago",
]


class _GridViewTableParser(HTMLParser):
    """Extrae filas/celdas de la primera <table> del reporte GridView."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row: list[str] = []
        self._current_text: list[str] = []
        self._table_done = False

    def handle_starttag(self, tag, attrs):
        if self._table_done:
            return
        if tag == "table" and not self._in_table:
            self._in_table = True
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_table and tag in ("td", "th"):
            self._in_cell = True
            self._current_text = []

    def handle_endtag(self, tag):
        if self._table_done:
            return
        if self._in_cell and tag in ("td", "th"):
            self._in_cell = False
            self._current_row.append("".join(self._current_text).strip())
        elif self._in_row and tag == "tr":
            self._in_row = False
            if self._current_row:
                self.rows.append(self._current_row)
        elif self._in_table and tag == "table":
            self._in_table = False
            self._table_done = True

    def handle_data(self, data):
        if self._in_cell:
            self._current_text.append(data)


def parse_html_xls(path: str) -> list[list[str]]:
    """Lee un archivo .xls (en realidad HTML) y devuelve filas de celdas."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        contenido = f.read()
    parser = _GridViewTableParser()
    parser.feed(contenido)
    return parser.rows


@dataclass
class ResultadoExtraccion:
    ventas: pd.DataFrame
    errores: list[str] = field(default_factory=list)


_PATRON_NOMBRE = re.compile(r"^(COM|Control) Sponser (\d{4})\.xls$", re.IGNORECASE)


def _fuente_y_anio(nombre_archivo: str) -> tuple[str, int]:
    m = _PATRON_NOMBRE.match(nombre_archivo)
    if not m:
        raise ValueError(f"Nombre de archivo inesperado: {nombre_archivo}")
    fuente = "COM" if m.group(1).lower() == "com" else "Control"
    return fuente, int(m.group(2))


def extract_ventas(raw_dir: str) -> ResultadoExtraccion:
    """Extrae y concatena todos los archivos COM/Control Sponser *.xls de raw_dir."""
    patrones = [
        os.path.join(raw_dir, "COM Sponser *.xls"),
        os.path.join(raw_dir, "Control Sponser *.xls"),
    ]
    archivos = sorted(p for patron in patrones for p in glob.glob(patron))

    frames: list[pd.DataFrame] = []
    errores: list[str] = []

    for path in archivos:
        nombre = os.path.basename(path)
        try:
            fuente, anio = _fuente_y_anio(nombre)
            filas = parse_html_xls(path)
            if not filas:
                errores.append(f"{nombre}: sin filas detectadas, se omite")
                continue

            header, datos = filas[0], filas[1:]
            if datos:
                datos = datos[:-1]  # última fila = totales del reporte, no confiable

            if [h.strip() for h in header] != COLUMNAS_VENTAS:
                errores.append(
                    f"{nombre}: encabezados distintos a lo esperado ({header}), se omite"
                )
                continue

            df = pd.DataFrame(datos, columns=COLUMNAS_VENTAS)
            df["fuente"] = fuente
            df["anio"] = anio
            df["archivo_origen"] = nombre
            frames.append(df)
        except Exception as e:  # noqa: BLE001 - no debe tumbar el pipeline por un archivo
            errores.append(f"{nombre}: error al procesar ({e})")

    ventas = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNAS_VENTAS)
    return ResultadoExtraccion(ventas=ventas, errores=errores)


def extract_inventario(raw_dir: str) -> pd.DataFrame:
    """Lee el archivo de inventario (xlsx genuino) de raw_dir."""
    candidatos = glob.glob(os.path.join(raw_dir, "Inventario*.xlsx"))
    if not candidatos:
        raise FileNotFoundError(f"No se encontró archivo Inventario*.xlsx en {raw_dir}")
    return pd.read_excel(candidatos[0])
