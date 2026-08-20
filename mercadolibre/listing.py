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

from marcas import elegir_marca, normalizar_texto as _norm


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
        # Solo un valor de la lista de MercadoLibre: uno inventado lo descarta
        # en silencio y después reclama el GTIN como si no se hubiera mandado.
        return _elegir(lambda v: v.startswith("otr"), "")
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
                   currency_id: str = "ARS",
                   campo_titulo: str = "family_name",
                   valores_permitidos: Optional[dict[str, list[dict]]] = None) -> dict:
    """Arma el payload para POST /items de MercadoLibre.

    `campo_titulo` elige cómo se manda el nombre del producto: MercadoLibre
    migró de `title` a `family_name` y **no acepta los dos juntos** (rechaza
    con "The fields [title] are invalid"). Se manda `family_name` por defecto,
    con `title` como alternativa para categorías que todavía lo esperen.

    `valores_permitidos` es {attr_id: [{"id", "name"}]} con los valores que ML
    acepta en la categoría. Cuando el valor coincide con uno de la lista se
    manda el `value_id`, que es la forma que MercadoLibre valida sin objetar.
    """
    permitidos = valores_permitidos or {}

    def _attr(aid: str, valor: str) -> Optional[dict]:
        """Atributo listo para mandar: con `value_id` si MercadoLibre nos dijo
        qué valores acepta y el nuestro está entre ellos, si no con el texto."""
        valor = (valor or "").strip()
        if not valor:
            return None
        objetivo = _norm(valor)
        for v in permitidos.get(aid, []):
            if _norm(v.get("name", "")) == objetivo:
                return {"id": aid, "value_id": v.get("id")}
        return {"id": aid, "value_name": valor}

    attrs = []
    # La marca viene del byline de Amazon ("Visit the LEGO Store"): hay que
    # limpiarla o MercadoLibre la rechaza por "invalid value name".
    marca = elegir_marca(producto.marca, producto.titulo_ml or producto.modelo or "",
                         permitidos.get("BRAND"))
    for aid, valor in (("BRAND", marca), ("MODEL", producto.modelo)):
        a = _attr(aid, valor)
        if a:
            attrs.append(a)
    # Atributos obligatorios extra que el usuario ya haya completado.
    extra = dict(producto.ml_attributes or {})
    # El "motivo de GTIN vacío" solo aplica cuando NO hay GTIN: mandarlo junto
    # con un GTIN cargado es contradictorio y MercadoLibre lo rechaza.
    if (extra.get("GTIN") or "").strip():
        extra.pop("EMPTY_GTIN_REASON", None)
    for aid, val in extra.items():
        if aid in ("BRAND", "MODEL"):
            continue
        a = _attr(aid, val)
        if a:
            attrs.append(a)

    titulo = (producto.titulo_ml or producto.modelo or producto.asin)[:60]
    campo = campo_titulo if campo_titulo in ("family_name", "title") else "family_name"
    item = {
        campo: titulo,
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


def construir_item_catalogo(producto, catalog_product_id: str,
                            listing_type_id: str = "gold_special",
                            condition: str = "new",
                            currency_id: str = "ARS") -> dict:
    """Payload para publicar **contra un producto del catálogo** de ML.

    Es la vía por la que MercadoLibre no pide GTIN ni el resto de los atributos:
    los toma de su propia ficha. Solo hay que decirle qué producto es, a qué
    precio y con cuánto stock. También deja la publicación matcheada con el
    catálogo, que es como aparece bien rankeada.
    """
    item = {
        "catalog_product_id": catalog_product_id,
        "catalog_listing": True,
        "price": round(_precio(producto), 2),
        "currency_id": currency_id,
        "available_quantity": max(0, int(producto.stock)),
        "buying_mode": "buy_it_now",
        "listing_type_id": listing_type_id,
        "condition": condition,
    }
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
    attrs = producto.ml_attributes or {}
    # El código de barras no siempre se consigue. MercadoLibre contempla el
    # caso: si se declara el motivo de GTIN vacío, deja de exigirlo.
    con_motivo = bool((attrs.get("EMPTY_GTIN_REASON") or "").strip())
    for a in (obligatorios or []):
        aid = a.get("id")
        if aid == "GTIN" and con_motivo:
            continue
        # Para la marca vale la limpia: "Visit the LEGO Store" sirve (da LEGO),
        # pero un byline sin nombre adentro no.
        if aid in ("BRAND",) and not elegir_marca(producto.marca,
                                                  producto.titulo_ml or producto.modelo or ""):
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
        # La marca resuelta, que es la que se va a mandar: el panel la muestra
        # editable para poder corregirla antes de publicar.
        "marca": elegir_marca(producto.marca, producto.titulo_ml or producto.modelo or ""),
        "modelo": producto.modelo,
        "descripcion": getattr(producto, "descripcion", "") or "",
        "atributos": producto.ml_attributes,
        "fotos": pictures or [],
        "costo_total_ars": producto.costo_total_ars,
        "precio_sugerido_ars": producto.precio_sugerido_ars,
        "margen_pct": producto.margen_pct,
    }
