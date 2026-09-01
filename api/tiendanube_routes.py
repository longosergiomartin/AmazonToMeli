"""
Rutas de la API para Tiendanube: OAuth, publicación y sincronización.

Van en su propio módulo —y no dentro de `catalogo_routes`— porque son un canal
aparte: el mismo producto puede estar publicado en MercadoLibre, en Tiendanube,
en los dos o en ninguno, y mezclarlas haría más difícil ver cuál es cuál.

Misma disciplina que con MercadoLibre: nada se publica solo. Publicar y
sincronizar son pasos explícitos que dispara el usuario, y todo lo que toca la
tienda de verdad exige sesión activa.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from catalogo import Catalogo, ProductoCatalogo
from tiendanube.oauth import (TiendanubeOAuth, TiendanubeCredenciales,
                              TiendanubeTokenStore)
from tiendanube.client import (TiendanubeClient, TiendanubeAPIError,
                               describir_error)
from tiendanube.listing import (construir_producto, vista_previa,
                                faltantes_para_publicar,
                                precio_para_tiendanube)


def registrar_tiendanube(app: FastAPI, conn, cat: Catalogo) -> None:
    cred = TiendanubeCredenciales.desde_entorno()
    store = TiendanubeTokenStore(conn)
    oauth = TiendanubeOAuth(cred, store)

    def _client() -> TiendanubeClient:
        if not cred.configurado:
            raise HTTPException(400, "Faltan credenciales de Tiendanube "
                                "(TIENDANUBE_CLIENT_ID / TIENDANUBE_CLIENT_SECRET).")
        if not store.hay_sesion():
            raise HTTPException(401, "No hay sesión de Tiendanube. Entrá a "
                                "/oauth/tiendanube/login para autorizar.")
        return TiendanubeClient(token_provider=oauth.access_token_valido,
                                store_provider=oauth.store_id,
                                user_agent=cred.user_agent)

    def _p(pid: int) -> ProductoCatalogo:
        p = cat.obtener(pid)
        if not p:
            raise HTTPException(404, f"Producto {pid} no encontrado")
        return p

    def _motivo(e: TiendanubeAPIError) -> str:
        """El texto de Tiendanube, no una interpretación mía.

        Con MercadoLibre reemplazar el mensaje crudo por una lectura propia
        costó una vuelta entera de diagnóstico a ciegas. Acá va crudo desde el
        principio, sobre todo porque el contrato de esta API todavía no se pudo
        verificar contra una llamada real.
        """
        return describir_error(getattr(e, "cuerpo", None)) or str(e)

    # ---- OAuth -----------------------------------------------------------

    @app.get("/oauth/tiendanube/status")
    def tn_status():
        fila = store.leer()
        # La URL de autorización se devuelve armada para poder VERLA. Si el
        # botón de conectar no lleva a ninguna parte, el problema está acá y no
        # hay forma de saberlo sin mirar qué se está construyendo: Tiendanube
        # pone el App ID en la ruta, no como parámetro, así que un id
        # equivocado da un 404 de su sitio en vez de un error de OAuth.
        url = oauth.url_autorizacion() if cred.configurado else ""
        return {"configurado": cred.configurado,
                "conectado": store.hay_sesion(),
                "store_id": (fila["store_id"] if fila else "") or "",
                "redirect_uri": cred.redirect_uri,
                "user_agent": cred.user_agent,
                "url_autorizacion": url,
                # El App ID de Tiendanube es un número. Si acá viene el "client
                # id" alfanumérico de otra pantalla del portal, la URL de
                # autorización no existe y no hay mensaje que lo explique.
                "client_id_es_numerico": cred.client_id.isdigit(),
                "client_id_largo": len(cred.client_id)}

    @app.get("/oauth/tiendanube/login")
    def tn_login():
        if not cred.configurado:
            raise HTTPException(400, "Configurá TIENDANUBE_CLIENT_ID y "
                                "TIENDANUBE_CLIENT_SECRET antes de conectar.")
        return RedirectResponse(oauth.url_autorizacion())

    @app.get("/oauth/tiendanube/callback", response_class=HTMLResponse)
    def tn_callback(code: Optional[str] = None, error: Optional[str] = None):
        if error or not code:
            return HTMLResponse(
                f"<h1>No se pudo conectar</h1><p>{error or 'sin code'}</p>",
                status_code=400)
        try:
            oauth.intercambiar_codigo(code)
        except RuntimeError as e:
            return HTMLResponse(f"<h1>No se pudo conectar</h1><pre>{e}</pre>",
                                status_code=400)
        return HTMLResponse("<h1>Tiendanube conectada</h1>"
                            "<p>Volvé al <a href='/panel'>panel</a>.</p>")

    @app.post("/oauth/tiendanube/salir")
    def tn_salir():
        store.borrar()
        return {"conectado": False}

    @app.get("/api/tiendanube/probar")
    def tn_probar():
        """Una llamada mínima a la tienda, para saber si de verdad anda.

        Los tres errores típicos de esta API —el header de auth se llama
        `Authentication` y no `Authorization`, falta el `User-Agent`, o el id de
        tienda está mal— dan todos el mismo 401 opaco desde cualquier endpoint.
        Esto los separa antes de intentar publicar 126 productos.
        """
        cli = _client()
        try:
            return cli.probar()
        except TiendanubeAPIError as e:
            raise HTTPException(502, {"error": _motivo(e), "status": e.status})

    # ---- cuánto se corrige el precio para la tienda propia ----------------

    @app.get("/api/tiendanube/config")
    def tn_config():
        return {"ajuste_pct": cat.tn_ajuste_pct,
                "publicados": len(cat.en_tiendanube())}

    @app.post("/api/tiendanube/config")
    def tn_config_set(body: dict):
        if "ajuste_pct" in (body or {}):
            try:
                cat.tn_ajuste_pct = (body or {}).get("ajuste_pct")
            except ValueError as e:
                raise HTTPException(422, str(e))
        return tn_config()

    # ---- publicar --------------------------------------------------------

    @app.get("/api/tiendanube/{pid}/previa")
    def tn_previa(pid: int):
        """Qué se va a publicar y a qué precio, sin tocar la tienda."""
        return vista_previa(_p(pid), cat.tn_ajuste_pct)

    def _publicar_uno(p: ProductoCatalogo, cli: TiendanubeClient) -> dict:
        if (p.tn_product_id or "").strip():
            raise HTTPException(409, f"Ya está publicado en Tiendanube "
                                     f"({p.tn_product_id}). Publicarlo de nuevo "
                                     f"crearía un duplicado; usá sincronizar.")
        faltan = faltantes_para_publicar(p, cat.tn_ajuste_pct)
        if faltan:
            raise HTTPException(422, {"faltantes": faltan})
        payload = construir_producto(p, cat.tn_ajuste_pct)
        try:
            creado = cli.crear_producto(payload)
        except TiendanubeAPIError as e:
            raise HTTPException(502, _motivo(e))
        variantes = creado.get("variants") or []
        actualizado = cat.registrar_tiendanube(
            p.id, creado.get("id"),
            (variantes[0].get("id") if variantes else ""),
            creado.get("canonical_url") or creado.get("permalink") or "")
        return {"id": p.id, "tn_product_id": actualizado.tn_product_id,
                "tn_permalink": actualizado.tn_permalink,
                "precio_ars": precio_para_tiendanube(p, cat.tn_ajuste_pct)}

    @app.post("/api/tiendanube/lote/publicar")
    def tn_lote_publicar(body: dict):
        """Publica varios. Como en MercadoLibre, de a pocos por llamada: cada
        producto es un viaje a la tienda y todos juntos darían una petición que
        el servidor corta a mitad de camino."""
        ids = [int(i) for i in (body or {}).get("ids", [])][:20]
        if not ids:
            raise HTTPException(422, "No hay productos para publicar.")
        cli = _client()
        resultados = []
        for pid in ids:
            p = cat.obtener(pid)
            if not p:
                continue
            nombre = (p.titulo_ml or p.modelo or p.asin or str(pid))[:60]
            try:
                fila = _publicar_uno(p, cli)
                resultados.append({**fila, "nombre": nombre, "ok": True})
            except HTTPException as e:
                detalle = e.detail
                if isinstance(detalle, dict) and detalle.get("faltantes"):
                    detalle = "falta " + ", ".join(detalle["faltantes"])
                resultados.append({"id": pid, "nombre": nombre, "ok": False,
                                   "error": str(detalle)})
        return {"resultados": resultados,
                "publicados": sum(1 for r in resultados if r["ok"]),
                "fallas": sum(1 for r in resultados if not r["ok"])}

    @app.post("/api/tiendanube/{pid}/publicar")
    def tn_publicar(pid: int):
        return _publicar_uno(_p(pid), _client())

    # ---- sincronizar precio y stock --------------------------------------

    def _sincronizar_uno(p: ProductoCatalogo, cli: TiendanubeClient) -> dict:
        """Lleva a Tiendanube el precio y el stock que la herramienta calcula hoy.

        El precio sale del sugerido de MercadoLibre corregido por el ajuste, así
        que arreglar un costo acá se refleja en los dos canales sin reeditar
        nada a mano. Y si el producto quedó pausado —Amazon sin stock, costo mal
        calculado— también se despublica allá: seguir vendiéndolo en la tienda
        propia es el mismo problema en otro canal.
        """
        if not (p.tn_product_id or "").strip():
            raise HTTPException(409, "Este producto no está publicado en Tiendanube.")
        precio = precio_para_tiendanube(p, cat.tn_ajuste_pct)
        if precio <= 0:
            raise HTTPException(422, "El precio calculado dio cero: revisá el costo.")
        activo = p.estado == "publicado"
        try:
            if (p.tn_variant_id or "").strip():
                cli.actualizar_variante(p.tn_product_id, p.tn_variant_id,
                                        precio=precio, stock=p.stock)
            # Sin id de variante no se puede tocar precio ni stock; se dice, no
            # se hace de cuenta que se sincronizó.
            cli.publicar(p.tn_product_id, activo)
        except TiendanubeAPIError as e:
            raise HTTPException(502, _motivo(e))
        return {"id": p.id, "precio_ars": precio, "stock": p.stock,
                "publicado": activo,
                "sin_variante": not (p.tn_variant_id or "").strip()}

    @app.post("/api/tiendanube/lote/sincronizar")
    def tn_lote_sincronizar(body: dict):
        """Empuja precio y stock de varios. Sin `ids`, de todo lo que esté
        publicado en la tienda."""
        cuerpo = body or {}
        pedidos = [int(i) for i in cuerpo.get("ids", [])]
        productos = ([p for p in cat.en_tiendanube() if p.id in set(pedidos)]
                     if pedidos else cat.en_tiendanube())
        productos = productos[:20]
        if not productos:
            return {"resultados": [], "sincronizados": 0, "fallas": 0}
        cli = _client()
        resultados = []
        for p in productos:
            nombre = (p.titulo_ml or p.modelo or p.asin or str(p.id))[:60]
            try:
                fila = _sincronizar_uno(p, cli)
                resultados.append({**fila, "nombre": nombre, "ok": True})
            except HTTPException as e:
                resultados.append({"id": p.id, "nombre": nombre, "ok": False,
                                   "error": str(e.detail)})
        return {"resultados": resultados,
                "sincronizados": sum(1 for r in resultados if r["ok"]),
                "fallas": sum(1 for r in resultados if not r["ok"])}

    @app.post("/api/tiendanube/{pid}/sincronizar")
    def tn_sincronizar(pid: int):
        return _sincronizar_uno(_p(pid), _client())

    # ---- soltar el vínculo -----------------------------------------------

    @app.post("/api/tiendanube/{pid}/olvidar")
    def tn_olvidar(pid: int):
        """Suelta el vínculo sin tocar la tienda: el producto sigue allá, pero
        la herramienta deja de considerarlo suyo."""
        _p(pid)
        return {"id": pid, "olvidado": True,
                "producto": cat.olvidar_tiendanube(pid).tn_product_id}
