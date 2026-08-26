"""Tests del catálogo y del ciclo de vida de la publicación (sin red)."""


import pytest

from db import conectar

from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from mercadolibre.listing import construir_item, faltantes_para_publicar, vista_previa


@pytest.fixture()
def cat():
    conn = conectar(":memory:")
    return Catalogo(conn, cfg=Config())


def _prod(**kw):
    base = dict(
        amazon_link="https://amazon.com/dp/B0TEST", asin="B0TEST",
        marca="HISEA", modelo="Waders neopreno", precio_usd=118.99,
        peso_kg=3.0, costo_envio_usd=84.35, categoria="default",
        margen_deseado=0.35, stock=5,
    )
    base.update(kw)
    return ProductoCatalogo(**base)


def test_alta_calcula_costo_y_precio_sugerido(cat):
    p = cat.agregar(_prod())
    assert p.id is not None
    assert p.costo_total_ars > 0
    assert p.precio_sugerido_ars > p.costo_total_ars  # debe dejar margen
    # El margen real al precio sugerido ~ el deseado (35%).
    assert p.margen_pct == pytest.approx(35.0, abs=1.0)


def test_envio_import_se_estima_como_26pct_si_no_se_carga(cat):
    # Sin costo de envío cargado: se estima 26% del precio de Amazon.
    p = cat.agregar(_prod(precio_usd=126.0, costo_envio_usd=0.0, regimen="landed"))
    assert p.costo_envio_usd == pytest.approx(126.0 * 0.26, abs=0.01)
    tc = cat.cfg.tc_compra()
    assert p.costo_total_ars == pytest.approx((126.0 * 1.26) * tc, abs=1)


def test_envio_import_cargado_a_mano_tiene_prioridad(cat):
    # Con el Total real del checkout, no se pisa con la estimación.
    p = cat.agregar(_prod(precio_usd=126.0, costo_envio_usd=34.36, regimen="landed"))
    assert p.costo_envio_usd == 34.36


def test_comparacion_dolar_oficial_vs_tarjeta():
    conn = conectar(":memory:")
    c = Catalogo(conn, cfg=Config(), cotizacion={"oficial": 1000.0, "tarjeta": 1300.0})
    p = c.agregar(_prod(regimen="landed", precio_usd=839.97, costo_envio_usd=530.36))
    comp = c.comparacion_dolar(p)
    assert set(comp) == {"oficial", "tarjeta"}
    # El costo al oficial es más barato que al tarjeta, en proporción 1000/1300.
    assert comp["oficial"]["costo_ars"] < comp["tarjeta"]["costo_ars"]
    assert comp["oficial"]["margen_pct"] > comp["tarjeta"]["margen_pct"]
    # El costo del producto se calculó con el dólar tarjeta (1300).
    assert p.costo_total_ars == round(1370.33 * 1300.0, 2)


def test_regimen_landed_usa_total_de_amazon_sin_sumar_impuestos(cat):
    # precio 839.97 + envío+import 530.36 = 1370.33 (Total real de Amazon).
    p = cat.agregar(_prod(regimen="landed", precio_usd=839.97,
                          costo_envio_usd=530.36, peso_kg=13.0))
    tc = cat.cfg.tc_compra()
    assert p.costo_total_ars == round(1370.33 * tc, 2)  # sin aduana extra


def test_landed_mas_barato_que_courier_cuando_courier_dobla_impuesto(cat):
    landed = cat.agregar(_prod(regimen="landed", precio_usd=839.97, costo_envio_usd=530.36))
    courier = cat.agregar(_prod(regimen="courier", precio_usd=839.97, costo_envio_usd=530.36))
    # Courier vuelve a estimar el 50% sobre el excedente → más caro que el Total real.
    assert courier.costo_total_ars > landed.costo_total_ars


def test_alta_registra_historial(cat):
    p = cat.agregar(_prod())
    h = cat.historial(p.id)
    assert h and h[0]["tipo"] == "alta"


def test_actualizar_precio_recalcula_margen_y_loguea(cat):
    p = cat.agregar(_prod())
    p2 = cat.actualizar_precio(p.id, 400000)
    assert p2.precio_publicado_ars == 400000
    # margen recalculado sobre el precio nuevo
    assert p2.margen_pct != 0
    tipos = [h["tipo"] for h in cat.historial(p.id)]
    assert "precio" in tipos


def test_margen_insuficiente_se_detecta(cat):
    p = cat.agregar(_prod())
    # Un precio apenas por encima del costo deja margen bajo → insuficiente.
    p = cat.actualizar_precio(p.id, p.costo_total_ars * 1.05)
    assert cat.margen_insuficiente(p) is True
    # El precio sugerido, en cambio, cumple el umbral.
    p2 = cat.actualizar_precio(p.id, p.precio_sugerido_ars)
    assert cat.margen_insuficiente(p2) is False


def test_stock_y_estado_con_historial(cat):
    p = cat.agregar(_prod())
    cat.actualizar_stock(p.id, 10)
    cat.cambiar_estado(p.id, "aprobado")
    p = cat.obtener(p.id)
    assert p.stock == 10 and p.estado == "aprobado"
    tipos = [h["tipo"] for h in cat.historial(p.id)]
    assert "stock" in tipos and "estado" in tipos


def test_cambiar_estado_invalido(cat):
    p = cat.agregar(_prod())
    with pytest.raises(ValueError):
        cat.cambiar_estado(p.id, "vendido")


def test_registrar_publicacion(cat):
    p = cat.agregar(_prod())
    cat.cambiar_estado(p.id, "aprobado")
    p = cat.registrar_publicacion(p.id, "MLA123456789", "http://articulo.ml/x")
    assert p.estado == "publicado"
    assert p.ml_item_id == "MLA123456789"
    assert p.precio_publicado_ars is not None


def test_descripcion_persiste_y_se_edita(cat):
    p = cat.agregar(_prod(descripcion="Descripción de Amazon"))
    assert cat.obtener(p.id).descripcion == "Descripción de Amazon"
    p2 = cat.actualizar_publicacion(p.id, descripcion="Editada")
    assert cat.obtener(p.id).descripcion == "Editada"


def test_item_manda_family_name_y_no_title(cat):
    """MercadoLibre migró de `title` a `family_name` y NO acepta los dos:
    manda uno u otro, nunca ambos."""
    p = cat.agregar(_prod(titulo_ml="LEGO Icons Ghostbusters ECTO-1 10274",
                          ml_category_id="MLA1157"))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    assert item["family_name"] == "LEGO Icons Ghostbusters ECTO-1 10274"
    assert "title" not in item


def test_item_puede_usar_title_para_categorias_viejas(cat):
    p = cat.agregar(_prod(titulo_ml="LEGO Star Wars 75192", ml_category_id="MLA1"))
    item = construir_item(p, pictures=["http://img/1.jpg"], campo_titulo="title")
    assert item["title"] == "LEGO Star Wars 75192"
    assert "family_name" not in item


def test_family_name_respeta_el_limite_de_titulo(cat):
    largo = "LEGO " + "x" * 120
    p = cat.agregar(_prod(titulo_ml=largo, ml_category_id="MLA1"))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    assert len(item["family_name"]) <= 60


def test_item_incluye_dias_de_preparacion(cat):
    p = cat.agregar(_prod(titulo_ml="Waders", ml_category_id="MLA1", dias_preparacion=25))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    terms = {t["id"]: t["value_name"] for t in item.get("sale_terms", [])}
    assert terms.get("MANUFACTURING_TIME") == "25 días"


def test_dias_preparacion_cero_no_agrega_sale_term(cat):
    p = cat.agregar(_prod(titulo_ml="Waders", ml_category_id="MLA1", dias_preparacion=0))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    assert "sale_terms" not in item


def test_editar_dias_preparacion_persiste(cat):
    p = cat.agregar(_prod())
    p2 = cat.actualizar_publicacion(p.id, dias_preparacion=30)
    assert p2.dias_preparacion == 30
    assert cat.obtener(p.id).dias_preparacion == 30


def test_valores_por_defecto_de_atributos_administrativos():
    from mercadolibre.listing import valor_por_defecto
    # Elige de la lista de valores permitidos por MercadoLibre.
    assert valor_por_defecto({"id": "IVA", "name": "IVA",
                              "values": ["0 %", "10.5 %", "21 %"]}) == "21 %"
    assert valor_por_defecto({"id": "INTERNAL_TAX", "name": "Impuesto interno",
                              "values": ["0 %", "5 %"]}) == "0 %"
    assert valor_por_defecto({"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío",
                              "values": ["Es un kit", "Otra razón"]}) == "Otra razón"
    # Sin lista de valores, usa el default razonable.
    assert valor_por_defecto({"id": "IVA", "name": "IVA", "values": []}) == "21 %"
    # Atributos normales no tienen default.
    assert valor_por_defecto({"id": "COLOR", "name": "Color", "values": ["Rojo"]}) == ""


def test_motivo_gtin_vacio_no_se_manda_si_hay_gtin(cat):
    p = cat.agregar(_prod(titulo_ml="Lego", ml_category_id="MLA1",
                          ml_attributes={"GTIN": "5702016914498",
                                         "EMPTY_GTIN_REASON": "Otra razón",
                                         "IVA": "21 %"}))
    ids = {a["id"] for a in construir_item(p, pictures=["http://img/1.jpg"])["attributes"]}
    assert "GTIN" in ids and "IVA" in ids
    assert "EMPTY_GTIN_REASON" not in ids  # contradictorio con un GTIN cargado


def test_motivo_gtin_vacio_se_manda_si_no_hay_gtin(cat):
    p = cat.agregar(_prod(titulo_ml="Lego", ml_category_id="MLA1",
                          ml_attributes={"EMPTY_GTIN_REASON": "Otra razón"}))
    ids = {a["id"] for a in construir_item(p, pictures=["http://img/1.jpg"])["attributes"]}
    assert "EMPTY_GTIN_REASON" in ids


def test_item_limpia_la_marca_que_trae_amazon(cat):
    """Amazon guarda "Visit the LEGO Store" en el byline; mandarlo tal cual hace
    que ML rechace el ítem con "Attribute BRAND has an invalid value name"."""
    p = cat.agregar(_prod(marca="Visit the LEGO Store", titulo_ml="LEGO Icons ECTO-1",
                          ml_category_id="MLA1157"))
    ids = {a["id"]: a.get("value_name") for a in
           construir_item(p, pictures=["http://img/1.jpg"])["attributes"]}
    assert ids["BRAND"] == "LEGO"


def test_item_manda_value_id_cuando_ml_dice_que_valores_acepta(cat):
    p = cat.agregar(_prod(marca="Visit the LEGO Store", titulo_ml="LEGO Icons ECTO-1",
                          ml_category_id="MLA1157"))
    item = construir_item(p, pictures=["http://img/1.jpg"],
                          valores_permitidos={"BRAND": [{"id": "9155", "name": "LEGO"}]})
    brand = next(a for a in item["attributes"] if a["id"] == "BRAND")
    assert brand["value_id"] == "9155"
    assert "value_name" not in brand  # ML valida el id, el texto sobra


def test_item_saca_la_marca_del_titulo_si_amazon_no_la_trajo(cat):
    p = cat.agregar(_prod(marca="", titulo_ml="LEGO Star Wars X-Wing 75355",
                          ml_category_id="MLA1157"))
    item = construir_item(p, pictures=["http://img/1.jpg"],
                          valores_permitidos={"BRAND": [{"id": "9155", "name": "LEGO"}]})
    brand = next(a for a in item["attributes"] if a["id"] == "BRAND")
    assert brand["value_id"] == "9155"


def test_item_sin_marca_usable_no_manda_brand_vacio(cat):
    p = cat.agregar(_prod(marca="", titulo_ml="Set de bloques", ml_category_id="MLA1157"))
    ids = {a["id"] for a in construir_item(p, pictures=["http://img/1.jpg"])["attributes"]}
    assert "BRAND" not in ids


def test_faltantes_detecta_marca_sucia_pero_recuperable(cat):
    obligatorios = [{"id": "BRAND", "name": "Marca"}]
    p = cat.agregar(_prod(marca="Visit the LEGO Store", titulo_ml="X",
                          ml_category_id="MLA1157"))
    assert faltantes_para_publicar(p, obligatorios, ["http://img/1.jpg"]) == []
    # En cambio, un byline sin marca adentro sí falta.
    p2 = cat.agregar(_prod(marca="Visit the Store", titulo_ml="X",
                           ml_category_id="MLA1157"))
    assert any("Marca" in f for f in
               faltantes_para_publicar(p2, obligatorios, ["http://img/1.jpg"]))


def test_limpiar_marcas_arregla_los_productos_ya_guardados(cat):
    """Los ~40 productos importados antes del arreglo tienen la marca sucia."""
    p = cat.agregar(_prod(marca="Visit the LEGO Store"))
    limpio = cat.agregar(_prod(marca="LEGO"))
    assert cat.limpiar_marcas() == 1          # solo el sucio
    assert cat.obtener(p.id).marca == "LEGO"
    assert cat.obtener(limpio.id).marca == "LEGO"
    assert cat.limpiar_marcas() == 0          # idempotente
    assert any(h["tipo"] == "marca" for h in cat.historial(p.id))


def test_construir_item_mapea_marca_y_modelo(cat):
    p = cat.agregar(_prod(titulo_ml="Waders HISEA neopreno con botas",
                          ml_category_id="MLA1234"))
    item = construir_item(p, pictures=["http://img/1.jpg"])
    assert item["category_id"] == "MLA1234"
    assert item["currency_id"] == "ARS"
    assert item["available_quantity"] == 5
    ids = {a["id"]: a["value_name"] for a in item["attributes"]}
    assert ids["BRAND"] == "HISEA" and ids["MODEL"] == "Waders neopreno"


def test_faltantes_para_publicar(cat):
    p = cat.agregar(_prod())  # sin titulo_ml, sin categoria, sin fotos
    faltan = faltantes_para_publicar(p, pictures=None)
    assert any("categoría" in f for f in faltan)
    assert any("foto" in f for f in faltan)
    # Con todo cargado, no falta nada.
    p = cat.agregar(_prod(titulo_ml="X", ml_category_id="MLA1"))
    assert faltantes_para_publicar(p, pictures=["http://img/1.jpg"]) == []


def test_vista_previa_expone_precio_y_margen(cat):
    p = cat.agregar(_prod(titulo_ml="Waders", ml_category_id="MLA1"))
    vp = vista_previa(p, pictures=["http://img/1.jpg"])
    assert vp["precio_ars"] == round(p.precio_sugerido_ars, 2)
    assert vp["marca"] == "HISEA"


def test_sin_gtin_pero_con_motivo_declarado_se_puede_publicar(cat):
    """El código de barras de los sets no siempre se consigue. MercadoLibre
    contempla el caso: declarando el motivo de GTIN vacío deja de exigirlo.
    Sin esto, los 72 productos del lote quedaban trabados pidiendo un dato
    que no existe."""
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"}]
    p = cat.agregar(_prod(titulo_ml="LEGO Star Wars 75339", ml_category_id="MLA1157",
                          ml_attributes={"EMPTY_GTIN_REASON": "Otra razón"}))
    assert faltantes_para_publicar(p, obligatorios, ["http://img/1.jpg"]) == []


def test_sin_gtin_y_sin_motivo_sigue_faltando(cat):
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"}]
    p = cat.agregar(_prod(titulo_ml="LEGO Star Wars 75339", ml_category_id="MLA1157"))
    faltan = faltantes_para_publicar(p, obligatorios, ["http://img/1.jpg"])
    assert any("Código universal" in f for f in faltan)


def test_con_gtin_no_hace_falta_el_motivo(cat):
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"}]
    p = cat.agregar(_prod(titulo_ml="LEGO", ml_category_id="MLA1157",
                          ml_attributes={"GTIN": "5702016914498"}))
    assert faltantes_para_publicar(p, obligatorios, ["http://img/1.jpg"]) == []


def test_limpiar_titulos_saca_el_codigo_interno(cat):
    """Los productos cargados antes del arreglo quedaron con el código de 7
    dígitos pegado al final del título."""
    p = cat.agregar(_prod(marca="LEGO",
                          modelo="LEGO Ideas La Catrina 21372 set de construcción",
                          modelo_fabricante="6589589",
                          titulo_ml="LEGO Ideas La Catrina 21372 6589589"))
    assert cat.limpiar_titulos() == 1
    nuevo = cat.obtener(p.id).titulo_ml
    assert "6589589" not in nuevo and "21372" in nuevo
    assert cat.limpiar_titulos() == 0            # idempotente


def test_limpiar_titulos_no_toca_los_que_estan_bien(cat):
    cat.agregar(_prod(marca="LEGO", modelo="LEGO Star Wars 75339",
                      titulo_ml="LEGO Star Wars 75339"))
    assert cat.limpiar_titulos() == 0


def test_no_se_da_por_publicado_sin_id_de_mercadolibre(cat):
    """Marcarlo igual sería decirle al usuario que publicó algo que no existe."""
    p = cat.agregar(_prod())
    with pytest.raises(ValueError, match="no devolvió el id"):
        cat.registrar_publicacion(p.id, "")
    assert cat.obtener(p.id).estado == "borrador"


def test_no_se_da_por_publicado_si_ml_lo_dejo_en_revision(cat):
    """MercadoLibre responde 200 creando el ítem, pero si queda en revisión o
    esperando pago, la publicación no está a la venta."""
    p = cat.agregar(_prod())
    with pytest.raises(ValueError, match="under_review"):
        cat.registrar_publicacion(p.id, "MLA123", "http://ml/x", "under_review")
    guardado = cat.obtener(p.id)
    assert guardado.estado == "borrador"       # no se da por publicado
    assert guardado.ml_item_id == "MLA123"     # pero el id no se pierde
    assert any("under_review" in (h["nota"] or "") for h in cat.historial(p.id))


def test_activo_si_se_publica(cat):
    p = cat.agregar(_prod())
    p2 = cat.registrar_publicacion(p.id, "MLA123", "http://ml/x", "active")
    assert p2.estado == "publicado"


# ---- videos -------------------------------------------------------------

def test_id_de_youtube_acepta_link_o_id():
    """Lo que se copia del navegador es la URL entera, no el id."""
    from catalogo import id_de_youtube

    assert id_de_youtube("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert id_de_youtube("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert id_de_youtube("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert id_de_youtube("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert id_de_youtube("https://youtube.com/watch?a=1&v=dQw4w9WgXcQ&t=3") == "dQw4w9WgXcQ"
    assert id_de_youtube("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_id_de_youtube_descarta_lo_que_no_entiende():
    """Mandar basura en video_id hace que MercadoLibre rechace la publicación."""
    from catalogo import id_de_youtube

    assert id_de_youtube("") == ""
    assert id_de_youtube("https://m.media-amazon.com/vse/video.mp4") == ""
    assert id_de_youtube("no es un id") == ""


def test_los_videos_se_guardan_y_se_releen(cat):
    p = cat.agregar(ProductoCatalogo(
        asin="B0VIDEO001", marca="LEGO", modelo="Set", precio_usd=100.0,
        videos=["https://m.media-amazon.com/vse/uno.mp4"]))
    assert cat.obtener(p.id).videos == ["https://m.media-amazon.com/vse/uno.mp4"]

    cat.actualizar_publicacion(p.id,
                               video_youtube="https://youtu.be/dQw4w9WgXcQ")
    # Se guarda el id, no la URL: es lo que espera MercadoLibre.
    assert cat.obtener(p.id).video_youtube == "dQw4w9WgXcQ"


def test_el_video_de_youtube_va_en_la_publicacion():
    from mercadolibre.listing import construir_item_catalogo

    p = ProductoCatalogo(asin="B0VIDEO002", marca="LEGO", modelo="Set 75339",
                         titulo_ml="LEGO Star Wars 75339", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1,
                         video_youtube="dQw4w9WgXcQ")
    # Las dos vías de publicación tienen que mandarlo.
    assert construir_item(p, pictures=["http://i/1.jpg"])["video_id"] == "dQw4w9WgXcQ"
    assert construir_item_catalogo(p, "MLA123")["video_id"] == "dQw4w9WgXcQ"


def test_sin_video_no_se_manda_el_campo():
    """Un video_id vacío es un campo de más que MercadoLibre puede objetar."""
    p = ProductoCatalogo(asin="B0VIDEO003", marca="LEGO", modelo="Set",
                         titulo_ml="LEGO Set", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1)
    assert "video_id" not in construir_item(p, pictures=["http://i/1.jpg"])


def test_los_videos_de_amazon_no_se_mandan_a_mercadolibre():
    """Son .mp4 de su CDN: ML solo acepta YouTube. Mandarlos sería un rechazo
    seguro."""
    p = ProductoCatalogo(asin="B0VIDEO004", marca="LEGO", modelo="Set",
                         titulo_ml="LEGO Set", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1,
                         videos=["https://m.media-amazon.com/vse/uno.mp4"])
    item = construir_item(p, pictures=["http://i/1.jpg"])
    assert "video_id" not in item
    assert "media-amazon.com/vse" not in str(item)


def test_las_filas_viejas_no_traen_none_en_el_video(tmp_path):
    """Las columnas nuevas llegan en NULL a las filas anteriores a la
    migración, y el campo está declarado como texto."""
    import sqlite3
    from arbitraje.config import Config
    from db import conectar

    ruta = str(tmp_path / "vieja.db")
    c = sqlite3.connect(ruta)
    c.execute("CREATE TABLE catalogo (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "creado TEXT NOT NULL, actualizado TEXT NOT NULL, amazon_link TEXT, "
              "asin TEXT, marca TEXT, modelo TEXT, modelo_fabricante TEXT, "
              "precio_usd REAL, peso_kg REAL, costo_envio_usd REAL, "
              "disponibilidad TEXT, regimen TEXT, arancel_pct REAL, categoria TEXT, "
              "margen_deseado REAL, stock INTEGER, dias_preparacion INTEGER, "
              "titulo_ml TEXT, descripcion TEXT, ml_category_id TEXT, "
              "ml_attributes TEXT, pictures TEXT, costo_total_ars REAL, "
              "precio_sugerido_ars REAL, precio_publicado_ars REAL, margen_pct REAL, "
              "estado TEXT, ml_item_id TEXT, ml_permalink TEXT)")
    c.execute("INSERT INTO catalogo (creado, actualizado, asin, marca, modelo, "
              "precio_usd, stock, estado, ml_attributes, pictures, margen_deseado, "
              "peso_kg, costo_envio_usd, dias_preparacion, arancel_pct, categoria, "
              "regimen, disponibilidad) VALUES ('2026-01-01','2026-01-01',"
              "'B0VIEJO001','LEGO','Set viejo',100.0,1,'borrador','{}','[]',0.35,"
              "0.5,0.0,25,0.0,'default','courier','in_stock')")
    c.commit(); c.close()

    viejo = Catalogo(conectar(ruta), cfg=Config(),
                     cotizacion={"oficial": 1000.0, "tarjeta": 1300.0}).todos()[0]
    assert viejo.video_youtube == ""
    assert viejo.videos == []


def test_con_gtin_no_se_pide_el_motivo_de_gtin_vacio():
    """Son excluyentes: al conseguir el código se borra el motivo, y la
    validación pasaba a reclamarlo justo cuando el producto quedaba listo."""
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"},
                    {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío"}]
    p = ProductoCatalogo(asin="B0GTIN00001", marca="LEGO", modelo="Set 21042",
                         titulo_ml="LEGO Architecture 21042", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1,
                         precio_sugerido_ars=100000.0,
                         ml_attributes={"GTIN": "673419283328"})
    assert faltantes_para_publicar(p, obligatorios, ["http://i/1.jpg"]) == []


def test_con_motivo_no_se_pide_el_gtin():
    """La dirección que ya andaba: sin código, el motivo alcanza."""
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"},
                    {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío"}]
    p = ProductoCatalogo(asin="B0GTIN00002", marca="LEGO", modelo="Set 21029",
                         titulo_ml="LEGO Architecture 21029", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1,
                         precio_sugerido_ars=100000.0,
                         ml_attributes={"EMPTY_GTIN_REASON": "Otra razón"})
    assert faltantes_para_publicar(p, obligatorios, ["http://i/1.jpg"]) == []


def test_sin_ninguno_de_los_dos_si_falta_algo():
    """Sin código ni motivo, MercadoLibre no publica: hay que avisar."""
    obligatorios = [{"id": "GTIN", "name": "Código universal de producto"},
                    {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío"}]
    p = ProductoCatalogo(asin="B0GTIN00003", marca="LEGO", modelo="Set",
                         titulo_ml="LEGO Set", precio_usd=100.0,
                         ml_category_id="MLA1157", stock=1,
                         precio_sugerido_ars=100000.0)
    faltan = faltantes_para_publicar(p, obligatorios, ["http://i/1.jpg"])
    assert len(faltan) == 2
