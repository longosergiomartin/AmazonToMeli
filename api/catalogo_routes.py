"""
Rutas de la API para el catálogo, OAuth de MercadoLibre y publicación.

Se registran sobre la app FastAPI existente. Toda operación que toca la cuenta
de MercadoLibre (predecir categoría, publicar, actualizar, pausar) requiere una
sesión OAuth activa; sin ella, se responde con un error claro en vez de fallar.

Regla de seguridad: `publicar` exige que el producto esté en estado "aprobado"
y que no falte ningún dato obligatorio. Nunca se publica en un solo paso.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

from arbitraje.config import Config, CONFIG_DEFAULT
from arbitraje.cotizacion import obtener_cotizaciones, invalidar_cache
from amazon_import import importar_desde_url
from catalogo import Catalogo, ProductoCatalogo
from mercadolibre.oauth import MeliOAuth, MeliCredenciales, TokenStore
from mercadolibre.client import MeliClient, MeliAPIError
from mercadolibre.listing import construir_item, vista_previa, faltantes_para_publicar


class AltaProducto(BaseModel):
    amazon_link: str = ""
    asin: str = ""
    marca: str = ""
    modelo: str = ""
    precio_usd: float = 0.0
    peso_kg: float = 0.5
    costo_envio_usd: float = 0.0
    disponibilidad: str = "in_stock"
    regimen: str = "courier"
    categoria: str = "default"
    margen_deseado: float = 0.35
    stock: int = 1
    dias_preparacion: int = 25
    titulo_ml: str = ""
    descripcion: str = ""
    pictures: list[str] = []
    ml_category_id: str = ""


class Precio(BaseModel):
    precio: float


class Stock(BaseModel):
    stock: int


class Borrador(BaseModel):
    pictures: list[str] = []
    listing_type_id: str = "gold_special"


class CodigoOAuth(BaseModel):
    # Se puede pegar el `code` suelto o la URL completa del callback.
    code: str = ""
    url: str = ""


class Publicacion(BaseModel):
    titulo_ml: Optional[str] = None
    ml_category_id: Optional[str] = None
    ml_attributes: Optional[dict] = None
    pictures: Optional[list[str]] = None
    dias_preparacion: Optional[int] = None
    descripcion: Optional[str] = None


def registrar_catalogo(app: FastAPI, conn: sqlite3.Connection,
                       cfg: Config = CONFIG_DEFAULT) -> None:
    cat = Catalogo(conn, cfg=cfg, cotizacion=obtener_cotizaciones(cfg))
    cred = MeliCredenciales.desde_entorno()
    store = TokenStore(conn)
    oauth = MeliOAuth(cred, store)

    def _cotizacion(refrescar: bool = False) -> dict:
        if refrescar:
            invalidar_cache()
        cat.cotizacion = obtener_cotizaciones(cfg)
        return cat.cotizacion

    def _client() -> MeliClient:
        if not cred.configurado:
            raise HTTPException(400, "Faltan credenciales de MercadoLibre "
                                "(MELI_CLIENT_ID / MELI_CLIENT_SECRET).")
        if not store.hay_sesion():
            raise HTTPException(401, "No hay sesión de MercadoLibre. Entrá a "
                                "/oauth/login para autorizar.")
        return MeliClient(token_provider=oauth.access_token_valido, site=cfg.meli.site)

    def _p(pid: int) -> ProductoCatalogo:
        p = cat.obtener(pid)
        if not p:
            raise HTTPException(404, f"Producto {pid} no encontrado")
        return p

    def _dict(p: ProductoCatalogo) -> dict:
        d = p.__dict__.copy()
        d["margen_insuficiente"] = cat.margen_insuficiente(p)
        d["comparacion"] = cat.comparacion_dolar(p)
        return d

    # ---- OAuth -----------------------------------------------------------

    @app.get("/oauth/status")
    def oauth_status():
        return {"configurado": cred.configurado, "conectado": store.hay_sesion(),
                "redirect_uri": cred.redirect_uri}

    @app.get("/oauth/login")
    def oauth_login():
        if not cred.configurado:
            raise HTTPException(400, "Configurá MELI_CLIENT_ID y MELI_CLIENT_SECRET "
                                "antes de conectar.")
        return RedirectResponse(oauth.url_autorizacion())

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(code: Optional[str] = None, error: Optional[str] = None):
        if error or not code:
            return HTMLResponse(f"<h1>No se pudo conectar</h1><p>{error or 'sin code'}</p>",
                                status_code=400)
        oauth.intercambiar_codigo(code)
        return HTMLResponse("<h1>✅ MercadoLibre conectado</h1>"
                            "<p>Ya podés cerrar esta pestaña y volver al panel.</p>")

    @app.post("/oauth/code")
    def oauth_code(body: CodigoOAuth):
        """Alta de sesión pegando el `code` (o la URL del callback) a mano.
        Útil en local, donde el redirect HTTPS no llega al servidor: el usuario
        copia el code de la barra de direcciones y lo pega acá."""
        from urllib.parse import urlparse, parse_qs
        code = body.code.strip()
        if not code and body.url:
            qs = parse_qs(urlparse(body.url.strip()).query)
            code = (qs.get("code") or [""])[0]
        if not code:
            raise HTTPException(400, "Pegá el 'code' o la URL completa del callback.")
        if not cred.configurado:
            raise HTTPException(400, "Faltan credenciales de MercadoLibre.")
        try:
            oauth.intercambiar_codigo(code)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        return {"conectado": True}

    @app.post("/oauth/logout")
    def oauth_logout():
        store.borrar()
        return {"conectado": False}

    # ---- cotización del dólar --------------------------------------------

    @app.get("/api/cotizacion")
    def cotizacion():
        return _cotizacion()

    @app.post("/api/cotizacion/refrescar")
    def cotizacion_refrescar():
        return _cotizacion(refrescar=True)

    # ---- importar datos desde un link de Amazon --------------------------

    @app.post("/api/amazon/importar")
    def amazon_importar(body: dict):
        url = (body or {}).get("url", "")
        return importar_desde_url(url)

    # ---- búsqueda automática del GTIN por ASIN ---------------------------

    @app.post("/api/gtin")
    def gtin(body: dict):
        from gtin_lookup import buscar_gtin
        return buscar_gtin((body or {}).get("asin", ""))

    # ---- catálogo --------------------------------------------------------

    @app.get("/api/catalogo")
    def listar():
        return [_dict(p) for p in cat.todos()]

    @app.post("/api/catalogo")
    def alta(body: AltaProducto):
        p = cat.agregar(ProductoCatalogo(**body.model_dump()))
        return _dict(p)

    @app.get("/api/catalogo/{pid}")
    def obtener(pid: int):
        return _dict(_p(pid))

    @app.post("/api/catalogo/{pid}/recalcular")
    def recalcular(pid: int):
        _p(pid)
        return _dict(cat.recalcular(pid))

    @app.patch("/api/catalogo/{pid}/precio")
    def precio(pid: int, body: Precio):
        _p(pid)
        p = cat.actualizar_precio(pid, body.precio)
        # Si está publicado y hay sesión, reflejar el precio en MercadoLibre.
        if p.estado == "publicado" and p.ml_item_id and store.hay_sesion():
            try:
                _client().actualizar_precio(p.ml_item_id, body.precio)
            except MeliAPIError as e:
                raise HTTPException(502, f"No se pudo actualizar en MercadoLibre: {e}")
        return _dict(p)

    @app.patch("/api/catalogo/{pid}/stock")
    def stock(pid: int, body: Stock):
        _p(pid)
        p = cat.actualizar_stock(pid, body.stock)
        if p.estado == "publicado" and p.ml_item_id and store.hay_sesion():
            try:
                _client().actualizar_stock(p.ml_item_id, body.stock)
            except MeliAPIError as e:
                raise HTTPException(502, f"No se pudo actualizar en MercadoLibre: {e}")
        return _dict(p)

    @app.get("/api/catalogo/{pid}/historial")
    def historial(pid: int):
        _p(pid)
        return cat.historial(pid)

    # ---- competencia: precios ya publicados del mismo producto -----------

    @app.get("/api/catalogo/{pid}/competencia")
    def competencia(pid: int, q: Optional[str] = None):
        """Busca publicaciones existentes en MercadoLibre del mismo producto y
        compara sus precios con el tuyo, para entrar con precio competitivo."""
        p = _p(pid)
        consulta = (q or p.titulo_ml or p.modelo or p.asin or "").strip()
        if not consulta:
            raise HTTPException(400, "El producto no tiene título/modelo para buscar.")
        from urllib.parse import quote_plus
        link_manual = ("https://listado.mercadolibre.com.ar/"
                       + quote_plus(consulta).replace("+", "-"))
        cli = _client()  # requiere sesión OAuth
        try:
            res = cli.buscar_listados(consulta, limit=10)
        except MeliAPIError as e:
            # MercadoLibre restringió la búsqueda pública: devolvemos el link
            # para mirarla a mano en vez de romper la pantalla.
            return {"consulta": consulta, "items": [], "stats": {},
                    "mi_precio": p.precio_publicado_ars or p.precio_sugerido_ars or 0,
                    "veredicto": "", "via": "", "producto": "",
                    "link_manual": link_manual,
                    "error": f"MercadoLibre no permitió la búsqueda automática ({e}). "
                             "Abrí el link para comparar a mano."}
        items = res["items"]
        precios = sorted(i["precio"] for i in items)
        stats = {}
        if precios:
            stats = {
                "cantidad": len(precios),
                "minimo": precios[0],
                "mediana": precios[len(precios) // 2],
                "maximo": precios[-1],
            }
        mi_precio = p.precio_publicado_ars or p.precio_sugerido_ars or 0
        veredicto = ""
        if precios and mi_precio:
            if mi_precio <= precios[0]:
                veredicto = "Sos el más barato: entrás competitivo."
            elif mi_precio <= stats["mediana"]:
                veredicto = "Estás por debajo de la mediana: razonable."
            else:
                veredicto = "Estás por encima de la mediana: te va a costar competir."
        return {"consulta": consulta, "items": items, "stats": stats,
                "mi_precio": mi_precio, "veredicto": veredicto,
                "via": res.get("via", ""), "producto": res.get("producto", ""),
                "link_manual": link_manual, "error": ""}

    # ---- desglose de margen a un precio dado -----------------------------

    @app.get("/api/catalogo/{pid}/desglose")
    def desglose(pid: int, precio: float):
        """Desglose completo del margen a un precio dado: comisión, costo fijo,
        envío, retenciones (recuperables) y neto en las dos variantes — la
        conservadora y la estilo 'Recibís' del simulador de MercadoLibre."""
        from arbitraje.meli import calcular_neto_venta_meli
        p = _p(pid)
        cfg_ef = cat._cfg_efectivo()
        venta = calcular_neto_venta_meli(precio, p.categoria, cfg_ef)
        d = venta.detalle_ars
        retenciones = d["iibb"] + d["ganancias"]
        neto_estilo_ml = venta.neto_ars + retenciones  # ML no descuenta retenciones en "Recibís"
        costo = p.costo_total_ars
        def _m(neto):
            m = neto - costo
            return {"margen_ars": round(m, 2),
                    "margen_pct": round(m / costo * 100, 1) if costo else 0.0}
        return {
            "precio": precio,
            "costo_puesto_ars": costo,
            "detalle": {
                "comision": d["comision"],
                "costo_fijo": d["costo_fijo"],
                "iva_sobre_comision": d["iva_sobre_comision"],
                "envio": d["envio_estimado"],
                "retenciones_iibb_ganancias": round(retenciones, 2),
            },
            "neto_conservador": venta.neto_ars,
            "conservador": _m(venta.neto_ars),
            "neto_estilo_ml": round(neto_estilo_ml, 2),
            "estilo_ml": _m(neto_estilo_ml),
            "comparacion_dolar": cat.comparacion_dolar(p),
        }

    @app.delete("/api/catalogo/{pid}")
    def eliminar(pid: int):
        _p(pid)
        cat.eliminar(pid)
        return {"eliminado": pid}

    @app.patch("/api/catalogo/{pid}/regimen")
    def regimen(pid: int, body: dict):
        _p(pid)
        reg = (body or {}).get("regimen", "")
        if reg not in ("landed", "courier", "general"):
            raise HTTPException(400, "Régimen inválido (landed/courier/general).")
        return _dict(cat.cambiar_regimen(pid, reg))

    # ---- borrador / vista previa / aprobación / publicación --------------

    @app.post("/api/catalogo/{pid}/borrador")
    def borrador(pid: int, body: Borrador):
        p = _p(pid)
        cat.cambiar_estado(pid, "borrador", "Generó borrador")
        pics = body.pictures or p.pictures
        sugeridas, obligatorios = [], []
        if store.hay_sesion() and cred.configurado:
            try:
                cli = _client()
                sugeridas = cli.predecir_categoria(p.titulo_ml or p.modelo or p.asin)
                catid = p.ml_category_id or (sugeridas[0].get("category_id") if sugeridas else "")
                if catid:
                    obligatorios = cli.atributos_obligatorios(catid)
            except (MeliAPIError, HTTPException):
                pass  # sin conexión seguimos con carga manual de categoría
        preview = vista_previa(p, pictures=pics)
        faltan = faltantes_para_publicar(p, obligatorios, pics)
        return {"preview": preview, "categorias_sugeridas": sugeridas,
                "atributos_obligatorios": obligatorios, "faltantes": faltan}

    @app.patch("/api/catalogo/{pid}/publicacion")
    def editar_publicacion(pid: int, body: Publicacion):
        _p(pid)
        datos = {k: v for k, v in body.model_dump().items() if v is not None}
        return _dict(cat.actualizar_publicacion(pid, **datos))

    @app.post("/api/catalogo/{pid}/aprobar")
    def aprobar(pid: int):
        _p(pid)
        return _dict(cat.cambiar_estado(pid, "aprobado", "Aprobado para publicar"))

    @app.post("/api/catalogo/{pid}/publicar")
    def publicar(pid: int, body: Borrador):
        p = _p(pid)
        if p.estado != "aprobado":
            raise HTTPException(409, "El producto debe estar APROBADO antes de "
                                "publicar. Revisá la vista previa y aprobalo.")
        pics = body.pictures or p.pictures
        # 1) Datos básicos (título/categoría/precio/foto): no requieren ML.
        faltan = faltantes_para_publicar(p, None, pics)
        if faltan:
            raise HTTPException(422, {"faltantes": faltan})
        cli = _client()  # exige sesión OAuth
        # 2) Atributos obligatorios reales de la categoría (GTIN, cantidad de
        #    piezas, etc.) para no mandar algo que ML va a rechazar.
        obligatorios = []
        try:
            obligatorios = cli.atributos_obligatorios(p.ml_category_id)
        except MeliAPIError:
            pass
        faltan = faltantes_para_publicar(p, obligatorios, pics)
        if faltan:
            raise HTTPException(422, {"faltantes": faltan})
        item = construir_item(p, pictures=pics,
                              listing_type_id=body.listing_type_id)
        try:
            creado = cli.publicar(item)
        except MeliAPIError as e:
            raise HTTPException(502, f"MercadoLibre rechazó la publicación: {e.cuerpo}")
        item_id = creado.get("id", "")
        # La descripción va en un endpoint aparte, después de crear el ítem.
        if item_id and (p.descripcion or "").strip():
            try:
                cli.poner_descripcion(item_id, p.descripcion)
            except MeliAPIError:
                pass  # el ítem ya se publicó; la descripción se puede reintentar
        p = cat.registrar_publicacion(pid, item_id, creado.get("permalink", ""))
        return _dict(p)

    @app.post("/api/catalogo/{pid}/pausar")
    def pausar(pid: int):
        p = _p(pid)
        if p.estado == "publicado" and p.ml_item_id:
            try:
                _client().pausar(p.ml_item_id)
            except MeliAPIError as e:
                raise HTTPException(502, f"No se pudo pausar en MercadoLibre: {e}")
        return _dict(cat.cambiar_estado(pid, "pausado", "Publicación pausada"))

    @app.post("/api/catalogo/{pid}/reactivar")
    def reactivar(pid: int):
        p = _p(pid)
        if p.ml_item_id and store.hay_sesion():
            try:
                _client().reactivar(p.ml_item_id)
            except MeliAPIError as e:
                raise HTTPException(502, f"No se pudo reactivar en MercadoLibre: {e}")
        return _dict(cat.cambiar_estado(pid, "publicado", "Publicación reactivada"))
