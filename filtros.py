"""
Filtro de productos: quedarse solo con sets LEGO de verdad.

El botón "Encolar toda la página" captura TODO lo que hay en una búsqueda de
Amazon: patrocinados, accesorios de terceros y productos que ni son LEGO. Este
módulo decide qué entra al catálogo.

Se aplica en dos momentos:
  1. En el bookmarklet, sobre el título del resultado, para no gastar pedidos
     a Amazon en cosas que van a descartarse igual (los pedidos son el recurso
     escaso: si nos pasamos, Amazon nos limita).
  2. Acá, al procesar, con el título y la marca reales de la ficha. Esta es la
     decisión que vale.

Qué se descarta:
  - Lo que no es LEGO (otras marcas, "compatible con LEGO").
  - Accesorios: luces, vitrinas, mesas, organizadores, soportes, fundas.
  - Cosas muy baratas (llaveros, sobres de minifiguras) según un precio mínimo.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Precio mínimo por defecto: por debajo de esto suelen ser llaveros, polybags
# o repuestos, no sets que valga la pena importar.
PRECIO_MIN_USD = 25.0

# Si el título trae alguna de estas, es un accesorio o algo de terceros:
# "compatible con LEGO" es la marca registrada de que NO es LEGO.
_ACCESORIOS = [
    "compatible", "compatibles", "no lego", "not lego",
    "luces led", "kit de luz", "kit de luces", "light kit", "led light",
    "vitrina", "display case", "expositor", "acrilico", "acrylic",
    "organizador", "almacenamiento", "storage", "contenedor",
    "mesa de", "escritorio", "alfombra", "manta",
    "soporte de pared", "montaje en pared", "wall mount", "stand para",
    # "bolsa de almacenamiento", no "bolsa de policía" (que sería un set).
    "funda", "bolsa de almacenamiento", "bolsa para", "estuche", "maletin",
    "separador de ladrillos", "brick separator", "pinza",
    "repuesto", "reemplazo", "replacement", "piezas sueltas",
    "adhesivo", "sticker", "calcomania", "pegatina",
    "llavero", "keychain", "iman", "magnet",
    "libro", "guia", "instrucciones", "manual",
    "disfraz", "remera", "camiseta", "taza",
]

# Marcas de terceros que suelen aparecer en búsquedas de LEGO.
_OTRAS_MARCAS = ["all4jig", "briksmax", "lightailing", "kyglaring", "vonado",
                 "mould king", "cada", "sembo", "panlos", "lepin", "wange"]


def _norm(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar sin sorpresas."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def es_set_lego(titulo: str, marca: str = "", precio_usd: Optional[float] = None,
                precio_min: float = PRECIO_MIN_USD) -> tuple[bool, str]:
    """¿Es un set LEGO que vale la pena cargar? Devuelve (sí/no, motivo)."""
    t = _norm(titulo)
    m = _norm(marca)

    if not t and not m:
        return False, "sin título"

    # 1) Marcas de terceros que se cuelan en las búsquedas de LEGO.
    for otra in _OTRAS_MARCAS:
        if otra in t or otra in m:
            return False, f"marca de terceros ({otra})"

    # 2) Accesorios y merchandising.
    for palabra in _ACCESORIOS:
        if palabra in t:
            return False, f"accesorio ({palabra})"

    # 3) Tiene que ser LEGO: por marca, o "lego" como palabra en el título.
    #    Se exige palabra completa para no aceptar "legolas" ni similares.
    es_lego = "lego" in m or bool(re.search(r"\blego\b", t))
    if not es_lego:
        return False, "no es LEGO"

    # 4) Precio mínimo: descarta llaveros, polybags y repuestos baratos.
    if precio_usd is not None and precio_min > 0 and precio_usd < precio_min:
        return False, f"precio bajo (USD {precio_usd:.0f} < {precio_min:.0f})"

    return True, "set LEGO"


def filtro_js() -> str:
    """Las mismas reglas, en JavaScript, para el bookmarklet.

    Es una versión simplificada (en la página de resultados solo hay título):
    la decisión definitiva la toma `es_set_lego` con los datos de la ficha.
    """
    accesorios = ",".join(f"'{p}'" for p in _ACCESORIOS)
    marcas = ",".join(f"'{p}'" for p in _OTRAS_MARCAS)
    return (
        "function(t){"
        "t=(t||'').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'');"
        f"var A=[{accesorios}],M=[{marcas}];"
        "for(var i=0;i<M.length;i++){if(t.indexOf(M[i])>=0)return false;}"
        "for(var j=0;j<A.length;j++){if(t.indexOf(A[j])>=0)return false;}"
        "return /\\blego\\b/.test(t);"
        "}"
    )
