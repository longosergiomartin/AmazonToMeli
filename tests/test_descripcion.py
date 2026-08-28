"""Tests de la descripción con la que sale la publicación.

Vendiendo importado, la objeción del comprador es el plazo y la desconfianza,
no el producto. La descripción tiene que despejar eso antes que nada.
"""

from descripcion import armar, COMPRA_DEFAULT


def test_las_condiciones_de_compra_van_antes_que_el_detalle_de_amazon():
    t = armar(titulo="Set LEGO Minecraft 21181", detalle="• Nice building toy",
              marca="LEGO", numero_set="21181", dias=25)
    assert t.index("CÓMO ES LA COMPRA") < t.index("SOBRE EL PRODUCTO")


def test_los_dias_de_entrega_salen_de_la_publicacion():
    """Si la descripción dice un plazo y MercadoLibre le promete otro al
    comprador, el reclamo es seguro."""
    t = armar(titulo="Set", dias=30)
    assert "30 días" in t and "{dias}" not in t


def test_la_ficha_pone_los_datos_duros_arriba():
    t = armar(titulo="Set LEGO Minecraft 21181", marca="LEGO",
              numero_set="21181", piezas="340")
    assert "Marca: LEGO" in t
    assert "Número de set: 21181" in t
    assert "Cantidad de piezas: 340" in t


def test_la_ficha_no_inventa_filas_vacias():
    t = armar(titulo="Set", marca="LEGO")
    assert "Marca: LEGO" in t
    assert "Número de set:" not in t and "Cantidad de piezas:" not in t


def test_si_algo_se_recorta_es_el_detalle_y_no_las_condiciones():
    """El límite de MercadoLibre es 5.000 caracteres y los bullets de Amazon
    pueden llenarlo solos. Lo que no puede faltar son las condiciones."""
    t = armar(titulo="Set LEGO", detalle="palabra " * 2000, marca="LEGO",
              dias=25, limite=1200)
    assert len(t) <= 1200
    assert "CÓMO ES LA COMPRA" in t
    assert "25 días" in t


def test_el_texto_de_compra_se_puede_reemplazar():
    """Son condiciones comerciales: las tiene que poder escribir el vendedor."""
    t = armar(titulo="Set", compra="MI TEXTO\n• Retiro en Córdoba.", dias=25)
    assert "MI TEXTO" in t and "Retiro en Córdoba" in t
    assert "CÓMO ES LA COMPRA" not in t


def test_el_texto_propio_tambien_recibe_los_dias():
    t = armar(titulo="Set", compra="Llega en {dias} días.", dias=18)
    assert "Llega en 18 días." in t


def test_sin_detalle_igual_sale_una_descripcion_util():
    """La mayoría de los productos importados no traen descripción larga."""
    t = armar(titulo="Set LEGO Minecraft 21181", marca="LEGO", numero_set="21181")
    assert "Set LEGO Minecraft 21181" in t
    assert COMPRA_DEFAULT.splitlines()[0] in t
    assert "SOBRE EL PRODUCTO" not in t


def test_el_detalle_no_se_corta_a_mitad_de_una_frase():
    largo = "\n".join(f"• Renglón número {i} con texto de relleno" for i in range(200))
    t = armar(titulo="Set", detalle=largo, limite=900)
    assert len(t) <= 900
    assert not t.rstrip().endswith("relle")
