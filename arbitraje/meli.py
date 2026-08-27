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
    """Cuánto queda neto (ARS) de una venta en MercadoLibre.

    Como % del precio de venta:
      - costos_ml : comisión por vender (sin el envío).
      - iva       : IVA débito fiscal de la venta (21% si sos RI). Ojo: la
                    importación genera crédito fiscal que lo compensa, así que
                    el impacto real suele ser menor. Poné 0 si sos Monotributista.
      - ganancias, iibb : retenciones, a cuenta de impuestos anuales
                    (recuperables, pero salen de la caja al momento de cobrar).
      - percepcion_iva : escalón, solo por encima del tope de ARCA.

    En pesos fijos, sin importar el precio:
      - envio     : lo que pagás por ofrecer envío gratis.
    """
    m = cfg.meli
    costos_ml = precio_venta_ars * m.costos_ml_pct(categoria)
    iva = precio_venta_ars * m.iva_pct
    ganancias = precio_venta_ars * m.ganancias_pct
    iibb = precio_venta_ars * m.iibb_pct
    percepcion_iva = m.percepcion_iva_ars(precio_venta_ars)
    envio = m.envio_gratis_ars

    neto = precio_venta_ars - (costos_ml + iva + ganancias + iibb
                               + percepcion_iva + envio)

    return ResultadoVentaMeli(
        precio_venta_ars=round(precio_venta_ars, 2),
        neto_ars=round(neto, 2),
        detalle_ars={
            "costos_ml": round(costos_ml, 2),
            "iva": round(iva, 2),
            "ganancias": round(ganancias, 2),
            "iibb": round(iibb, 2),
            "percepcion_iva": round(percepcion_iva, 2),
            "envio": round(envio, 2),
        },
    )
