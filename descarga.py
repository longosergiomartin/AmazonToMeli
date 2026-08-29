"""
Bajar páginas, por proxy cuando hay clave configurada.

Amazon y los buscadores rechazan las IP de datacenter: desde Render las
lecturas directas fallan casi siempre, y sin ellas no se consigue ni el código
de barras ni los datos del producto. Con `SCRAPER_API_KEY`, las peticiones van
por ScraperAPI, que pone IP residencial y resuelve el captcha.

Vive aparte porque lo necesitan dos módulos que no deberían depender uno del
otro: la importación de productos y la búsqueda de códigos.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import requests

SCRAPERAPI = "https://api.scraperapi.com/"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def configurada() -> bool:
    return bool(os.getenv("SCRAPER_API_KEY", "").strip())


def bajar(url: str, timeout: int = 12, params: dict | None = None,
          headers: dict | None = None, country: str = "us",
          usar_proxy: bool = True):
    """Devuelve `(respuesta, por_proxy)`.

    `por_proxy` sirve para explicar los errores donde corresponde: un 403 del
    proxy es quedarse sin créditos, no que Amazon nos haya bloqueado, y se
    arreglan de forma distinta.

    `usar_proxy=False` fuerza la lectura directa aunque haya clave configurada.
    Sirve para las tareas que no valen créditos: cada página son 5 de los 1.000
    del mes. Desde un servidor la directa casi siempre la rechaza Amazon, pero
    desde una PC hogareña anda.
    """
    clave = os.getenv("SCRAPER_API_KEY", "").strip() if usar_proxy else ""
    if not clave:
        return requests.get(url, timeout=timeout, params=params,
                            headers=headers or {"User-Agent": _UA}), False
    # El proxy recibe la URL como parámetro, así que lo que iba en `params`
    # tiene que ir ya pegado a la URL.
    destino = f"{url}?{urlencode(params)}" if params else url
    return requests.get(SCRAPERAPI, timeout=max(timeout, 70),
                        params={"api_key": clave, "url": destino,
                                "country_code": country}), True


def explicar_error(status: int, por_proxy: bool, quien: str = "Amazon") -> str:
    """Mensaje para el usuario según de dónde vino el error."""
    if not por_proxy:
        return (f"{quien} respondió {status} (suele pasar en servidores, que "
                "rechaza por IP).")
    return {
        401: "ScraperAPI rechazó la clave: revisá SCRAPER_API_KEY en Render.",
        403: "ScraperAPI sin créditos este mes (el plan gratis da 1.000, y cada "
             "página gasta 5). Se retoma el mes que viene o leyendo desde tu "
             "navegador.",
    }.get(status, f"ScraperAPI respondió {status}.")
