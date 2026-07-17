"""
Evaluador: junta todo el pipeline.

Para cada producto:
  1. Determina el precio de venta en MercadoLibre (precio manual si está fijado,
     si no busca en la API y toma una referencia).
  2. Calcula el costo puesto en Argentina bajo el/los régimen(es) elegidos.
  3. Calcula el neto de la venta en MeLi (comisiones + impuestos + envío).
  4. Arma la Oportunidad con el margen en ARS y en %.

Después podés rankear las oportunidades por margen y quedarte con las mejores.
"""

from __future__ import annotations

from typing import List, Optional

from .config import Config, CONFIG_DEFAULT
from .importacion import calcular_costo
from .meli import buscar_precio_meli, calcular_neto_venta_meli, MeliError
from .models import Producto, Oportunidad, ResultadoMeliBusqueda


def _precio_referencia_meli(
    producto: Producto,
    cfg: Config,
    access_token: Optional[str],
    usar_api: bool,
) -> tuple[Optional[float], List[ResultadoMeliBusqueda]]:
    """Devuelve (precio_venta_referencia, resultados_de_busqueda)."""
    # 1) Precio manual tiene prioridad: es el dato más confiable.
    if producto.precio_meli_manual is not None:
        return producto.precio_meli_manual, []

    # 2) Si no, intentamos la API pública de MeLi.
    if not usar_api:
        return None, []
    try:
        resultados = buscar_precio_meli(
            producto.query_meli, cfg=cfg, access_token=access_token,
        )
    except MeliError:
        return None, []

    if not resultados:
        return None, []

    # Referencia = mediana de precios para evitar outliers (accesorios, usados).
    precios = sorted(r.precio for r in resultados)
    mediana = precios[len(precios) // 2]
    return mediana, resultados


def evaluar_producto(
    producto: Producto,
    regimen: str = "courier",
    cfg: Config = CONFIG_DEFAULT,
    access_token: Optional[str] = None,
    usar_api: bool = True,
) -> Optional[Oportunidad]:
    """Evalúa un producto bajo un régimen. Devuelve None si no se pudo obtener
    un precio de venta de referencia en MeLi."""
    precio_venta, resultados = _precio_referencia_meli(
        producto, cfg, access_token, usar_api,
    )
    if precio_venta is None:
        return None

    costo = calcular_costo(producto, regimen=regimen, cfg=cfg)
    venta = calcular_neto_venta_meli(precio_venta, producto.categoria, cfg=cfg)

    margen_ars = venta.neto_ars - costo.total_ars
    margen_pct = (margen_ars / costo.total_ars * 100) if costo.total_ars else 0.0

    return Oportunidad(
        producto=producto,
        regimen=regimen,
        costo=costo,
        venta=venta,
        precio_venta_ars=precio_venta,
        margen_ars=round(margen_ars, 2),
        margen_pct=round(margen_pct, 1),
        resultados_meli=resultados,
    )


def evaluar_muchos(
    productos: List[Producto],
    regimenes: List[str] | None = None,
    cfg: Config = CONFIG_DEFAULT,
    access_token: Optional[str] = None,
    usar_api: bool = True,
) -> List[Oportunidad]:
    """Evalúa una lista de productos bajo uno o varios regímenes y devuelve las
    oportunidades ordenadas de mayor a menor margen en pesos."""
    regimenes = regimenes or ["courier"]
    oportunidades: List[Oportunidad] = []
    for p in productos:
        for reg in regimenes:
            op = evaluar_producto(
                p, regimen=reg, cfg=cfg,
                access_token=access_token, usar_api=usar_api,
            )
            if op is not None:
                oportunidades.append(op)
    oportunidades.sort(key=lambda o: o.margen_ars, reverse=True)
    return oportunidades
