"""Clasificación de productos en las 32 líneas comerciales reales del catálogo Sponser.

`data/lineas-comerciales.json` (proyecto de diseño "Sponser Costa Rica Sales
Dashboard") documenta las 32 líneas y sus criterios en texto libre, pero no trae
un mapeo directo código->línea. Este módulo reconstruye ese mapeo con reglas de
texto sobre la descripción del producto (ver criterios en el docstring de cada
bloque), codificando exactamente lo que ese documento describe.

Cualquier producto que no calce con ninguna regla queda como "Sin clasificar"
(nunca se oculta ni se fuerza a una línea incorrecta).
"""

from __future__ import annotations

import re

SIN_CLASIFICAR = "Sin clasificar"

# Orden importa: la primera regla que haga match gana. Los patrones más
# específicos (ej. "Ultra Competition") van antes que los genéricos que los
# contienen (ej. "Competition"), y "Liquid Energy" va antes que la regla de
# BCAA de Aminoácidos porque "Liquid Energy BCAA ..." es Liquid Energy, no
# un aminoácido suelto.
REGLAS: list[tuple[str, re.Pattern]] = [
    ("Ultra Competition", re.compile(r"ultra competition", re.I)),
    ("Tokyo Competition", re.compile(r"tokyo competition", re.I)),
    ("Competition", re.compile(r"\bcompetition\b", re.I)),
    ("Isotonic", re.compile(r"\bisotonic\b", re.I)),
    ("Long Energy", re.compile(r"\blong energy\b", re.I)),
    ("Carbo Loader / Fuel / Maltodextrina", re.compile(r"carbo loader|carbo fuel|maltodextrin", re.I)),
    ("Carbo Hydrogel", re.compile(r"carbo hydrogel", re.I)),
    ("Electrolytes", re.compile(r"\belectrolytes\b", re.I)),
    ("Magnesium", re.compile(r"\bmagnesium\b", re.I)),
    ("Sales y minerales", re.compile(r"salt caps|basic minerals", re.I)),
    ("Liquid Energy", re.compile(r"\bliquid energy\b", re.I)),
    ("Power Gums", re.compile(r"\bpower gums\b", re.I)),
    ("High Energy", re.compile(r"\bhigh[- ]energy\b", re.I)),
    ("Energy Balls", re.compile(r"\benergy balls\b", re.I)),
    ("Cereal Energy", re.compile(r"\bcereal energy\b", re.I)),
    ("Oat Pack / Porridge", re.compile(r"oat pack|power porridge", re.I)),
    ("Relax & Recover", re.compile(r"relax\s*&?\s*recover|muscle relax", re.I)),
    ("Recovery", re.compile(r"recovery drink|recovery shake", re.I)),
    ("Whey Isolate 94", re.compile(r"whey isolate 94", re.I)),
    ("Clear Iso Whey", re.compile(r"clear iso whey", re.I)),
    ("Proteínas vegetales y especiales", re.compile(r"vegan protein|senior protein|premium plant protein", re.I)),
    ("Barras proteicas", re.compile(r"protein crunchy|crunchy protein|protein low carb|protein 50|protein cookie dough", re.I)),
    ("Mass Gainer", re.compile(r"mass gainer", re.I)),
    ("Creatina", re.compile(r"creatine monohydrate|hmb\s*&?\s*creatine synergy", re.I)),
    ("Buffers (Beta Alanine, Lactat, Bicarb)", re.compile(r"beta alanine|lactat buffer|bicarb caps", re.I)),
    ("Activator 200", re.compile(r"activator 200", re.I)),
    ("Aminoácidos", re.compile(r"amino 12500|\bbcaa\b|l-glutamine", re.I)),
    ("Pre-entreno y foco", re.compile(r"pre-workout booster|mental focus|caffeine caps|nitroflow|red beet vinitrox", re.I)),
    ("Pérdida de peso", re.compile(r"carnitin 1000|lipox burner|low carb burner|mct oil", re.I)),
    (
        "Salud e inmunidad",
        re.compile(
            r"immunoguard|sport biotics|daily greens|omega-3 plus|glucosamin chondroitin|lactoferrin|collagen hydrolsate",
            re.I,
        ),
    ),
    (
        "Accesorios",
        re.compile(
            r"\bbottle\b|soft flask|wave shaker|sponser cap|replacement cap|\btowel\b|pro bottle",
            re.I,
        ),
    ),
]


def clasificar(descripcion: str) -> str:
    """Devuelve el nombre de la línea comercial para una descripción de producto,
    o SIN_CLASIFICAR si ninguna regla calza (ej. insumos de empaque como "BLISTER 0.6")."""
    texto = descripcion or ""
    for nombre, patron in REGLAS:
        if patron.search(texto):
            return nombre
    return SIN_CLASIFICAR
