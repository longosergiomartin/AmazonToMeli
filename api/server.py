"""
Servidor de la API local (estilo Rainforest, pero tuyo y gratis).

Endpoints:
  GET /                → página de inicio con el bookmarklet e instrucciones
  GET /capture         → recibe una captura del bookmarklet (Amazon o MeLi)
  GET /search?q=...    → busca productos capturados (JSON)
  GET /product/{asin}  → producto + últimos precios (JSON)
  GET /history/{asin}  → histórico de precios (JSON)
  GET /export.csv      → CSV compatible con `python -m arbitraje.cli --csv`
  GET /productos       → todos los productos capturados (JSON)

Correr:  python -m api.server   →  http://localhost:8321
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .storage import Almacen

PUERTO = 8321

# El bookmarklet: JS que corre en la página que estás mirando. En Amazon
# extrae ASIN/título/precio; en MercadoLibre extrae el precio y pregunta a qué
# ASIN corresponde. Después abre /capture en una pestaña nueva.
# __BASE__ se reemplaza por la URL real con la que accediste a la API, así el
# botón apunta al host/puerto correcto (localhost, 127.0.0.1, otro puerto...).
_BOOKMARKLET_TPL = (
    "javascript:(function(){"
    "var B='__BASE__';"
    "var h=location.hostname;"
    "if(h.indexOf('amazon.')>-1){"
    "var m=(location.pathname+location.search).match(/\\/dp\\/([A-Z0-9]{10})/);"
    "var asin=m?m[1]:prompt('No encontre el ASIN en la URL. Pegalo:');"
    "if(!asin)return;"
    "var t=document.getElementById('productTitle');"
    "t=t?t.textContent.trim():document.title;"
    "var p=document.querySelector('#corePrice_feature_div .a-offscreen,.a-price .a-offscreen');"
    "var precio=p?p.textContent.replace(/[^0-9.]/g,''):(prompt('Precio USD:')||'');"
    "var landed=prompt('Total puesto en Argentina (USD), del recuadro \\'Detalles de envio y tarifa\\' (opcional):','')||'';"
    "window.open(B+'/capture?site=amazon&asin='+encodeURIComponent(asin)"
    "+'&titulo='+encodeURIComponent(t.slice(0,150))"
    "+'&precio_usd='+encodeURIComponent(precio)"
    "+'&landed_usd='+encodeURIComponent(landed)"
    "+'&link='+encodeURIComponent(location.origin+location.pathname),'_blank');"
    "}else if(h.indexOf('mercadolibre.com')>-1){"
    "var f=document.querySelector('.andes-money-amount__fraction');"
    "var precio=f?f.textContent.replace(/\\./g,''):(prompt('Precio ARS:')||'');"
    "var asin=prompt('Precio detectado: $'+precio+'. ASIN del producto de Amazon al que corresponde:');"
    "if(!asin)return;"
    "window.open(B+'/capture?site=meli&asin='+encodeURIComponent(asin.trim())"
    "+'&precio_ars='+encodeURIComponent(precio)"
    "+'&link='+encodeURIComponent(location.origin+location.pathname),'_blank');"
    "}else{alert('Usa este boton en una pagina de producto de Amazon o MercadoLibre');}"
    "})();"
)

_PAGINA_INICIO = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>API de Arbitraje</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.5}}
 .btn{{display:inline-block;background:#3483fa;color:#fff;padding:10px 18px;border-radius:8px;
      text-decoration:none;font-weight:600}}
 code{{background:#f0f0f0;padding:2px 6px;border-radius:4px}}
 li{{margin:6px 0}}
</style></head><body>
<h1>🛒 Tu API de arbitraje</h1>
<p>Estado: <strong>funcionando</strong> · {n} producto(s) capturado(s)</p>
<h2>El botón mágico</h2>
<p>Arrastrá este botón a tu <strong>barra de favoritos</strong> (una sola vez):</p>
<p><a class="btn" href="{bm}">➜ Capturar producto</a></p>
<h2>Cómo se usa</h2>
<ol>
 <li>Navegá Amazon como siempre. En la página de un producto, tocá el botón:
     se captura ASIN, título y precio (te pregunta el total puesto en Argentina, opcional).</li>
 <li>En MercadoLibre, en la publicación equivalente, tocá el botón: captura el
     precio y te pregunta el ASIN de Amazon al que corresponde.</li>
 <li>Cada captura queda fechada: se va armando tu <strong>histórico de precios</strong>.</li>
</ol>
<h2>Endpoints</h2>
<ul>
 <li><code>GET /productos</code> — todo lo capturado</li>
 <li><code>GET /search?q=texto</code> — buscar</li>
 <li><code>GET /product/ASIN</code> — un producto con últimos precios</li>
 <li><code>GET /history/ASIN</code> — histórico de precios</li>
 <li><code>GET /export.csv</code> — CSV listo para
     <code>python -m arbitraje.cli --csv export.csv --sin-api</code></li>
</ul>
</body></html>"""

_PAGINA_CAPTURA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Capturado</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;margin-top:80px">
<h1>✅ {msg}</h1><p>Podés cerrar esta pestaña.</p>
<script>setTimeout(function(){{window.close()}},1500);</script>
</body></html>"""


def _num(v: Optional[str]) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def crear_app(db_path: str = "data/arbitraje.db") -> FastAPI:
    app = FastAPI(title="API de arbitraje Amazon→MeLi", version="0.1.0")
    almacen = Almacen(db_path)

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request):
        n = len(almacen.todos())
        # La URL con la que entraste (host y puerto reales) se usa como base
        # del bookmarklet, así siempre apunta adonde corresponde.
        base = str(request.base_url).rstrip("/")
        bm = _BOOKMARKLET_TPL.replace("__BASE__", base).replace('"', "&quot;")
        return _PAGINA_INICIO.format(n=n, bm=bm)

    @app.get("/capture", response_class=HTMLResponse)
    def capture(site: str, asin: str, titulo: str = "",
                precio_usd: Optional[str] = None, landed_usd: Optional[str] = None,
                precio_ars: Optional[str] = None, link: Optional[str] = None):
        asin = asin.strip().upper()
        if site == "amazon":
            almacen.guardar_producto(asin, titulo or asin, link=link)
            almacen.registrar_precio(
                asin, "amazon",
                precio_usd=_num(precio_usd), landed_usd=_num(landed_usd),
            )
            msg = f"Amazon capturado: {asin}"
        elif site == "meli":
            if almacen.producto(asin) is None:
                # Permitimos registrar el precio MeLi aunque el producto de
                # Amazon todavía no se haya capturado (queda pendiente de datos).
                almacen.guardar_producto(asin, asin)
            almacen.registrar_precio(asin, "meli", precio_ars=_num(precio_ars))
            msg = f"Precio de MercadoLibre asociado a {asin}"
        else:
            raise HTTPException(400, "site debe ser 'amazon' o 'meli'")
        return _PAGINA_CAPTURA.format(msg=msg)

    @app.get("/productos")
    def productos():
        return almacen.todos()

    @app.get("/search")
    def search(q: str):
        return almacen.buscar(q)

    @app.get("/product/{asin}")
    def product(asin: str):
        p = almacen.producto(asin.strip().upper())
        if p is None:
            raise HTTPException(404, f"No hay datos capturados para {asin}")
        return p

    @app.get("/history/{asin}")
    def history(asin: str):
        return almacen.historial(asin.strip().upper())

    @app.get("/export.csv", response_class=PlainTextResponse)
    def export_csv():
        filas = almacen.filas_csv()
        buf = io.StringIO()
        campos = ["nombre", "query_meli", "precio_amazon_usd", "peso_kg",
                  "categoria", "arancel_pct", "precio_meli_manual",
                  "precio_landed_usd", "cantidad", "precio_landed_lote_usd",
                  "link_amazon"]
        writer = csv.DictWriter(buf, fieldnames=campos)
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)
        return buf.getvalue()

    return app


if __name__ == "__main__":
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="API local de arbitraje")
    parser.add_argument("--puerto", type=int,
                        default=int(os.environ.get("ARBITRAJE_PUERTO", PUERTO)),
                        help=f"Puerto de escucha (default {PUERTO}; probá 8080 "
                             "si tu red bloquea puertos poco comunes)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host de escucha (default 127.0.0.1, solo tu PC)")
    args = parser.parse_args()

    print(f"API de arbitraje en http://127.0.0.1:{args.puerto}  (Ctrl+C para frenar)")
    uvicorn.run(crear_app(), host=args.host, port=args.puerto)
