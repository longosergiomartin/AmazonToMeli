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


@pytest.mark.parametrize("marca, titulo, set_id, esperado", [
    # Títulos traducidos reales: la marca queda en el medio y el número de set
    # se perdía al recortar a 60 caracteres.
    ("LEGO", "Set de construcción Star Wars de LEGO, Darth Vader, talla única",
     "75304", "LEGO Star Wars Darth Vader talla única 75304"),
    ("LEGO", "Juguete para armar Star Wars 75050 B-Wing LEGO",
     "75050", "LEGO Star Wars B-Wing 75050"),
    # El número va al final aunque el título sea largo: es el dato con el que
    # después se busca el producto en el catálogo de MercadoLibre.
    ("LEGO", "LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 75256 "
             "Kit de construcción (1005 piezas)", "75256",
     "LEGO Star Wars: El ascenso de Skywalker Nave de Kylo 75256"),
])
def test_titulo_para_ml(marca, titulo, set_id, esperado):
    from titulos import titulo_para_ml
    assert titulo_para_ml(marca, titulo, set_id) == esperado


def test_titulo_para_ml_respeta_el_limite():
    from titulos import titulo_para_ml
    largo = "LEGO " + "palabra " * 40
    t = titulo_para_ml("LEGO", largo, "75339")
    assert len(t) <= 60
    assert t.startswith("LEGO ") and t.endswith("75339")


def test_titulo_para_ml_sin_numero_de_set():
    """Sin número declarado no se inventa sufijo: se deja el título como está."""
    from titulos import titulo_para_ml
    assert (titulo_para_ml("LEGO", "LEGO Casco de conductor AT-AT de Star Wars 75429")
            == "LEGO Casco de conductor AT-AT de Star Wars 75429")


def test_titulo_para_ml_no_repite_la_marca():
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Star Wars X-Wing", "75355")
    assert t.count("LEGO") == 1
