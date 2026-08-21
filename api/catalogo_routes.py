"""
Rutas de la API para el catálogo, OAuth de MercadoLibre y publicación.

Se registran sobre la app FastAPI existente. Toda operación que toca la cuenta
de MercadoLibre (predecir categoría, publicar, actualizar, pausar) requiere una
sesión OAuth activa; sin ella, se responde con un error claro en vez de fallar.

Regla de seguridad: `publicar` exige que el producto esté en estado "aprobado"
y que no falte ningún dato obligatorio. Nunca se publica en un solo paso.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

from arbitraje.config import Config, CONFIG_DEFAULT, CONDICIONES_FISCALES
from arbitraje.cotizacion import obtener_cotizaciones, invalidar_cache
from amazon_import import importar_desde_url
from catalogo import Catalogo, ProductoCatalogo, DOLARES_COSTO
from importador import ColaImportacion
from mercadolibre.oauth import MeliOAuth, MeliCredenciales, TokenStore
from mercadolibre.client import MeliClient, MeliAPIError, describir_error
from mercadolibre.listing import (construir_item, construir_item_catalogo,
                                  vista_previa, faltantes_para_publicar,
                                  valor_por_defecto)
from marcas import elegir_marca
from titulos import numero_de_set, piezas_del_titulo


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
    modelo_fabricante: str = ""
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
    marca: Optional[str] = None
    modelo: Optional[str] = None
    modelo_fabricante: Optional[str] = None
    ml_category_id: Optional[str] = None
    ml_attributes: Optional[dict] = None
    pictures: Optional[list[str]] = None
    dias_preparacion: Optional[int] = None
    descripcion: Optional[str] = None


def registrar_catalogo(app: FastAPI, conn,
                       cfg: Config = CONFIG_DEFAULT) -> None:
    # La cotización se busca en el primer uso, no al arrancar: si la API del
    # dólar tarda o no responde, el arranque no se puede quedar esperándola.
    cat = Catalogo(conn, cfg=cfg,
                   proveedor_cotizacion=lambda: obtener_cotizaciones(cfg))
    cola = ColaImportacion(conn, cat)
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

    # ---- condición fiscal del vendedor -----------------------------------

    @app.get("/api/fiscal")
    def fiscal():
        c = cat._cfg_efectivo().meli
        return {"condicion_fiscal": cat.condicion_fiscal,
                "opciones": list(CONDICIONES_FISCALES),
                "iva_pct": round(c.iva_pct * 100, 1),
                "ganancias_pct": round(c.ganancias_pct * 100, 1),
                "iibb_pct": round(c.iibb_pct * 100, 1)}

    @app.patch("/api/fiscal")
    def fiscal_set(body: dict):
        valor = (body or {}).get("condicion_fiscal", "")
        try:
            cat.condicion_fiscal = valor
        except ValueError as e:
            raise HTTPException(400, str(e))
        cat.recalcular_todos()  # los márgenes cambian con las alícuotas
        return fiscal()

    # ---- qué productos entran al catálogo ---------------------------------

    @app.get("/api/filtro")
    def filtro():
        return cat.filtro

    @app.patch("/api/filtro")
    def filtro_set(body: dict):
        try:
            cat.filtro = body or {}
        except ValueError as e:
            raise HTTPException(400, str(e))
        return cat.filtro

    # ---- dólar con el que se valúa la compra en Amazon --------------------

    @app.get("/api/almacenamiento")
    def almacenamiento():
        """Dónde se guardan los datos: sirve para saber si la sesión de
        MercadoLibre y el catálogo van a sobrevivir a un reinicio."""
        from db import describir
        persistente = getattr(conn, "postgres", False)
        return {"persistente": bool(persistente), "detalle": describir(conn)}

    @app.get("/api/dolar-costo")
    def dolar_costo():
        cfg_ef = cat._cfg_efectivo()
        return {"dolar_costo": cat.dolar_costo,
                "opciones": list(DOLARES_COSTO),
                "tc_usado": round(cfg_ef.tc_compra(), 2)}

    @app.patch("/api/dolar-costo")
    def dolar_costo_set(body: dict):
        try:
            cat.dolar_costo = (body or {}).get("dolar_costo", "")
        except ValueError as e:
            raise HTTPException(400, str(e))
        cat.recalcular_todos()  # el costo puesto cambia con el tipo de cambio
        return dolar_costo()

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

    # ---- importación por lote --------------------------------------------

    @app.get("/api/importar/estado")
    def importar_estado():
        return {**cola.estado(), "items": cola.items()}

    @app.post("/api/importar/encolar")
    def importar_encolar(body: dict):
        """Recibe links de Amazon o ASIN (uno por línea) y los encola."""
        crudo = (body or {}).get("entradas", "")
        entradas = crudo if isinstance(crudo, list) else str(crudo).splitlines()
        return cola.encolar(entradas)

    @app.post("/api/importar/procesar")
    def importar_procesar(body: dict):
        """Procesa unos pocos por llamada: el navegador vuelve a llamar para
        seguir. Así el avance se ve en vivo y no hay peticiones eternas."""
        maximo = int((body or {}).get("maximo", 3))
        pausa = float((body or {}).get("pausa_seg", 2.0))
        return cola.procesar_lote(maximo=min(maximo, 10), pausa_seg=pausa)

    @app.post("/api/importar/reactivar")
    def importar_reactivar():
        """Retoma lo que quedó frenado por un bloqueo (continuar otro día)."""
        return cola.reactivar_bloqueados()

    @app.post("/api/importar/limpiar")
    def importar_limpiar():
        return cola.limpiar_terminados()

    @app.get("/importar/capturar", response_class=HTMLResponse)
    def importar_capturar(asins: str = ""):
        """Destino del bookmarklet que captura los ASIN de una página de
        resultados de Amazon que el usuario ya tiene abierta."""
        r = cola.encolar([a.strip() for a in asins.split(",") if a.strip()])
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<div style=\"font-family:system-ui;text-align:center;margin-top:70px\">"
            f"<h1>✅ {r['nuevos']} producto(s) encolado(s)</h1>"
            f"<p>{r['duplicados']} ya estaban · {r['pendientes']} pendientes en total.</p>"
            "<p>Volvé al panel y tocá <b>Procesar cola</b>.</p></div>")

    # ---- conversor ASIN ⇄ código de barras --------------------------------

    @app.post("/api/codigos")
    def codigos(body: dict):
        """Convierte ASIN ⇄ GTIN (EAN/UPC/ISBN), de a varios.

        Sin límite diario: primero mira el catálogo propio, después el de
        MercadoLibre y recién al final sale a Amazon.
        """
        from codigos import convertir_lote
        crudo = (body or {}).get("entradas", "")
        entradas = crudo if isinstance(crudo, list) else str(crudo).splitlines()
        cli = None
        if store.hay_sesion() and cred.configurado:
            try:
                cli = _client()
            except HTTPException:
                pass
        return convertir_lote(entradas, catalogo=cat, cliente_ml=cli,
                              maximo=int((body or {}).get("maximo", 25)))

    # ---- búsqueda automática del GTIN por ASIN ---------------------------

    @app.post("/api/gtin")
    def gtin(body: dict):
        from gtin_lookup import buscar_gtin
        return buscar_gtin((body or {}).get("asin", ""))

    # ---- catálogo --------------------------------------------------------

    @app.post("/api/catalogo/vaciar")
    def vaciar(body: dict):
        """Vacía el catálogo y la cola para empezar de cero.

        Se limpian las dos cosas juntas a propósito: `encolar` descarta los ASIN
        que ya figuran en la cola aunque estén procesados, así que borrar solo
        los productos haría que al reencolarlos rebotaran como duplicados.
        """
        if not (body or {}).get("confirmar"):
            raise HTTPException(400, "Falta confirmar: esta acción borra el catálogo.")
        r = cat.vaciar(incluir_publicados=bool((body or {}).get("incluir_publicados")))
        return {**r, "cola": cola.vaciar()}

    @app.post("/api/catalogo/limpiar-marcas")
    def limpiar_marcas():
        """Arregla las marcas guardadas con el texto del byline de Amazon
        ("Visit the LEGO Store" → "LEGO"), que MercadoLibre rechaza."""
        return {"corregidos": cat.limpiar_marcas()}

    # Se corre una vez por proceso, la primera vez que se lista el catálogo (no
    # al arrancar: si la base está dormida, el arranque no debe depender de ella).
    reparado = {"marcas": False}

    @app.get("/api/catalogo")
    def listar():
        if not reparado["marcas"]:
            reparado["marcas"] = True
            cat.limpiar_marcas()
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
        # Las retenciones y el IVA son "a cuenta" / compensables: el simulador
        # de ML solo descuenta sus propios costos, por eso mostramos las dos.
        impuestos = d["iibb"] + d["ganancias"] + d["iva"]
        neto_estilo_ml = venta.neto_ars + impuestos
        costo = p.costo_total_ars
        def _m(neto):
            m = neto - costo
            return {"margen_ars": round(m, 2),
                    "margen_pct": round(m / costo * 100, 1) if costo else 0.0}
        return {
            "precio": precio,
            "costo_puesto_ars": costo,
            "detalle": {
                "costos_ml": d["costos_ml"],
                "iva": d["iva"],
                "ganancias": d["ganancias"],
                "iibb": d["iibb"],
                "impuestos_total": round(impuestos, 2),
                "costos_ml_pct": round(cfg_ef.meli.costos_ml_pct(p.categoria) * 100, 1),
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
        # Valores por defecto para atributos administrativos (IVA 21 %,
        # impuesto interno 0 %, motivo de GTIN vacío "Otro"), para no cargarlos
        # a mano en cada publicación.
        for a in obligatorios:
            a["sugerido"] = valor_por_defecto(a)
        preview = vista_previa(p, pictures=pics)
        faltan = faltantes_para_publicar(p, obligatorios, pics)
        return {"preview": preview, "categorias_sugeridas": sugeridas,
                "atributos_obligatorios": obligatorios, "faltantes": faltan}

    @app.patch("/api/catalogo/{pid}/publicacion")
    def editar_publicacion(pid: int, body: Publicacion):
        _p(pid)
        datos = {k: v for k, v in body.model_dump().items() if v is not None}
        return _dict(cat.actualizar_publicacion(pid, **datos))

    # ---- acciones en lote ------------------------------------------------
    #
    # Cargar de a uno es el cuello de botella: cada producto necesita categoría,
    # GTIN, atributos y fotos. `preparar` completa todo eso solo; `publicar`
    # aprueba y publica los que ya quedaron completos. Nada se publica sin que
    # el usuario apriete el botón: la aprobación sigue siendo un acto explícito.

    # Los atributos de una categoría se preguntan una sola vez: en un lote de 70
    # productos la categoría se repite y no tiene sentido pedir lo mismo 70 veces.
    _cache_attrs: dict = {}

    def _defs_categoria(cli: Optional[MeliClient], categoria: str) -> dict:
        """{"obligatorios": [...], "todos": {id: attr}} de la categoría."""
        if not categoria or cli is None:
            return {"obligatorios": [], "todos": {}}
        if categoria not in _cache_attrs:
            obligatorios: list = []
            todos: dict = {}
            try:
                obligatorios = cli.atributos_obligatorios(categoria)
            except MeliAPIError:
                pass
            try:
                todos = {a.get("id"): a for a in cli.atributos(categoria) if a.get("id")}
            except MeliAPIError:
                pass
            _cache_attrs[categoria] = {"obligatorios": obligatorios, "todos": todos}
        return _cache_attrs[categoria]

    def _limpiar_para_buscar(titulo: str) -> str:
        """Título reducido a lo que sirve para buscar: sin marketing, sin
        cantidades de piezas ni edades, que solo ensucian la consulta."""
        import re
        t = re.sub(r"\(?\s*[\d.,]+\s*(piezas|pzas|pcs|pieces|bloques)\b\)?", " ",
                   titulo or "", flags=re.I)
        t = re.sub(r"\b\d+\s*\+\s*", " ", t)              # "18+"
        t = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _ficha_catalogo(titulo_completo: str, cli: Optional[MeliClient],
                        set_declarado: str = "", marca: str = "") -> dict:
        """Producto del catálogo de MercadoLibre que corresponde a este título.

        Se busca por el número de modelo del fabricante, que es inequívoco. Se
        prueban los dos que tenemos —el del título y el que declara Amazon en la
        ficha— porque ninguno es confiable solo: el título a veces no lo trae, y
        Amazon a veces declara un código interno (6530082 en vez del set 10302).

        Ojo con el título: hay que pasarle el completo de Amazon, porque el de
        ML está recortado a 60 caracteres y ahí el número queda cortado
        ("...Kylo Ren 752" por 75256).
        """
        if cli is None:
            return {}
        candidatos = []
        for n in (numero_de_set(titulo_completo), (set_declarado or "").strip()):
            if n and n not in candidatos:
                candidatos.append(n)
        prefijo = (marca or "").strip()
        for numero in candidatos:
            consulta = f"{prefijo} {numero}".strip()
            try:
                ficha = cli.ficha_de_catalogo(consulta, debe_contener=numero)
            except MeliAPIError:
                continue
            if ficha.get("product_id"):
                return ficha
        # Por nombre: el número puede no estar, o MercadoLibre puede tener el
        # producto cargado sin él en el nombre. Acá la guarda es el parecido
        # entre los títulos, que además descarta otro set de la misma línea.
        consulta = f"{prefijo} {_limpiar_para_buscar(titulo_completo)}".strip()
        if len(consulta) > 3:
            try:
                ficha = cli.ficha_de_catalogo(consulta[:120],
                                              parecido_a=titulo_completo)
            except MeliAPIError:
                return {}
            if ficha.get("product_id"):
                return ficha
        return {}

    def _codigo_de(titulo: str, asin: str, cli: Optional[MeliClient],
                   set_declarado: str = "", marca: str = "") -> dict:
        """Código de barras del producto, de la fuente más confiable a la menos.

        Devuelve {gtin, fuente, bloqueado}. `bloqueado` avisa que Amazon nos
        está limitando, para poder frenar un lote en vez de seguir insistiendo.
        """
        from gtin_lookup import buscar_gtin, validar_gtin
        from fuentes_gtin import gtin_de_brickset, gtin_de_upcitemdb

        numero = (set_declarado or "").strip() or numero_de_set(titulo)

        # 1) Lo que ya tenemos cargado de otro producto con el mismo ASIN.
        if asin:
            for p in cat.todos():
                otro = (p.ml_attributes or {}).get("GTIN", "")
                if p.asin.upper() == asin.upper() and otro and validar_gtin(otro):
                    return {"gtin": otro, "fuente": "tu catálogo", "bloqueado": False}

        # 2) Brickset: la base de referencia de LEGO, por número de set. Es una
        #    API de verdad, no bloquea servidores y el dato viene de la caja.
        if numero and (marca or "").strip().upper() == "LEGO":
            try:
                r = gtin_de_brickset(numero)
            except Exception:  # noqa: BLE001 - una fuente caída no frena al resto
                r = {}
            if r.get("gtin"):
                return {"gtin": r["gtin"], "fuente": "Brickset", "bloqueado": False}

        # 3) El catálogo de MercadoLibre: oficial y donde ya estamos autenticados.
        ficha = _ficha_catalogo(titulo, cli, set_declarado, marca)
        if ficha.get("gtin") and validar_gtin(ficha["gtin"]):
            return {"gtin": ficha["gtin"], "fuente": "catálogo de MercadoLibre",
                    "bloqueado": False}

        # 4) UPCitemdb: base genérica de códigos de barras, para cualquier rubro.
        consulta = f"{marca} {_limpiar_para_buscar(titulo)}".strip()
        try:
            r = gtin_de_upcitemdb(consulta, parecido_a=titulo)
        except Exception:  # noqa: BLE001
            r = {}
        if r.get("gtin"):
            return {"gtin": r["gtin"], "fuente": "UPCitemdb", "bloqueado": False}

        # 5) Amazon y la web. Va última porque bloquea a los servidores de la
        #    nube: desde Render casi siempre falla, desde una PC hogareña no.
        if asin:
            try:
                r = buscar_gtin(asin)
            except Exception:  # noqa: BLE001 - es best-effort
                return {"gtin": "", "fuente": "", "bloqueado": False}
            if r.get("ok") and r.get("gtin"):
                return {"gtin": r["gtin"], "fuente": r.get("fuente", "amazon"),
                        "bloqueado": False}
            return {"gtin": "", "fuente": "", "bloqueado": bool(r.get("bloqueado"))}
        return {"gtin": "", "fuente": "", "bloqueado": False}

    def _buscar_gtin(titulo: str, asin: str, cli: Optional[MeliClient],
                     set_declarado: str = "", marca: str = "") -> str:
        return _codigo_de(titulo, asin, cli, set_declarado, marca)["gtin"]

    def _preparar_uno(p: ProductoCatalogo, cli: Optional[MeliClient]) -> ProductoCatalogo:
        """Completa lo que se pueda deducir solo: marca, categoría, fotos, GTIN
        y los atributos administrativos con valor fijo."""
        datos: dict = {}
        marca = elegir_marca(p.marca, p.titulo_ml or p.modelo or "")
        if marca and marca != p.marca:
            datos["marca"] = marca
        if not (p.titulo_ml or "").strip() and p.modelo:
            datos["titulo_ml"] = p.modelo[:60]
        titulo = datos.get("titulo_ml") or p.titulo_ml or p.modelo or p.asin
        # El título de MercadoLibre está recortado a 60 caracteres y ahí se
        # pierden justo los datos del final: el número de set queda cortado
        # ("...Kylo Ren 752" por 75256) y la cantidad de piezas desaparece. Para
        # leer datos siempre se usa el título completo de Amazon.
        titulo_completo = p.modelo or p.titulo_ml or titulo

        categoria = p.ml_category_id
        if not categoria and cli is not None:
            try:
                sug = cli.predecir_categoria(titulo)
                categoria = (sug[0].get("category_id") if sug else "") or ""
                if categoria:
                    datos["ml_category_id"] = categoria
            except MeliAPIError:
                pass

        attrs = dict(p.ml_attributes or {})
        if not (attrs.get("GTIN") or "").strip():
            gtin = _buscar_gtin(titulo_completo, p.asin, cli,
                                p.modelo_fabricante, marca or p.marca)
            if gtin:
                attrs["GTIN"] = gtin
        defs = _defs_categoria(cli, categoria)
        obligatorios = defs["obligatorios"]
        for a in obligatorios:
            aid = a.get("id")
            if aid and not (attrs.get(aid) or "").strip():
                valor = valor_por_defecto(a)
                if valor:
                    attrs[aid] = valor
        # La cantidad de piezas viene en el propio título ("(802 piezas)"). Sin
        # ella MercadoLibre publica igual, pero avisa que falta y la publicación
        # matchea peor con su catálogo.
        if not (attrs.get("PIECES_NUMBER") or "").strip():
            piezas = piezas_del_titulo(titulo_completo)
            if piezas:
                attrs["PIECES_NUMBER"] = piezas
        # El código de barras de los sets no siempre se consigue. La vía oficial
        # de MercadoLibre para ese caso es declarar el motivo de GTIN vacío; sin
        # eso el producto queda trabado pidiendo un dato que no existe.
        if (attrs.get("GTIN") or "").strip():
            attrs.pop("EMPTY_GTIN_REASON", None)
        elif not (attrs.get("EMPTY_GTIN_REASON") or "").strip():
            motivo = valor_por_defecto(defs["todos"].get("EMPTY_GTIN_REASON") or {})
            if motivo:
                attrs["EMPTY_GTIN_REASON"] = motivo
        if attrs != (p.ml_attributes or {}):
            datos["ml_attributes"] = attrs

        # El estado no se toca: los productos ya nacen en "borrador" y los
        # publicados o pausados no deben volver atrás por completarles datos.
        if datos:
            p = cat.actualizar_publicacion(p.id, **datos)
        return p

    def _en_lote(ids: list[int], accion) -> dict:
        """Corre `accion` sobre cada id sin que un error corte el resto."""
        resultados = []
        for pid in ids[:25]:
            p = cat.obtener(int(pid))
            if not p:
                resultados.append({"id": pid, "ok": False, "error": "no existe"})
                continue
            nombre = (p.titulo_ml or p.modelo or p.asin or str(pid))[:60]
            try:
                accion(p)
                resultados.append({"id": pid, "ok": True, "nombre": nombre})
            except HTTPException as e:
                detalle = e.detail
                if isinstance(detalle, dict) and detalle.get("faltantes"):
                    detalle = "falta " + ", ".join(detalle["faltantes"])
                resultados.append({"id": pid, "ok": False, "nombre": nombre,
                                   "error": str(detalle)})
            except Exception as e:  # noqa: BLE001 - un producto no frena el lote
                resultados.append({"id": pid, "ok": False, "nombre": nombre,
                                   "error": str(e)})
        return {"resultados": resultados}

    @app.post("/api/catalogo/lote/preparar")
    def lote_preparar(body: dict):
        ids = (body or {}).get("ids") or []
        try:
            cli = _client()
        except HTTPException:
            cli = None  # sin sesión de ML se completa lo que no necesita red
        return _en_lote(ids, lambda p: _preparar_uno(p, cli))

    @app.post("/api/catalogo/lote/codigos")
    def lote_codigos(body: dict):
        """Busca el código de barras de los productos ya cargados y lo guarda.

        Es el conversor aplicado al catálogo: sin GTIN, MercadoLibre no deja
        publicar en varias categorías. Va de a uno y con pausa, y frena apenas
        Amazon nos limita: lo que quedó se retoma más tarde.
        """
        import time
        ids = [int(i) for i in (body or {}).get("ids", [])][:50]
        pausa = float((body or {}).get("pausa_seg", 1.5))
        solo_faltantes = (body or {}).get("solo_faltantes", True)
        cli = None
        if store.hay_sesion() and cred.configurado:
            try:
                cli = _client()
            except HTTPException:
                pass

        resultados = []
        detenido = False
        for i, pid in enumerate(ids):
            p = cat.obtener(pid)
            if not p:
                continue
            attrs = dict(p.ml_attributes or {})
            if solo_faltantes and (attrs.get("GTIN") or "").strip():
                resultados.append({"id": pid, "nombre": p.titulo_ml or p.modelo,
                                   "gtin": attrs["GTIN"], "fuente": "ya lo tenía",
                                   "ok": True})
                continue
            r = _codigo_de(p.modelo or p.titulo_ml or "", p.asin, cli,
                           p.modelo_fabricante, p.marca)
            if r["gtin"]:
                attrs["GTIN"] = r["gtin"]
                # Con GTIN de verdad el motivo de GTIN vacío sobra, y mandarlos
                # juntos es contradictorio: MercadoLibre lo rechaza.
                attrs.pop("EMPTY_GTIN_REASON", None)
                cat.actualizar_publicacion(pid, ml_attributes=attrs)
            resultados.append({"id": pid, "nombre": p.titulo_ml or p.modelo,
                               "gtin": r["gtin"], "fuente": r["fuente"],
                               "ok": bool(r["gtin"])})
            if r["bloqueado"]:
                detenido = True
                break
            if i < len(ids) - 1 and r["fuente"] not in ("tu catálogo", "ya lo tenía"):
                time.sleep(min(pausa, 5.0))

        return {"resultados": resultados, "detenido": detenido,
                "encontrados": sum(1 for r in resultados if r["ok"]),
                "total": len(resultados),
                "pendientes": max(0, len(ids) - len(resultados))}

    @app.post("/api/catalogo/codigos/cargar")
    def cargar_codigos(body: dict):
        """Carga códigos de barras a mano, en lote.

        La salida garantizada cuando ninguna fuente automática lo tiene: se
        pegan líneas `clave;código`, donde la clave es el ASIN o el número de
        set. Para LEGO, Brickset deja exportar esa lista de una.
        """
        import re as _re
        from gtin_lookup import validar_gtin

        crudo = (body or {}).get("lineas", "")
        lineas = crudo if isinstance(crudo, list) else str(crudo).splitlines()
        productos = cat.todos()
        aplicados, sin_producto, invalidos = [], [], []

        for linea in lineas:
            partes = [p.strip() for p in _re.split(r"[;,\t|]+|\s{2,}", linea.strip())
                      if p.strip()]
            if len(partes) < 2:
                if linea.strip():
                    invalidos.append(linea.strip()[:60])
                continue
            clave, codigo = partes[0], _re.sub(r"\D", "", partes[-1])
            if not validar_gtin(codigo):
                invalidos.append(f"{clave}: {partes[-1]} no es un código válido")
                continue
            destino = next(
                (p for p in productos
                 if p.asin.upper() == clave.upper()
                 or (p.modelo_fabricante or "") == clave
                 or numero_de_set(p.modelo or p.titulo_ml or "") == clave), None)
            if not destino:
                sin_producto.append(clave)
                continue
            attrs = dict(destino.ml_attributes or {})
            attrs["GTIN"] = codigo
            attrs.pop("EMPTY_GTIN_REASON", None)
            cat.actualizar_publicacion(destino.id, ml_attributes=attrs)
            aplicados.append({"id": destino.id, "clave": clave, "gtin": codigo,
                              "nombre": destino.titulo_ml or destino.modelo})

        return {"aplicados": aplicados, "sin_producto": sin_producto,
                "invalidos": invalidos, "total": len(aplicados)}

    @app.get("/api/codigos/pendientes")
    def codigos_pendientes(limite: int = 40):
        """Productos del catálogo que todavía no tienen código de barras."""
        faltan = [{"id": p.id, "asin": p.asin,
                   "nombre": p.titulo_ml or p.modelo or p.asin}
                  for p in cat.todos()
                  if p.asin and not (p.ml_attributes or {}).get("GTIN")]
        return {"total": len(faltan), "items": faltan[:max(1, min(limite, 100))]}

    @app.get("/codigos/recibir", response_class=HTMLResponse)
    def codigos_recibir(datos: str = "", corto: str = ""):
        """Destino del botón que lee las fichas de Amazon desde el navegador.

        Llega `ASIN:codigo,ASIN:codigo`. Se valida cada código antes de
        guardarlo: el botón ya lo hizo del lado del navegador, pero lo que llega
        por la URL no es de fiar.
        """
        from gtin_lookup import validar_gtin
        productos = {p.asin.upper(): p for p in cat.todos() if p.asin}
        guardados, ignorados = 0, 0
        for par in (datos or "").split(","):
            asin, _, codigo = par.partition(":")
            asin, codigo = asin.strip().upper(), codigo.strip()
            p = productos.get(asin)
            if not p or not validar_gtin(codigo):
                ignorados += 1 if par.strip() else 0
                continue
            attrs = dict(p.ml_attributes or {})
            attrs["GTIN"] = codigo
            attrs.pop("EMPTY_GTIN_REASON", None)
            cat.actualizar_publicacion(p.id, ml_attributes=attrs)
            guardados += 1
        aviso = ("<p>Amazon pidió verificación antes de terminar: volvé a tocar "
                 "el botón más tarde para los que falten.</p>" if corto else "")
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<div style=\"font-family:system-ui;text-align:center;margin-top:70px;"
            "line-height:1.6\">"
            f"<h1>✅ {guardados} código(s) guardado(s)</h1>"
            + (f"<p>{ignorados} no se pudieron aplicar.</p>" if ignorados else "")
            + aviso +
            "<p>Volvé al panel: ya podés preparar los borradores y publicar.</p>"
            "<p><a href='/panel'>← Ir al panel</a></p></div>")

    @app.get("/api/codigos/fuentes")
    def fuentes_de_codigos():
        """Qué fuentes de código están disponibles ahora mismo."""
        from fuentes_gtin import brickset_configurado
        return {
            "brickset": brickset_configurado(),
            "mercadolibre": store.hay_sesion() and cred.configurado,
            "upcitemdb": True,       # nivel de prueba, sin registro
            "amazon": True,          # disponible, pero bloquea servidores
        }

    @app.post("/api/catalogo/lote/publicar")
    def lote_publicar(body: dict):
        """Aprueba y publica. El clic del usuario en el panel ES la aprobación:
        cada producto pasa por el mismo `publicar` de siempre, con sus
        validaciones."""
        ids = (body or {}).get("ids") or []

        def _uno(p: ProductoCatalogo) -> None:
            if p.estado != "aprobado":
                cat.cambiar_estado(p.id, "aprobado", "Aprobado en lote")
            publicar(p.id, Borrador())

        return _en_lote(ids, _uno)

    @app.post("/api/catalogo/lote/borrar")
    def lote_borrar(body: dict):
        ids = (body or {}).get("ids") or []
        return _en_lote(ids, lambda p: cat.eliminar(p.id))

    @app.get("/api/catalogo/{pid}/payload")
    def payload(pid: int):
        """Exactamente el JSON que se le va a mandar a MercadoLibre, sin
        publicar nada. Sirve para ver qué valor está viajando cuando ML rechaza
        el ítem sin decir cuál es el que no le gusta."""
        p = _p(pid)
        permitidos = {}
        if store.hay_sesion() and cred.configurado and p.ml_category_id:
            try:
                permitidos = _client().valores_permitidos(p.ml_category_id)
            except (MeliAPIError, HTTPException):
                pass
        marca_ml = permitidos.get("BRAND") or []
        return {
            "item": construir_item(p, pictures=p.pictures, valores_permitidos=permitidos),
            "marca_guardada": p.marca,
            "marca_resuelta": elegir_marca(p.marca, p.titulo_ml or p.modelo or "",
                                           permitidos.get("BRAND")),
            "ml_conoce_valores_de_marca": bool(marca_ml),
            "marcas_de_ejemplo": [v["name"] for v in marca_ml[:15]],
        }

    @app.get("/api/catalogo/{pid}/diagnostico")
    def diagnostico(pid: int):
        """Por qué no se consigue el GTIN de este producto, paso por paso.

        Muestra el título completo, el número de set que se extrae, qué
        devuelve la búsqueda en el catálogo de MercadoLibre y qué error tiró,
        si tiró alguno. Sin esto hay que adivinar en qué eslabón se corta.
        """
        p = _p(pid)
        completo = p.modelo or p.titulo_ml or ""
        set_id = (p.modelo_fabricante or "").strip() or numero_de_set(completo)
        out = {
            "titulo_completo": completo,
            "titulo_ml_recortado": p.titulo_ml,
            "numero_de_set": set_id or "(no se pudo extraer)",
            "numero_declarado_por_amazon": p.modelo_fabricante or "(no vino en la ficha)",
            "piezas": piezas_del_titulo(completo) or "(no figura en el título)",
            "gtin_guardado": (p.ml_attributes or {}).get("GTIN", ""),
            "consulta": f"LEGO {set_id}" if set_id else "(sin número de set)",
            "candidatos_del_catalogo": [],
            "ficha_elegida": {},
            "error": "",
        }
        if not (store.hay_sesion() and cred.configurado):
            out["error"] = "No hay sesión de MercadoLibre."
            return out
        if not set_id:
            out["error"] = ("Sin número de set no se puede buscar en el catálogo. "
                            "Cargá el GTIN a mano.")
            return out
        try:
            cli = _client()
            out["candidatos_del_catalogo"] = cli.buscar_productos_catalogo(
                out["consulta"], limit=5)
            out["ficha_elegida"] = cli.ficha_de_catalogo(out["consulta"],
                                                         debe_contener=set_id)
        except (MeliAPIError, HTTPException) as e:
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    @app.post("/api/catalogo/{pid}/aprobar")
    def aprobar(pid: int):
        _p(pid)
        return _dict(cat.cambiar_estado(pid, "aprobado", "Aprobado para publicar"))

    def _publicado(pid: int, p: ProductoCatalogo, creado: dict, cli) -> dict:
        """Cierre común de las dos vías de publicación."""
        item_id = creado.get("id", "")
        # La descripción va en un endpoint aparte, después de crear el ítem.
        if item_id and (p.descripcion or "").strip():
            try:
                cli.poner_descripcion(item_id, p.descripcion)
            except MeliAPIError:
                pass  # el ítem ya se publicó; la descripción se puede reintentar
        return _dict(cat.registrar_publicacion(pid, item_id,
                                               creado.get("permalink", "")))

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

        # 2) Vía del catálogo: si el producto está en el catálogo de
        #    MercadoLibre, se publica contra su ficha. ML toma de ahí el GTIN y
        #    el resto de los atributos, así que no hay nada más que validar. Es
        #    la única forma de publicar los sets cuyo código de barras no se
        #    consigue, y además deja la publicación bien matcheada.
        ficha = _ficha_catalogo(p.modelo or p.titulo_ml or "", cli,
                                p.modelo_fabricante, p.marca)
        creado = None
        if ficha.get("product_id"):
            try:
                creado = cli.publicar(construir_item_catalogo(
                    p, ficha["product_id"], listing_type_id=body.listing_type_id))
            except MeliAPIError:
                creado = None  # no es catalogable: seguimos por la vía normal

        if creado is not None:
            return _publicado(pid, p, creado, cli)

        # 3) Vía normal: hay que mandar todos los atributos obligatorios de la
        #    categoría (GTIN, cantidad de piezas, etc.).
        obligatorios = []
        try:
            obligatorios = cli.atributos_obligatorios(p.ml_category_id)
        except MeliAPIError:
            pass
        faltan = faltantes_para_publicar(p, obligatorios, pics)
        if faltan:
            raise HTTPException(422, {"faltantes": faltan})
        # 4) Valores que ML acepta en la categoría: nos dejan mandar `value_id`
        #    en vez de texto libre y evitan el "invalid value name" (la marca
        #    de Amazon viene como "Visit the LEGO Store").
        permitidos = {}
        try:
            permitidos = cli.valores_permitidos(p.ml_category_id)
        except MeliAPIError:
            pass
        # La marca es el rechazo más común: si no se puede resolver, se corta acá
        # con un mensaje que dice qué campo llenar, en vez de mandarlo y que
        # MercadoLibre conteste "The attributes [BRAND] are required".
        marca_final = elegir_marca(p.marca, p.titulo_ml or p.modelo or "",
                                   permitidos.get("BRAND"))
        if not marca_final:
            raise HTTPException(422, {"faltantes": [
                "marca: cargala en el campo Marca del editor (por ejemplo: LEGO). "
                "MercadoLibre la exige y rechaza la publicación sin ella."]})
        # MercadoLibre migró de `title` a `family_name` y no acepta los dos.
        # Probamos con el campo nuevo y, si la categoría todavía espera el
        # viejo, reintentamos una vez con `title`.
        def _armar(campo: str) -> dict:
            return construir_item(p, pictures=pics,
                                  listing_type_id=body.listing_type_id,
                                  campo_titulo=campo,
                                  valores_permitidos=permitidos)

        def _rechazo(cuerpo) -> HTTPException:
            # Se incluye la marca enviada: es el dato que más veces provoca el
            # rechazo y el que no se ve en el mensaje de MercadoLibre.
            return HTTPException(502, "MercadoLibre rechazó la publicación: "
                                 + describir_error(cuerpo)
                                 + f"\n(se mandó Marca = «{marca_final}»)")

        try:
            creado = cli.publicar(_armar("family_name"))
        except MeliAPIError as e:
            if "family_name" in str(e.cuerpo):
                try:
                    creado = cli.publicar(_armar("title"))
                except MeliAPIError as e2:
                    raise _rechazo(e2.cuerpo)
            else:
                raise _rechazo(e.cuerpo)
        return _publicado(pid, p, creado, cli)

    # ---- agente: completa y publica solo ----------------------------------

    def _agente() -> "Agente":
        """El agente reusa exactamente los mismos pasos que hacés a mano: no
        hay un segundo camino de publicación con reglas distintas."""
        from agente import Agente

        def _paso_preparar(p):
            cli = None
            if store.hay_sesion() and cred.configurado:
                try:
                    cli = _client()
                except HTTPException:
                    pass
            p = _preparar_uno(p, cli)
            # Las fotos vienen de Amazon; sin al menos una no se puede publicar.
            if not p.pictures:
                raise RuntimeError("sin fotos: cargalas desde el editor")
            return p

        def _paso_codigo(p):
            cli = None
            if store.hay_sesion() and cred.configurado:
                try:
                    cli = _client()
                except HTTPException:
                    pass
            r = _codigo_de(p.modelo or p.titulo_ml or "", p.asin, cli,
                           p.modelo_fabricante, p.marca)
            if r["gtin"]:
                attrs = dict(p.ml_attributes or {})
                attrs["GTIN"] = r["gtin"]
                attrs.pop("EMPTY_GTIN_REASON", None)
                cat.actualizar_publicacion(p.id, ml_attributes=attrs)
            return r

        def _paso_publicar(p):
            if p.estado != "aprobado":
                cat.cambiar_estado(p.id, "aprobado", "Aprobado por el agente")
            return publicar(p.id, Borrador())

        def _faltan(p):
            return faltantes_para_publicar(p, None, p.pictures)

        if not hasattr(app.state, "agente"):
            app.state.agente = Agente(cat, _paso_preparar, _paso_codigo,
                                      _paso_publicar, _faltan)
        return app.state.agente

    @app.get("/api/agente")
    def agente_estado():
        return _agente().estado()

    @app.patch("/api/agente")
    def agente_config(body: dict):
        ag = _agente()
        try:
            ag.config = body or {}
        except (TypeError, ValueError) as e:
            raise HTTPException(400, f"Configuración inválida: {e}")
        return ag.estado()

    @app.post("/api/agente/tick")
    def agente_tick():
        """Avanza un paso sobre un producto. El panel lo llama en bucle mientras
        el agente está encendido; también sirve para un cron externo."""
        return _agente().tick()

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
