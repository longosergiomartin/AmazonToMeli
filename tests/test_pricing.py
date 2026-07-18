"""Tests del precio de venta sugerido (cálculo inverso del margen)."""

import pytest

from arbitraje.config import Config
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
    # Comisión absurda que se come todo el precio.
    cfg.meli.comisiones["default"].comision_pct = 0.95
    with pytest.raises(ValueError):
        precio_sugerido(100000, 0.30, "default", cfg)
