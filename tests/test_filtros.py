"""Tests del filtro "solo sets LEGO".

Los casos vienen de títulos reales que aparecieron al capturar una búsqueda de
LEGO en Amazon: entre los resultados venían accesorios de terceros y productos
que ni siquiera eran LEGO.
"""

import pytest

from filtros import es_set_lego, filtro_js


# ---- casos reales de una búsqueda de LEGO --------------------------------

def test_acepta_sets_lego_reales():
    aceptados = [
        ("LEGO Icons Ghostbusters ECTO-1 10274 - Kit de automóvil, juego grande "
         "para adultos, idea de regalo para hombres, mujeres", "LEGO", 199.99),
        ("LEGO Star Wars Millennium Falcon 75192 (7541 piezas)", "LEGO", 839.97),
        ("LEGO Disney El Joven Simba El Rey León 43243", "LEGO", 126.00),
        ("LEGO Star Wars Darth Vader Casco 75304", "", 79.99),
    ]
    for titulo, marca, precio in aceptados:
        ok, motivo = es_set_lego(titulo, marca, precio)
        assert ok is True, f"debería aceptar: {titulo[:40]} ({motivo})"


def test_rechaza_accesorios_de_terceros():
    """El caso que motivó el filtro: luces LED 'compatibles con Lego'."""
    ok, motivo = es_set_lego(
        "Juego de luces LED compatibles con Lego Scuderia Ferrari HP Lewis "
        "Hamilton Helmet 43022, kit de luz para modelo de bloques", "", 25.99)
    assert ok is False
    assert "accesorio" in motivo


def test_rechaza_lo_que_no_es_lego():
    """La mesa de rompecabezas que se coló entre los resultados."""
    ok, motivo = es_set_lego(
        "ALL4JIG Mesa de rompecabezas de 1500 piezas con patas y ruedas, mesa "
        "de rompecabezas de madera de 24.92 x 33.58 pulgadas", "ALL4JIG", 89.99)
    assert ok is False


@pytest.mark.parametrize("titulo", [
    "BRIKSMAX Kit de iluminación LED para Lego Millennium Falcon",
    "Vitrina acrílica para LEGO Star Wars UCS",
    "Organizador de almacenamiento compatible con ladrillos LEGO",
    "Mould King Bloques de construcción tipo LEGO 7500 piezas",
    "Soporte de pared para exhibir tu LEGO",
    "Llavero LEGO Star Wars Darth Vader",
    "Libro de instrucciones LEGO ideas",
])
def test_rechaza_accesorios_y_otras_marcas(titulo):
    ok, _ = es_set_lego(titulo, "", 50.0)
    assert ok is False, f"debería rechazar: {titulo}"


def test_rechaza_por_precio_bajo():
    """Polybags y llaveros: no vale la pena importarlos."""
    ok, motivo = es_set_lego("LEGO City Bolsa de policía", "LEGO", 4.99)
    assert ok is False and "precio bajo" in motivo
    # Con el mínimo desactivado, entra.
    ok2, _ = es_set_lego("LEGO City Bolsa de policía", "LEGO", 4.99, precio_min=0)
    assert ok2 is True


def test_no_confunde_palabras_que_contienen_lego():
    ok, _ = es_set_lego("Figura de Legolas El Señor de los Anillos", "", 60.0)
    assert ok is False


def test_funciona_sin_acentos_ni_mayusculas():
    ok, _ = es_set_lego("lego technic FERRARI", "", 100.0)
    assert ok is True
    ok2, motivo = es_set_lego("Vitrina ACRÍLICA para LEGO", "", 100.0)
    assert ok2 is False and "accesorio" in motivo


def test_sin_titulo_ni_marca():
    ok, motivo = es_set_lego("", "", 100.0)
    assert ok is False and "sin título" in motivo


# ---- el filtro que va al bookmarklet -------------------------------------

def test_filtro_js_es_javascript_valido():
    js = filtro_js()
    assert js.startswith("function(t)") and js.rstrip().endswith("}")
    # Tiene que traer las listas de palabras.
    assert "compatible" in js and "lightailing" in js
