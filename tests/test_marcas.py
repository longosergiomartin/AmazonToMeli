"""Limpieza de la marca que viene del byline de Amazon."""

import pytest

from marcas import elegir_marca, limpiar_marca


@pytest.mark.parametrize("crudo, esperado", [
    # amazon.com responde en inglés: este era el valor que MercadoLibre
    # rechazaba con "Attribute BRAND has an invalid value name".
    ("Visit the LEGO Store", "LEGO"),
    ("Visita la tienda de LEGO", "LEGO"),
    ("Visitá la tienda oficial de LEGO", "LEGO"),
    ("Brand: LEGO", "LEGO"),
    ("Marca: LEGO", "LEGO"),
    ("LEGO Store", "LEGO"),
    ("LEGO Official Store", "LEGO"),
    ("Tienda de Playmobil", "Playmobil"),
    ("  LEGO  ", "LEGO"),
    ("LEGO", "LEGO"),
])
def test_limpiar_marca(crudo, esperado):
    assert limpiar_marca(crudo) == esperado


@pytest.mark.parametrize("basura", ["", "   ", "-", ":", "Visit the Store"])
def test_limpiar_marca_descarta_lo_que_no_es_marca(basura):
    assert limpiar_marca(basura) == ""


def test_limpiar_marca_respeta_marcas_con_varias_palabras():
    assert limpiar_marca("Visit the Mega Bloks Store") == "Mega Bloks"


def test_elegir_marca_usa_el_nombre_exacto_de_mercadolibre():
    """Si ML escribe la marca distinto, gana su forma: así el value_id matchea."""
    permitidas = [{"id": "9155", "name": "LEGO"}, {"id": "1", "name": "Playmobil"}]
    assert elegir_marca("lego", permitidas=permitidas) == "LEGO"
    assert elegir_marca("Visit the LEGO Store", permitidas=permitidas) == "LEGO"


def test_elegir_marca_la_saca_del_titulo_si_el_byline_vino_vacio():
    permitidas = [{"id": "9155", "name": "LEGO"}, {"id": "1", "name": "Playmobil"}]
    assert elegir_marca("", "LEGO Icons Ghostbusters ECTO-1 10274",
                        permitidas=permitidas) == "LEGO"


def test_elegir_marca_prefiere_la_coincidencia_mas_especifica():
    permitidas = [{"id": "1", "name": "LEGO"}, {"id": "2", "name": "LEGO Duplo"}]
    assert elegir_marca("", "LEGO Duplo Mi Primer Tren", permitidas=permitidas) == "LEGO Duplo"


def test_elegir_marca_sin_lista_devuelve_la_limpia():
    assert elegir_marca("Visit the HISEA Store") == "HISEA"


def test_elegir_marca_no_inventa_si_no_hay_de_donde():
    assert elegir_marca("", "Set de construcción genérico",
                        permitidas=[{"id": "1", "name": "LEGO"}]) == ""


def test_elegir_marca_usa_el_titulo_aunque_ml_no_liste_valores():
    """MercadoLibre no siempre devuelve valores para BRAND. Si igual no
    mandamos marca, el ítem se rechaza entero: se usa la del título."""
    assert elegir_marca("", "LEGO Icons Ghostbusters ECTO-1 10274") == "LEGO"
    assert elegir_marca("", "Playmobil City Action 70443") == "Playmobil"


def test_elegir_marca_manda_texto_si_la_marca_no_esta_en_la_lista_de_ml():
    """La lista de ML es de sugerencias: una marca que no figura igual se manda."""
    assert elegir_marca("", "HISEA Waders neopreno",
                        permitidas=[{"id": "1", "name": "LEGO"}]) == "HISEA"


@pytest.mark.parametrize("titulo", [
    "Set de bloques de construcción",
    "Juego de mesa familiar",
    "New arrivals 2024",
    "2352 piezas de construcción",
    "",
])
def test_marca_del_titulo_no_toma_palabras_genericas(titulo):
    from marcas import marca_del_titulo
    assert marca_del_titulo(titulo) == ""


def test_elegir_marca_desconocida_se_manda_igual():
    """Una marca que no está en la lista de ML se manda como texto: la lista
    de valores es de sugerencias, no cierra el universo de marcas."""
    assert elegir_marca("HISEA", permitidas=[{"id": "1", "name": "LEGO"}]) == "HISEA"
