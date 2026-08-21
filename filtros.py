"""
Filtro de productos: decide qué entra al catálogo.

El botón "Encolar toda la página" captura TODO lo que hay en una búsqueda de
Amazon: patrocinados, accesorios, merchandising y réplicas de terceros. Este
módulo separa lo que vale la pena de lo que no.

Es configurable, porque la herramienta sirve para cualquier rubro:

  - `marca`: si se indica, solo entra esa marca. Vacío = cualquiera.
  - `descartar_accesorios`: saca luces, vitrinas, fundas, llaveros, remeras y
    demás merchandising, que rara vez conviene importar.
  - `precio_min`: piso de precio, para no cargar chucherías.

Se aplica en dos momentos:
  1. En el bookmarklet, sobre el título del resultado, para no gastar pedidos
     a Amazon en cosas que van a descartarse igual (los pedidos son el recurso
     escaso: si nos pasamos, Amazon nos limita).
  2. Acá, al procesar, con el título y la marca reales de la ficha. Esta es la
     decisión que vale.
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

# Marcas de réplicas y accesorios que se cuelan en las búsquedas de sets de
# construcción. Solo se aplican cuando se filtra por la marca LEGO.
_REPLICAS_LEGO = ["all4jig", "briksmax", "lightailing", "kyglaring", "vonado",
                  "mould king", "cada", "sembo", "panlos", "lepin", "wange"]


def _norm(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar sin sorpresas."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


def acepta(titulo: str, marca_producto: str = "",
           precio_usd: Optional[float] = None,
           marca: str = "", descartar_accesorios: bool = True,
           precio_min: float = PRECIO_MIN_USD) -> tuple[bool, str]:
    """¿Este producto entra al catálogo? Devuelve (sí/no, motivo).

    `marca` vacío significa cualquier marca: la herramienta no está atada a un
    rubro. Con una marca cargada, además se descartan las réplicas conocidas.
    """
    t = _norm(titulo)
    m = _norm(marca_producto)
    buscada = _norm(marca).strip()

    if not t and not m:
        return False, "sin título"

    if buscada:
        # Réplicas y "compatible con X": se venden en las búsquedas de la marca
        # pero no son el producto.
        if buscada == "lego":
            for otra in _REPLICAS_LEGO:
                if otra in t or otra in m:
                    return False, f"marca de terceros ({otra})"
        # Palabra completa, para no aceptar "legolas" por "lego".
        patron = r"\b" + re.escape(buscada) + r"\b"
        if not (buscada in m or re.search(patron, t)):
            return False, f"no es {marca}"

    if descartar_accesorios:
        for palabra in _ACCESORIOS:
            if palabra in t:
                return False, f"accesorio ({palabra})"

    if precio_usd is not None and precio_min > 0 and precio_usd < precio_min:
        return False, f"precio bajo (USD {precio_usd:.0f} < {precio_min:.0f})"

    return True, "aceptado"


def es_set_lego(titulo: str, marca: str = "", precio_usd: Optional[float] = None,
                precio_min: float = PRECIO_MIN_USD) -> tuple[bool, str]:
    """Compatibilidad: el filtro de antes, clavado a LEGO."""
    return acepta(titulo, marca, precio_usd, marca="LEGO",
                  descartar_accesorios=True, precio_min=precio_min)


def filtro_js(marca: str = "", descartar_accesorios: bool = True) -> str:
    """Las mismas reglas, en JavaScript, para el bookmarklet.

    Es una versión simplificada (en la página de resultados solo hay título):
    la decisión definitiva la toma `acepta` con los datos de la ficha.
    """
    accesorios = ",".join(f"'{p}'" for p in _ACCESORIOS) if descartar_accesorios else ""
    buscada = _norm(marca).strip()
    replicas = (",".join(f"'{p}'" for p in _REPLICAS_LEGO)
                if buscada == "lego" else "")
    # Sin marca configurada el bookmarklet no filtra por marca: la herramienta
    # sirve para cualquier rubro.
    prueba = (f"if(!(new RegExp('\\\\b{buscada}\\\\b')).test(t))return false;"
              if buscada else "")
    return (
        "function(t){"
        "t=(t||'').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g,'');"
        f"var A=[{accesorios}],M=[{replicas}];"
        "for(var i=0;i<M.length;i++){if(t.indexOf(M[i])>=0)return false;}"
        "for(var j=0;j<A.length;j++){if(t.indexOf(A[j])>=0)return false;}"
        f"{prueba}"
        "return true;"
        "}"
    )
