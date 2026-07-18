"""
Construcción del ítem de MercadoLibre a partir de un producto del catálogo.

`construir_item` arma el JSON que espera el endpoint POST /items. `vista_previa`
devuelve una versión legible para mostrarle al usuario antes de aprobar.

El precio que se usa es el publicado si existe, si no el sugerido. El estado no
se toca acá: la creación en MercadoLibre la hace el servicio solo tras la
aprobación explícita.
"""

from __future__ import annotations

from typing import Optional


def _precio(producto) -> float:
    return float(producto.precio_publicado_ars or producto.precio_sugerido_ars or 0)


def construir_item(producto, pictures: Optional[list[str]] = None,
                   listing_type_id: str = "gold_special",
                   condition: str = "new",
                   currency_id: str = "ARS") -> dict:
    """Arma el payload para POST /items de MercadoLibre."""
    attrs = []
    if producto.marca:
        attrs.append({"id": "BRAND", "value_name": producto.marca})
    if producto.modelo:
        attrs.append({"id": "MODEL", "value_name": producto.modelo})
    # Atributos obligatorios extra que el usuario ya haya completado.
    for aid, val in (producto.ml_attributes or {}).items():
        if aid in ("BRAND", "MODEL"):
            continue
        attrs.append({"id": aid, "value_name": val})

    item = {
        "title": (producto.titulo_ml or producto.modelo or producto.asin)[:60],
        "category_id": producto.ml_category_id,
        "price": round(_precio(producto), 2),
        "currency_id": currency_id,
        "available_quantity": max(0, int(producto.stock)),
        "buying_mode": "buy_it_now",
        "listing_type_id": listing_type_id,
        "condition": condition,
        "pictures": [{"source": u} for u in (pictures or []) if u],
        "attributes": attrs,
    }
    return item


def faltantes_para_publicar(producto, obligatorios: Optional[list[dict]] = None,
                            pictures: Optional[list[str]] = None) -> list[str]:
    """Lista de cosas que faltan para poder publicar (validación previa)."""
    faltan = []
    if not producto.titulo_ml and not producto.modelo:
        faltan.append("título de la publicación")
    if not producto.ml_category_id:
        faltan.append("categoría de MercadoLibre")
    if _precio(producto) <= 0:
        faltan.append("precio de venta")
    if not pictures:
        faltan.append("al menos una foto")
    for a in (obligatorios or []):
        aid = a.get("id")
        if aid in ("BRAND",) and not producto.marca:
            faltan.append(f"atributo obligatorio: {a.get('name', aid)}")
        elif aid in ("MODEL",) and not producto.modelo:
            faltan.append(f"atributo obligatorio: {a.get('name', aid)}")
        elif aid not in ("BRAND", "MODEL") and aid not in (producto.ml_attributes or {}):
            faltan.append(f"atributo obligatorio: {a.get('name', aid)}")
    return faltan


def vista_previa(producto, pictures: Optional[list[str]] = None) -> dict:
    """Versión legible del borrador para mostrar antes de aprobar."""
    return {
        "titulo": (producto.titulo_ml or producto.modelo or producto.asin)[:60],
        "categoria_id": producto.ml_category_id,
        "precio_ars": round(_precio(producto), 2),
        "moneda": "ARS",
        "stock": producto.stock,
        "condicion": "nuevo",
        "marca": producto.marca,
        "modelo": producto.modelo,
        "atributos": producto.ml_attributes,
        "fotos": pictures or [],
        "costo_total_ars": producto.costo_total_ars,
        "margen_pct": producto.margen_pct,
    }
