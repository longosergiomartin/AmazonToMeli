"""
Construcción del ítem de MercadoLibre a partir de un producto del catálogo.

`construir_item` arma el JSON que espera el endpoint POST /items. `vista_previa`
devuelve una versión legible para mostrarle al usuario antes de aprobar.

El precio que se usa es el publicado si existe, si no el sugerido. El estado no
se toca acá: la creación en MercadoLibre la hace el servicio solo tras la
aprobación explícita.
"""

from __future__ import annotations

import unicodedata
from typing import Optional


def _norm(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres de atributos/valores."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def valor_por_defecto(attr: dict) -> str:
    """Valor sugerido para atributos administrativos que siempre se completan
    igual, eligiendo —cuando se puede— una de las opciones que MercadoLibre
    permite para ese atributo:

      - IVA                     → 21 %
      - Impuesto interno        → 0 %
      - Motivo de GTIN vacío    → la opción de tipo "Otro"

    Devuelve "" si el atributo no es de los que tienen default.
    """
    aid = (attr.get("id") or "").upper()
    nombre = _norm(attr.get("name", ""))
    valores = [v for v in (attr.get("values") or []) if v]

    def _elegir(predicado, fallback: str) -> str:
        for v in valores:
            if predicado(_norm(v)):
                return v
        return fallback

    if aid == "EMPTY_GTIN_REASON" or ("gtin" in nombre and "vaci" in nombre):
        return _elegir(lambda v: v.startswith("otr"), "Otro")
    if aid in ("IVA", "VAT") or nombre == "iva":
        return _elegir(lambda v: v.startswith("21"), "21 %")
    if aid == "INTERNAL_TAX" or "impuesto interno" in nombre:
        return _elegir(lambda v: v.startswith("0"), "0 %")
    return ""


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
    extra = dict(producto.ml_attributes or {})
    # El "motivo de GTIN vacío" solo aplica cuando NO hay GTIN: mandarlo junto
    # con un GTIN cargado es contradictorio y MercadoLibre lo rechaza.
    if (extra.get("GTIN") or "").strip():
        extra.pop("EMPTY_GTIN_REASON", None)
    for aid, val in extra.items():
        if aid in ("BRAND", "MODEL"):
            continue
        attrs.append({"id": aid, "value_name": val})

    titulo = (producto.titulo_ml or producto.modelo or producto.asin)[:60]
    item = {
        "title": titulo,
        # MercadoLibre reemplazó `title` por `family_name`: lo mapea solo por
        # compatibilidad, pero varias categorías ya lo exigen explícito (si no,
        # rechaza con "body does not contains ... [family_name]").
        "family_name": titulo,
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
    # Días de preparación: se muestran en la entrega (MercadoLibre los suma a la
    # fecha estimada). Es el "El vendedor necesita N días para tener listo el
    # producto" que se ve en la publicación.
    dias = int(getattr(producto, "dias_preparacion", 0) or 0)
    if dias > 0:
        item["sale_terms"] = [{"id": "MANUFACTURING_TIME", "value_name": f"{dias} días"}]
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
        "dias_preparacion": int(getattr(producto, "dias_preparacion", 0) or 0),
        "condicion": "nuevo",
        "marca": producto.marca,
        "modelo": producto.modelo,
        "descripcion": getattr(producto, "descripcion", "") or "",
        "atributos": producto.ml_attributes,
        "fotos": pictures or [],
        "costo_total_ars": producto.costo_total_ars,
        "precio_sugerido_ars": producto.precio_sugerido_ars,
        "margen_pct": producto.margen_pct,
    }
