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


def test_envio_import_se_estima_como_26pct_si_hay_envio_gratis(cat):
    # Con envío internacional gratis de Amazon solo se pagan los cargos de
    # importación: 26% del precio.
    p = cat.agregar(_prod(precio_usd=126.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=True))
    assert p.costo_envio_usd == pytest.approx(126.0 * 0.26, abs=0.01)
    tc = cat.cfg.tc_compra()
    assert p.costo_total_ars == pytest.approx((126.0 * 1.26) * tc, abs=1)


def test_sin_envio_gratis_se_estima_70pct(cat):
    """Sin la promoción de Amazon hay que pagar el flete a Argentina, y el
    total se va a ~70% del precio. Aplicarle el 26% es publicar perdiendo."""
    p = cat.agregar(_prod(precio_usd=126.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=False))
    assert p.costo_envio_usd == pytest.approx(126.0 * 0.70, abs=0.01)


def test_sin_marcar_se_cobra_como_si_no_tuviera_envio_gratis(cat):
    """Tres estados, y el desconocido cae del lado caro a propósito: hasta que
    alguien mire el checkout no hay motivo para suponer que Amazon regala el
    envío, y equivocarse para abajo es vender por debajo del costo."""
    p = cat.agregar(_prod(precio_usd=126.0, costo_envio_usd=0.0,
                          regimen="landed"))
    assert p.envio_gratis_amazon is None
    assert p.costo_envio_usd == pytest.approx(126.0 * 0.70, abs=0.01)


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


def test_limpiar_solo_sucios_no_toca_los_que_estan_bien(cat):
    """La pasada automática solo arregla basura evidente. Rearmar todos ahí
    pisaría, sin avisar, los títulos que el usuario editó a mano."""
    cat.agregar(_prod(marca="LEGO", modelo="LEGO Star Wars 75339",
                      titulo_ml="LEGO Star Wars 75339"))
    assert cat.limpiar_titulos(solo_sucios=True) == 0


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


# ---- comprar a un dólar y vender a otro ---------------------------------

def test_compro_a_dolar_1600_y_el_margen_es_el_que_queda_limpio(cat):
    """El costo se valúa al dólar oficial que se estima para cuando se compre, y
    el precio sale del margen que tiene que quedar después de TODO: comisión de
    MercadoLibre, envío gratis, IIBB y percepción."""
    p = cat.agregar(ProductoCatalogo(
        asin="B0SW000001", marca="LEGO", modelo="LEGO Star Wars 75192",
        titulo_ml="LEGO Star Wars 75192", precio_usd=100.0, regimen="landed",
        margen_deseado=0.30, stock=1, envio_gratis_amazon=True))
    p = cat.obtener(p.id)

    # 100 USD de Amazon + 26% de envío e impuestos = 126 USD puestos acá.
    assert p.precio_usd + p.costo_envio_usd == 126.0

    r = cat.simular(p, tc_costo=1600)
    assert r["costo_ars"] == 126.0 * 1600
    # El margen pedido es el que efectivamente queda.
    assert r["margen_pct"] == pytest.approx(30.0, abs=0.1)


def test_el_envio_gratis_se_descuenta_del_margen(cat):
    """Si el envío no se cobra en el precio, sale del bolsillo. Es la falla que
    tenía el modelo viejo, que lo mezclaba dentro de un porcentaje."""
    p = cat.agregar(ProductoCatalogo(
        asin="B0SW000002", marca="LEGO", modelo="Set", titulo_ml="Set",
        precio_usd=100.0, regimen="landed", margen_deseado=0.30, stock=1))
    p = cat.obtener(p.id)

    con_envio = cat.simular(p, tc_costo=1600, envio=9860)
    sin_envio = cat.simular(p, tc_costo=1600, envio=0)
    # Mismo costo de compra, pero hay que cobrar más para bancar el envío.
    assert con_envio["costo_ars"] == sin_envio["costo_ars"]
    assert con_envio["precio_ars"] > sin_envio["precio_ars"]
    # Y lo que hay que cobrar de más es el envío, ya bruto de comisiones.
    assert con_envio["margen_ars"] == pytest.approx(sin_envio["margen_ars"], abs=1)


def test_el_dolar_a_mano_gana_sobre_la_cotizacion_en_vivo(cat):
    """Es el número que escribió el usuario, no una estimación."""
    p = cat.agregar(ProductoCatalogo(
        asin="B0SW000003", marca="LEGO", modelo="Set", titulo_ml="Set",
        precio_usd=100.0, regimen="landed", stock=1, envio_gratis_amazon=True))
    p = cat.obtener(p.id)

    assert cat.simular(p, tc_costo=1600)["costo_ars"] == 126.0 * 1600
    # Sin `tc` se usa el tipo de cambio de compra configurado, que es otro.
    de_siempre = cat.simular(p)["costo_ars"]
    assert de_siempre == round(126.0 * cat._cfg_efectivo().tc_compra(), 2)
    assert de_siempre != 126.0 * 1600


def test_sin_dolar_de_venta_se_usa_el_margen(cat):
    """El modo de siempre sigue andando."""
    p = cat.agregar(ProductoCatalogo(
        asin="B0SW000004", marca="LEGO", modelo="Set", titulo_ml="Set",
        precio_usd=100.0, regimen="landed", margen_deseado=0.35, stock=1))
    p = cat.obtener(p.id)

    con_margen = cat.simular(p, tc_costo=1600, margen=0.80)
    del_producto = cat.simular(p, tc_costo=1600)
    assert con_margen["precio_ars"] > del_producto["precio_ars"]
    # Mismo costo: cambia el precio, no el costo.
    assert con_margen["costo_ars"] == del_producto["costo_ars"]


def test_simular_no_modifica_el_producto(cat):
    p = cat.agregar(ProductoCatalogo(
        asin="B0SW000005", marca="LEGO", modelo="Set", titulo_ml="Set",
        precio_usd=100.0, regimen="landed", margen_deseado=0.35, stock=1))
    antes = cat.obtener(p.id)
    cat.simular(antes, tc_costo=9999, margen=5.0, envio=1234)
    despues = cat.obtener(p.id)

    assert despues.costo_total_ars == antes.costo_total_ars
    assert despues.margen_deseado == antes.margen_deseado


def _contar_viajes(cat):
    """Cuenta consultas y commits: con la base por red, cada uno es ida y vuelta.

    Es lo que importa acá. El tiempo de reloj en un SQLite en memoria no dice
    nada del servidor real, donde cada viaje son decenas de milisegundos.
    """
    conn = cat.conn
    real_q, real_c = conn.execute, conn.commit
    n = {"viajes": 0, "pref": 0}

    def q(sql, *a, **k):
        n["viajes"] += 1
        if "preferencias" in str(sql):
            n["pref"] += 1
        return real_q(sql, *a, **k)

    def c(*a, **k):
        n["viajes"] += 1
        return real_c(*a, **k)

    conn.execute, conn.commit = q, c
    return n


def test_recalcular_todo_no_relee_las_preferencias_por_producto(cat):
    """Recalcular 114 productos eran ~1.250 viajes a la base, 1.026 de ellos
    releyendo cuatro preferencias que no cambian en toda la operación. Con la
    base por red eso solo se comía el minuto y medio del pedido, y el cambio de
    precios se cortaba antes de llamar a MercadoLibre."""
    for i in range(40):
        cat.agregar(ProductoCatalogo(
            asin=f"B{i:09d}", marca="LEGO", modelo=f"Set {i}", titulo_ml=f"Set {i}",
            precio_usd=100.0, regimen="landed", margen_deseado=0.30, stock=1))
    cat.tc_manual = 1600
    cat.envio_manual = 9860

    n = _contar_viajes(cat)
    cat.recalcular_todos()

    assert n["pref"] == 0, "se releen preferencias que ya estaban en memoria"
    # Un SELECT de todos, un UPDATE por producto y un solo commit al final.
    assert n["viajes"] <= 45, f"{n['viajes']} viajes para 40 productos"


def test_recalcular_todo_hace_un_solo_commit(cat):
    """Un commit por producto son 114 esperas de durabilidad contra el servidor."""
    for i in range(20):
        cat.agregar(ProductoCatalogo(
            asin=f"C{i:09d}", marca="LEGO", modelo=f"Set {i}", titulo_ml=f"Set {i}",
            precio_usd=100.0, regimen="landed", margen_deseado=0.30, stock=1))
    conn = cat.conn
    real = conn.commit
    commits = []
    conn.commit = lambda *a, **k: (commits.append(1), real(*a, **k))[1]

    cat.recalcular_todos()
    assert len(commits) == 1, f"{len(commits)} commits para 20 productos"


def test_la_cache_de_preferencias_no_devuelve_valores_viejos(cat):
    """Cachear sin invalidar sería peor que el problema que arregla: el catálogo
    seguiría valuando al dólar anterior después de cambiarlo."""
    cat.tc_manual = 1600
    assert cat.tc_manual == 1600
    cat.tc_manual = 1700
    assert cat.tc_manual == 1700
    cat.tc_manual = None
    assert cat.tc_manual is None


def test_la_cache_no_le_pega_el_default_de_una_clave_a_otra_lectura(cat):
    """`_pref` recibe defaults distintos según quién llame. Si se cacheara el
    default en vez de lo que hay en la base, el primero en preguntar le fijaría
    su default a todos los demás."""
    assert cat._pref("no_existe", "primero") == "primero"
    assert cat._pref("no_existe", "segundo") == "segundo"


# ---- envío + importación: el número más importante de la cuenta ----------

def test_el_envio_se_suma_al_costo(cat):
    """El envío+importación sí entra en el costo. Verificado porque se sospechó
    que el costo salía del precio de Amazon pelado."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=True))
    assert p.costo_envio_usd == pytest.approx(26.0, abs=0.01)
    tc = cat._cfg_efectivo().tc_compra()
    assert p.costo_total_ars == pytest.approx(126.0 * tc, abs=1)


def test_cambiar_el_porcentaje_reestima_lo_que_estaba_estimado(cat):
    """Sin esto el porcentaje nuevo no serviría: `costo_envio_usd` se guarda, y
    los productos ya cargados se quedarían con la estimación vieja."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=True))
    assert p.costo_envio_usd == pytest.approx(26.0, abs=0.01)
    costo_antes = p.costo_total_ars

    cat.envio_import_pct = 0.60
    assert cat.reestimar_envios(pct_anterior=0.26) == 1

    p2 = cat.obtener(p.id)
    assert p2.costo_envio_usd == pytest.approx(60.0, abs=0.01)
    assert p2.costo_total_ars > costo_antes


def test_el_total_real_del_checkout_no_se_pisa(cat):
    """Si alguien cargó el Total de verdad, ese dato es mejor que cualquier
    porcentaje: pisarlo sería perder la única medición real que hay."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=73.40,
                          regimen="landed"))

    cat.envio_import_pct = 0.60
    cat.reestimar_envios(pct_anterior=0.26)

    assert cat.obtener(p.id).costo_envio_usd == 73.40


def test_un_porcentaje_disparatado_se_rechaza(cat):
    with pytest.raises(ValueError):
        cat.envio_import_pct = 5.0        # 500%
    with pytest.raises(ValueError):
        cat.envio_import_pct = -1


def test_el_porcentaje_configurado_manda_sobre_el_de_fabrica(cat):
    cat.envio_import_pct = 0.55
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=True))
    assert p.costo_envio_usd == pytest.approx(55.0, abs=0.01)


# ---- costo puesto a mano -------------------------------------------------

def test_el_costo_a_mano_gana_sobre_la_estimacion(cat):
    """Cuando se conoce el costo real —del checkout, del resumen de la
    tarjeta— estimarlo es peor que usarlo."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", margen_deseado=0.30))
    estimado = p.costo_total_ars

    p2 = cat.actualizar_costo_manual(p.id, 250000)

    assert p2.costo_total_ars == 250000
    assert p2.costo_total_ars != estimado


def test_el_costo_a_mano_recalcula_el_sugerido(cat):
    """Es para lo que sirve: saber a cuánto hay que vender con el costo real."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", margen_deseado=0.30))
    sugerido_antes = p.precio_sugerido_ars

    p2 = cat.actualizar_costo_manual(p.id, p.costo_total_ars * 2)

    assert p2.precio_sugerido_ars > sugerido_antes
    from arbitraje.pricing import margen_real_al_precio
    m = margen_real_al_precio(p2.costo_total_ars, p2.precio_sugerido_ars,
                              p2.categoria, cat._cfg_efectivo())
    assert m["margen_pct"] == pytest.approx(30.0, abs=0.5)


def test_sacar_el_costo_a_mano_vuelve_a_estimarlo(cat):
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed"))
    estimado = p.costo_total_ars
    cat.actualizar_costo_manual(p.id, 999999)

    p2 = cat.actualizar_costo_manual(p.id, None)

    assert p2.costo_total_ars == pytest.approx(estimado, abs=1)
    assert p2.costo_manual_ars is None


def test_el_costo_a_mano_no_lo_pisa_la_reestimacion_de_envios(cat):
    """Cambiar el porcentaje de envío recalcula estimaciones. Un costo real
    cargado a mano no es una estimación."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed"))
    cat.actualizar_costo_manual(p.id, 300000)

    cat.envio_import_pct = 0.75
    cat.reestimar_envios(pct_anterior=0.26)

    assert cat.obtener(p.id).costo_total_ars == 300000


def test_el_costo_a_mano_manda_tambien_al_simular_precios(cat):
    """La simulación de precios recalcula el costo al dólar que se pida. Con
    costo real cargado, ese número es el que vale."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed"))
    cat.actualizar_costo_manual(p.id, 275000)

    r = cat.simular(cat.obtener(p.id), tc_costo=1600)
    assert r["costo_ars"] == 275000


def test_el_precio_del_producto_a_mano_le_suma_el_envio_que_corresponda(cat):
    """El otro número que se conoce es lo que sale el producto, no lo que sale
    traerlo: la herramienta le suma el envío según la marca."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=True))

    p2 = cat.actualizar_costo_producto(p.id, 100000)

    assert p2.costo_producto_manual_ars == 100000
    assert p2.costo_total_ars == pytest.approx(126000, abs=1)   # +26%


def test_el_mismo_precio_a_mano_da_dos_costos_segun_el_envio(cat):
    """Es exactamente lo que la marca tiene que producir: el mismo producto
    cuesta —y hay que venderlo a— distinto precio según Amazon lo mande gratis
    o no."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=True))
    con = cat.actualizar_costo_producto(p.id, 100000)
    caro_antes = con.precio_sugerido_ars

    sin = cat.marcar_envio_gratis(p.id, False)

    assert sin.costo_total_ars == pytest.approx(170000, abs=1)  # +70%
    assert sin.precio_sugerido_ars > caro_antes


def test_cargar_el_total_a_mano_borra_el_precio_del_producto(cat):
    """Un solo costo por producto: si quedaran los dos no se sabría cuál manda."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed"))
    cat.actualizar_costo_producto(p.id, 100000)

    p2 = cat.actualizar_costo_manual(p.id, 333000)

    assert p2.costo_producto_manual_ars is None
    assert p2.costo_total_ars == 333000


def test_cargar_el_precio_del_producto_borra_el_total_a_mano(cat):
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=True))
    cat.actualizar_costo_manual(p.id, 333000)

    p2 = cat.actualizar_costo_producto(p.id, 100000)

    assert p2.costo_manual_ars is None
    assert p2.costo_total_ars == pytest.approx(126000, abs=1)


def test_el_total_a_mano_no_lo_mueve_la_marca_de_envio(cat):
    """El total ya puesto acá no es una estimación de nada: el porcentaje de
    envío no tiene por qué tocarlo."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed"))
    cat.actualizar_costo_manual(p.id, 333000)

    p2 = cat.marcar_envio_gratis(p.id, True)

    assert p2.costo_total_ars == 333000


def test_cambiar_el_porcentaje_mueve_el_precio_del_producto_a_mano(cat):
    """El porcentaje se le suma arriba, así que su costo total sí depende de
    él aunque el envío en dólares no se mueva."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=False))
    cat.actualizar_costo_producto(p.id, 100000)
    assert cat.obtener(p.id).costo_total_ars == pytest.approx(170000, abs=1)

    cat.envio_import_sin_gratis_pct = 0.90
    cat.reestimar_envios(pct_anterior=(0.26, 0.70))

    assert cat.obtener(p.id).costo_total_ars == pytest.approx(190000, abs=1)


def test_el_precio_del_producto_a_mano_manda_al_simular(cat):
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=True))
    cat.actualizar_costo_producto(p.id, 100000)

    r = cat.simular(cat.obtener(p.id), tc_costo=1600)

    assert r["costo_ars"] == pytest.approx(126000, abs=1)


def test_sacar_el_precio_del_producto_vuelve_a_estimar_todo(cat):
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed",
                          envio_gratis_amazon=True))
    estimado = p.costo_total_ars
    cat.actualizar_costo_producto(p.id, 999000)

    p2 = cat.actualizar_costo_producto(p.id, None)

    assert p2.costo_producto_manual_ars is None
    assert p2.costo_total_ars == pytest.approx(estimado, abs=1)


def test_un_precio_de_producto_invalido_se_rechaza(cat):
    p = cat.agregar(_prod())
    with pytest.raises(ValueError):
        cat.actualizar_costo_producto(p.id, -100)
    with pytest.raises(ValueError):
        cat.actualizar_costo_producto(p.id, "muchos pesos")


def test_marcar_envio_gratis_baja_el_costo_y_el_sugerido(cat):
    """Es el punto de toda la marca: el mismo producto sale mucho menos si
    Amazon paga el flete, y el precio al que hay que venderlo baja con él."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", margen_deseado=0.30))
    caro, sugerido_caro = p.costo_total_ars, p.precio_sugerido_ars

    p2 = cat.marcar_envio_gratis(p.id, True)

    assert p2.envio_gratis_amazon is True
    assert p2.costo_envio_usd == pytest.approx(26.0, abs=0.01)
    assert p2.costo_total_ars < caro
    assert p2.precio_sugerido_ars < sugerido_caro


def test_destildar_el_envio_gratis_vuelve_a_encarecer(cat):
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                          regimen="landed", envio_gratis_amazon=True))
    barato = p.costo_total_ars

    p2 = cat.marcar_envio_gratis(p.id, False)

    assert p2.envio_gratis_amazon is False
    assert p2.costo_envio_usd == pytest.approx(70.0, abs=0.01)
    assert p2.costo_total_ars > barato


def test_marcar_el_envio_no_pisa_el_total_real_del_checkout(cat):
    """Si alguien cargó el Total de verdad, cambiar la marca no puede
    reemplazarlo por una estimación: sería perder la única medición real."""
    p = cat.agregar(_prod(precio_usd=100.0, costo_envio_usd=41.75,
                          regimen="landed"))

    p2 = cat.marcar_envio_gratis(p.id, True)

    assert p2.envio_gratis_amazon is True
    assert p2.costo_envio_usd == 41.75


def test_el_margen_deseado_se_respeta_con_y_sin_envio_gratis(cat):
    """El costo cambia, el margen no: el precio sugerido tiene que dejar el
    mismo 30% limpio en los dos casos."""
    from arbitraje.pricing import margen_real_al_precio
    cfg = cat._cfg_efectivo()
    for gratis in (True, False):
        p = cat.agregar(_prod(asin=f"B0EG00000{int(gratis)}", precio_usd=100.0,
                              costo_envio_usd=0.0, regimen="landed",
                              margen_deseado=0.30, envio_gratis_amazon=gratis))
        m = margen_real_al_precio(p.costo_total_ars, p.precio_sugerido_ars,
                                  p.categoria, cfg)
        assert m["margen_pct"] == pytest.approx(30.0, abs=0.5)


def test_la_marca_de_envio_sobrevive_al_guardado(cat):
    """Va a la base como 0/1/NULL y tiene que volver como True/False/None:
    confundir 'no lo miré' con 'no tiene envío gratis' esconde el trabajo
    pendiente."""
    a = cat.agregar(_prod(asin="B0EG000010", envio_gratis_amazon=True))
    b = cat.agregar(_prod(asin="B0EG000011", envio_gratis_amazon=False))
    c = cat.agregar(_prod(asin="B0EG000012"))

    assert cat.obtener(a.id).envio_gratis_amazon is True
    assert cat.obtener(b.id).envio_gratis_amazon is False
    assert cat.obtener(c.id).envio_gratis_amazon is None


def test_cambiar_el_pct_sin_envio_gratis_no_toca_a_los_que_si_lo_tienen(cat):
    """Son dos números independientes: mover uno no puede arrastrar al otro."""
    con = cat.agregar(_prod(asin="B0EG000020", precio_usd=100.0,
                            costo_envio_usd=0.0, regimen="landed",
                            envio_gratis_amazon=True))
    sin = cat.agregar(_prod(asin="B0EG000021", precio_usd=100.0,
                            costo_envio_usd=0.0, regimen="landed",
                            envio_gratis_amazon=False))

    cat.envio_import_sin_gratis_pct = 0.90
    assert cat.reestimar_envios(pct_anterior=(0.26, 0.70)) == 1

    assert cat.obtener(con.id).costo_envio_usd == pytest.approx(26.0, abs=0.01)
    assert cat.obtener(sin.id).costo_envio_usd == pytest.approx(90.0, abs=0.01)


def _sin_migrar(c):
    """Deja la base como estaba antes de que existieran los dos porcentajes."""
    c.conn.execute("DELETE FROM preferencias WHERE clave = ?",
                   (Catalogo._PREF_MIGRACION_PCT,))
    c.conn.commit()
    c._cache_pref.clear()


def _reabrir(ruta):
    """Abre el catálogo como lo hace la app: la migración no corre al arrancar
    —la base puede estar dormida— sino cuando llega el primer pedido, que es
    donde el endpoint del listado llama a `migrar_pct_envio()`."""
    c = Catalogo(conectar(ruta), cfg=Config())
    c.migrar_pct_envio()
    return c


def test_el_porcentaje_unico_viejo_pasa_a_ser_el_de_sin_envio_gratis(tmp_path):
    """El que configuró el porcentaje único lo calibró contra lo que compraba
    —productos sin envío gratis—, así que ese es su lugar. Dejarlo en la casilla
    de "con envío gratis" hacía que tildar un producto lo encareciera."""
    ruta = str(tmp_path / "mig.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.70
    _sin_migrar(c)

    c2 = _reabrir(ruta)

    assert c2.envio_import_sin_gratis_pct == pytest.approx(0.70)
    assert c2.envio_import_pct == pytest.approx(0.26)   # vuelve al de fábrica


def test_la_migracion_no_le_cambia_el_costo_a_lo_que_ya_estaba(tmp_path):
    """Todo lo cargado nace sin marcar, o sea "sin envío gratis": moverlo a esa
    casilla es justamente lo que deja el costo donde estaba."""
    ruta = str(tmp_path / "mig2.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.70
    p = c.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                        regimen="landed"))
    antes = p.costo_total_ars
    _sin_migrar(c)

    c2 = _reabrir(ruta)

    assert c2.obtener(p.id).costo_total_ars == pytest.approx(antes, abs=1)


def test_la_migracion_recalcula_lo_que_ya_estaba_tildado(tmp_path):
    """El producto que se tildó antes de la migración quedó estimado al
    porcentaje equivocado: hay que rehacerle la cuenta."""
    ruta = str(tmp_path / "mig3.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.70
    p = c.agregar(_prod(precio_usd=100.0, costo_envio_usd=0.0,
                        regimen="landed"))
    c.marcar_envio_gratis(p.id, True)
    assert c.obtener(p.id).costo_envio_usd == pytest.approx(70.0, abs=0.01)
    _sin_migrar(c)

    c2 = _reabrir(ruta)

    assert c2.obtener(p.id).costo_envio_usd == pytest.approx(26.0, abs=0.01)


def test_la_migracion_no_pisa_el_total_real_del_checkout(tmp_path):
    ruta = str(tmp_path / "mig4.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.70
    p = c.agregar(_prod(precio_usd=100.0, costo_envio_usd=43.17, regimen="landed"))
    _sin_migrar(c)

    c2 = _reabrir(ruta)

    assert c2.obtener(p.id).costo_envio_usd == 43.17


def test_la_migracion_corre_una_sola_vez(tmp_path):
    """Si volviera a correr, se llevaría puesto lo que el usuario configuró
    después en la casilla de "con envío gratis"."""
    ruta = str(tmp_path / "mig5.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.70
    _sin_migrar(c)
    _reabrir(ruta)

    c3 = Catalogo(conectar(ruta), cfg=Config())
    c3.envio_import_pct = 0.31          # el usuario mide su caso con envío gratis
    c4 = _reabrir(ruta)

    assert c4.envio_import_pct == pytest.approx(0.31)
    assert c4.envio_import_sin_gratis_pct == pytest.approx(0.70)


def test_sin_nada_configurado_la_migracion_no_toca_nada(tmp_path):
    """Una instalación nueva no tiene un porcentaje viejo que repartir."""
    c = _reabrir(str(tmp_path / "mig6.db"))

    assert c.envio_import_pct == pytest.approx(0.26)
    assert c.envio_import_sin_gratis_pct == pytest.approx(0.70)


def test_la_migracion_respeta_la_casilla_nueva_si_ya_se_uso(tmp_path):
    ruta = str(tmp_path / "mig7.db")
    c = Catalogo(conectar(ruta), cfg=Config())
    c.envio_import_pct = 0.30
    c.envio_import_sin_gratis_pct = 0.85
    _sin_migrar(c)

    c2 = _reabrir(ruta)

    assert c2.envio_import_pct == pytest.approx(0.30)
    assert c2.envio_import_sin_gratis_pct == pytest.approx(0.85)


def test_un_pct_sin_envio_gratis_disparatado_se_rechaza(cat):
    with pytest.raises(ValueError):
        cat.envio_import_sin_gratis_pct = 5.0
    with pytest.raises(ValueError):
        cat.envio_import_sin_gratis_pct = -1


def test_la_marca_de_envio_queda_en_el_historial(cat):
    """Es un dato que cambia el costo: tiene que poder rastrearse quién y
    cuándo lo cambió."""
    p = cat.agregar(_prod(precio_usd=100.0, regimen="landed"))
    cat.marcar_envio_gratis(p.id, True)

    campos = [h["campo"] for h in cat.historial(p.id)]
    assert "envio_gratis_amazon" in campos


def test_un_costo_a_mano_invalido_se_rechaza(cat):
    p = cat.agregar(_prod())
    with pytest.raises(ValueError):
        cat.actualizar_costo_manual(p.id, -100)
    with pytest.raises(ValueError):
        cat.actualizar_costo_manual(p.id, "muchos pesos")


def test_el_costo_a_mano_queda_en_el_historial(cat):
    p = cat.agregar(_prod())
    cat.actualizar_costo_manual(p.id, 200000)
    assert any(h["tipo"] == "costo" for h in cat.historial(p.id))
