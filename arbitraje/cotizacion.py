"""
Cotización del dólar en vivo (oficial y tarjeta).

Usa la API pública y gratuita de dolarapi.com. Cachea el resultado unos minutos
para no consultar en cada cálculo, y si no hay conexión cae a los valores de la
config (para que el sistema siga funcionando offline).

  - oficial: dólar oficial (venta).
  - tarjeta: dólar tarjeta (oficial + percepciones), que es lo que realmente
    pagás al comprar en Amazon con tarjeta argentina.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import requests

URL_OFICIAL = "https://dolarapi.com/v1/dolares/oficial"
URL_TARJETA = "https://dolarapi.com/v1/dolares/tarjeta"

_cache: dict = {}


def _fallback(cfg) -> dict:
    oficial = getattr(cfg, "tipo_cambio_oficial", 1300.0) if cfg else 1300.0
    recargo = getattr(cfg, "recargo_tarjeta_pct", 0.30) if cfg else 0.30
    return {
        "oficial": round(oficial, 2),
        "tarjeta": round(oficial * (1 + recargo), 2),
        "fuente": "config (sin conexión)",
        "online": False,
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def obtener_cotizaciones(cfg=None, ttl: int = 900, timeout: int = 8) -> dict:
    """Devuelve {oficial, tarjeta, fuente, online, actualizado}. Cachea `ttl`
    segundos. Nunca lanza: ante cualquier error, cae a la config."""
    ahora = time.time()
    if _cache and ahora - _cache.get("_ts", 0) < ttl:
        return _cache["data"]

    try:
        of = requests.get(URL_OFICIAL, timeout=timeout).json()
        ta = requests.get(URL_TARJETA, timeout=timeout).json()
        oficial = float(of.get("venta"))
        tarjeta = float(ta.get("venta"))
        if oficial <= 0 or tarjeta <= 0:
            raise ValueError("cotización inválida")
        data = {
            "oficial": round(oficial, 2),
            "tarjeta": round(tarjeta, 2),
            "fuente": "dolarapi.com",
            "online": True,
            "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception:
        data = _fallback(cfg)

    _cache["_ts"] = ahora
    _cache["data"] = data
    return data


def invalidar_cache() -> None:
    _cache.clear()
