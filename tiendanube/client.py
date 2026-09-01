"""
Cliente de la API de Tiendanube / Nuvemshop.

Recibe un `token_provider` y un `store_provider` (funciones que devuelven el
token y el id de tienda) para poder testear inyectando falsos, sin tocar la red.

Tres cosas de esta API que no son como uno esperaría, y que si se hacen mal
fallan sin decir por qué:

  1. **El header de autenticación se llama `Authentication`, no `Authorization`.**
     Es lo primero a revisar si todo devuelve 401.
  2. **`User-Agent` es obligatorio** y tiene que identificar la app con un mail
     de contacto. Sin él, Tiendanube rechaza.
  3. **Los precios viajan como texto**, no como número: `"1234.50"`. Mandar un
     float puede perder centavos en la serialización.

Endpoints usados:
  - GET  /products                        → listar (sirve de prueba de conexión)
  - POST /products                        → crear producto
  - PUT  /products/{id}                   → actualizar nombre/descripción
  - PUT  /products/{id}/variants/{vid}    → actualizar precio y stock
  - DELETE /products/{id}                 → borrar
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import requests

API_BASE = "https://api.tiendanube.com/v1"

# Tiendanube limita por "bucket" y contesta 429 al pasarse. Mismas esperas que
# con MercadoLibre: tres intentos alcanzan para pasar una ráfaga sin dejar el
# pedido colgado.
ESPERAS_429 = (2.0, 5.0, 12.0)


class TiendanubeAPIError(RuntimeError):
    def __init__(self, mensaje: str, status: Optional[int] = None, cuerpo=None):
        super().__init__(mensaje)
        self.status = status
        self.cuerpo = cuerpo


def describir_error(cuerpo) -> str:
    """El texto legible de un rechazo de Tiendanube.

    La API contesta de varias formas: `{"campo": ["mensaje"]}` para errores de
    validación, `{"message": "..."}`, `{"description": "..."}`, o texto pelado.
    Se devuelven todas tal cual: interpretar de más acá ya costó caro con
    MercadoLibre, donde una lectura mía tapó el mensaje que explicaba el fallo.
    """
    if isinstance(cuerpo, str):
        return cuerpo[:400]
    if not isinstance(cuerpo, dict):
        return ""
    for clave in ("message", "description", "error"):
        if isinstance(cuerpo.get(clave), str):
            return cuerpo[clave][:400]
    partes = []
    for campo, detalle in cuerpo.items():
        if isinstance(detalle, list):
            partes.append(f"{campo}: {'; '.join(str(x) for x in detalle)}")
        else:
            partes.append(f"{campo}: {detalle}")
    return " · ".join(partes)[:400]


def precio_texto(valor) -> str:
    """Un precio como lo quiere Tiendanube: texto con dos decimales."""
    return f"{float(valor or 0):.2f}"


class TiendanubeClient:
    def __init__(self, token_provider: Callable[[], str],
                 store_provider: Callable[[], str],
                 user_agent: str = "AmazonToMeli",
                 base_url: str = API_BASE,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self._token = token_provider
        self._store = store_provider
        self.user_agent = user_agent
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    # ---- helpers ---------------------------------------------------------

    def _headers(self) -> dict:
        # `Authentication`, no `Authorization`: no es un typo.
        return {"Authentication": f"bearer {self._token()}",
                "User-Agent": self.user_agent,
                "Content-Type": "application/json"}

    def _req(self, metodo: str, path: str, _intento: int = 0, **kw):
        """Una llamada a la API, con espera y reintento si limita el ritmo."""
        url = f"{self.base_url}/{self._store()}{path}"
        resp = self.session.request(metodo, url, headers=self._headers(),
                                    timeout=self.timeout, **kw)
        if resp.status_code == 429 and _intento < len(ESPERAS_429):
            espera = ESPERAS_429[_intento]
            cabeceras = getattr(resp, "headers", {}) or {}
            # Tiendanube dice en milisegundos cuánto falta para que se libere
            # el bucket; su número manda sobre el nuestro.
            for clave, divisor in (("Retry-After", 1.0),
                                   ("X-Rate-Limit-Reset", 1000.0)):
                try:
                    valor = float(cabeceras.get(clave, 0)) / divisor
                except (TypeError, ValueError):
                    continue
                if valor > 0:
                    espera = max(espera, valor)
            time.sleep(min(espera, 30.0))
            return self._req(metodo, path, _intento=_intento + 1, **kw)
        if resp.status_code >= 400:
            try:
                cuerpo = resp.json()
            except ValueError:
                cuerpo = resp.text
            raise TiendanubeAPIError(
                f"Tiendanube {metodo} {path} → {resp.status_code}",
                status=resp.status_code, cuerpo=cuerpo,
            )
        if resp.status_code == 204 or not (resp.text or "").strip():
            return {}
        return resp.json()

    # ---- productos -------------------------------------------------------

    def probar(self) -> dict:
        """Una llamada mínima para confirmar que el token y la tienda andan.

        Existe porque los tres errores típicos —header mal nombrado, falta de
        User-Agent, id de tienda equivocado— dan todos el mismo 401 opaco desde
        cualquier otro endpoint.
        """
        datos = self._req("GET", "/products", params={"per_page": 1})
        return {"ok": True, "store_id": self._store(),
                "productos_visibles": len(datos) if isinstance(datos, list) else 0}

    def listar_productos(self, pagina: int = 1, por_pagina: int = 50) -> list:
        datos = self._req("GET", "/products",
                          params={"page": pagina, "per_page": por_pagina})
        return datos if isinstance(datos, list) else []

    def obtener_producto(self, product_id) -> dict:
        return self._req("GET", f"/products/{product_id}")

    def crear_producto(self, payload: dict) -> dict:
        return self._req("POST", "/products", json=payload)

    def actualizar_producto(self, product_id, payload: dict) -> dict:
        return self._req("PUT", f"/products/{product_id}", json=payload)

    def actualizar_variante(self, product_id, variant_id,
                            precio=None, stock=None) -> dict:
        """Precio y stock viven en la variante, no en el producto."""
        cuerpo: dict = {}
        if precio is not None:
            cuerpo["price"] = precio_texto(precio)
        if stock is not None:
            cuerpo["stock"] = int(stock)
        if not cuerpo:
            return {}
        return self._req("PUT", f"/products/{product_id}/variants/{variant_id}",
                         json=cuerpo)

    def publicar(self, product_id, publicado: bool) -> dict:
        """Saca o pone a la venta. Tiendanube no tiene «pausado»: es published
        sí o no, y despublicar conserva el producto y sus datos."""
        return self._req("PUT", f"/products/{product_id}",
                         json={"published": bool(publicado)})

    def borrar_producto(self, product_id) -> dict:
        return self._req("DELETE", f"/products/{product_id}")
