"""
Datos que se pueden leer del propio título del producto.

Los títulos de Amazon traen, además del nombre, identificadores que
MercadoLibre pide como atributos: el número de set del fabricante y la cantidad
de piezas. Sacarlos de acá evita tener que cargarlos a mano uno por uno.
"""

from __future__ import annotations

import re
import unicodedata


def numero_de_set(titulo: str) -> str:
    """Número de set del fabricante ("LEGO Star Wars 75339" → "75339").

    Es el identificador con el que el producto está cargado en el catálogo de
    MercadoLibre, así que sirve para encontrarlo sin ambigüedad. Los sets tienen
    4 o 5 dígitos; los años (19xx/20xx) y las cantidades ("802 piezas") se
    descartan para no confundirlos con el set.
    """
    texto = titulo or ""
    candidatos = []
    for m in re.finditer(r"\b(\d{4,5})\b", texto):
        num = m.group(1)
        if len(num) == 4 and (num.startswith("19") or num.startswith("20")):
            continue  # un año, no un set
        resto = texto[m.end():m.end() + 12].lower()
        if re.match(r"\s*(piezas|pcs|pieces|bloques|ml\b|g\b|cm)", resto):
            continue
        candidatos.append(num)
    # El número de set suele ser el último token numérico del título.
    return candidatos[-1] if candidatos else ""


# Arranques de marketing que Amazon le pone a los títulos traducidos y que no
# aportan nada en MercadoLibre.
_PREFIJOS_MARKETING = (
    r"set de construcci[óo]n( de)?", r"juego de construcci[óo]n( de)?",
    r"juguete para armar", r"kit de construcci[óo]n( de)?",
    r"juego de", r"set de", r"kit de", r"building (kit|set|toy)",
)


def titulo_para_ml(marca: str, titulo: str, numero_set: str = "",
                   limite: int = 60) -> str:
    """Título para MercadoLibre: marca adelante y número de set al final.

    El título de Amazon es marketing traducido ("Set de construcción Star Wars
    de LEGO, Darth Vader, talla única") y recortarlo a 60 caracteres deja afuera
    justo el número de set. Acá se arma uno que entra en el límite conservando
    lo que sirve para buscar y para que el comprador entienda qué es.
    """
    base = re.sub(r"\s+", " ", (titulo or "")).strip()
    for p in _PREFIJOS_MARKETING:
        base = re.sub(r"^" + p + r"\s*", "", base, flags=re.I).strip()
    marca = (marca or "").strip()
    if marca:
        # La marca va una sola vez y adelante: en los títulos traducidos aparece
        # en el medio ("...Star Wars de LEGO, Darth Vader").
        base = re.sub(r"\bde\s+" + re.escape(marca) + r"\b", "", base, flags=re.I)
        base = re.sub(r"\b" + re.escape(marca) + r"\b", "", base, flags=re.I)
    # El número de set se saca del cuerpo y se vuelve a poner al final: si se
    # deja donde estaba, el recorte a 60 caracteres se lo come justo a él, que
    # es el dato con el que después se busca el producto en el catálogo.
    if numero_set:
        base = re.sub(r"[#nN]?[°º]?\s*" + re.escape(numero_set) + r"\b", "", base)
    # La cantidad de piezas va como atributo aparte; en el título solo gasta
    # caracteres y, al quitar el número de set, deja restos ("Set # – 1 103").
    base = re.sub(r"\(?\s*[\d.,]+\s*(piezas|pzas|pcs|pieces|bloques)\b\)?",
                  "", base, flags=re.I)
    base = re.sub(r"\s*[,;·|]\s*", " ", base)
    base = re.sub(r"[#]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" ,-–—:")

    sufijo = f" {numero_set}" if numero_set else ""
    prefijo = f"{marca} " if marca else ""
    espacio = limite - len(prefijo) - len(sufijo)
    if espacio < 1:
        return (prefijo + sufijo.strip())[:limite].strip()
    if len(base) > espacio:
        base = base[:espacio].rsplit(" ", 1)[0].strip(" ,-–—")
    return (prefijo + base + sufijo).strip()


# Palabras que aparecen en cualquier título y no distinguen un producto de otro.
_VACIAS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "con", "para", "por",
    "y", "a", "en", "the", "of", "for", "with", "and", "to",
    "set", "sets", "kit", "kits", "juego", "juegos", "juguete", "juguetes",
    "building", "toy", "toys", "block", "blocks", "bloques", "construccion",
    "piezas", "pieces", "pzas", "pcs", "modelo", "model", "coleccionable",
    "regalo", "gift", "nuevo", "new", "edicion", "edition", "adultos", "adults",
}


def _tokens(texto: str) -> set:
    palabras = re.findall(r"[0-9a-zA-ZÁÉÍÓÚÜÑáéíóúüñ]+", texto or "")
    return {p for p in (normalizar(w) for w in palabras)
            if len(p) >= 3 and p not in _VACIAS}


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def parecido(referencia: str, candidato: str) -> float:
    """Qué proporción de las palabras distintivas de `referencia` aparece en
    `candidato`. Sirve para decidir si dos títulos son el mismo producto.

    Si los dos traen número de modelo y **no coinciden**, devuelve 0: son
    productos distintos por más que compartan todas las palabras (dos sets de
    Star Wars comparten casi todo el título menos el número, que es justo lo
    único que los distingue).
    """
    a, b = _tokens(referencia), _tokens(candidato)
    if not a or not b:
        return 0.0
    na, nb = numero_de_set(referencia), numero_de_set(candidato)
    if na and nb and na != nb:
        return 0.0
    return len(a & b) / len(a)


def piezas_del_titulo(titulo: str) -> str:
    """Cantidad de piezas anunciada en el título ("(802 piezas)" → "802").

    MercadoLibre la pide para los sets de construcción; sin ella la publicación
    sale igual pero peor matcheada con su catálogo.
    """
    m = re.search(r"\b(\d{1,6})\s*(?:piezas|piezas\.|pzas|pcs|pieces|bloques)\b",
                  titulo or "", re.I)
    return m.group(1) if m else ""
