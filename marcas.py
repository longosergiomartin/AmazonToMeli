"""
Normalización de la marca del producto.

Amazon no publica la marca sola: la pone dentro del "byline" de la página, con
texto alrededor y en el idioma del sitio ("Visit the LEGO Store", "Marca: LEGO",
"LEGO Store"). Si eso viaja tal cual a MercadoLibre, el ítem se rechaza con:

    Attribute BRAND has an invalid value name
    The attributes [BRAND] are required for category MLA1157

(ML descarta el valor por inválido y después se queja de que falta).

Acá se limpia ese texto y, cuando MercadoLibre nos dice qué valores acepta para
la categoría, se elige el valor exacto de su lista para mandar el `value_id`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


def normalizar_texto(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres sin depender del formato."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


# Texto que Amazon agrega alrededor de la marca, en español y en inglés.
_PREFIJOS = (
    r"visit[aá]?\s+la\s+tienda\s+(?:oficial\s+)?de\s+",
    r"visit\s+the\s+",
    r"visit\s+",
    r"tienda\s+(?:oficial\s+)?de\s+",
    r"marca\s*[:\-]\s*",
    r"brand\s*[:\-]\s*",
    r"de\s+la\s+marca\s+",
    r"^by\s+",
)
# Lo que puede quedar solo cuando la página no traía marca de verdad.
_GENERICOS = {"store", "shop", "tienda", "marca", "brand", "the store",
              "la tienda", "tienda oficial", "official store", "generico",
              "generic", "sin marca", "no aplica"}
_SUFIJOS = (
    r"\s+(?:official\s+)?store$",
    r"\s+shop$",
    r"\s+tienda(?:\s+oficial)?$",
)


def limpiar_marca(texto: str) -> str:
    """Deja solo el nombre de la marca. Devuelve "" si no queda nada usable."""
    m = re.sub(r"\s+", " ", (texto or "").strip())
    # Dos pasadas: "Visit the LEGO Store" necesita sacar prefijo y sufijo.
    for _ in range(2):
        for p in _PREFIJOS:
            m = re.sub(r"^" + p, "", m, flags=re.I).strip()
        for s in _SUFIJOS:
            m = re.sub(s, "", m, flags=re.I).strip()
    m = m.strip(" \t\"'“”·,;:-—|")
    # Sin letras ni números no es una marca (quedó solo puntuación o basura).
    if not re.search(r"[0-9a-zA-ZÁÉÍÓÚÜÑáéíóúüñ]", m):
        return ""
    # Si lo único que quedó es la palabra genérica ("Visit the Store" → "Store"),
    # la página no traía marca.
    if normalizar_texto(m) in _GENERICOS:
        return ""
    return m[:60]


def elegir_marca(marca: str, titulo: str = "",
                 permitidas: Optional[list[dict]] = None) -> str:
    """Marca lista para mandar a MercadoLibre.

    `permitidas` es la lista de valores que ML acepta para BRAND en la categoría
    ([{"id", "name"}]). Cuando la tenemos:

      1. Si la marca limpia coincide con una, se usa el nombre tal cual lo
         escribe MercadoLibre (así el `value_id` matchea exacto).
      2. Si no coincide, se busca alguna de esas marcas dentro del título — es
         el caso típico de "LEGO Icons Ghostbusters ECTO-1", donde el título
         dice la marca aunque el byline de Amazon haya venido sucio.

    Sin lista de valores, se devuelve simplemente la marca limpia.
    """
    limpia = limpiar_marca(marca)
    opciones = [v for v in (permitidas or []) if v.get("name")]
    if not opciones:
        return limpia

    if limpia:
        objetivo = normalizar_texto(limpia)
        for v in opciones:
            if normalizar_texto(v["name"]) == objetivo:
                return v["name"]
        return limpia

    # Sin marca usable: buscarla en el título, quedándose con la más larga
    # (entre "LEGO" y "LEGO Duplo" gana la específica).
    t = normalizar_texto(titulo)
    candidatas = [v["name"] for v in opciones
                  if len(normalizar_texto(v["name"])) >= 3
                  and re.search(r"\b" + re.escape(normalizar_texto(v["name"])) + r"\b", t)]
    if candidatas:
        return max(candidatas, key=len)
    return ""
