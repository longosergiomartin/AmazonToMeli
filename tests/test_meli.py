"""Tests del cálculo de neto de venta en MeLi y del evaluador (sin red)."""

import pytest

from arbitraje.config import Config
from arbitraje.models import Producto
from arbitraje.meli import calcular_neto_venta_meli
from arbitraje.evaluador import evaluar_producto, evaluar_muchos


def test_neto_menor_al_precio_de_venta():
    cfg = Config()
    r = calcular_neto_venta_meli(100000, "electronica", cfg)
    assert r.neto_ars < 100000
    assert set(r.detalle_ars) == {"costos_ml", "iva", "ganancias", "iibb",
                                  "percepcion_iva", "envio"}


def test_el_envio_no_escala_con_el_precio_pero_la_comision_si():
    """El envío gratis es un monto fijo en pesos. Meterlo dentro de un
    porcentaje se lo come en los productos baratos, que es donde más pesa."""
    cfg = Config()
    d1 = calcular_neto_venta_meli(100000, "default", cfg).detalle_ars
    d2 = calcular_neto_venta_meli(200000, "default", cfg).detalle_ars
    for k in ("costos_ml", "iva", "ganancias", "iibb"):
        assert d2[k] == pytest.approx(d1[k] * 2, abs=0.5)
    assert d2["envio"] == d1["envio"] == cfg.meli.envio_gratis_ars
    # Comisión de LEGO: 14,5% del precio, sin el envío adentro.
    assert d1["costos_ml"] == pytest.approx(100000 * 0.145, abs=1)


def test_la_percepcion_de_iva_es_un_escalon():
    """Recién se paga por encima del tope de ARCA, no es una alícuota que
    corra desde el peso uno."""
    cfg = Config()
    tope = cfg.meli.percepcion_iva_desde_ars
    assert calcular_neto_venta_meli(tope - 1, "default", cfg
                                    ).detalle_ars["percepcion_iva"] == 0
    arriba = calcular_neto_venta_meli(tope + 1000, "default", cfg)
    assert arriba.detalle_ars["percepcion_iva"] == pytest.approx(
        (tope + 1000) * 0.07, abs=1)


def test_monotributo_es_el_default_y_no_paga_iva_ni_ganancias():
    cfg = Config()
    assert cfg.meli.condicion_fiscal == "monotributo"
    d = calcular_neto_venta_meli(100000, "default", cfg).detalle_ars
    assert d["iva"] == 0 and d["ganancias"] == 0
    assert d["iibb"] > 0  # IIBB puede retenerse igual


def test_responsable_inscripto_paga_iva_y_ganancias():
    from dataclasses import replace
    base = Config()
    cfg = replace(base, meli=base.meli.con_condicion_fiscal("responsable_inscripto"))
    d = calcular_neto_venta_meli(100000, "default", cfg).detalle_ars
    assert d["iva"] == pytest.approx(21000, abs=1)
    assert d["ganancias"] == pytest.approx(6000, abs=1)
    # Al RI le queda menos neto que al monotributista al mismo precio.
    assert (calcular_neto_venta_meli(100000, "default", cfg).neto_ars
            < calcular_neto_venta_meli(100000, "default", base).neto_ars)


def test_condicion_fiscal_invalida():
    with pytest.raises(ValueError):
        Config().meli.con_condicion_fiscal("inventada")


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


def test_margen_lote_multiplica_por_cantidad():
    cfg = Config()
    p = Producto(
        nombre="Kit", query_meli="kit", precio_amazon_usd=18.0,
        categoria="default", cantidad=6, precio_landed_lote_usd=300.0,
        precio_meli_manual=80000,
    )
    op = evaluar_producto(p, cfg=cfg, usar_api=False)
    assert op is not None
    assert op.margen_lote_ars == round(op.margen_ars * 6, 2)


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
