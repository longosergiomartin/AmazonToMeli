"""
Datos que se pueden leer del propio título del producto.

Los títulos de Amazon traen, además del nombre, identificadores que
MercadoLibre pide como atributos: el número de set del fabricante y la cantidad
de piezas. Sacarlos de acá evita tener que cargarlos a mano uno por uno.
"""

from __future__ import annotations

import re


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


def piezas_del_titulo(titulo: str) -> str:
    """Cantidad de piezas anunciada en el título ("(802 piezas)" → "802").

    MercadoLibre la pide para los sets de construcción; sin ella la publicación
    sale igual pero peor matcheada con su catálogo.
    """
    m = re.search(r"\b(\d{1,6})\s*(?:piezas|piezas\.|pzas|pcs|pieces|bloques)\b",
                  titulo or "", re.I)
    return m.group(1) if m else ""
