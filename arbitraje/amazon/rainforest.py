"""
Proveedor Rainforest API (stub listo para activar).

Rainforest (https://www.rainforestapi.com/) es una API paga que expone la
búsqueda y los precios de Amazon de forma legal y estable (Keepa es otra
alternativa equivalente). Este proveedor deja el enganche hecho: cuando tengas
una API key, la app puede buscar productos en Amazon automáticamente en vez de
cargarlos a mano.

Uso:
    prov = RainforestProvider(api_key="TU_KEY", dominio="amazon.com")
    productos = prov.buscar("wireless earbuds")

Sin API key, cualquier llamado lanza `RainforestNoConfigurado` con instrucciones.
"""

from __future__ import annotations

import os
from typing import List, Optional

import requests

from ..models import Producto
from .base import AmazonProvider

BASE_URL = "https://api.rainforestapi.com/request"


class RainforestNoConfigurado(RuntimeError):
    pass


class RainforestProvider(AmazonProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        dominio: str = "amazon.com",
        categoria_default: str = "default",
        arancel_default: float = 0.16,
        timeout: int = 20,
    ):
        # La key puede venir por argumento o por la variable de entorno.
        self.api_key = api_key or os.environ.get("RAINFOREST_API_KEY")
        self.dominio = dominio
        self.categoria_default = categoria_default
        self.arancel_default = arancel_default
        self.timeout = timeout

    def _check(self) -> None:
        if not self.api_key:
            raise RainforestNoConfigurado(
                "Falta la API key de Rainforest. Pasala como RainforestProvider("
                "api_key=...) o exportá RAINFOREST_API_KEY. Mientras tanto usá "
                "ManualProvider para cargar productos a mano."
            )

    def cargar(self) -> List[Producto]:
        # No hay una "lista fija" en una API de búsqueda; se usa buscar().
        raise RainforestNoConfigurado(
            "RainforestProvider no tiene una lista fija: usá .buscar('término')."
        )

    def buscar(self, query: str) -> List[Producto]:
        self._check()
        params = {
            "api_key": self.api_key,
            "type": "search",
            "amazon_domain": self.dominio,
            "search_term": query,
        }
        resp = requests.get(BASE_URL, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        productos: List[Producto] = []
        for r in data.get("search_results", []):
            precio = (r.get("price") or {}).get("value")
            if precio is None:
                continue
            peso = self._extraer_peso_kg(r)
            productos.append(Producto(
                nombre=r.get("title", "")[:120],
                query_meli=r.get("title", "")[:60],
                precio_amazon_usd=float(precio),
                peso_kg=peso,
                categoria=self.categoria_default,
                arancel_pct=self.arancel_default,
                link_amazon=r.get("link"),
            ))
        return productos

    @staticmethod
    def _extraer_peso_kg(r: dict) -> float:
        # Rainforest no siempre trae el peso en el listado; queda un default
        # razonable. Para peso real conviene el endpoint type=product por ASIN.
        return 0.5
