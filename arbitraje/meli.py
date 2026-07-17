"""
MercadoLibre: búsqueda de precios de venta + cálculo del neto de una venta.

Dos responsabilidades:
  1. `buscar_precio_meli`: consulta la API de MercadoLibre para traer precios de
     venta reales en Argentina. OJO: MeLi restringió el acceso anónimo a varios
     endpoints; hoy suele requerir un access token (OAuth). El cliente maneja el
     error con elegancia y, si no hay datos, se usa `precio_meli_manual`.
  2. `calcular_neto_venta_meli`: descuenta comisión + IVA sobre comisión + costo
     fijo + IIBB + Ganancias + envío para saber cuánto queda neto en el bolsillo.
"""

from __future__ import annotations

from typing import List, Optional

import requests

from .config import Config, CONFIG_DEFAULT
from .models import ResultadoVentaMeli, ResultadoMeliBusqueda


class MeliError(RuntimeError):
    """Error al consultar la API de MercadoLibre."""


def buscar_precio_meli(
    query: str,
    cfg: Config = CONFIG_DEFAULT,
    limit: int = 5,
    access_token: Optional[str] = None,
    timeout: int = 10,
) -> List[ResultadoMeliBusqueda]:
    """Busca en la API de MercadoLibre y devuelve los primeros resultados.

    Devuelve lista vacía si no hay resultados. Lanza `MeliError` si la API
    rechaza la consulta (típicamente 401/403 por falta de token), para que el
    llamador decida si cae al precio manual.
    """
    url = f"https://api.mercadolibre.com/sites/{cfg.meli.site}/search"
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        resp = requests.get(
            url, params={"q": query, "limit": limit},
            headers=headers, timeout=timeout,
        )
    except requests.RequestException as e:
        raise MeliError(f"No se pudo conectar con MercadoLibre: {e}") from e

    if resp.status_code in (401, 403):
        raise MeliError(
            "MercadoLibre rechazó la consulta (probable falta de access token). "
            "Generá un token OAuth o cargá el precio a mano con 'precio_meli_manual'."
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise MeliError(f"Error HTTP de MercadoLibre: {e}") from e

    data = resp.json()
    resultados = []
    for item in data.get("results", []):
        precio = item.get("price")
        if precio is None:
            continue
        resultados.append(ResultadoMeliBusqueda(
            titulo=item.get("title", ""),
            precio=float(precio),
            link=item.get("permalink", ""),
        ))
    return resultados


def calcular_neto_venta_meli(
    precio_venta_ars: float,
    categoria: str = "default",
    cfg: Config = CONFIG_DEFAULT,
) -> ResultadoVentaMeli:
    """Cuánto queda neto (ARS) de una venta en MeLi, descontando comisión +
    IVA sobre comisión + costo fijo + IIBB + Ganancias + envío estimado."""
    m = cfg.meli
    com = m.comisiones.get(categoria, m.comisiones["default"])

    comision = precio_venta_ars * com.comision_pct
    costo_fijo = com.costo_fijo if precio_venta_ars < m.umbral_costo_fijo_ars else 0.0
    iva_sobre_comision = (comision + costo_fijo) * m.iva_sobre_comision
    iibb = precio_venta_ars * m.iibb_pct
    ganancias = precio_venta_ars * m.ganancias_pct
    envio = m.costo_envio_estimado_ars

    total_descuentos = comision + costo_fijo + iva_sobre_comision + iibb + ganancias + envio
    neto = precio_venta_ars - total_descuentos

    return ResultadoVentaMeli(
        precio_venta_ars=round(precio_venta_ars, 2),
        neto_ars=round(neto, 2),
        detalle_ars={
            "comision": round(comision, 2),
            "costo_fijo": round(costo_fijo, 2),
            "iva_sobre_comision": round(iva_sobre_comision, 2),
            "iibb": round(iibb, 2),
            "ganancias": round(ganancias, 2),
            "envio_estimado": round(envio, 2),
        },
    )
