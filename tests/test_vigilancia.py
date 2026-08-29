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

def test_un_precio_nuevo_recalcula_el_costo_y_el_margen(cat):
    p = _pub(cat)
    costo_antes, margen_antes = p.costo_total_ars, p.margen_pct

    p2 = cat.marcar_revisado(p.id, precio_usd=53.00, disponible=True)

    assert p2.precio_usd == 53.00
    assert p2.costo_total_ars > costo_antes
    # Mismo precio publicado y más costo: el margen tiene que caer.
    assert p2.margen_pct < margen_antes
    assert p2.revisado_en


def test_sin_stock_queda_registrado(cat):
    p = _pub(cat)
    p2 = cat.marcar_revisado(p.id, precio_usd=None, disponible=False)
    assert p2.disponibilidad == "out_of_stock"
    assert any(h["tipo"] == "stock_amazon" for h in cat.historial(p.id))


def test_no_se_marca_agotado_lo_que_solo_no_se_pudo_leer(cat):
    """Pausar por no haber podido leer la página sacaría de venta un producto
    que sí está disponible."""
    p = _pub(cat)
    p2 = cat.marcar_revisado(p.id, precio_usd=None, disponible=None)
    assert p2.disponibilidad == "in_stock"
    assert p2.revisado_en, "igual se anota que se intentó, para no reintentarlo ya"


def test_un_precio_que_no_se_pudo_leer_no_pisa_el_guardado(cat):
    p = _pub(cat)
    p2 = cat.marcar_revisado(p.id, precio_usd=None, disponible=True)
    assert p2.precio_usd == 31.69


# ---- a quién revisar -----------------------------------------------------

def test_se_revisan_primero_los_que_hace_mas_que_no_se_miran(cat):
    """Revisar el catálogo entero son 5 créditos por producto: con 126 se va
    dos tercios del mes en una pasada. Hay que rotar."""
    a = _pub(cat, asin="B0AAA", amazon_link="https://amazon.com/dp/B0AAA")
    b = _pub(cat, asin="B0BBB", amazon_link="https://amazon.com/dp/B0BBB")
    cat.marcar_revisado(a.id, None, True)          # a queda recién revisado

    orden = [p.id for p in cat.a_revisar(10)]
    assert orden[0] == b.id, "el que nunca se revisó tiene que ir primero"


def test_no_se_revisa_lo_que_no_esta_publicado(cat):
    cat.agregar(ProductoCatalogo(asin="B0BORRADOR", marca="LEGO",
                                 modelo="Sin publicar", precio_usd=20.0))
    _pub(cat)
    assert [p.asin for p in cat.a_revisar(10)] == ["B0TEST0001"]


def test_el_limite_se_respeta(cat):
    for i in range(5):
        _pub(cat, asin=f"B0X{i:05d}", amazon_link=f"https://amazon.com/dp/B0X{i:05d}")
    assert len(cat.a_revisar(3)) == 3


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
