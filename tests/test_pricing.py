"""Tests del precio de venta sugerido (cálculo inverso del margen)."""

import pytest

from arbitraje.config import Config
from arbitraje.meli import calcular_neto_venta_meli
from arbitraje.pricing import precio_sugerido, margen_real_al_precio


def test_precio_sugerido_da_el_margen_pedido():
    cfg = Config()
    costo = 343645.0
    for margen in (0.20, 0.35, 0.50):
        p = precio_sugerido(costo, margen, "default", cfg)
        real = margen_real_al_precio(costo, p, "default", cfg)
        # El margen real al precio sugerido debe coincidir con el pedido.
        assert real["margen_pct"] == pytest.approx(margen * 100, abs=0.2)


def test_precio_sugerido_sube_con_el_margen():
    cfg = Config()
    p20 = precio_sugerido(100000, 0.20, "default", cfg)
    p40 = precio_sugerido(100000, 0.40, "default", cfg)
    assert p40 > p20


def test_categoria_mas_cara_requiere_precio_mas_alto():
    cfg = Config()
    # Electrónica tiene la comisión más alta → para el mismo margen, precio mayor.
    p_elec = precio_sugerido(200000, 0.30, "electronica", cfg)
    p_def = precio_sugerido(200000, 0.30, "default", cfg)
    assert p_elec > p_def


def test_config_imposible_lanza_error():
    cfg = Config()
    # Costos de ML absurdos que se comen todo el precio.
    cfg.meli.costos_ml["default"] = 1.05
    with pytest.raises(ValueError):
        precio_sugerido(100000, 0.30, "default", cfg)


def test_por_debajo_del_tope_el_margen_pedido_es_el_que_queda():
    """La cuenta tiene que cerrar exacta: comisión, envío e IIBB descontados,
    lo que sobra es el margen."""
    cfg = Config()
    costo = 131264.0
    p = precio_sugerido(costo, 0.30, "lego", cfg)
    assert p < cfg.meli.percepcion_iva_desde_ars
    assert margen_real_al_precio(costo, p, "lego", cfg)["margen_pct"] == \
        pytest.approx(30.0, abs=0.1)


def test_si_cruzar_el_tope_cuesta_poco_margen_se_publica_pegado_abajo():
    """Pagar la percepción obliga a subir ~9% el precio. Resignar dos puntos de
    margen y quedarse abajo del tope deja la publicación mucho más competitiva."""
    cfg = Config()
    tope = cfg.meli.percepcion_iva_desde_ars
    # Un costo que deja el precio deseado apenas por encima del tope.
    costo = 460000.0
    p = precio_sugerido(costo, 0.30, "lego", cfg)

    assert p < tope, "se saltó el tope pudiendo quedarse abajo"
    real = margen_real_al_precio(costo, p, "lego", cfg)["margen_pct"]
    assert real >= cfg.margen_piso_pct * 100
    assert real < 30.0, "si diera el margen entero no habría escalón que evitar"


def test_si_quedarse_abajo_del_tope_hunde_el_margen_se_paga_la_percepcion():
    """Hay un punto en que sostener el precio bajo el tope ya no deja negocio.
    Ahí sí conviene saltar y cobrar la percepción."""
    cfg = Config()
    tope = cfg.meli.percepcion_iva_desde_ars
    costo = 900000.0
    p = precio_sugerido(costo, 0.30, "lego", cfg)

    assert p > tope
    assert margen_real_al_precio(costo, p, "lego", cfg)["margen_pct"] == \
        pytest.approx(30.0, abs=0.1)


def test_el_envio_se_suma_al_objetivo_y_no_se_diluye_en_el_porcentaje():
    """Meter el envío dentro de un % lo subestima en los productos baratos, que
    es justo donde más pesa: fue lo que dejó las publicaciones sin cubrirlo."""
    cfg = Config()
    barato, caro = 120000.0, 400000.0
    for costo in (barato, caro):
        p = precio_sugerido(costo, 0.30, "lego", cfg)
        d = calcular_neto_venta_meli(p, "lego", cfg).detalle_ars
        assert d["envio"] == cfg.meli.envio_gratis_ars
        assert margen_real_al_precio(costo, p, "lego", cfg)["margen_pct"] == \
            pytest.approx(30.0, abs=0.1)
