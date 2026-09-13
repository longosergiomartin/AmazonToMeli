"""Vigilancia del precio y el stock en Amazon de lo ya publicado.

Es el agujero que se comió la primera venta: se publica con el precio del día
que se capturó el producto, y para cuando alguien compra Amazon pudo haber
subido el precio o haberse quedado sin stock.
"""

import pytest

from db import conectar
from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from amazon_import import _parse_disponible


@pytest.fixture()
def cat():
    return Catalogo(conectar(":memory:"), cfg=Config())


def _pub(cat, **kw):
    base = dict(asin="B0TEST0001", amazon_link="https://amazon.com/dp/B0TEST0001",
                marca="LEGO", modelo="LEGO Ideas Mineral Collection 21362",
                titulo_ml="Set LEGO Ideas Mineral Collection 21362",
                precio_usd=31.69, regimen="landed", margen_deseado=0.30, stock=1)
    base.update(kw)
    p = cat.agregar(ProductoCatalogo(**base))
    cat.cambiar_estado(p.id, "aprobado")
    return cat.registrar_publicacion(p.id, "MLA100", "http://ml/x")


# ---- leer la disponibilidad de la página --------------------------------

def test_detecta_que_no_hay_stock():
    html = '<div id="availability"><span>Currently unavailable.</span></div>'
    assert _parse_disponible(html) is False


def test_detecta_que_hay_stock():
    html = '<div id="availability"><span>In Stock</span></div>'
    assert _parse_disponible(html) is True


def test_el_boton_de_comprar_alcanza_como_señal():
    assert _parse_disponible('<input id="add-to-cart-button" value="Add">') is True


def test_no_se_confunde_con_out_of_stock_de_otra_parte_de_la_pagina():
    """"Out of stock" aparece en reseñas y en los productos del costado. Leerlo
    de ahí pausaría publicaciones que sí se pueden comprar."""
    html = ('<div id="availability"><span>In Stock</span></div>'
            '<div class="reviews">This was out of stock for months</div>')
    assert _parse_disponible(html) is True


def test_si_no_se_puede_saber_devuelve_none():
    """Distinguir "no hay stock" de "no lo pude leer" es lo que decide si se
    pausa una publicación."""
    assert _parse_disponible("<html><body>otra cosa</body></html>") is None


# ---- guardar lo que se vio ----------------------------------------------

# ---- a quién revisar -----------------------------------------------------

# ---- envío a Argentina ---------------------------------------------------

from amazon_import import _parse_envia_al_exterior as _envia, _parse_vendedor


def test_detecta_que_amazon_no_lo_manda_afuera():
    html = '<div id="deliveryBlock">This item cannot be shipped to your selected delivery location.</div>'
    assert _envia(html) is False


def test_detecta_amazonglobal_como_que_si_manda():
    html = '<div>AmazonGlobal Import Fees Deposit included</div>'
    assert _envia(html) is True


def test_desde_estados_unidos_lo_normal_es_no_saber():
    """La página se lee desde una IP de EE.UU.: ahí Amazon muestra la entrega
    dentro de EE.UU. y no dice nada de Argentina."""
    html = '<div id="deliveryBlockMessage">FREE delivery Tuesday, September 2</div>'
    assert _envia(html) is None


def test_el_vendedor_se_lee_porque_es_la_pista_indirecta():
    """Lo que despacha Amazon suele entrar en AmazonGlobal; lo de un vendedor
    externo, casi nunca."""
    assert _parse_vendedor('<a id="sellerProfileTriggerId">Amazon.com</a>') == "Amazon.com"


def test_solo_se_descarta_lo_que_amazon_dice_que_no_manda():
    """`None` no puede descartar: dejaría afuera casi todo el catálogo, porque
    leyendo desde EE.UU. el resultado normal es no saber."""
    from filtros import acepta
    comun = dict(marca="LEGO", exigir_envio=True)
    assert acepta("LEGO Star Wars 75192", "LEGO", 100.0,
                  envia_al_exterior=False, **comun)[0] is False
    assert acepta("LEGO Star Wars 75192", "LEGO", 100.0,
                  envia_al_exterior=None, **comun)[0] is True
    assert acepta("LEGO Star Wars 75192", "LEGO", 100.0,
                  envia_al_exterior=True, **comun)[0] is True


def test_sin_exigir_envio_no_se_descarta_ni_el_que_no_manda():
    from filtros import acepta
    ok, _ = acepta("LEGO Star Wars 75192", "LEGO", 100.0, marca="LEGO",
                   envia_al_exterior=False, exigir_envio=False)
    assert ok is True


def test_el_pais_de_lectura_solo_acepta_us_o_ar(cat):
    cat.filtro = {"pais_lectura": "ar"}
    assert cat.filtro["pais_lectura"] == "ar"
    with pytest.raises(ValueError):
        cat.filtro = {"pais_lectura": "brasil"}
