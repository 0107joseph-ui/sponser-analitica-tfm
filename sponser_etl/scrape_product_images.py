"""
Descarga las imagenes de producto (maxima resolucion disponible) del catalogo
publico de sponser.com y genera un CSV que las asocia al codigo_producto (SKU)
usado en sku_config.csv, para cruzar con los datos de venta del ETL.

Fuente de datos: sitemap.xml + endpoint publico /products/{handle}.json de
Shopify (mismo JSON que usa la tienda para renderizar la pagina de producto).
Las imagenes en ese JSON son los archivos "master" subidos a Shopify, sin
redimensionar -> la mejor resolucion que ofrece el sitio.

Respeta robots.txt: solo se golpean /products/*.json y el sitemap, con una
pausa entre requests.
"""

import csv
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = "https://sponser.com/en"
SITEMAP_URL = f"{BASE}/sitemap_products_1.xml?from=8157793583385&to=16001122664830"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SponserCatalogSync/1.0; +https://sponser.com)"}
REQUEST_DELAY = 0.4

ROOT = Path(__file__).parent
IMAGES_DIR = ROOT / "data" / "images"
CSV_OUT = ROOT / "data" / "product_images.csv"


def get_product_handles():
    r = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    handles = re.findall(r"https://sponser\.com/en/products/([a-z0-9\-]+)", r.text)
    return sorted(set(handles))


def fetch_product(handle):
    r = requests.get(f"{BASE}/products/{handle}.json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["product"]


def download_image(url, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        return False
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return True


def safe_ext(url):
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    return ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"


def main():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)

    handles = get_product_handles()
    print(f"{len(handles)} productos encontrados en el sitemap.")

    rows = []
    downloaded = 0
    for i, handle in enumerate(handles, 1):
        try:
            product = fetch_product(handle)
        except Exception as exc:
            print(f"[{i}/{len(handles)}] ERROR {handle}: {exc}")
            continue

        title = product.get("title", "")
        variants = product.get("variants", [])
        variants_by_id = {v["id"]: v for v in variants}
        all_skus = [v.get("sku", "").strip() for v in variants if v.get("sku")]
        images = product.get("images", [])

        for idx, img in enumerate(images, 1):
            src = img["src"]
            variant_ids = img.get("variant_ids") or []
            img_skus = [variants_by_id[vid].get("sku", "").strip() for vid in variant_ids if vid in variants_by_id]
            img_skus = [s for s in img_skus if s]
            # si la imagen no esta atada a una variante especifica, se asocia a todos los SKUs del producto
            if not img_skus:
                img_skus = all_skus

            primary_sku = img_skus[0] if img_skus else ""
            ext = safe_ext(src)
            base_name = primary_sku if primary_sku else handle
            fname = f"{base_name}_{idx}{ext}" if len(images) > 1 else f"{base_name}{ext}"
            dest = IMAGES_DIR / fname

            try:
                was_downloaded = download_image(src, dest)
                if was_downloaded:
                    downloaded += 1
                    time.sleep(REQUEST_DELAY)
            except Exception as exc:
                print(f"  ERROR descargando {src}: {exc}")
                continue

            rows.append({
                "codigo_producto": primary_sku,
                "skus_asociados": ";".join(img_skus),
                "handle": handle,
                "titulo": title,
                "variante": ";".join(variants_by_id[vid].get("title", "") for vid in variant_ids if vid in variants_by_id),
                "archivo_imagen": fname,
                "url_origen": src,
                "ancho_px": img.get("width"),
                "alto_px": img.get("height"),
            })

        print(f"[{i}/{len(handles)}] {handle}: {len(images)} imagen(es), skus={all_skus}")
        time.sleep(REQUEST_DELAY)

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "codigo_producto", "skus_asociados", "handle", "titulo", "variante",
            "archivo_imagen", "url_origen", "ancho_px", "alto_px",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nListo. {len(rows)} imagenes registradas ({downloaded} descargadas nuevas).")
    print(f"CSV: {CSV_OUT}")
    print(f"Imagenes: {IMAGES_DIR}")


if __name__ == "__main__":
    main()
