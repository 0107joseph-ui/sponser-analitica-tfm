"""
Mapeo manual de SKUs de venta 'legacy' (ya no existen tal cual en sponser.com/en)
hacia el SKU actual del mismo producto, confirmado por nombre/tamano de empaque
con el usuario. Copia la imagen ya descargada con el nombre del SKU viejo (alias)
para que el pipeline de ventas pueda resolverla directamente por codigo_producto,
y actualiza match_report.csv.
"""

import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
IMAGES_DIR = ROOT / "data" / "images"
PRODUCT_IMAGES = ROOT / "data" / "product_images.csv"
MATCH_REPORT = ROOT / "data" / "match_report.csv"

# sku_venta_legacy -> (archivo_imagen_actual, sku_actual, nota)
LEGACY_MAP = {
    "1306":  ("08227_2.png", "08227", "Carbo Fuel Blackcurrant -> Display 10x83g"),
    "1309":  ("45010_1.png", "45010", "Sport Biotics 90 caps -> SKU renumerado"),
    "17222": ("17223_2.png", "17223", "Liquid Energy Pure 18x70g -> Display 18x70g"),
    "17322": ("17324_4.png", "17324", "Liquid Energy Plus 18x70g (con cafeina, cola-lemon descontinuado) -> Display 18x70g"),
    "17400": ("17402_2.png", "17402", "Liquid Energy BCAA Strawberry-Banana 18x70g -> Display 18x70g"),
    "4700":  ("04717.png",   "04717", "Long Energy Berry 1000g -> match directo"),
    "82080": ("07709.png",   "07709", "Premium Plant Protein Vanille 455g -> match directo"),
}

# 1) crear alias de archivo con el nombre del SKU legacy
for legacy_sku, (fname, current_sku, nota) in LEGACY_MAP.items():
    src = IMAGES_DIR / fname
    if not src.exists():
        print(f"AVISO: no existe {src}, se omite {legacy_sku}")
        continue
    ext = src.suffix
    dest = IMAGES_DIR / f"{legacy_sku}{ext}"
    if not dest.exists():
        shutil.copyfile(src, dest)
    print(f"{legacy_sku} -> alias {dest.name} (copia de {fname}, sku actual {current_sku})")

# 2) actualizar match_report.csv
rows = []
with open(MATCH_REPORT, encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    if "sku_actual" not in fieldnames:
        fieldnames = fieldnames + ["sku_actual", "nota"]
    for row in reader:
        if "sku_actual" not in row:
            row["sku_actual"] = ""
            row["nota"] = ""
        sku = row["codigo_producto"]
        if sku in LEGACY_MAP:
            fname, current_sku, nota = LEGACY_MAP[sku]
            alias = f"{sku}{Path(fname).suffix}"
            row["tiene_imagen"] = "SI"
            row["archivos_imagen"] = alias
            row["sku_actual"] = current_sku
            row["nota"] = f"match manual: {nota}"
        rows.append(row)

with open(MATCH_REPORT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

total = len(rows)
matched = sum(1 for r in rows if r["tiene_imagen"] == "SI")
print(f"\nTotal SKUs de venta: {total}")
print(f"Con imagen (directo + manual): {matched} ({matched/total:.0%})")
print(f"Reporte actualizado: {MATCH_REPORT}")
