"""Tests de la cotización del dólar y del importador de Amazon (sin red real)."""

import arbitraje.cotizacion as cot
from amazon_import import extraer_asin, importar_desde_url
from arbitraje.config import Config


# ---- cotización ----------------------------------------------------------

def test_cotizacion_fallback_sin_red(monkeypatch):
    cot.invalidar_cache()

    def _fallar(*a, **k):
        raise RuntimeError("sin red")
    monkeypatch.setattr(cot.requests, "get", _fallar)

    cfg = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.30)
    c = cot.obtener_cotizaciones(cfg)
    assert c["online"] is False
    assert c["oficial"] == 1000.0
    assert c["tarjeta"] == 1300.0  # oficial + 30%


def test_cotizacion_online(monkeypatch):
    cot.invalidar_cache()

    class _Resp:
        def __init__(self, venta): self._v = venta
        def json(self): return {"venta": self._v}

    def _get(url, timeout=8):
        return _Resp(1050 if "oficial" in url else 1470)
    monkeypatch.setattr(cot.requests, "get", _get)

    c = cot.obtener_cotizaciones(Config())
    assert c["online"] is True and c["oficial"] == 1050 and c["tarjeta"] == 1470


# ---- importador de Amazon ------------------------------------------------

def test_extraer_asin_varios_formatos():
    assert extraer_asin("https://www.amazon.com/-/es/dp/B075SDMMMV/ref=x") == "B075SDMMMV"
    assert extraer_asin("https://amazon.com/gp/product/B0BBHHT8LY") == "B0BBHHT8LY"
    assert extraer_asin("https://amazon.com/algo?x=1&asin=B0CZ1ABCDE&y=2") == "B0CZ1ABCDE"
    assert extraer_asin("https://amazon.com/sin-asin") == ""


def test_importar_url_invalida():
    d = importar_desde_url("no-es-url")
    assert d["ok"] is False and d["asin"] == ""


def test_importar_saca_asin_aunque_falle_la_lectura(monkeypatch):
    import amazon_import
    def _fallar(*a, **k):
        raise amazon_import.requests.RequestException("bloqueado")
    monkeypatch.setattr(amazon_import.requests, "get", _fallar)
    d = importar_desde_url("https://www.amazon.com/dp/B075SDMMMV")
    assert d["asin"] == "B075SDMMMV" and d["ok"] is False
    assert "mano" in d["mensaje"].lower()


def test_importar_parsea_pagina(monkeypatch):
    import amazon_import
    html = ('<span id="productTitle"> LEGO Star Wars Millennium Falcon </span>'
            '<span id="bylineInfo">Visita la tienda de LEGO</span>'
            '"priceAmount":839.97,'
            'Item Weight 28.76 pounds'
            '<div id="feature-bullets"><ul>'
            '<li><span class="a-list-item">7541 piezas de coleccion</span></li>'
            '<li><span class="a-list-item">Incluye minifiguras</span></li>'
            '</ul></div></div>'
            '<img id="landingImage" src="https://m.media-amazon.com/images/I/91abc.jpg">'
            '"hiRes":"https://m.media-amazon.com/images/I/91abc.jpg"')

    class _Resp:
        status_code = 200
        text = html
    monkeypatch.setattr(amazon_import.requests, "get", lambda *a, **k: _Resp())
    d = importar_desde_url("https://www.amazon.com/dp/B075SDMMMV")
    assert d["ok"] is True
    assert "Millennium Falcon" in d["modelo"]
    assert d["marca"] == "LEGO"
    assert d["precio_usd"] == 839.97
    assert d["peso_kg"] == round(28.76 * 0.453592, 2)
    assert "7541 piezas" in d["descripcion"] and "minifiguras" in d["descripcion"]
    assert d["imagenes"] == ["https://m.media-amazon.com/images/I/91abc.jpg"]


def test_detalles_del_producto_traen_marca_y_numero_de_modelo():
    """Lo que arregla la carga en lote: el título es marketing traducido, pero
    la ficha declara marca y número de set con etiqueta."""
    from amazon_import import _parse_detalles, _de_detalles
    from amazon_import import _ETIQUETAS_MODELO, _ETIQUETAS_MARCA
    html = """
    <table id="productDetails_detailBullets_sections1">
      <tr><th>Marca</th><td>LEGO</td></tr>
      <tr><th>Número de modelo del artículo</th><td>75304</td></tr>
      <tr><th>Peso del producto</th><td>1.2 Kilogramos</td></tr>
    </table>"""
    d = _parse_detalles(html)
    assert _de_detalles(d, _ETIQUETAS_MARCA) == "LEGO"
    assert _de_detalles(d, _ETIQUETAS_MODELO) == "75304"


def test_detalles_en_formato_de_vinetas():
    """Amazon usa dos formatos según la página; los dos tienen que andar."""
    from amazon_import import _parse_detalles, _de_detalles, _ETIQUETAS_MODELO
    html = """
    <div id="detailBullets_feature_div"><ul>
      <li><span><span class="a-text-bold">Item model number  :</span>
          <span>75256</span></span></li>
    </ul></div>"""
    assert _de_detalles(_parse_detalles(html), _ETIQUETAS_MODELO) == "75256"


def test_numero_de_modelo_se_limpia():
    from amazon_import import _numero_de_modelo
    assert _numero_de_modelo("LEGO 75304") == "75304"
    assert _numero_de_modelo("75304-1") == "75304"
    assert _numero_de_modelo("  75304  ") == "75304"
    assert _numero_de_modelo("") == ""


def test_importar_prefiere_la_marca_de_la_ficha_sobre_el_byline(monkeypatch):
    """El byline viene con texto ("Visit the LEGO Store") o con HTML del
    scraping; la ficha declara la marca limpia."""
    import amazon_import

    html = """<html><span id="productTitle">Set de construcción Star Wars</span>
      <a id="bylineInfo">Visit the LEGO Store</a>
      <span class="a-offscreen">$59.99</span>
      <table><tr><th>Marca</th><td>LEGO</td></tr>
             <tr><th>Número de modelo del artículo</th><td>75304</td></tr></table>
      </html>"""

    class _Resp:
        status_code, text = 200, html

    monkeypatch.setattr(amazon_import.requests, "get", lambda *a, **k: _Resp())
    d = amazon_import.importar_desde_url("https://www.amazon.com/dp/B0TESTAAAA")
    assert d["marca"] == "LEGO"
    assert d["modelo_fabricante"] == "75304"
