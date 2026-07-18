"""Tests del catálogo y del ciclo de vida de la publicación (sin red)."""

import sqlite3

import pytest

from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from mercadolibre.listing import construir_item, faltantes_para_publicar, vista_previa


@pytest.fixture()
def cat():
    conn = sqlite3.connect(":memory:")
    return Catalogo(conn, cfg=Config())


def _prod(**kw):
    base = dict(
        amazon_link="https://amazon.com/dp/B0TEST", asin="B0TEST",
        marca="HISEA", modelo="Waders neopreno", precio_usd=118.99,
        peso_kg=3.0, costo_envio_usd=84.35, categoria="default",
        margen_deseado=0.35, stock=5,
    )
    base.update(kw)
    return ProductoCatalogo(**base)


def test_alta_calcula_costo_y_precio_sugerido(cat):
    p = cat.agregar(_prod())
    assert p.id is not None
    assert p.costo_total_ars > 0
    assert p.precio_sugerido_ars > p.costo_total_ars  # debe dejar margen
    # El margen real al precio sugerido ~ el deseado (35%).
    assert p.margen_pct == pytest.approx(35.0, abs=1.0)


def test_regimen_landed_usa_total_de_amazon_sin_sumar_impuestos(cat):
    # precio 839.97 + envío+import 530.36 = 1370.33 (Total real de Amazon).
    p = cat.agregar(_prod(regimen="landed", precio_usd=839.97,
                          costo_envio_usd=530.36, peso_kg=13.0))
    tc = cat.cfg.tc_compra()
    assert p.costo_total_ars == round(1370.33 * tc, 2)  # sin aduana extra


def test_landed_mas_barato_que_courier_cuando_courier_dobla_impuesto(cat):
    landed = cat.agregar(_prod(regimen="landed", precio_usd=839.97, costo_envio_usd=530.36))
    courier = cat.agregar(_prod(regimen="courier", precio_usd=839.97, costo_envio_usd=530.36))
    # Courier vuelve a estimar el 50% sobre el excedente → más caro que el Total real.
    assert courier.costo_total_ars > landed.costo_total_ars


def test_alta_registra_historial(cat):
    p = cat.agregar(_prod())
    h = cat.historial(p.id)
    assert h and h[0]["tipo"] == "alta"


def test_actualizar_precio_recalcula_margen_y_loguea(cat):
    p = cat.agregar(_prod())
    p2 = cat.actualizar_precio(p.id, 400000)
    assert p2.precio_publicado_ars == 400000
    # margen recalculado sobre el precio nuevo
    assert p2.margen_pct != 0
    tipos = [h["tipo"] for h in cat.historial(p.id)]
    assert "precio" in tipos


def test_margen_insuficiente_se_detecta(cat):
    p = cat.agregar(_prod())
    # Un precio apenas por encima del costo deja margen bajo → insuficiente.
    p = cat.actualizar_precio(p.id, p.costo_total_ars * 1.05)
    assert cat.margen_insuficiente(p) is True
    # El precio sugerido, en cambio, cumple el umbral.
    p2 = cat.actualizar_precio(p.id, p.precio_sugerido_ars)
    assert cat.margen_insuficiente(p2) is False


def test_stock_y_estado_con_historial(cat):
    p = cat.agregar(_prod())
    cat.actualizar_stock(p.id, 10)
    cat.cambiar_estado(p.id, "aprobado")
    p = cat.obtener(p.id)
    assert p.stock == 10 and p.estado == "aprobado"
    tipos = [h["tipo"] for h in cat.historial(p.id)]
    assert "stock" in tipos and "estado" in tipos


def test_cambiar_estado_invalido(cat):
    p = cat.agregar(_prod())
    with pytest.raises(ValueError):
        cat.cambiar_estado(p.id, "vendido")


def test_registrar_publicacion(cat):
    p = cat.agregar(_prod())
    cat.cambiar_estado(p.id, "aprobado")
    p = cat.registrar_publicacion(p.id, "MLA123456789", "http://articulo.ml/x")
    assert p.estado == "publicado"
    assert p.ml_item_id == "MLA123456789"
    assert p.precio_publicado_ars is not None


def test_construir_item_mapea_marca_y_modelo(cat):
    p = cat.agregar(_prod(titulo_ml="Waders HISEA neopreno con botas",
                          ml_category_id="MLA1234"))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    assert item["category_id"] == "MLA1234"
    assert item["currency_id"] == "ARS"
    assert item["available_quantity"] == 5
    ids = {a["id"]: a["value_name"] for a in item["attributes"]}
    assert ids["BRAND"] == "HISEA" and ids["MODEL"] == "Waders neopreno"


def test_faltantes_para_publicar(cat):
    p = cat.agregar(_prod())  # sin titulo_ml, sin categoria, sin fotos
    faltan = faltantes_para_publicar(p, pictures=None)
    assert any("categoría" in f for f in faltan)
    assert any("foto" in f for f in faltan)
    # Con todo cargado, no falta nada.
    p = cat.agregar(_prod(titulo_ml="X", ml_category_id="MLA1"))
    assert faltantes_para_publicar(p, pictures=["http://img/1.jpg"]) == []


def test_vista_previa_expone_precio_y_margen(cat):
    p = cat.agregar(_prod(titulo_ml="Waders", ml_category_id="MLA1"))
    vp = vista_previa(p, pictures=["http://img/1.jpg"])
    assert vp["precio_ars"] == round(p.precio_sugerido_ars, 2)
    assert vp["marca"] == "HISEA"
