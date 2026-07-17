"""Tests del cálculo de neto de venta en MeLi y del evaluador (sin red)."""

from arbitraje.config import Config
from arbitraje.models import Producto
from arbitraje.meli import calcular_neto_venta_meli
from arbitraje.evaluador import evaluar_producto, evaluar_muchos


def test_neto_menor_al_precio_de_venta():
    cfg = Config()
    r = calcular_neto_venta_meli(100000, "electronica", cfg)
    assert r.neto_ars < 100000
    assert set(r.detalle_ars) == {
        "comision", "costo_fijo", "iva_sobre_comision",
        "iibb", "ganancias", "envio_estimado",
    }


def test_costo_fijo_solo_debajo_del_umbral():
    cfg = Config()  # umbral 33000
    barato = calcular_neto_venta_meli(20000, "default", cfg)
    caro = calcular_neto_venta_meli(50000, "default", cfg)
    assert barato.detalle_ars["costo_fijo"] > 0
    assert caro.detalle_ars["costo_fijo"] == 0


def test_categoria_desconocida_usa_default():
    cfg = Config()
    r1 = calcular_neto_venta_meli(50000, "categoria_rara", cfg)
    r2 = calcular_neto_venta_meli(50000, "default", cfg)
    assert r1.neto_ars == r2.neto_ars


def test_evaluar_producto_con_precio_manual_no_usa_api():
    cfg = Config()
    p = Producto(
        nombre="Reloj", query_meli="reloj", precio_amazon_usd=50.0,
        peso_kg=0.2, categoria="electronica", precio_meli_manual=200000,
    )
    # usar_api=False garantiza que no toca la red; con precio manual alcanza.
    op = evaluar_producto(p, regimen="courier", cfg=cfg, usar_api=False)
    assert op is not None
    assert op.precio_venta_ars == 200000
    assert op.margen_ars == round(op.venta.neto_ars - op.costo.total_ars, 2)


def test_evaluar_sin_precio_ni_api_devuelve_none():
    cfg = Config()
    p = Producto(nombre="X", query_meli="x", precio_amazon_usd=50.0)
    assert evaluar_producto(p, cfg=cfg, usar_api=False) is None


def test_evaluar_muchos_ordena_por_margen():
    cfg = Config()
    productos = [
        Producto(nombre="A", query_meli="a", precio_amazon_usd=50,
                 categoria="electronica", precio_meli_manual=120000),
        Producto(nombre="B", query_meli="b", precio_amazon_usd=50,
                 categoria="electronica", precio_meli_manual=400000),
    ]
    ops = evaluar_muchos(productos, regimenes=["courier"], cfg=cfg, usar_api=False)
    assert [o.producto.nombre for o in ops] == ["B", "A"]  # mayor margen primero
