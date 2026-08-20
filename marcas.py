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
    m = texto or ""
    # El scraping a veces se lleva HTML puesto (comentarios y etiquetas del
    # markup de Amazon). Eso llegaba a MercadoLibre como marca y hacía que
    # rechazara el ítem entero con "invalid value name".
    m = re.sub(r"<!--.*?(?:-->|$)", " ", m, flags=re.S)
    m = re.sub(r"<[^>]*>", " ", m)
    m = re.sub(r"\s+", " ", m.strip())
    if "<" in m or "-->" in m:
        return ""  # quedó markup a medias: no es una marca
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
    # Una marca es corta. Si vino un párrafo, es texto de la página, no la marca.
    if len(m) > 60 or len(m.split()) > 5:
        return ""
    return m


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

    if limpia:
        objetivo = normalizar_texto(limpia)
        for v in opciones:
            if normalizar_texto(v["name"]) == objetivo:
                return v["name"]
        return limpia

    # Sin marca usable, se busca en el título. Con la lista de MercadoLibre se
    # elige la coincidencia más larga (entre "LEGO" y "LEGO Duplo" gana la
    # específica); sin lista, se usa la primera palabra, que en los títulos de
    # Amazon es la marca ("LEGO Icons Ghostbusters ECTO-1 10274").
    t = normalizar_texto(titulo)
    candidatas = [v["name"] for v in opciones
                  if len(normalizar_texto(v["name"])) >= 3
                  and re.search(r"\b" + re.escape(normalizar_texto(v["name"])) + r"\b", t)]
    if candidatas:
        return max(candidatas, key=len)
    # La lista de MercadoLibre es de sugerencias, no cierra el universo de
    # marcas: si no hubo match conviene mandar el texto igual, porque mandar el
    # atributo vacío es un rechazo seguro ("The attributes [BRAND] are required").
    return marca_del_titulo(titulo)


# Palabras con las que puede arrancar un título sin ser la marca.
_NO_ES_MARCA = {
    "set", "sets", "kit", "juego", "juegos", "juguete", "juguetes", "pack",
    "nuevo", "nueva", "new", "the", "el", "la", "los", "las", "un", "una",
    "bloques", "building", "toy", "toys", "figura", "coleccion", "colección",
}


def _parece_marca(palabra: str) -> bool:
    if not (2 <= len(palabra) <= 30):
        return False
    if normalizar_texto(palabra) in _NO_ES_MARCA:
        return False
    return bool(re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", palabra))


def marca_del_titulo(titulo: str) -> str:
    """Marca deducida del título.

    Los títulos de Amazon suelen arrancar con la marca ("LEGO Icons…"), pero no
    siempre: los traducidos la corren de lugar ("Set de construcción Star Wars
    de LEGO, Darth Vader"). Por eso, si la primera palabra no sirve, se busca un
    token en mayúsculas dentro del título, que es como se escriben las marcas.

    Es una estimación, no un dato: el panel muestra el campo Marca editable.
    """
    palabras = [p.strip(".-&,") for p in
                re.findall(r"[0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ&.\-]+", titulo or "")]
    palabras = [p for p in palabras if p]
    if not palabras:
        return ""
    if _parece_marca(palabras[0]):
        return palabras[0]
    for p in palabras[1:]:
        # Marca escrita en mayúsculas en medio del título (LEGO, HISEA, BOSCH).
        if len(p) >= 3 and p.isupper() and _parece_marca(p):
            return p
    return ""
