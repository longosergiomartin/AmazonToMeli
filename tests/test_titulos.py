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


def test_parecido_reconoce_el_mismo_producto():
    from titulos import parecido
    ref = "LEGO Ideas Magic of Disney Set #21352 personajes icónicos minifigura"
    assert parecido(ref, "Lego Ideas Magic Of Disney 21352 - 1103 Piezas") >= 0.5
    assert parecido(ref, "Lego Ideas Disney 21352") >= 0.4


def test_parecido_descarta_otro_set_de_la_misma_linea():
    """Dos sets de la misma línea comparten casi todo el título menos el
    número, que es justo lo único que los distingue."""
    from titulos import parecido
    ref = "LEGO Ideas Magic of Disney 21352"
    assert parecido(ref, "Lego Ideas Magic Of Disney 43222") == 0.0


def test_parecido_descarta_un_producto_distinto():
    from titulos import parecido
    assert parecido("LEGO Ideas Magic of Disney 21352",
                    "Lego Star Wars Halcón Milenario 75192") == 0.0


def test_parecido_sin_numeros_compara_solo_palabras():
    from titulos import parecido
    assert parecido("Bosch Taladro percutor profesional",
                    "Bosch Taladro Percutor Profesional 600W") >= 0.9
    assert parecido("Bosch Taladro percutor", "Cafetera Oster") == 0.0


@pytest.mark.parametrize("numero, va", [
    ("21372", True),      # set de LEGO
    ("10295", True),      # set de LEGO
    ("123456", True),     # modelo de 6 dígitos, plausible
    ("6474652", False),   # código interno de Amazon: no identifica nada
    ("65899889", False),
])
def test_titulo_no_agrega_codigos_internos_de_amazon(numero, va):
    """Amazon declara a veces un código propio de 7 dígitos. Pegado al título
    solo ensucia: no le dice nada al comprador ni sirve para buscar."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Ideas La Catrina set de construcción", numero)
    assert t.endswith(numero) is va


# --- estrategia de título para publicar ---------------------------------

def test_corta_la_cola_de_marketing_de_amazon():
    """En 60 caracteres, "Toy for Kids, Boys and Girls Age 8 Plus" se come el
    nombre del set, que es lo que el comprador busca."""
    from titulos import titulo_para_ml
    t = titulo_para_ml(
        "LEGO",
        "LEGO Minecraft The Rabbit Ranch House Farm Set, 21181 Animals Toy for "
        "Kids, Boys and Girls Age 8 Plus with Tamer and Zombie Figures",
        "21181", tipo="Set")
    assert t == "Set LEGO Minecraft The Rabbit Ranch House Farm 21181"
    for basura in ("Kids", "Boys", "Girls", "Age", "Zombie Figures"):
        assert basura not in t


def test_no_termina_en_conector_suelto():
    """Recortar a lo bruto dejaba "...Farm with" y "...Creeper and 2": se lee
    como una frase partida al medio."""
    from titulos import titulo_para_ml
    t = titulo_para_ml(
        "LEGO",
        "LEGO Minecraft The Pig House, 21170 with Alex, Creeper and 2 Pig "
        "Figures, Animal Building Toy, Great Gift for Kids", "21170", tipo="Set")
    assert not t.rstrip().endswith(("with", "and", "de", "para", "the"))
    assert t.endswith("21170")


def test_no_rompe_el_tres_en_uno():
    """"3 in 1" es el nombre del producto, no una frase cortada: sacarle el
    número final lo destruye."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO",
                       "LEGO Minecraft Overworld Adventures 3 in 1 Building Set Pack",
                       "66779", tipo="Set")
    assert "3 in 1" in t


def test_saca_las_palabras_que_repiten_lo_que_ya_dice_set_lego():
    """"Building Kit" son 12 caracteres para decir en inglés lo que ya dice
    "Set LEGO", y nadie lo busca así en Argentina."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Minecraft The Polar Igloo 21142 Building Kit "
                               "(278 Pieces)", "21142", tipo="Set")
    assert "Building Kit" not in t and "Pieces" not in t
    assert "Polar Igloo" in t and t.count("Set") == 1


def test_usa_el_lugar_que_sobra_para_las_piezas():
    """La cantidad de piezas es lo que el comprador compara entre dos sets
    parecidos. Si entra, va."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Minecraft The Polar Igloo", "21142",
                       piezas="278", tipo="Set")
    assert t.endswith("278 Piezas") and len(t) <= 60


def test_las_piezas_nunca_desplazan_al_numero_de_set():
    """Con el título justo, el número manda: sin él la publicación no se
    encuentra por búsqueda exacta."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Star Wars Halcon Milenario Ultimate "
                               "Collector Series Edicion Coleccionista", "75192",
                       piezas="7541", tipo="Set")
    assert len(t) <= 60
    assert t.endswith("75192"), "el número se perdió por meter las piezas"


def test_el_tipo_de_producto_es_opcional():
    """Sin tipo configurado el título arranca con la marca, como antes."""
    from titulos import titulo_para_ml
    assert titulo_para_ml("LEGO", "LEGO Minecraft Crafting Box", "21116") \
        == "LEGO Minecraft Crafting Box 21116"


def test_no_corta_cuando_el_argumento_de_venta_es_el_nombre():
    """Si el patrón cae en las primeras palabras es parte del nombre del
    producto, no una cola de marketing: cortar ahí dejaría el título vacío."""
    from titulos import titulo_para_ml
    t = titulo_para_ml("LEGO", "LEGO Gift Ideas Set Navidad 40604", "40604")
    assert "40604" in t and len(t) > 10
