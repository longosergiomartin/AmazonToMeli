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


def describir_error(cuerpo) -> str:
    """Traduce el cuerpo de error de MercadoLibre a algo legible.

    ML devuelve una lista `cause` que mezcla lo que realmente bloquea la
    publicación (`type: error`) con advertencias que no la impiden (envío
    gratis agregado, atributos de catálogo sugeridos). Mostrar el dict crudo
    hace imposible saber qué hay que corregir, así que se separan.
    """
    if not isinstance(cuerpo, dict):
        return str(cuerpo)
    causas = cuerpo.get("cause")
    if not isinstance(causas, list) or not causas:
        return str(cuerpo.get("error") or cuerpo.get("message") or cuerpo)

    def _linea(c) -> str:
        if not isinstance(c, dict):
            return str(c)
        refs = [r for r in (c.get("references") or []) if r]
        detalle = f" ({', '.join(refs)})" if refs else ""
        return f"{c.get('message') or c.get('code') or ''}{detalle}".strip()

    errores = [_linea(c) for c in causas
               if isinstance(c, dict) and c.get("type") == "error"]
    avisos = [_linea(c) for c in causas
              if isinstance(c, dict) and c.get("type") != "error"]
    partes = []
    if errores:
        partes.append(" · ".join(errores))
    else:
        partes.append(str(cuerpo.get("error") or cuerpo.get("message") or "error"))
    if avisos:
        partes.append("Advertencias (no bloquean): " + " · ".join(avisos))
    return "\n".join(partes)


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

    def valores_permitidos(self, category_id: str) -> dict[str, list[dict]]:
        """{attr_id: [{"id", "name"}]} con los valores que MercadoLibre acepta
        en la categoría. Sirve para mandar `value_id` en vez de texto libre:
        es la forma que ML valida sin objetar (evita el clásico "Attribute
        BRAND has an invalid value name")."""
        out: dict[str, list[dict]] = {}
        for a in self.atributos(category_id):
            aid = a.get("id")
            vals = [{"id": v.get("id"), "name": v.get("name")}
                    for v in (a.get("values") or [])
                    if v.get("id") and v.get("name")]
            if aid and vals:
                out[aid] = vals
        return out

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

    # ---- competencia: precios de publicaciones existentes ----------------

    @staticmethod
    def _item_desde_oferta(r: dict) -> Optional[dict]:
        precio = r.get("price")
        if precio is None:
            return None
        envio = (r.get("shipping") or {}).get("free_shipping", False)
        return {
            "titulo": r.get("title") or r.get("name") or "",
            "precio": float(precio),
            "link": r.get("permalink") or "",
            "envio_gratis": bool(envio),
            "vendidos": r.get("sold_quantity") or 0,
        }

    def buscar_productos_catalogo(self, query: str, limit: int = 5) -> list[dict]:
        """Busca productos del CATÁLOGO de MercadoLibre (no publicaciones).
        Devuelve [{id, nombre}]. Endpoint habilitado para apps normales."""
        data = self._req("GET", "/products/search",
                         params={"site_id": self.site, "q": query,
                                 "status": "active", "limit": limit})
        out = []
        for r in (data.get("results") or []):
            pid = r.get("id")
            if pid:
                out.append({"id": pid, "nombre": r.get("name", "")})
        return out

    def producto_catalogo(self, product_id: str) -> dict:
        """Ficha de un producto del catálogo, con sus atributos."""
        return self._req("GET", f"/products/{product_id}")

    def ficha_de_catalogo(self, query: str, debe_contener: str = "",
                          limit: int = 5, parecido_a: str = "",
                          minimo_parecido: float = 0.5) -> dict:
        """Busca el producto en el catálogo de MercadoLibre y devuelve su ficha.

        Es la fuente más confiable que tenemos: los sets ya están cargados en el
        catálogo de ML, con su GTIN y sus atributos. Devuelve
        {product_id, nombre, gtin} o {} si no hay match seguro.

        Hay dos guardas contra quedarse con otro producto, y se usa una u otra:

          - `debe_contener` (el número de set, "75339"): si el nombre del
            candidato no lo trae, se descarta. Es la más precisa.
          - `parecido_a` (el título del producto): cuando no hay número, se
            compara el nombre y se exige un mínimo de coincidencia.
        """
        from titulos import parecido

        clave = (debe_contener or "").strip().lower()
        for prod in self.buscar_productos_catalogo(query, limit=limit):
            nombre = prod.get("nombre") or ""
            if clave and clave not in nombre.lower():
                continue
            if parecido_a and parecido(parecido_a, nombre) < minimo_parecido:
                continue
            gtin = ""
            try:
                ficha = self.producto_catalogo(prod["id"])
            except MeliAPIError:
                ficha = {}
            for a in (ficha.get("attributes") or []):
                if (a.get("id") or "").upper() == "GTIN":
                    gtin = (a.get("value_name") or "").strip()
                    break
            return {"product_id": prod["id"], "nombre": nombre, "gtin": gtin}
        return {}

    def gtin_de_catalogo(self, query: str, debe_contener: str = "",
                         limit: int = 5) -> dict:
        """Solo el código de barras: {gtin, product_id, nombre} o {}."""
        ficha = self.ficha_de_catalogo(query, debe_contener, limit)
        return ficha if ficha.get("gtin") else {}

    def ofertas_de_producto(self, product_id: str, limit: int = 10) -> list[dict]:
        """Publicaciones (ofertas) que compiten por un producto del catálogo:
        es exactamente la competencia de precio de ese producto."""
        data = self._req("GET", f"/products/{product_id}/items",
                         params={"limit": limit})
        items = []
        for r in (data.get("results") or []):
            it = self._item_desde_oferta(r)
            if it:
                items.append(it)
        return items

    def buscar_listados(self, query: str, limit: int = 10) -> dict:
        """Busca publicaciones existentes para comparar precios, probando en
        orden las vías que MercadoLibre habilita:

          1. Catálogo: producto del catálogo + sus ofertas (la más precisa;
             es la competencia real por el mismo producto).
          2. Búsqueda de sitio (/sites/{site}/search): MercadoLibre la
             restringió (403) para la mayoría de las apps, queda como intento.

        Devuelve {"items": [...], "via": "catalogo"|"busqueda", "producto": str}.
        Lanza MeliAPIError solo si ninguna vía funcionó.
        """
        errores = []
        # 1) Catálogo
        try:
            productos = self.buscar_productos_catalogo(query, limit=3)
            for prod in productos:
                try:
                    items = self.ofertas_de_producto(prod["id"], limit=limit)
                except MeliAPIError as e:
                    errores.append(str(e))
                    continue
                if items:
                    return {"items": items, "via": "catalogo",
                            "producto": prod.get("nombre", "")}
        except MeliAPIError as e:
            errores.append(str(e))

        # 2) Búsqueda clásica del sitio (suele dar 403 hoy)
        try:
            data = self._req("GET", f"/sites/{self.site}/search",
                             params={"q": query, "limit": limit})
            items = [i for i in (self._item_desde_oferta(r)
                                 for r in (data.get("results") or [])) if i]
            if items:
                return {"items": items, "via": "busqueda", "producto": ""}
        except MeliAPIError as e:
            errores.append(str(e))

        raise MeliAPIError(
            "MercadoLibre no permitió buscar publicaciones "
            f"({'; '.join(errores[:2]) or 'sin resultados'})")

    def poner_descripcion(self, item_id: str, texto: str) -> dict:
        """Setea la descripción del ítem (endpoint aparte de la creación)."""
        return self._req("POST", f"/items/{item_id}/description",
                         json={"plain_text": texto})

    def obtener(self, item_id: str) -> dict:
        return self._req("GET", f"/items/{item_id}")
