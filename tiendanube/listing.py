"""
Construcción del producto de Tiendanube a partir de un producto del catálogo.

`construir_producto` arma el JSON que espera POST /products.
`faltantes_para_publicar` dice qué falta antes de intentarlo, para no gastar una
llamada que va a volver rechazada.

Diferencias con MercadoLibre que definen la forma del payload:

  - **Nombre y descripción son diccionarios por idioma** (`{"es": "..."}`), no
    strings. Mandar un string pelado lo rechaza.
  - **El precio y el stock viven en la variante**, no en el producto. Un
    producto sin variantes no se puede comprar.
  - **No hay categoría obligatoria ni atributos obligatorios**: nada de GTIN,
    ni predicción de categoría, ni los `sale_terms` de ML. Es mucho más simple.
  - **No hay límite de 60 caracteres en el título.** El título recortado para
    ML no tiene por qué aplicarse acá, así que se usa el nombre largo cuando
    está disponible.
"""

from __future__ import annotations

from typing import Optional

IDIOMA = "es"


def titulo_para_tiendanube(producto) -> str:
    """El nombre a mostrar en la tienda.

    En MercadoLibre el título se recorta a 60 caracteres, y esa poda existe por
    un límite de ML que acá no rige. Se prefiere el nombre largo del producto y
    se cae al título de ML solo si no hay otra cosa.
    """
    for campo in (producto.modelo, producto.titulo_ml, producto.asin):
        texto = (campo or "").strip()
        if texto:
            return texto
    return "Producto"


def precio_base(producto) -> float:
    """El precio del que se parte: el publicado en ML si existe, si no el
    sugerido. Es el mismo criterio que usa la publicación de MercadoLibre."""
    return float(producto.precio_publicado_ars or producto.precio_sugerido_ars or 0)


def precio_para_tiendanube(producto, ajuste_pct: float = 0.0) -> float:
    """El precio de la tienda propia.

    Arranca igual que el de MercadoLibre y se corrige con un porcentaje
    configurable. En la tienda propia no se paga la comisión de MercadoLibre ni
    el envío gratis subsidiado, así que hay lugar para vender más barato; pero
    cuánto de eso conviene resignar es una decisión comercial, no una cuenta:
    por eso es un número que se pone a mano y no una fórmula.
    """
    return round(precio_base(producto) * (1 + (ajuste_pct or 0.0) / 100.0), 2)


def faltantes_para_publicar(producto, ajuste_pct: float = 0.0) -> list[str]:
    """Qué falta para poder publicarlo. Vacío = está listo."""
    faltan = []
    if not titulo_para_tiendanube(producto).strip():
        faltan.append("nombre del producto")
    if precio_para_tiendanube(producto, ajuste_pct) <= 0:
        faltan.append("precio de venta")
    if not (producto.pictures or []):
        faltan.append("al menos una foto")
    return faltan


def construir_producto(producto, ajuste_pct: float = 0.0,
                       pictures: Optional[list] = None,
                       publicado: bool = True) -> dict:
    """El JSON de POST /products."""
    fotos = pictures if pictures is not None else (producto.pictures or [])
    item: dict = {
        "name": {IDIOMA: titulo_para_tiendanube(producto)},
        # Se manda como texto plano dentro del dict de idioma. Tiendanube
        # acepta HTML acá, pero la descripción que arma la herramienta ya viene
        # formateada en texto y meterla como HTML sin escapar rompería el
        # renderizado con cualquier `<` que traiga la ficha de Amazon.
        "description": {IDIOMA: producto.descripcion or ""},
        "images": [{"src": u} for u in fotos if u],
        "variants": [{
            # Texto, no número: mandar un float puede perder centavos.
            "price": f"{precio_para_tiendanube(producto, ajuste_pct):.2f}",
            "stock": int(producto.stock or 0),
        }],
        "published": bool(publicado),
    }
    peso = float(producto.peso_kg or 0)
    if peso > 0:
        item["variants"][0]["weight"] = f"{peso:.3f}"
    return item


def vista_previa(producto, ajuste_pct: float = 0.0,
                 pictures: Optional[list] = None) -> dict:
    """Versión legible para mostrar antes de publicar."""
    fotos = pictures if pictures is not None else (producto.pictures or [])
    base = precio_base(producto)
    precio = precio_para_tiendanube(producto, ajuste_pct)
    return {
        "nombre": titulo_para_tiendanube(producto),
        "precio_ars": precio,
        "precio_en_ml": base,
        "diferencia_ars": round(precio - base, 2),
        "stock": int(producto.stock or 0),
        "fotos": list(fotos),
        "descripcion": (producto.descripcion or "")[:600],
        "faltantes": faltantes_para_publicar(producto, ajuste_pct),
    }
