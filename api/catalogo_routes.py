"""
Rutas de la API para el catálogo, OAuth de MercadoLibre y publicación.

Se registran sobre la app FastAPI existente. Toda operación que toca la cuenta
de MercadoLibre (predecir categoría, publicar, actualizar, pausar) requiere una
sesión OAuth activa; sin ella, se responde con un error claro en vez de fallar.

Regla de seguridad: `publicar` exige que el producto esté en estado "aprobado"
y que no falte ningún dato obligatorio. Nunca se publica en un solo paso.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel

from arbitraje.config import Config, CONFIG_DEFAULT, CONDICIONES_FISCALES
from arbitraje.cotizacion import obtener_cotizaciones, invalidar_cache
from arbitraje.pricing import margen_real_al_precio
from amazon_import import importar_desde_url, scraperapi_configurada
from catalogo import Catalogo, ProductoCatalogo, DOLARES_COSTO
from importador import ColaImportacion
from mercadolibre.oauth import MeliOAuth, MeliCredenciales, TokenStore
from mercadolibre.client import MeliClient, MeliAPIError, describir_error
from mercadolibre.listing import (construir_item, construir_item_catalogo,
                                  vista_previa, faltantes_para_publicar,
                                  valor_por_defecto)
from marcas import elegir_marca
from titulos import numero_de_set, piezas_del_titulo
from videos_youtube import buscar_video, configurado as youtube_configurado


class AltaProducto(BaseModel):
    amazon_link: str = ""
    asin: str = ""
    marca: str = ""
    modelo: str = ""
    precio_usd: float = 0.0
    peso_kg: float = 0.5
    costo_envio_usd: float = 0.0
    # None = nadie lo miró. Se costea como si no tuviera envío gratis.
    envio_gratis_amazon: Optional[bool] = None
    disponibilidad: str = "in_stock"
    regimen: str = "courier"
    categoria: str = "default"
    margen_deseado: float = 0.30
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
    videos: Optional[list[str]] = None
    # Se acepta el link de YouTube pegado tal cual, no solo el id.
    video_youtube: Optional[str] = None


# Cuánto puede tardar como mucho una tanda del cambio de precios antes de
# devolver lo hecho y dejar el resto para la próxima. Bien por debajo del tope
# del panel (180 s), para que conteste él y no lo corte el navegador.
TOPE_APLICAR_SEG = 100.0


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

    # ---- cómo se arman título y descripción -------------------------------

    @app.get("/api/catalogo/config")
    def config_publicacion():
        from descripcion import COMPRA_DEFAULT
        return {"tipo_producto": cat.tipo_producto,
                "ganancia_minima": cat.ganancia_minima,
                "envio_import_pct": round(cat.envio_import_pct * 100, 1),
                "envio_import_sin_gratis_pct": round(
                    cat.envio_import_sin_gratis_pct * 100, 1),
                "texto_compra": cat.texto_compra,
                "texto_compra_default": COMPRA_DEFAULT,
                "publicar_en_catalogo": cat.publicar_en_catalogo}

    @app.post("/api/catalogo/config")
    def config_publicacion_set(body: dict):
        cuerpo = body or {}
        if "tipo_producto" in cuerpo:
            cat.tipo_producto = cuerpo.get("tipo_producto") or ""
        # Los dos porcentajes de envío se tocan igual y reestiman una sola vez:
        # si vinieran los dos en el mismo pedido, reestimar por cada uno haría
        # que el segundo barrido tomara como "cargado a mano" lo que acababa de
        # escribir el primero.
        pcts_antes = cat.pcts_envio()
        toco_envio = False
        for clave, atributo in (("envio_import_pct", "envio_import_pct"),
                                ("envio_import_sin_gratis_pct",
                                 "envio_import_sin_gratis_pct")):
            if clave not in cuerpo:
                continue
            try:
                setattr(cat, atributo, float(cuerpo.get(clave) or 0) / 100.0)
            except (TypeError, ValueError) as e:
                raise HTTPException(422, str(e))
            toco_envio = True
        if toco_envio:
            # Sin reestimar, el porcentaje nuevo no movería a los productos que
            # ya tienen el envío guardado, que son todos los del catálogo.
            cat.reestimar_envios(pct_anterior=pcts_antes)
        if "ganancia_minima" in cuerpo:
            try:
                cat.ganancia_minima = cuerpo.get("ganancia_minima")
            except ValueError as e:
                raise HTTPException(422, str(e))
            # El piso cambia el precio sugerido de TODO el catálogo.
            cat.recalcular_todos()
        if "texto_compra" in cuerpo:
            cat.texto_compra = cuerpo.get("texto_compra") or ""
        if "publicar_en_catalogo" in cuerpo:
            cat.publicar_en_catalogo = bool(cuerpo.get("publicar_en_catalogo"))
        return config_publicacion()

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
                # Fijado a mano: manda sobre la cotización en vivo.
                "tc_manual": cat.tc_manual,
                "tc_usado": round(cfg_ef.tc_compra(), 2)}

    @app.patch("/api/dolar-costo")
    def dolar_costo_set(body: dict):
        cuerpo = body or {}
        try:
            if "dolar_costo" in cuerpo:
                cat.dolar_costo = cuerpo.get("dolar_costo", "")
            # Vacío o cero vuelve a la cotización en vivo.
            if "tc_manual" in cuerpo:
                cat.tc_manual = cuerpo.get("tc_manual")
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
        seguir. Así el avance se ve en vivo y no hay peticiones eternas.

        Yendo por proxy se procesan menos por llamada: el proxy tarda bastante
        más que leer directo —tiene que conseguir una IP que Amazon acepte— y
        varios productos seguidos harían justo la petición eterna que este
        endpoint evita, que el servidor corta a mitad de camino.
        """
        maximo = int((body or {}).get("maximo", 3))
        pausa = float((body or {}).get("pausa_seg", 2.0))
        tope = 2 if scraperapi_configurada() else 10
        return cola.procesar_lote(maximo=min(maximo, tope), pausa_seg=pausa)

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
            # Solo los que traen basura evidente. Rearmar todos acá pisaría los
            # títulos editados a mano, y sin avisar: eso se pide aparte.
            cat.limpiar_titulos(solo_sucios=True)
            # El porcentaje de envío viejo quedó en la casilla equivocada y los
            # costos se estimaron con él: acá se rehace esa cuenta, antes de
            # devolver la tabla, para que no se muestre un costo que ya se sabe
            # mal. Es idempotente: se anota en la base cuando queda pendiente.
            cat.migrar_pct_envio()
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
                "envio": d["envio"],
                "percepcion_iva": d["percepcion_iva"],
                "iva": d["iva"],
                "ganancias": d["ganancias"],
                "iibb": d["iibb"],
                "impuestos_total": round(impuestos, 2),
                "costos_ml_pct": round(cfg_ef.meli.costos_ml_pct(p.categoria) * 100, 1),
                "percepcion_iva_desde": cfg_ef.meli.percepcion_iva_desde_ars,
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
        # La vista previa tiene que mostrar lo que se va a publicar de verdad,
        # no el campo crudo: la descripción se arma al publicar.
        preview["descripcion_a_publicar"] = cat.descripcion_armada(p)
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

    def _limpiar_para_buscar(titulo: str, palabras: int = 6) -> str:
        """Consulta corta para buscar el producto en el catálogo de ML.

        Los títulos de Amazon tienen 100+ caracteres de marketing ("Kit de
        diorama para fanáticos, regalo coleccionable para adultos..."). Mandados
        enteros, la búsqueda no devuelve nada: hay que quedarse con las primeras
        palabras, que son las que identifican el producto.
        """
        import re
        from titulos import _VACIAS, normalizar
        t = re.sub(r"\(?\s*[\d.,]+\s*(piezas|pzas|pcs|pieces|bloques)\b\)?", " ",
                   titulo or "", flags=re.I)
        t = re.sub(r"\b\d+\s*\+\s*", " ", t)              # "18+"
        t = re.sub(r"\b\d{7,}\b", " ", t)                 # códigos internos
        t = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]+", " ", t)
        utiles = [w for w in t.split() if normalizar(w) not in _VACIAS]
        return " ".join(utiles[:palabras])

    def _set_declarado(valor: str) -> str:
        """El modelo que declara Amazon, solo si parece número de set.

        Amazon a veces pone un código interno de 7 dígitos (6332955 en vez del
        set 10282). Buscar por ese número no devuelve nada útil: el catálogo
        contesta cualquier cosa que lo contenga y no hay match posible.
        """
        import re

        v = (valor or "").strip()
        return v if re.fullmatch(r"\d{4,6}", v) else ""

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
        for n in (numero_de_set(titulo_completo), _set_declarado(set_declarado)):
            if n and n not in candidatos:
                candidatos.append(n)
        prefijo = (marca or "").strip()
        for numero in candidatos:
            consulta = f"{prefijo} {numero}".strip()
            try:
                # 25 y no 5: buscando "LEGO 21042" los primeros lugares se los
                # llevan repuestos de auto que comparten el número (IMC, Monroe,
                # Cardone) y el set queda tapado. En el de Shrek el correcto ya
                # venía tercero. Filtrar de más no cuesta nada —la marca y el
                # número descartan lo ajeno— y es una sola llamada igual.
                ficha = cli.ficha_de_catalogo(consulta, debe_contener=numero,
                                              marca=marca, limit=25)
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
                                              parecido_a=titulo_completo,
                                              marca=marca)
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

        # Video: solo si hay clave de YouTube configurada y el producto todavía
        # no tiene uno. Que no aparezca ninguno es lo normal —se exige que el
        # canal sea el de la marca— y no traba nada: el video es opcional.
        if youtube_configurado() and not (p.video_youtube or "").strip():
            try:
                v = buscar_video(titulo_completo, marca=marca or p.marca,
                                 numero_set=_set_declarado(p.modelo_fabricante)
                                 or numero_de_set(titulo_completo))
            except Exception:  # noqa: BLE001 - el video nunca frena la preparación
                v = {}
            if v.get("video_id"):
                datos["video_youtube"] = v["video_id"]

        # El título, al final: para armarlo bien hacen falta la marca ya
        # limpia y la cantidad de piezas, que se resuelven más arriba.
        #
        # Solo se rearma **antes de publicar**. Una vez publicado, el título es
        # el que ve el comprador y el que MercadoLibre indexó: cambiarlo por
        # nuestra cuenta sería pisar lo que el usuario pudo haber ajustado a
        # mano mirando la competencia.
        if p.estado not in ("publicado", "pausado"):
            copia = replace(p, marca=datos.get("marca", p.marca),
                            ml_attributes=datos.get("ml_attributes",
                                                    p.ml_attributes))
            armado = cat.titulo_armado(copia)
            if armado and armado != p.titulo_ml:
                datos["titulo_ml"] = armado

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

    # ---- actualización de precios ----------------------------------------
    #
    # El dólar se mueve y los costos quedan viejos: sin esto hay que reeditar
    # cada publicación a mano. Va en dos partes a propósito —simular y
    # aplicar— porque toca el precio de publicaciones vivas: se ve qué cambia
    # antes de cambiarlo.

    def _motivo(e: MeliAPIError) -> str:
        """El texto legible de un rechazo de MercadoLibre.

        `describir_error` sirve para los cuerpos de error de ML; con cualquier
        otra cosa escupe el diccionario crudo y tapa el mensaje bueno de la
        excepción, que es justo el que explica qué pasó.
        """
        cuerpo = getattr(e, "cuerpo", None)
        detalle = (describir_error(cuerpo)
                   if isinstance(cuerpo, dict)
                   and {"cause", "message", "error"} & set(cuerpo)
                   else "")
        return detalle or str(e)

    def _numero(body, clave, etiqueta, permitir_cero: bool = False) -> Optional[float]:
        """Un número puesto a mano, o None si el campo vino vacío."""
        crudo = (body or {}).get(clave)
        if crudo in (None, ""):
            return None
        try:
            valor = float(crudo)
        except (TypeError, ValueError):
            raise HTTPException(422, f"{etiqueta} tiene que ser un número.")
        if permitir_cero:
            # El envío en cero es un dato válido: significa que no ofrecés envío
            # gratis. Distinto de vacío, que significa "usá el configurado".
            if valor < 0:
                raise HTTPException(422, f"{etiqueta} no puede ser negativo.")
        elif valor <= 0:
            raise HTTPException(422, f"{etiqueta} tiene que ser mayor que cero.")
        return valor

    def _parametros(body) -> dict:
        """Con qué números recalcular: el dólar del costo, el margen y el envío.

        El dólar es el oficial que se estima para cuando se compre —hoy 1600,
        mañana 1650— y el margen es lo que tiene que quedar limpio después de
        todos los descuentos.
        """
        margen = (body or {}).get("margen_pct")
        if margen in (None, ""):
            margen = None
        else:
            try:
                margen = float(margen)
            except (TypeError, ValueError):
                raise HTTPException(422, "El margen tiene que ser un número.")
            if margen < 0:
                raise HTTPException(422, "El margen no puede ser negativo.")
            margen = margen / 100.0
        return {"tc_costo": _numero(body, "tc_costo", "El dólar de costo"),
                "margen": margen,
                "envio": _numero(body, "envio_ars", "El costo de envío",
                                 permitir_cero=True)}

    def _publicados_con_item(ids=None):
        """Las publicaciones que se pueden repreciar, opcionalmente filtradas.

        `ids` viene de los tildes de la tabla. Vacío o ausente significa "todas":
        repreciar el catálogo entero es el caso normal cuando cambió el dólar,
        y obligar a tildar 126 filas para eso sería peor. Pero cuando el usuario
        tildó algo, tocar lo que no tildó es cambiarle precios en publicaciones
        vivas sin que lo haya pedido.
        """
        elegidos = {int(i) for i in ids} if ids else None
        return [p for p in cat.todos()
                if p.estado in ("publicado", "pausado") and (p.ml_item_id or "").strip()
                and (elegidos is None or p.id in elegidos)]

    @app.post("/api/catalogo/precios/simular")
    def simular_precios(body: dict):
        """Qué precio quedaría en cada publicación, sin tocar nada.

        Es la mitad importante: cambiar precios en publicaciones vivas sin ver
        antes qué cambia es como publicar a ciegas. Acá no se guarda nada ni se
        llama a MercadoLibre.
        """
        par = _parametros(body)
        if (body or {}).get("refrescar_cotizacion") and not par["tc_costo"]:
            _cotizacion(refrescar=True)

        seleccion = (body or {}).get("ids") or []
        filas = []
        for p in _publicados_con_item(seleccion):
            r = cat.simular(p, **par)
            actual = p.precio_publicado_ars or p.precio_sugerido_ars or 0.0
            filas.append({
                "id": p.id, "nombre": p.titulo_ml or p.modelo,
                "ml_item_id": p.ml_item_id,
                "precio_actual": round(actual, 2),
                "precio_nuevo": r["precio_ars"],
                "costo_nuevo": r["costo_ars"],
                "margen_pct": r["margen_pct"],
                "variacion_pct": round((r["precio_ars"] / actual - 1) * 100, 1) if actual else None,
            })
        c = cat.cotizacion or {}
        return {"filas": filas, "total": len(filas),
                # Para que el panel pueda decir sobre qué está operando: no es
                # lo mismo repreciar 3 tildadas que las 126 del catálogo.
                "solo_seleccionadas": bool(seleccion),
                "dolar_costo": cat.dolar_costo,
                "tc_costo": par["tc_costo"],
                "envio_ars": cat.envio_efectivo(par["envio"]),
                "cotizacion": {"oficial": c.get("oficial"),
                               "tarjeta": c.get("tarjeta"),
                               "actualizado": c.get("actualizado")}}

    @app.post("/api/catalogo/precios/aplicar")
    def aplicar_precios(body: dict):
        """Guarda el precio nuevo y lo manda a MercadoLibre.

        De a pocos por llamada: cada publicación es un viaje a MercadoLibre y
        todas juntas serían una petición eterna que el servidor corta.

        Y aun así la llamada se controla el tiempo: una publicación lenta puede
        tardar hasta 20 segundos, así que cinco alcanzan para pasarse del tope
        del panel. Si se acaba el presupuesto se devuelve lo hecho y los que
        quedaron sin tocar, en vez de que la llamada muera entera y no se sepa
        qué se aplicó.
        """
        par = _parametros(body)
        ids = [int(i) for i in (body or {}).get("ids", [])][:20]
        if not ids:
            raise HTTPException(422, "No hay publicaciones para actualizar.")
        cli = _client()
        # El dólar de costo y el envío elegidos pasan a ser los del catálogo.
        # Sin esto la tabla volvería a mostrar el costo a la cotización del
        # mercado y el margen no coincidiría con el que se vio al decidir el
        # precio.
        cambio = False
        if par["tc_costo"] and par["tc_costo"] != cat.tc_manual:
            cat.tc_manual = par["tc_costo"]
            cambio = True
        if par["envio"] is not None and par["envio"] != cat.envio_manual:
            cat.envio_manual = par["envio"]
            cambio = True
        if cambio:
            # Se recalcula todo el catálogo, no solo lo que se está
            # actualizando: si no, unos productos quedarían valuados al dólar
            # nuevo y otros al viejo, en la misma tabla.
            cat.recalcular_todos()

        resultados = []
        pendientes = []
        arranque = time.monotonic()
        for i, pid in enumerate(ids):
            # El recálculo de arriba ya consumió parte del presupuesto, así que
            # se mide desde el principio de la llamada. Al menos uno se intenta
            # siempre: si no, con el presupuesto agotado no avanzaría nunca.
            if i and time.monotonic() - arranque > TOPE_APLICAR_SEG:
                pendientes = ids[i:]
                break
            p = cat.obtener(pid)
            if not p or not (p.ml_item_id or "").strip():
                continue
            precio = cat.simular(p, **par)["precio_ars"]
            anterior = round(p.precio_publicado_ars or 0.0, 2)
            fila = {"id": pid, "nombre": p.titulo_ml or p.modelo,
                    "precio_anterior": anterior,
                    "precio_nuevo": precio, "ok": False, "error": "",
                    # Mandar el precio que la publicación ya tenía y contarlo
                    # como actualizado es indistinguible de que funcione: así
                    # se informaron "114 actualizadas" sin haber cambiado nada.
                    "sin_cambios": abs(precio - anterior) < 0.01}
            if precio <= 0:
                fila["error"] = "el precio calculado dio cero: revisá el costo"
                resultados.append(fila)
                continue
            try:
                cli.actualizar_precio(p.ml_item_id, precio)
            except MeliAPIError as e:
                # Si MercadoLibre lo rechaza —o acepta y deja el precio como
                # estaba— no se guarda: el catálogo no puede decir un precio que
                # la publicación no tiene.
                fila["error"] = _motivo(e)
                resultados.append(fila)
                continue
            if par["margen"] is not None:
                cat.actualizar_margen(pid, par["margen"])
            cat.actualizar_precio(pid, precio)
            fila["ok"] = True
            resultados.append(fila)

        return {"resultados": resultados,
                # Los que no se llegaron a tocar vuelven a la cola del panel.
                # Sin esto se perderían: el panel ya los sacó de su lista.
                "pendientes": pendientes,
                # Se cuentan aparte los que cambiaron de precio de verdad.
                "actualizados": sum(1 for r in resultados
                                    if r["ok"] and not r["sin_cambios"]),
                "sin_cambios": sum(1 for r in resultados
                                   if r["ok"] and r["sin_cambios"]),
                "total": len(resultados)}

    # ---- mejorar el título y la descripción de lo ya publicado -------------

    @app.post("/api/catalogo/publicaciones/simular")
    def simular_publicaciones(body: dict = None):
        """Qué título y qué descripción quedarían, sin tocar MercadoLibre.

        Lo ya publicado se armó con el título crudo de Amazon recortado a 60
        caracteres. Rehacerlo vale tanto o más que hacerlo bien de acá en
        adelante: son las publicaciones que ya están compitiendo.
        """
        filas = []
        for p in _publicados_con_item():
            titulo = cat.titulo_armado(p)
            filas.append({
                "id": p.id, "ml_item_id": p.ml_item_id,
                "titulo_actual": p.titulo_ml or "",
                "titulo_nuevo": titulo,
                "cambia_titulo": bool(titulo) and titulo != (p.titulo_ml or ""),
                "descripcion_nueva": cat.descripcion_armada(p),
            })
        return {"filas": filas, "total": len(filas),
                "con_titulo_nuevo": sum(1 for f in filas if f["cambia_titulo"])}

    # ---- vigilancia: precio y stock en Amazon de lo ya publicado -----------

    @app.post("/api/catalogo/vigilancia/revisar")
    def revisar_amazon(body: dict = None):
        """Relee en Amazon el precio y el stock de lo que está publicado.

        Es el agujero que se comió la primera venta: se publica con el precio
        del día que se capturó el producto, y para cuando alguien compra Amazon
        puede haber subido el precio o haberse quedado sin stock. Se vende algo
        que ya no se puede comprar.

        No cambia nada en MercadoLibre: informa. Pausar o republicar es un paso
        aparte, porque son decisiones con consecuencias.
        """
        cuerpo = body or {}
        limite = max(1, min(int(cuerpo.get("limite") or 10), 40))
        productos = cat.a_revisar(limite)

        filas = []
        for p in productos:
            url = p.amazon_link or f"https://www.amazon.com/dp/{p.asin}"
            try:
                d = importar_desde_url(url)
            except Exception as e:  # noqa: BLE001 - un producto no frena la vuelta
                filas.append({"id": p.id, "nombre": p.titulo_ml or p.modelo,
                              "error": str(e), "leido": False})
                continue
            precio_nuevo = d.get("precio_usd")
            disponible = d.get("disponible")
            antes_usd, antes_costo = p.precio_usd, p.costo_total_ars
            p = cat.marcar_revisado(p.id, precio_nuevo, disponible)

            # Lo que importa no es que el precio de Amazon suba, sino si el
            # precio YA PUBLICADO en MercadoLibre sigue dejando ganancia.
            publicado = p.precio_publicado_ars or p.precio_sugerido_ars or 0.0
            m = margen_real_al_precio(p.costo_total_ars, publicado, p.categoria,
                                      cat._cfg_efectivo()) if publicado else {}
            filas.append({
                "id": p.id, "nombre": p.titulo_ml or p.modelo,
                "leido": bool(d.get("ok")) or precio_nuevo is not None,
                "bloqueado": bool(d.get("bloqueado")),
                "error": "" if d.get("ok") else (d.get("mensaje") or ""),
                "amazon_link": url,
                "precio_antes_usd": antes_usd,
                "precio_ahora_usd": precio_nuevo,
                "disponible": disponible,
                "costo_antes_ars": round(antes_costo, 2),
                "costo_ahora_ars": round(p.costo_total_ars, 2),
                "precio_publicado_ars": round(publicado, 2),
                "margen_ahora_pct": m.get("margen_pct"),
                "margen_ars": m.get("margen_ars"),
            })

        sin_stock = [f for f in filas if f.get("disponible") is False]
        en_perdida = [f for f in filas
                      if f.get("margen_ahora_pct") is not None
                      and f["margen_ahora_pct"] < 0]
        return {"filas": filas, "revisados": len(filas),
                "sin_stock": len(sin_stock), "en_perdida": len(en_perdida),
                "no_leidos": sum(1 for f in filas if not f.get("leido")),
                # 5 créditos por producto en el plan de ScraperAPI.
                "creditos_usados": len(filas) * 5,
                "quedan_sin_revisar": max(
                    0, len([p for p in cat.todos()
                            if p.estado in ("publicado", "pausado")]) - len(filas)),
                }

    @app.post("/api/catalogo/vigilancia/pausar")
    def pausar_sin_stock(body: dict):
        """Pausa en MercadoLibre las publicaciones que se indiquen.

        Se pausa, no se borra: cuando Amazon vuelva a tener stock se reactiva y
        la publicación conserva su antigüedad y sus visitas.
        """
        ids = [int(i) for i in (body or {}).get("ids", [])][:40]
        if not ids:
            raise HTTPException(422, "No hay publicaciones para pausar.")
        cli = _client()
        resultados = []
        for pid in ids:
            p = cat.obtener(pid)
            if not p or not (p.ml_item_id or "").strip():
                continue
            fila = {"id": pid, "nombre": p.titulo_ml or p.modelo, "ok": False,
                    "error": ""}
            try:
                cli.pausar(p.ml_item_id)
                cat.cambiar_estado(pid, "pausado")
                fila["ok"] = True
            except MeliAPIError as e:
                fila["error"] = _motivo(e)
            resultados.append(fila)
        return {"resultados": resultados,
                "pausadas": sum(1 for r in resultados if r["ok"]),
                "fallas": sum(1 for r in resultados if r["error"])}

    @app.get("/api/catalogo/publicaciones/diagnostico")
    def diagnostico_publicaciones():
        """Por qué se puede o no cambiar el título de cada publicación.

        Solo lee: ni un PUT. Intentar el cambio para averiguarlo cuesta un
        pedido por producto, dispara el límite de ritmo de MercadoLibre y deja
        el diagnóstico mezclado con los errores. Una sola consulta —el multiget
        trae el ítem entero— contesta lo mismo sin tocar nada.
        """
        cli = _client()
        publicados = _publicados_con_item()
        items = cli.obtener_varios([p.ml_item_id for p in publicados])

        filas, resumen = [], {"catalogo": 0, "familia": 0, "editable": 0,
                              "no_leido": 0}
        # Una publicación pausada o sin stock se puede seguir viendo, pero no se
        # puede comprar: junta visitas y hasta intenciones de compra, y ninguna
        # termina en venta. Es la primera explicación a mirar cuando el reporte
        # de MercadoLibre muestra visitas y cero ventas.
        salud = {"activas": 0, "pausadas": 0, "otro_estado": 0, "sin_stock": 0}
        for p in publicados:
            it = items.get(p.ml_item_id)
            if it is not None:
                estado = (it.get("status") or "").lower()
                if estado == "active":
                    salud["activas"] += 1
                elif estado == "paused":
                    salud["pausadas"] += 1
                else:
                    salud["otro_estado"] += 1
                if not int(it.get("available_quantity") or 0):
                    salud["sin_stock"] += 1
            if it is None:
                motivo, clave = "no se pudo leer en MercadoLibre", "no_leido"
            elif it.get("catalog_listing"):
                motivo, clave = ("publicación de catálogo: el título lo pone "
                                 "MercadoLibre", "catalogo")
            elif (it.get("family_name") or "").strip():
                # Se describe lo que se ve, no un veredicto. Que tenga
                # `family_name` es un hecho; que por eso ML no deje cambiar el
                # título es una lectura, y para afirmarla hay que probarla:
                # está el botón de prueba sobre una publicación.
                motivo, clave = ("se creó con «family_name» (probá el cambio en "
                                 "una para confirmar si ML lo permite)", "familia")
            else:
                motivo, clave = "se le puede cambiar el título", "editable"
            resumen[clave] += 1
            filas.append({
                "id": p.id, "ml_item_id": p.ml_item_id,
                "nombre": p.titulo_ml or p.modelo,
                "titulo_en_ml": (it or {}).get("title") or "",
                "family_name": (it or {}).get("family_name") or "",
                "catalog_listing": bool((it or {}).get("catalog_listing")),
                "estado_ml": (it or {}).get("status") or "",
                "stock": (it or {}).get("available_quantity"),
                "vendidos": (it or {}).get("sold_quantity"),
                "motivo": motivo, "clave": clave,
            })
        return {"filas": filas, "total": len(filas), "resumen": resumen,
                "salud": salud}

    @app.post("/api/catalogo/{pid}/probar-titulo")
    def probar_titulo(pid: int):
        """Intenta cambiar el título de UNA publicación y devuelve el crudo.

        Es la única forma de saber si MercadoLibre lo permite: leer el ítem dice
        cómo se creó, no qué acepta al editarlo. Sobre una sola publicación el
        costo es un pedido y el riesgo es nulo —si sale, el título queda mejor;
        si no sale, no cambia nada—, en vez de descubrirlo sobre las 126.
        """
        p = _p(pid)
        if not (p.ml_item_id or "").strip():
            raise HTTPException(409, "Este producto no está publicado.")
        cli = _client()
        nuevo = cat.titulo_armado(p)
        salida = {"id": pid, "ml_item_id": p.ml_item_id,
                  "titulo_actual": p.titulo_ml or "", "titulo_probado": nuevo}
        try:
            cli.actualizar_titulo(p.ml_item_id, nuevo)
        except MeliAPIError as e:
            salida.update(ok=False, error=_motivo(e),
                          # El cuerpo tal cual lo manda ML: es el dato con el
                          # que se diagnostica, y ninguna lectura mía lo
                          # reemplaza.
                          crudo=getattr(e, "cuerpo", None), status=e.status)
            return salida
        cat.actualizar_publicacion(pid, titulo_ml=nuevo)
        salida.update(ok=True, error="", crudo=None, status=200)
        return salida

    @app.post("/api/catalogo/publicaciones/aplicar")
    def aplicar_publicaciones(body: dict):
        """Manda a MercadoLibre el título y la descripción nuevos.

        Mismo esquema que el cambio de precios: de a pocos, con presupuesto de
        tiempo y devolviendo los que no se llegaron a tocar.
        """
        ids = [int(i) for i in (body or {}).get("ids", [])][:20]
        if not ids:
            raise HTTPException(422, "No hay publicaciones para actualizar.")
        con_titulo = (body or {}).get("titulo", True)
        con_descripcion = (body or {}).get("descripcion", True)
        cli = _client()

        resultados, pendientes = [], []
        arranque = time.monotonic()
        for i, pid in enumerate(ids):
            if i and time.monotonic() - arranque > TOPE_APLICAR_SEG:
                pendientes = ids[i:]
                break
            p = cat.obtener(pid)
            if not p or not (p.ml_item_id or "").strip():
                continue
            fila = {"id": pid, "nombre": p.titulo_ml or p.modelo,
                    "ml_item_id": p.ml_item_id, "titulo": "", "error": "",
                    "titulo_ok": False, "descripcion_ok": False}
            if con_titulo:
                nuevo = cat.titulo_armado(p)
                if nuevo and nuevo != (p.titulo_ml or ""):
                    try:
                        cli.actualizar_titulo(p.ml_item_id, nuevo)
                        # Recién con el título ya cambiado en MercadoLibre se
                        # guarda acá: el catálogo no puede afirmar un título que
                        # la publicación no tiene.
                        cat.actualizar_publicacion(pid, titulo_ml=nuevo)
                        fila["titulo"], fila["titulo_ok"] = nuevo, True
                    except MeliAPIError as e:
                        fila["error"] = _motivo(e)
            if con_descripcion:
                try:
                    texto = cat.descripcion_armada(cat.obtener(pid) or p)
                    if texto.strip():
                        cli.poner_descripcion(p.ml_item_id, texto)
                        fila["descripcion_ok"] = True
                except MeliAPIError as e:
                    # La descripción es independiente del título: que falle una
                    # no puede tapar que la otra sí salió.
                    fila["error"] = (fila["error"] + " · " if fila["error"] else "") \
                        + _motivo(e)
            resultados.append(fila)

        return {"resultados": resultados, "pendientes": pendientes,
                "titulos": sum(1 for r in resultados if r["titulo_ok"]),
                "descripciones": sum(1 for r in resultados if r["descripcion_ok"]),
                "fallas": sum(1 for r in resultados if r["error"]),
                "total": len(resultados)}

    def _buscar_video(p) -> dict:
        """El video de YouTube de este producto, si hay uno que pase el filtro."""
        numero = _set_declarado(p.modelo_fabricante) or \
            numero_de_set(p.modelo or p.titulo_ml or "")
        return buscar_video(p.modelo or p.titulo_ml or "", marca=p.marca,
                            numero_set=numero)

    @app.post("/api/catalogo/lote/videos")
    def lote_videos(body: dict):
        """Busca en YouTube el video de cada producto y lo guarda.

        MercadoLibre solo acepta videos de YouTube, así que el que trae Amazon
        no sirve para publicar: el que sirve es el oficial del fabricante.
        Encontrar pocos es lo esperable —se exige que el canal sea el de la
        marca— y es preferible a poner el video de otro producto.
        """
        if not youtube_configurado():
            raise HTTPException(422, "Falta configurar YOUTUBE_API_KEY para "
                                "buscar videos. Se crea gratis en la consola de "
                                "Google Cloud (YouTube Data API v3).")
        ids = [int(i) for i in (body or {}).get("ids", [])][:50]
        solo_faltantes = (body or {}).get("solo_faltantes", True)

        resultados = []
        for pid in ids:
            p = cat.obtener(pid)
            if not p:
                continue
            if solo_faltantes and (p.video_youtube or "").strip():
                resultados.append({"id": pid, "nombre": p.titulo_ml or p.modelo,
                                   "video_id": p.video_youtube,
                                   "canal": "ya lo tenía", "ok": True})
                continue
            r = _buscar_video(p)
            if r.get("video_id"):
                cat.actualizar_publicacion(pid, video_youtube=r["video_id"])
            resultados.append({"id": pid, "nombre": p.titulo_ml or p.modelo,
                               "video_id": r.get("video_id", ""),
                               "titulo_video": r.get("titulo", ""),
                               "canal": r.get("canal", ""),
                               # Los de canal de confianza conviene mirarlos
                               # antes de publicar: no son del fabricante.
                               "oficial": r.get("oficial"),
                               "ok": bool(r.get("video_id"))})
        return {"resultados": resultados,
                "encontrados": sum(1 for r in resultados if r["ok"]),
                "total": len(resultados)}

    @app.post("/api/catalogo/{pid}/video")
    def buscar_video_de(pid: int):
        """Busca el video de un producto solo."""
        p = _p(pid)
        if not youtube_configurado():
            raise HTTPException(422, "Falta configurar YOUTUBE_API_KEY para "
                                "buscar videos. Se crea gratis en la consola de "
                                "Google Cloud (YouTube Data API v3).")
        r = _buscar_video(p)
        if r.get("video_id"):
            cat.actualizar_publicacion(pid, video_youtube=r["video_id"])
        return {"encontrado": bool(r.get("video_id")), **r}

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

    @app.post("/api/catalogo/verificar")
    def verificar():
        """Contrasta lo que la herramienta cree con lo que hay en MercadoLibre.

        Existe porque el estado local puede mentir: MercadoLibre responde 200 al
        crear un ítem que después queda en revisión, o el ítem se cierra del lado
        de ellos. Acá se pregunta por cada uno y **se corrige el estado local**:
        lo que no está a la venta deja de figurar como publicado.
        """
        cli = _client()
        try:
            en_la_cuenta = set(cli.mis_items())
        except MeliAPIError:
            en_la_cuenta = set()

        a_revisar = [p for p in cat.todos()
                     if p.estado == "publicado" or p.ml_item_id]
        # De a uno era una llamada HTTPS por producto: con medio centenar de
        # publicaciones la petición se cortaba antes de terminar y el panel
        # mostraba un error vacío. El multiget los trae de a 20.
        try:
            items = cli.obtener_varios([p.ml_item_id for p in a_revisar])
        except MeliAPIError as e:
            raise HTTPException(502, f"No se pudo consultar MercadoLibre: {e}")

        revisados, corregidos = [], 0
        for p in a_revisar:
            fila = {"id": p.id, "nombre": p.titulo_ml or p.modelo,
                    "ml_item_id": p.ml_item_id, "estado_local": p.estado}
            if not p.ml_item_id:
                fila["estado_ml"] = "sin id"
            else:
                item = items.get(p.ml_item_id)
                if item is None:
                    # El multiget no lo trajo: no existe en MercadoLibre.
                    fila["estado_ml"] = "no existe"
                else:
                    fila["estado_ml"] = item.get("status", "?")
                    fila["permalink"] = item.get("permalink", "")
                    # Si el permalink no se había guardado al publicar, se
                    # guarda ahora: es el link para abrir la publicación.
                    if fila["permalink"] and fila["permalink"] != p.ml_permalink:
                        cat.actualizar_publicacion(p.id)
                        p.ml_permalink = fila["permalink"]
                        cat._guardar(p)
            fila["en_mis_publicaciones"] = p.ml_item_id in en_la_cuenta
            # El estado local sigue al de MercadoLibre: `active` es publicado y
            # `paused` es pausado —la publicación existe, ML la tiene en pausa
            # mientras revisa—. Es también la vía por la que un pausado vuelve a
            # publicado solo, cuando ML termina de revisarlo.
            esperado = {"active": "publicado",
                        "paused": "pausado"}.get(fila["estado_ml"], "")
            if esperado:
                # Sigue al estado de MercadoLibre. Además de mantenerlo al día,
                # rescata los que quedaron mal de antes: con ítem creado en ML
                # pero figurando acá como no publicados.
                nuevo = esperado
            elif p.estado in ("publicado", "pausado"):
                # No está ni a la venta ni en pausa: acá no puede figurar como
                # publicado. Es el caso del ítem que no existe en la cuenta.
                nuevo = "borrador"
            else:
                nuevo = p.estado
            if nuevo != p.estado:
                cat.cambiar_estado(p.id, nuevo,
                                   f"MercadoLibre lo tiene en "
                                   f"«{fila['estado_ml']}»")
                corregidos += 1
                fila["corregido"] = True
            revisados.append(fila)

        return {"revisados": revisados, "corregidos": corregidos,
                "publicaciones_en_la_cuenta": len(en_la_cuenta),
                "total": len(revisados)}

    @app.get("/codigos/diagnostico", response_class=HTMLResponse)
    def diagnostico_codigos(cantidad: int = 6):
        """Por qué no se encuentra el código, para varios productos de una.

        Muestra el paso a paso —número de set, consulta, qué devolvió el
        catálogo de MercadoLibre— en una sola pantalla. El diagnóstico por
        producto existe, pero mirarlos de a uno no deja ver el patrón.
        """
        pendientes = [p for p in cat.todos()
                      if p.asin and not (p.ml_attributes or {}).get("GTIN")]
        cli = None
        error_cliente = ""
        try:
            cli = _client()
        except HTTPException as e:
            error_cliente = str(e.detail)

        filas = []
        for p in pendientes[:max(1, min(cantidad, 15))]:
            completo = p.modelo or p.titulo_ml or ""
            numero = _set_declarado(p.modelo_fabricante) or numero_de_set(completo)
            consulta = f"{p.marca} {numero}".strip() if numero else \
                f"{p.marca} {_limpiar_para_buscar(completo)}".strip()
            fila = {"nombre": p.titulo_ml or p.modelo, "numero": numero or "—",
                    "consulta": consulta, "resultados": [], "error": "",
                    "elegido": ""}
            if cli is not None and consulta:
                try:
                    fila["resultados"] = cli.buscar_productos_catalogo(consulta,
                                                                       limit=25)
                    # Cuál se queda: es la respuesta que importa. Ver la lista
                    # cruda obliga a adivinar cuál pasaría el filtro.
                    ficha = _ficha_catalogo(completo, cli, p.modelo_fabricante,
                                            p.marca)
                    fila["elegido"] = ficha.get("product_id", "")
                except MeliAPIError as e:
                    fila["error"] = f"{e}"
                except Exception as e:  # noqa: BLE001
                    fila["error"] = f"{type(e).__name__}: {e}"
            filas.append(fila)

        def _esc(s):
            return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))

        bloques = []
        for f in filas:
            res = f["resultados"]
            if f["error"]:
                detalle = f"<div style='color:#c0392b'>ERROR: {_esc(f['error'])}</div>"
            elif not res:
                detalle = "<div style='color:#9a6410'>Sin resultados.</div>"
            else:
                filas_res = []
                for r in res:
                    rid = r.get("id", "")
                    gana = bool(rid) and rid == f["elegido"]
                    li = "<li style='background:#eafaef'>" if gana else "<li>"
                    sello = "<b style='color:#1e7a34'> ← ELEGIDO</b>" if gana else ""
                    filas_res.append(
                        f"{li}<code>{_esc(rid)}</code> — "
                        f"{_esc(r.get('nombre',''))}{sello}</li>")
                detalle = "<ul>" + "".join(filas_res) + "</ul>"
            if not f["error"] and res and not f["elegido"]:
                detalle += ("<div style='color:#9a6410'>Ninguno pasa el filtro: "
                            "ninguno es de la marca. MercadoLibre no tiene este "
                            "producto cargado, o lo tiene con otro nombre.</div>")
            bloques.append(
                f"<div style='border:1px solid #ddd;border-radius:9px;padding:12px;"
                f"margin-bottom:12px'><b>{_esc(f['nombre'])}</b>"
                f"<div>N° de set: <code>{_esc(f['numero'])}</code></div>"
                f"<div>Consulta al catálogo: <code>{_esc(f['consulta'])}</code></div>"
                f"{detalle}</div>")

        cabecera = (f"<p style='color:#c0392b'><b>Sin sesión de MercadoLibre:</b> "
                    f"{_esc(error_cliente)}</p>" if error_cliente else
                    f"<p>Sesión de MercadoLibre activa. Productos sin código: "
                    f"<b>{len(pendientes)}</b>.</p>")
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Diagnóstico de códigos</title>"
            "<div style=\"font-family:system-ui;max-width:820px;margin:32px auto;"
            "padding:0 16px;line-height:1.55\">"
            "<h1>Diagnóstico de códigos</h1>"
            "<p>Qué se le pregunta al catálogo de MercadoLibre por cada producto "
            "y qué contesta. Si acá no aparecen resultados, el problema está en "
            "la búsqueda, no en el producto.</p>"
            + cabecera + "".join(bloques) +
            "<p><a href='/panel'>← Volver al panel</a></p></div>")

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
            "youtube": youtube_configurado(),
            "proxy_amazon": scraperapi_configurada(),
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

    @app.post("/api/catalogo/pausar-todo")
    def pausar_todo(body: dict = None):
        """Freno de mano: saca de venta todo lo publicado, de una.

        Sirve cuando hay que corregir el catálogo con calma —costos mal
        estimados, precios viejos— y seguir vendiendo mientras tanto significa
        vender a pérdida o no poder entregar.

        Se pausa, no se borra: cada publicación conserva su antigüedad y sus
        visitas, y se reactiva cuando vuelva a cerrar.
        """
        cli = _client()
        vivos = [p for p in cat.todos()
                 if p.estado == "publicado" and (p.ml_item_id or "").strip()]
        resultados = []
        arranque = time.monotonic()
        for i, p in enumerate(vivos):
            # Mismo presupuesto que el resto: con 126 publicaciones esto no
            # entra en un solo pedido, así que se devuelve lo que falta y el
            # panel vuelve a llamar.
            if i and time.monotonic() - arranque > TOPE_APLICAR_SEG:
                break
            fila = {"id": p.id, "nombre": p.titulo_ml or p.modelo, "ok": False,
                    "error": ""}
            try:
                cli.pausar(p.ml_item_id)
                cat.cambiar_estado(p.id, "pausado", "Pausado todo desde el panel")
                fila["ok"] = True
            except MeliAPIError as e:
                fila["error"] = _motivo(e)
            resultados.append(fila)
        quedan = len([p for p in cat.todos()
                      if p.estado == "publicado" and (p.ml_item_id or "").strip()])
        return {"resultados": resultados,
                "pausadas": sum(1 for r in resultados if r["ok"]),
                "fallas": sum(1 for r in resultados if r["error"]),
                "quedan": quedan}

    @app.post("/api/catalogo/lote/pausar")
    def lote_pausar(body: dict):
        """Pausa varias publicaciones de una. Se pausa, no se borra: cuando se
        pueda volver a cumplir se reactiva y conserva antigüedad y visitas."""
        ids = (body or {}).get("ids") or []
        cli = _client() if ids else None

        def _uno(p):
            if p.estado == "pausado":
                return {"ya": True}
            if (p.ml_item_id or "").strip():
                cli.pausar(p.ml_item_id)
            return _dict(cat.cambiar_estado(p.id, "pausado",
                                            "Pausada en lote desde el panel"))

        return _en_lote(ids, _uno)

    @app.post("/api/catalogo/lote/reactivar")
    def lote_reactivar(body: dict):
        ids = (body or {}).get("ids") or []
        cli = _client() if ids else None

        def _uno(p):
            if (p.ml_item_id or "").strip():
                cli.reactivar(p.ml_item_id)
            return _dict(cat.cambiar_estado(p.id, "publicado",
                                            "Reactivada en lote desde el panel"))

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
        """Cierre común de las dos vías de publicación.

        MercadoLibre responde 200 aunque el ítem quede sin publicar —en revisión
        o esperando pago del tipo de publicación—, así que hay que mirar el
        `status` que devuelve y no dar por publicado lo que no lo está.
        """
        item_id = creado.get("id", "")
        estado_ml = (creado.get("status") or "").strip()
        if not item_id:
            raise HTTPException(502, "MercadoLibre respondió sin id de "
                                f"publicación: {str(creado)[:200]}")
        # `paused` no es un fallo: el ítem existe y tiene su link. Se intenta
        # activarlo, pero si MercadoLibre lo mantiene en pausa —lo hace mientras
        # revisa las fotos y los datos— **no se toca**: queda registrado como
        # pausado, con su id. Tratarlo como error dejaba el ítem vivo en ML y
        # sin publicar acá, y el siguiente intento creaba un duplicado.
        if estado_ml == "paused":
            try:
                cli.reactivar(item_id)
                estado_ml = (cli.obtener(item_id).get("status") or "").strip()
            except MeliAPIError:
                pass
        try:
            return _registrar(pid, p, creado, cli, item_id, estado_ml)
        except ValueError as e:
            raise HTTPException(502, str(e))

    def _registrar(pid, p, creado, cli, item_id, estado_ml) -> dict:
        # La descripción va en un endpoint aparte, después de crear el ítem. Se
        # arma acá —ficha, condiciones de compra y recién después el detalle de
        # Amazon— porque vendiendo importado la objeción del comprador es el
        # plazo, no el producto: eso tiene que estar antes que nada.
        if item_id:
            texto = cat.descripcion_armada(p)
            if texto.strip():
                try:
                    cli.poner_descripcion(item_id, texto)
                except MeliAPIError:
                    pass  # el ítem ya se publicó; la descripción se reintenta
        return _dict(cat.registrar_publicacion(pid, item_id,
                                               creado.get("permalink", ""),
                                               estado_ml))

    def _intento_catalogo(p, cli, listing_type_id):
        """Publica contra la ficha del catálogo de ML, o None si no se puede.

        Nunca lanza: es una salida de emergencia, así que un fallo acá tiene que
        dejar que el que llama informe el problema original.
        """
        try:
            ficha = _ficha_catalogo(p.modelo or p.titulo_ml or "", cli,
                                    p.modelo_fabricante, p.marca)
        except Exception:  # noqa: BLE001 - la búsqueda de ficha no puede tumbar esto
            return None
        if not ficha.get("product_id"):
            return None
        try:
            return cli.publicar(construir_item_catalogo(
                p, ficha["product_id"], listing_type_id=listing_type_id))
        except MeliAPIError:
            return None

    @app.post("/api/catalogo/{pid}/publicar")
    def publicar(pid: int, body: Borrador):
        p = _p(pid)
        if p.estado != "aprobado":
            raise HTTPException(409, "El producto debe estar APROBADO antes de "
                                "publicar. Revisá la vista previa y aprobalo.")
        # Ya tiene ítem en MercadoLibre: volver a publicar crearía un duplicado.
        # Pasaba cuando ML devolvía un estado distinto de `active` y el producto
        # quedaba figurando como no publicado.
        if (p.ml_item_id or "").strip():
            raise HTTPException(409, f"Este producto ya tiene la publicación "
                                f"{p.ml_item_id} en MercadoLibre. Publicarlo de "
                                f"nuevo crearía un duplicado; si quedó pausada, "
                                f"reactivala desde el listado.")
        pics = body.pictures or p.pictures
        # 1) Datos básicos (título/categoría/precio/foto): no requieren ML.
        faltan = faltantes_para_publicar(p, None, pics)
        if faltan:
            raise HTTPException(422, {"faltantes": faltan})
        cli = _client()  # exige sesión OAuth

        # 2) Publicación propia, que es la que conviene salvo que no haya otra.
        #
        #    En el catálogo de MercadoLibre todos los vendedores comparten la
        #    misma ficha: mismo título, mismas fotos, misma descripción. Lo
        #    único que distingue una oferta de otra es el precio y el tiempo de
        #    entrega, y esas son justamente las dos peores cartas de un
        #    dropshipper que importa: contra alguien con stock local y envío
        #    Full no gana la caja de compra, y queda como oferta secundaria.
        #
        #    Con publicación propia se compite en la búsqueda, no en una caja
        #    de compra: título propio, y la ventaja pasa a ser tener sets que
        #    nadie más tiene en el país.
        #
        #    El catálogo queda como salida de emergencia (paso 5): es la única
        #    vía cuando no se consigue el código de barras, porque ML toma el
        #    GTIN de su propia ficha.
        creado = None
        if cat.publicar_en_catalogo:
            creado = _intento_catalogo(p, cli, body.listing_type_id)
            if creado is not None:
                return _publicado(pid, p, creado, cli)

        # 3) Vía propia: hay que mandar todos los atributos obligatorios de la
        #    categoría (GTIN, cantidad de piezas, etc.).
        obligatorios = []
        try:
            obligatorios = cli.atributos_obligatorios(p.ml_category_id)
        except MeliAPIError:
            pass
        faltan = faltantes_para_publicar(p, obligatorios, pics)
        if faltan:
            # Emergencia: lo que falta casi siempre es el código de barras, y
            # publicando contra la ficha del catálogo lo pone MercadoLibre.
            # Mejor una publicación en el catálogo que ninguna publicación.
            creado = _intento_catalogo(p, cli, body.listing_type_id)
            if creado is not None:
                return _publicado(pid, p, creado, cli)
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
            cuerpo = e.cuerpo
            if "family_name" in str(e.cuerpo):
                try:
                    creado = cli.publicar(_armar("title"))
                except MeliAPIError as e2:
                    creado, cuerpo = None, e2.cuerpo
            else:
                creado = None
            if creado is None:
                # 5) Emergencia: MercadoLibre rechazó la publicación propia. Antes
                #    de darla por perdida se prueba contra su catálogo, que exige
                #    muchos menos datos.
                creado = _intento_catalogo(p, cli, body.listing_type_id)
                if creado is None:
                    raise _rechazo(cuerpo)
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

        def _paso_video(p):
            r = _buscar_video(p)
            if r.get("video_id"):
                cat.actualizar_publicacion(p.id, video_youtube=r["video_id"])
            return r

        if not hasattr(app.state, "agente"):
            # Sin clave de YouTube el paso no se arma: el agente ni lo anuncia.
            app.state.agente = Agente(cat, _paso_preparar, _paso_codigo,
                                      _paso_publicar, _faltan,
                                      buscar_video=(_paso_video
                                                    if youtube_configurado()
                                                    else None))
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

    # ---- agente de revisión: mantiene al día lo ya publicado ---------------

    def _agente_revision():
        """El hermano del agente de publicación, al revés: recorre lo que ya
        está a la venta y verifica que todavía se pueda cumplir."""
        from agente_revision import AgenteRevision

        def _leer(url: str) -> dict:
            # **Sin proxy**: cada página son 5 créditos de ScraperAPI y esta
            # tarea recorre el catálogo entero. Desde un servidor la lectura
            # directa casi siempre la rechaza Amazon; el camino que sí anda es
            # el botón que corre en el navegador del usuario (/revisar).
            return importar_desde_url(
                url, pais=cat.filtro.get("pais_lectura", "us"),
                usar_proxy=cat.revisar_con_proxy)

        def _pausar(p) -> None:
            _client().pausar(p.ml_item_id)
            cat.cambiar_estado(p.id, "pausado",
                               "Amazon se quedó sin stock: pausada por el agente")

        if getattr(app.state, "agente_revision", None) is None:
            app.state.agente_revision = AgenteRevision(
                cat, _leer, _pausar,
                margen_minimo=cat.cfg.umbral_margen_bueno_pct)
        ag = app.state.agente_revision
        ag.margen_minimo = cat.cfg.umbral_margen_bueno_pct
        return ag

    @app.get("/api/revision")
    def revision_estado():
        e = _agente_revision().estado()
        e["con_proxy"] = cat.revisar_con_proxy
        e["proxy_disponible"] = scraperapi_configurada()
        return e

    @app.patch("/api/revision")
    def revision_config(body: dict):
        cuerpo = body or {}
        if "con_proxy" in cuerpo:
            cat.revisar_con_proxy = bool(cuerpo.get("con_proxy"))
        return revision_estado()

    @app.post("/api/revision/reiniciar")
    def revision_reiniciar():
        _agente_revision().reiniciar()
        return revision_estado()

    @app.post("/api/revision/tick")
    def revision_tick():
        """Revisa un producto. El panel lo llama en bucle, como al otro agente."""
        r = _agente_revision().tick()
        r["con_proxy"] = cat.revisar_con_proxy
        return r

    @app.post("/api/revision/reportar")
    def revision_reportar(body: dict):
        """Recibe lo que leyó el navegador del usuario desde Amazon.

        Es el camino que funciona sin gastar créditos: la página la lee su PC,
        con IP hogareña, que es la que Amazon no rechaza. El servidor solo
        guarda y decide.
        """
        filas = (body or {}).get("productos") or []
        if not filas:
            raise HTTPException(422, "No vino ningún producto.")
        cli = None
        resultados = []
        for f in filas[:200]:
            try:
                pid = int(f.get("id"))
            except (TypeError, ValueError):
                continue
            p = cat.obtener(pid)
            if not p:
                continue
            precio = f.get("precio_usd")
            disponible = f.get("disponible")
            if precio is None and disponible is None:
                resultados.append({"id": pid, "nombre": p.titulo_ml or p.modelo,
                                   "leido": False, "pausado": False})
                continue
            p = cat.marcar_revisado(pid, precio, disponible)
            fila = {"id": pid, "nombre": p.titulo_ml or p.modelo, "leido": True,
                    "precio_ahora": precio, "pausado": False,
                    "margen_pct": p.margen_pct}
            if disponible is False and p.estado != "pausado" and p.ml_item_id:
                try:
                    cli = cli or _client()
                    cli.pausar(p.ml_item_id)
                    cat.cambiar_estado(pid, "pausado",
                                       "Amazon se quedó sin stock: pausada")
                    fila["pausado"] = True
                except (MeliAPIError, HTTPException) as e:
                    fila["error"] = str(e)
            resultados.append(fila)
        return {"resultados": resultados,
                "revisados": sum(1 for r in resultados if r["leido"]),
                "pausadas": sum(1 for r in resultados if r["pausado"]),
                "no_leidos": sum(1 for r in resultados if not r["leido"])}

    @app.get("/api/revision/pendientes")
    def revision_pendientes(limite: int = 200):
        """Qué productos hay que mirar y en qué link. Lo usa el botón del
        navegador, que necesita la lista antes de arrancar."""
        return {"productos": [
            {"id": p.id, "asin": p.asin,
             "url": p.amazon_link or (f"https://www.amazon.com/dp/{p.asin}"
                                      if p.asin else ""),
             "nombre": p.titulo_ml or p.modelo}
            for p in cat.a_revisar(limite) if (p.amazon_link or p.asin)]}

    @app.patch("/api/catalogo/{pid}/costo")
    def costo_manual(pid: int, body: dict):
        """Costo real en pesos, puesto a mano. Vacío vuelve a la estimación.

        Dos formas de cargarlo según qué número se tenga:
          - `costo_ars`: el total ya puesto acá. Se usa tal cual.
          - `costo_producto_ars`: lo que sale el producto, sin el envío
            internacional. Se le suma el % que corresponda según tenga o no
            envío gratis de Amazon, así el sugerido cambia con esa marca.
        """
        _p(pid)
        cuerpo = body or {}
        vacio = (None, "", 0)
        base, total = cuerpo.get("costo_producto_ars"), cuerpo.get("costo_ars")
        try:
            # Cargar uno borra el otro: hay un solo costo por producto.
            if base not in vacio:
                return _dict(cat.actualizar_costo_producto(pid, base))
            if total not in vacio:
                return _dict(cat.actualizar_costo_manual(pid, total))
            # Los dos vacíos: se borra lo que se haya pedido borrar y el costo
            # vuelve a estimarse entero.
            p = None
            if "costo_producto_ars" in cuerpo:
                p = cat.actualizar_costo_producto(pid, None)
            if "costo_ars" in cuerpo:
                p = cat.actualizar_costo_manual(pid, None)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if p is None:
            raise HTTPException(422, "Mandá costo_ars o costo_producto_ars.")
        return _dict(p)

    def _envio_gratis_pedido(body: dict):
        """Los tres estados que acepta la marca de envío gratis.

        Vacío/None es "no lo miré", que no es lo mismo que "no tiene envío
        gratis" aunque para la cuenta pesen igual: uno pide que alguien lo mire.
        """
        v = (body or {}).get("envio_gratis")
        if v is None or v == "":
            return None
        if isinstance(v, str):
            if v.lower() in ("true", "1", "si", "sí"):
                return True
            if v.lower() in ("false", "0", "no"):
                return False
            raise HTTPException(422, "envio_gratis tiene que ser true, false o vacío.")
        return bool(v)

    @app.patch("/api/catalogo/{pid}/envio-gratis")
    def envio_gratis(pid: int, body: dict):
        """Si Amazon manda este producto gratis a Argentina.

        Es el dato que más mueve el costo: con envío gratis se estima ~26% del
        precio de Amazon, sin envío gratis ~70%. Cambiarlo vuelve a estimar el
        envío de ese producto y recalcula su precio sugerido.
        """
        _p(pid)
        try:
            return _dict(cat.marcar_envio_gratis(
                pid, _envio_gratis_pedido(body)))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/catalogo/lote/envio-gratis")
    def lote_envio_gratis(body: dict):
        """La misma marca sobre varios productos de una."""
        ids = (body or {}).get("ids") or []
        valor = _envio_gratis_pedido(body)
        return _en_lote(ids, lambda p: cat.marcar_envio_gratis(p.id, valor))

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
