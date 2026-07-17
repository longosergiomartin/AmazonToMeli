"""
Cálculo del costo puesto en Argentina de un producto importado desde Amazon.

Soporta dos regímenes:
  - courier  : régimen simplificado "Puerta a Puerta" (default, realista para
               productos sueltos).
  - general  : régimen del importador registrado (aranceles NCM, despachante,
               IVA + percepciones). Sirve para volumen.

Todas las cuentas se hacen en USD y al final se pasan a ARS con el tipo de
cambio de la config.
"""

from __future__ import annotations

from .config import Config, CONFIG_DEFAULT
from .models import Producto, ResultadoImportacion


def costo_courier(
    producto: Producto,
    cfg: Config = CONFIG_DEFAULT,
    franquicia_disponible_usd: float | None = None,
) -> ResultadoImportacion:
    """Costo puesto en Argentina bajo régimen courier / puerta a puerta.

    franquicia_disponible_usd: cuánto te queda de la franquicia anual exenta
    (default: toda la franquicia). El impuesto único se aplica solo sobre el
    excedente por encima de la franquicia disponible.
    """
    c = cfg.courier
    fob = producto.precio_amazon_usd

    if franquicia_disponible_usd is None:
        franquicia_disponible_usd = c.franquicia_anual_usd
    franquicia = max(0.0, min(franquicia_disponible_usd, c.franquicia_anual_usd))

    flete = max(producto.peso_kg * c.flete_usd_por_kg, c.flete_minimo_usd)

    base_imponible = max(0.0, fob - franquicia)
    impuesto = base_imponible * c.tasa_impuesto

    total_usd = fob + flete + impuesto
    total_ars = total_usd * cfg.tipo_cambio_oficial

    excede_tope = fob > c.tope_por_envio_usd

    return ResultadoImportacion(
        regimen="courier",
        total_usd=round(total_usd, 2),
        total_ars=round(total_ars, 2),
        detalle_usd={
            "fob": round(fob, 2),
            "flete_courier": round(flete, 2),
            "franquicia_aplicada": round(franquicia, 2),
            "base_imponible": round(base_imponible, 2),
            "impuesto_50pct": round(impuesto, 2),
            "excede_tope_por_envio": float(excede_tope),  # 1.0 = ojo, supera el tope
        },
    )


def costo_general(
    producto: Producto,
    cfg: Config = CONFIG_DEFAULT,
) -> ResultadoImportacion:
    """Costo puesto en Argentina bajo régimen general / importador registrado."""
    g = cfg.general
    fob = producto.precio_amazon_usd

    flete_seguro = fob * g.flete_seguro_pct
    cif = fob + flete_seguro  # Cost + Insurance + Freight

    derechos_importacion = cif * producto.arancel_pct
    tasa_estadistica = cif * g.tasa_estadistica_pct

    base_iva = cif + derechos_importacion + tasa_estadistica
    iva = base_iva * g.iva_pct
    percepcion_iva = base_iva * g.percepcion_iva_pct
    percepcion_ganancias = base_iva * g.percepcion_ganancias_pct
    percepcion_iibb = base_iva * g.percepcion_iibb_pct

    despachante = max(cif * g.despachante_pct, g.despachante_minimo_usd)
    gastos_portuarios = g.gastos_portuarios_usd

    total_usd = (cif + derechos_importacion + tasa_estadistica + iva +
                 percepcion_iva + percepcion_ganancias + percepcion_iibb +
                 despachante + gastos_portuarios)
    total_ars = total_usd * cfg.tipo_cambio_oficial

    return ResultadoImportacion(
        regimen="general",
        total_usd=round(total_usd, 2),
        total_ars=round(total_ars, 2),
        detalle_usd={
            "fob": round(fob, 2),
            "flete_seguro": round(flete_seguro, 2),
            "cif": round(cif, 2),
            "derechos_importacion": round(derechos_importacion, 2),
            "tasa_estadistica": round(tasa_estadistica, 2),
            "iva": round(iva, 2),
            "percepcion_iva": round(percepcion_iva, 2),
            "percepcion_ganancias": round(percepcion_ganancias, 2),
            "percepcion_iibb": round(percepcion_iibb, 2),
            "despachante": round(despachante, 2),
            "gastos_portuarios": round(gastos_portuarios, 2),
        },
    )


def calcular_costo(
    producto: Producto,
    regimen: str = "courier",
    cfg: Config = CONFIG_DEFAULT,
    **kwargs,
) -> ResultadoImportacion:
    """Despachador: elige el régimen por nombre."""
    if regimen == "courier":
        return costo_courier(producto, cfg, **kwargs)
    if regimen == "general":
        return costo_general(producto, cfg)
    raise ValueError(f"Régimen desconocido: {regimen!r} (usá 'courier' o 'general')")
