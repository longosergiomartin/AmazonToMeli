"""Datos que se leen del título del producto."""

import pytest

from titulos import numero_de_set, piezas_del_titulo


@pytest.mark.parametrize("titulo, esperado", [
    # Títulos reales del catálogo del usuario.
    ("LEGO Star Wars Death Star - Compactador de basura Diorama 75339 Kit "
     "de construcción (802 piezas)", "75339"),
    ("Juguete para armar Star Wars 75050 B-Wing LEGO", "75050"),
    ("LEGO Icons Dune Atreides Royal Ornithopter 10327", "10327"),
    ("LEGO Casco de conductor AT-AT de Star Wars 75429", "75429"),
    ("LEGO Star Wars: Venganza de los Sith duelo en Mustafar 75269", "75269"),
])
def test_numero_de_set(titulo, esperado):
    assert numero_de_set(titulo) == esperado


@pytest.mark.parametrize("titulo", [
    "Set de construcción sin número",
    "LEGO edición 2024",          # un año no es un set
    "LEGO set de 802 piezas",     # una cantidad tampoco
    "",
])
def test_numero_de_set_no_inventa(titulo):
    assert numero_de_set(titulo) == ""


@pytest.mark.parametrize("titulo, esperado", [
    ("LEGO Star Wars Death Star 75339 Kit de construcción (802 piezas)", "802"),
    ("LEGO Technic Ferrari 42143 (3778 piezas)", "3778"),
    ("Building kit 1329 pieces", "1329"),
    ("Set de 210 bloques", "210"),
])
def test_piezas_del_titulo(titulo, esperado):
    assert piezas_del_titulo(titulo) == esperado


def test_piezas_del_titulo_sin_dato():
    assert piezas_del_titulo("LEGO Star Wars 75339 Kit de construcción") == ""
