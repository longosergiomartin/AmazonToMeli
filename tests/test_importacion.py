"""Tests de los cálculos de costo de importación (no tocan la red)."""

from arbitraje.config import Config
from arbitraje.models import Producto
from arbitraje.importacion import costo_courier, costo_general, calcular_costo


def _producto(**kw):
    base = dict(nombre="Test", query_meli="test", precio_amazon_usd=100.0, peso_kg=1.0)
    base.update(kw)
    return Producto(**base)


def test_courier_por_debajo_de_franquicia_no_paga_impuesto():
    cfg = Config()  # franquicia 400, tasa 50%, flete 55/kg
    p = _producto(precio_amazon_usd=100.0, peso_kg=1.0)
    r = costo_courier(p, cfg)
    # 100 < 400 => sin impuesto. total = fob(100) + flete(55) = 155 USD
    assert r.detalle_usd["impuesto_50pct"] == 0.0
    assert r.total_usd == 155.0
    # La conversión usa el dólar de compra (oficial + recargo de tarjeta).
    assert r.total_ars == 155.0 * cfg.tc_compra()


def test_courier_sobre_franquicia_paga_50pct_del_excedente():
    cfg = Config()
    p = _producto(precio_amazon_usd=600.0, peso_kg=2.0)
    r = costo_courier(p, cfg)
    # excedente = 600 - 400 = 200 ; impuesto = 100 ; flete = 110
    assert r.detalle_usd["impuesto_50pct"] == 100.0
    assert r.total_usd == 600.0 + 110.0 + 100.0


def test_courier_respeta_franquicia_disponible_parcial():
    cfg = Config()
    p = _producto(precio_amazon_usd=300.0, peso_kg=0.0)
    # Solo quedan 100 USD de franquicia => base imponible 200 => impuesto 100
    r = costo_courier(p, cfg, franquicia_disponible_usd=100.0)
    assert r.detalle_usd["impuesto_50pct"] == 100.0


def test_courier_marca_exceso_de_tope():
    cfg = Config()
    p = _producto(precio_amazon_usd=3500.0, peso_kg=1.0)
    r = costo_courier(p, cfg)
    assert r.detalle_usd["excede_tope_por_envio"] == 1.0


def test_general_incluye_todos_los_componentes():
    cfg = Config()
    p = _producto(precio_amazon_usd=100.0, arancel_pct=0.16)
    r = costo_general(p, cfg)
    for clave in ("cif", "derechos_importacion", "iva", "despachante"):
        assert clave in r.detalle_usd
    # El general debe ser bastante más caro que solo el FOB.
    assert r.total_usd > 100.0


def test_general_mas_caro_que_courier_para_producto_suelto():
    cfg = Config()
    p = _producto(precio_amazon_usd=100.0, peso_kg=0.3)
    assert costo_general(p, cfg).total_usd > costo_courier(p, cfg).total_usd


def test_dispatcher_regimen_invalido():
    import pytest
    with pytest.raises(ValueError):
        calcular_costo(_producto(), regimen="inexistente")


def test_tc_compra_aplica_recargo():
    cfg = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.30)
    assert cfg.tc_compra() == 1300.0
    cfg2 = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.0)
    assert cfg2.tc_compra() == 1000.0


def test_landed_usa_el_total_de_amazon_sin_aduana():
    cfg = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.30)
    p = _producto(precio_amazon_usd=234.59, precio_landed_usd=309.20)
    r = calcular_costo(p, regimen="courier", cfg=cfg)
    assert r.regimen == "landed"
    assert r.total_usd == 309.20
    assert r.total_ars == round(309.20 * 1300.0, 2)  # ignora FOB y aduana


def test_landed_tiene_prioridad_sobre_general():
    cfg = Config()
    p = _producto(precio_amazon_usd=234.59, precio_landed_usd=309.20)
    # Aunque se pida 'general', si hay landed se usa ese dato.
    assert calcular_costo(p, regimen="general", cfg=cfg).regimen == "landed"


def test_landed_lote_reparte_el_costo_entre_unidades():
    cfg = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.0)
    p = _producto(precio_amazon_usd=18.0, cantidad=6, precio_landed_lote_usd=300.0)
    r = calcular_costo(p, cfg=cfg)
    assert r.regimen == "landed"
    assert r.total_usd == 50.0            # 300 / 6 por unidad
    assert r.total_ars == 50.0 * 1000.0
    assert r.detalle_usd["landed_lote"] == 300.0
    assert r.detalle_usd["cantidad"] == 6


def test_landed_por_unidad_con_cantidad_calcula_el_lote():
    cfg = Config(tipo_cambio_oficial=1000.0, recargo_tarjeta_pct=0.0)
    p = _producto(precio_amazon_usd=18.0, cantidad=3, precio_landed_usd=48.0)
    r = calcular_costo(p, cfg=cfg)
    assert r.total_usd == 48.0            # por unidad
    assert r.detalle_usd["landed_lote"] == 144.0  # 48 * 3


# ---- videos -------------------------------------------------------------

def test_saca_los_videos_del_producto():
    """Amazon los deja en el JSON de la página, como .mp4 o como playlist."""
    from amazon_import import _parse_videos

    html = '''
      <script>var obj = {"videoUrl":"https://m.media-amazon.com/vse/uno.mp4",
      "hlsUrl":"https://m.media-amazon.com/vse/dos.m3u8"};</script>
    '''
    r = _parse_videos(html)
    assert "https://m.media-amazon.com/vse/uno.mp4" in r
    assert "https://m.media-amazon.com/vse/dos.m3u8" in r


def test_los_videos_no_traen_cualquier_cosa():
    """Sin videos no se inventa nada, y lo que no es de Amazon no entra."""
    from amazon_import import _parse_videos

    assert _parse_videos("<html>sin videos</html>") == []
    assert _parse_videos('{"url":"https://otrositio.com/x.mp4"}') == []


def test_las_barras_escapadas_del_json_se_deshacen():
    from amazon_import import _parse_videos

    html = r'{"videoUrl":"https:\/\/m.media-amazon.com\/vse\/tres.mp4"}'
    assert _parse_videos(html) == ["https://m.media-amazon.com/vse/tres.mp4"]
