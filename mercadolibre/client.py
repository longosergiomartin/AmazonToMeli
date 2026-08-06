"""
Cliente de la API de MercadoLibre para publicar y gestionar ítems.

El cliente recibe un `token_provider` (una función que devuelve un access_token
válido, típicamente `MeliOAuth.access_token_valido`) y una `base_url`. Así se
puede testear inyectando un provider y una sesión falsos, sin tocar la red.

Endpoints usados (API pública de MercadoLibre):
  - GET  /sites/MLA/domain_discovery/search   → predecir categoría por título
  - GET  /categories/{id}/attributes          → atributos (obligatorios) de la cat.
  - POST /items                               → crear/publicar ítem
  - PUT  /items/{id}                          → actualizar precio, stock, estado
"""

from __future__ import annotations

from typing import Callable, Optional

import requests

API_BASE = "https://api.mercadolibre.com"


class MeliAPIError(RuntimeError):
    def __init__(self, mensaje: str, status: Optional[int] = None, cuerpo=None):
        super().__init__(mensaje)
        self.status = status
        self.cuerpo = cuerpo


class MeliClient:
    def __init__(self, token_provider: Callable[[], str],
                 site: str = "MLA", base_url: str = API_BASE,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self._token = token_provider
        self.site = site
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    # ---- helpers ---------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json"}

    def _req(self, metodo: str, path: str, **kw):
        url = f"{self.base_url}{path}"
        resp = self.session.request(metodo, url, headers=self._headers(),
                                    timeout=self.timeout, **kw)
        if resp.status_code >= 400:
            try:
                cuerpo = resp.json()
            except ValueError:
                cuerpo = resp.text
            raise MeliAPIError(
                f"MercadoLibre {metodo} {path} → {resp.status_code}",
                status=resp.status_code, cuerpo=cuerpo,
            )
        return resp.json()

    # ---- categorías y atributos -----------------------------------------

    def predecir_categoria(self, titulo: str) -> list[dict]:
        """Sugiere categorías para un título (domain discovery)."""
        data = self._req("GET", f"/sites/{self.site}/domain_discovery/search",
                         params={"q": titulo, "limit": 5})
        return data if isinstance(data, list) else []

    def atributos(self, category_id: str) -> list[dict]:
        return self._req("GET", f"/categories/{category_id}/attributes")

    def atributos_obligatorios(self, category_id: str) -> list[dict]:
        """Atributos que MercadoLibre exige para publicar en la categoría:
        required, catalog_required y conditional_required (ej: GTIN / código de
        barras, que ML pide como condicional en muchas categorías)."""
        req = []
        for a in self.atributos(category_id):
            tags = a.get("tags", {}) or {}
            if (tags.get("required") or tags.get("catalog_required")
                    or tags.get("conditional_required")):
                req.append({
                    "id": a.get("id"), "name": a.get("name"),
                    "value_type": a.get("value_type"),
                    "values": [v.get("name") for v in (a.get("values") or [])][:20],
                })
        return req

    # ---- publicación y gestión ------------------------------------------

    def publicar(self, item: dict) -> dict:
        """Crea el ítem en MercadoLibre (POST /items). Devuelve el ítem creado
        (incluye su `id`). Solo se llama tras la aprobación del usuario."""
        return self._req("POST", "/items", json=item)

    def actualizar(self, item_id: str, cambios: dict) -> dict:
        """PUT /items/{id}: sirve para precio, stock y estado (status)."""
        return self._req("PUT", f"/items/{item_id}", json=cambios)

    def actualizar_precio(self, item_id: str, precio: float) -> dict:
        return self.actualizar(item_id, {"price": precio})

    def actualizar_stock(self, item_id: str, cantidad: int) -> dict:
        return self.actualizar(item_id, {"available_quantity": cantidad})

    def pausar(self, item_id: str) -> dict:
        return self.actualizar(item_id, {"status": "paused"})

    def reactivar(self, item_id: str) -> dict:
        return self.actualizar(item_id, {"status": "active"})

    def obtener(self, item_id: str) -> dict:
        return self._req("GET", f"/items/{item_id}")
