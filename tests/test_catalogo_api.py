"""Tests de los endpoints del catálogo y las guardas de publicación (sin red)."""

import pytest
from fastapi.testclient import TestClient

from api.server import crear_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(crear_app(db_path=str(tmp_path / "t.db")))


def _alta(client, **kw):
    body = dict(asin="B0TEST", marca="HISEA", modelo="Waders", precio_usd=118.99,
                peso_kg=3.0, costo_envio_usd=84.35, margen_deseado=0.35, stock=5)
    body.update(kw)
    return client.post("/api/catalogo", json=body)


def test_alta_y_listado(client):
    r = _alta(client)
    assert r.status_code == 200
    p = r.json()
    assert p["id"] and p["costo_total_ars"] > 0 and p["precio_sugerido_ars"] > 0
    assert client.get("/api/catalogo").json()[0]["asin"] == "B0TEST"


def test_borrador_devuelve_preview_y_faltantes(client):
    pid = _alta(client).json()["id"]
    r = client.post(f"/api/catalogo/{pid}/borrador", json={"pictures": []})
    assert r.status_code == 200
    data = r.json()
    assert "preview" in data
    # Sin categoría ni fotos, debe faltar algo.
    assert any("foto" in f for f in data["faltantes"])


def test_publicar_bloqueado_si_no_esta_aprobado(client):
    pid = _alta(client, ml_category_id="MLA1", titulo_ml="Waders").json()["id"]
    r = client.post(f"/api/catalogo/{pid}/publicar",
                    json={"pictures": ["http://img/1.jpg"]})
    assert r.status_code == 409  # falta aprobación explícita


def test_publicar_bloqueado_si_faltan_datos(client):
    pid = _alta(client).json()["id"]
    client.post(f"/api/catalogo/{pid}/aprobar")
    r = client.post(f"/api/catalogo/{pid}/publicar", json={"pictures": []})
    assert r.status_code == 422  # faltan categoría/fotos


def test_publicar_sin_sesion_ml_da_401(client):
    pid = _alta(client, ml_category_id="MLA1", titulo_ml="Waders").json()["id"]
    client.post(f"/api/catalogo/{pid}/aprobar")
    r = client.post(f"/api/catalogo/{pid}/publicar",
                    json={"pictures": ["http://img/1.jpg"]})
    # Datos completos y aprobado, pero sin credenciales/sesión de MercadoLibre.
    assert r.status_code in (400, 401)


def test_precio_y_stock_actualizan(client):
    pid = _alta(client).json()["id"]
    assert client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 400000}).json()["precio_publicado_ars"] == 400000
    assert client.patch(f"/api/catalogo/{pid}/stock", json={"stock": 9}).json()["stock"] == 9
    tipos = [h["tipo"] for h in client.get(f"/api/catalogo/{pid}/historial").json()]
    assert "precio" in tipos and "stock" in tipos


def test_margen_insuficiente_en_respuesta(client):
    pid = _alta(client).json()["id"]
    p = client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 250000}).json()
    assert "margen_insuficiente" in p


def test_borrar_producto(client):
    pid = _alta(client).json()["id"]
    assert client.delete(f"/api/catalogo/{pid}").status_code == 200
    assert client.get(f"/api/catalogo/{pid}").status_code == 404


def test_cambiar_regimen_recalcula_costo(client):
    # Alta courier vs cambio a landed: landed usa el total sin re-sumar impuesto.
    pid = _alta(client, regimen="courier", precio_usd=839.97, costo_envio_usd=530.36).json()["id"]
    costo_courier = client.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]
    p = client.patch(f"/api/catalogo/{pid}/regimen", json={"regimen": "landed"}).json()
    assert p["regimen"] == "landed"
    assert p["costo_total_ars"] < costo_courier


def test_cambiar_regimen_invalido(client):
    pid = _alta(client).json()["id"]
    assert client.patch(f"/api/catalogo/{pid}/regimen", json={"regimen": "x"}).status_code == 400


def test_precio_competitivo_muestra_margen(client):
    # Precio por debajo del sugerido: la app igual calcula el margen (aunque sea bajo).
    pid = _alta(client, regimen="landed", precio_usd=839.97, costo_envio_usd=530.36).json()["id"]
    p = client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 3000000}).json()
    assert p["precio_publicado_ars"] == 3000000
    assert "margen_pct" in p and "margen_insuficiente" in p


def test_competencia_sin_sesion_ml_da_error_claro(client):
    pid = _alta(client, titulo_ml="Lego Simba").json()["id"]
    r = client.get(f"/api/catalogo/{pid}/competencia")
    assert r.status_code in (400, 401)  # sin credenciales/sesión de ML


def test_competencia_bloqueada_devuelve_link_manual(client, monkeypatch, tmp_path):
    """Si MercadoLibre bloquea la búsqueda (403), la pantalla no se rompe:
    devuelve el link para comparar a mano."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    pid = _alta(client, titulo_ml="Lego Simba 43243").json()["id"]

    class _CliFalso:
        def buscar_listados(self, *a, **k):
            raise MeliAPIError("MercadoLibre GET /sites/MLA/search → 403")

    # Inyectamos un cliente que simula el bloqueo de ML.
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    app = crear_app(db_path=str(tmp_path / "t2.db"))
    c2 = TestClient(app)
    pid2 = _alta(c2, titulo_ml="Lego Simba 43243").json()["id"]
    r = c2.get(f"/api/catalogo/{pid2}/competencia")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == [] and data["error"]
    assert "listado.mercadolibre.com.ar" in data["link_manual"]


def test_competencia_sin_titulo_da_400(client):
    pid = _alta(client, modelo="", asin="", titulo_ml="").json()["id"]
    assert client.get(f"/api/catalogo/{pid}/competencia").status_code == 400


def test_desglose_devuelve_ambas_variantes(client):
    # Como Responsable Inscripto, para que haya IVA y Ganancias en el desglose.
    client.patch("/api/fiscal", json={"condicion_fiscal": "responsable_inscripto"})
    pid = _alta(client, regimen="landed", precio_usd=126.0,
                costo_envio_usd=34.36).json()["id"]
    d = client.get(f"/api/catalogo/{pid}/desglose", params={"precio": 590000}).json()
    det = d["detalle"]
    # El neto estilo ML no descuenta impuestos argentinos; el conservador sí.
    assert d["neto_estilo_ml"] == pytest.approx(
        d["neto_conservador"] + det["impuestos_total"], abs=0.5)
    assert d["estilo_ml"]["margen_ars"] > d["conservador"]["margen_ars"]
    # El margen es neto - costo.
    assert d["conservador"]["margen_ars"] == pytest.approx(
        d["neto_conservador"] - d["costo_puesto_ars"], abs=0.5)
    for k in ("costos_ml", "iva", "ganancias", "iibb"):
        assert det[k] > 0
    assert det["costos_ml_pct"] == pytest.approx(16.0, abs=2)


def test_publicar_reintenta_con_title_si_ml_pide_family_name(tmp_path, monkeypatch):
    """Si la categoría rechaza `family_name`, se reintenta con `title`."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    enviados = []

    class _CliFalso:
        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            enviados.append(item)
            if "family_name" in item:
                raise MeliAPIError("rechazo", status=400,
                                   cuerpo={"error": "The fields [family_name] are invalid"})
            return {"id": "MLA999", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "t.db")))
    pid = _alta(c, titulo_ml="LEGO Star Wars", ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200 and r.json()["ml_item_id"] == "MLA999"
    assert len(enviados) == 2                     # primer intento + reintento
    assert "family_name" in enviados[0] and "title" not in enviados[0]
    assert "title" in enviados[1] and "family_name" not in enviados[1]


def test_publicar_manda_la_marca_limpia_con_value_id(tmp_path, monkeypatch):
    """El caso real: el producto quedó guardado con "Visit the LEGO Store" y ML
    lo rechazaba. Ahora se resuelve contra los valores de la categoría."""
    import api.catalogo_routes as rutas

    enviados = []

    class _CliFalso:
        def atributos_obligatorios(self, cat_id):
            return [{"id": "BRAND", "name": "Marca"}]

        def valores_permitidos(self, cat_id):
            return {"BRAND": [{"id": "9155", "name": "LEGO"}]}

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA123", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "marca.db")))
    pid = _alta(c, marca="Visit the LEGO Store", titulo_ml="LEGO Icons ECTO-1 10274",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    brand = next(a for a in enviados[0]["attributes"] if a["id"] == "BRAND")
    assert brand == {"id": "BRAND", "value_id": "9155"}


def test_publicar_usa_la_marca_del_titulo_si_amazon_no_la_trajo(tmp_path, monkeypatch):
    """Caso real: la cola importó el producto sin marca (Amazon no respondió el
    byline) y ML no lista valores para BRAND. Antes se mandaba el atributo vacío
    y ML rechazaba con "The attributes [BRAND] are required"."""
    import api.catalogo_routes as rutas

    enviados = []

    class _CliFalso:
        def atributos_obligatorios(self, cat_id):
            return [{"id": "BRAND", "name": "Marca"}]

        def valores_permitidos(self, cat_id):
            return {}  # ML no devuelve valores para BRAND en esta categoría

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA456", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "sinmarca.db")))
    pid = _alta(c, marca="", titulo_ml="LEGO Technic Ferrari Daytona SP3 42143",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    brand = next(a for a in enviados[0]["attributes"] if a["id"] == "BRAND")
    assert brand["value_name"] == "LEGO"


def test_publicar_sin_marca_resoluble_avisa_antes_de_llamar_a_ml(tmp_path, monkeypatch):
    import api.catalogo_routes as rutas

    class _CliFalso:
        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            raise AssertionError("no debería llegar a MercadoLibre")

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "nomarca.db")))
    pid = _alta(c, marca="", titulo_ml="Set de bloques", modelo="",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 422
    assert "Marca" in str(r.json())


def test_editar_marca_y_modelo_desde_el_editor(client):
    pid = _alta(client, marca="Visit the LEGO Store", modelo="viejo").json()["id"]
    r = client.patch(f"/api/catalogo/{pid}/publicacion",
                     json={"marca": "LEGO", "modelo": "Icons ECTO-1 10274"})
    assert r.status_code == 200
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["marca"] == "LEGO" and p["modelo"] == "Icons ECTO-1 10274"


def test_payload_muestra_lo_que_se_le_manda_a_ml(client):
    """Endpoint de diagnóstico: qué viaja realmente, sin publicar."""
    pid = _alta(client, marca="Visit the LEGO Store",
                titulo_ml="LEGO Icons ECTO-1", ml_category_id="MLA1157").json()["id"]
    d = client.get(f"/api/catalogo/{pid}/payload").json()
    assert d["marca_guardada"] in ("Visit the LEGO Store", "LEGO")
    assert d["marca_resuelta"] == "LEGO"
    assert d["item"]["family_name"] == "LEGO Icons ECTO-1"
    assert any(a["id"] == "BRAND" for a in d["item"]["attributes"])


def test_vista_previa_muestra_la_marca_resuelta(client):
    pid = _alta(client, marca="Visit the LEGO Store", titulo_ml="LEGO Icons").json()["id"]
    v = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()["preview"]
    assert v["marca"] == "LEGO"


def test_listar_catalogo_repara_las_marcas_viejas(tmp_path):
    """Al abrir el panel se corrigen las marcas que quedaron sucias, sin que el
    usuario tenga que hacer nada."""
    c = TestClient(crear_app(db_path=str(tmp_path / "rep.db")))
    _alta(c, marca="Visit the LEGO Store")
    assert c.get("/api/catalogo").json()[0]["marca"] == "LEGO"


def test_describir_error_separa_lo_que_bloquea_de_las_advertencias():
    from mercadolibre.client import describir_error
    cuerpo = {
        "cause": [
            {"type": "warning", "code": "item.attributes.value_name.invalid",
             "references": ["BRAND"], "message": "Attribute BRAND has an invalid value name."},
            {"type": "error", "code": "item.attributes.missing_required",
             "references": ["item.attributes"],
             "message": "The attributes [BRAND] are required for category MLA1157"},
            {"type": "warning", "code": "item.shipping.mandatory_free_shipping",
             "references": [], "message": "Mandatory free shipping added"},
        ],
        "message": "Validation error", "error": "validation_error", "status": 400,
    }
    texto = describir_error(cuerpo)
    lineas = texto.splitlines()
    assert lineas[0].startswith("The attributes [BRAND] are required")  # lo que bloquea
    assert "Advertencias (no bloquean)" in lineas[1]
    assert "Mandatory free shipping" in lineas[1]


def test_describir_error_sin_causas_no_se_rompe():
    from mercadolibre.client import describir_error
    assert "invalid" in describir_error(
        {"cause": [], "error": "The fields [title] are invalid.", "status": 400})
    assert describir_error("texto pelado") == "texto pelado"


def test_almacenamiento_avisa_si_no_es_persistente(client):
    a = client.get("/api/almacenamiento").json()
    # En tests corre sobre SQLite: debe avisar que no es persistente.
    assert a["persistente"] is False and "SQLite" in a["detalle"]


def test_dolar_costo_default_tarjeta(client):
    d = client.get("/api/dolar-costo").json()
    assert d["dolar_costo"] == "tarjeta"


def test_cambiar_a_dolar_oficial_baja_el_costo(client):
    # 135 USD + 26% = 170,10 USD. Al oficial el costo puesto es menor.
    pid = _alta(client, regimen="landed", precio_usd=135.0,
                costo_envio_usd=0.0).json()["id"]
    costo_tarjeta = client.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]

    d = client.patch("/api/dolar-costo", json={"dolar_costo": "oficial"}).json()
    assert d["dolar_costo"] == "oficial"
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["costo_total_ars"] < costo_tarjeta
    # El costo al oficial coincide con la fila "oficial" de la comparación.
    assert p["costo_total_ars"] == pytest.approx(
        p["comparacion"]["oficial"]["costo_ars"], abs=1)
    # Y la comparación sigue mostrando ambas puntas distintas.
    assert p["comparacion"]["tarjeta"]["costo_ars"] > p["comparacion"]["oficial"]["costo_ars"]


def test_dolar_costo_invalido_da_400(client):
    assert client.patch("/api/dolar-costo", json={"dolar_costo": "blue"}).status_code == 400


def test_fiscal_default_monotributo(client):
    f = client.get("/api/fiscal").json()
    assert f["condicion_fiscal"] == "monotributo"
    assert f["iva_pct"] == 0 and f["ganancias_pct"] == 0


def test_cambiar_condicion_fiscal_recalcula_margenes(client):
    pid = _alta(client, regimen="landed", precio_usd=126.0).json()["id"]
    margen_mono = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]

    f = client.patch("/api/fiscal", json={"condicion_fiscal": "responsable_inscripto"}).json()
    assert f["condicion_fiscal"] == "responsable_inscripto" and f["iva_pct"] == 21.0
    # Al RI el precio sugerido sube (tiene que cubrir IVA y Ganancias).
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["precio_sugerido_ars"] > 0
    # El margen al mismo precio publicado baja para el RI.
    client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 600000})
    margen_ri = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]
    client.patch("/api/fiscal", json={"condicion_fiscal": "monotributo"})
    margen_mono2 = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]
    assert margen_mono2 > margen_ri


def test_condicion_fiscal_invalida_da_400(client):
    assert client.patch("/api/fiscal", json={"condicion_fiscal": "x"}).status_code == 400


def test_oauth_status_sin_credenciales(client):
    s = client.get("/oauth/status").json()
    assert s["configurado"] is False and s["conectado"] is False


def test_oauth_code_sin_code_da_400(client):
    assert client.post("/oauth/code", json={}).status_code == 400


def test_oauth_code_sin_credenciales_da_400(client):
    # Con code pero sin MELI_CLIENT_ID/SECRET configurados.
    r = client.post("/oauth/code", json={"url": "https://127.0.0.1:8321/oauth/callback?code=ABC123"})
    assert r.status_code == 400


def test_pausar_sin_publicar_cambia_estado_local(client):
    pid = _alta(client).json()["id"]
    r = client.post(f"/api/catalogo/{pid}/pausar")
    assert r.status_code == 200 and r.json()["estado"] == "pausado"


def test_editar_publicacion_completa_faltantes(client):
    pid = _alta(client, titulo_ml="Waders HISEA").json()["id"]
    # Faltan categoría, foto y atributos.
    b0 = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()
    assert b0["faltantes"]
    # Cargamos categoría, atributos y foto.
    r = client.patch(f"/api/catalogo/{pid}/publicacion", json={
        "ml_category_id": "MLA66238",
        "ml_attributes": {"GENDER": "Hombre", "COLOR": "Camuflado", "SIZE": "44"},
        "pictures": ["https://img/1.jpg"],
    })
    assert r.status_code == 200
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["ml_category_id"] == "MLA66238" and p["pictures"] == ["https://img/1.jpg"]
    # Sin sesión ML no hay atributos obligatorios remotos: los básicos ya están.
    b1 = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()
    assert not b1["faltantes"]


def test_pictures_persisten_para_publicar(client):
    pid = _alta(client, titulo_ml="Waders", ml_category_id="MLA1").json()["id"]
    client.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["https://img/1.jpg"]})
    client.post(f"/api/catalogo/{pid}/aprobar")
    # Publicar sin pasar pictures en el body: usa las guardadas → llega a pedir sesión ML.
    r = client.post(f"/api/catalogo/{pid}/publicar", json={})
    assert r.status_code in (400, 401)  # faltaría solo la sesión de ML, no las fotos


def _cli_lote(enviados, con_categoria=True, gtin_catalogo=None):
    class _Cli:
        def ficha_de_catalogo(self, query, debe_contener="", limit=5):
            return dict(gtin_catalogo) if gtin_catalogo else {}

        def gtin_de_catalogo(self, query, debe_contener="", limit=5):
            f = self.ficha_de_catalogo(query, debe_contener, limit)
            return f if f.get("gtin") else {}

        def predecir_categoria(self, titulo):
            return [{"category_id": "MLA1157", "category_name": "Sets"}] if con_categoria else []

        def atributos_obligatorios(self, cat_id):
            return [{"id": "IVA", "name": "IVA", "values": ["0 %", "21 %"]},
                    {"id": "BRAND", "name": "Marca"},
                    {"id": "GTIN", "name": "Código universal de producto"}]

        def atributos(self, cat_id):
            return self.atributos_obligatorios(cat_id) + [
                {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío",
                 "values": ["Es un kit", "Otra razón"]}]

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA" + str(len(enviados)), "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}
    return _Cli()


def _con_ml(monkeypatch, cli):
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)


def test_lote_preparar_completa_marca_categoria_y_atributos(tmp_path, monkeypatch):
    """Lo que evita cargar producto por producto: se deduce todo lo deducible."""
    _con_ml(monkeypatch, _cli_lote([]))
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": True, "gtin": "5702016914498", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote.db")))
    ids = [_alta(c, marca="Visit the LEGO Store", modelo="LEGO Icons ECTO-1 10274",
                 titulo_ml="", asin="B0TEST1").json()["id"],
           _alta(c, marca="", modelo="LEGO Technic Ferrari 42143", asin="B0TEST2").json()["id"]]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    assert r.status_code == 200
    assert all(x["ok"] for x in r.json()["resultados"])

    for pid in ids:
        p = c.get(f"/api/catalogo/{pid}").json()
        assert p["marca"] == "LEGO"
        assert p["ml_category_id"] == "MLA1157"
        assert p["titulo_ml"]                                  # se completó del modelo
        assert p["ml_attributes"]["GTIN"] == "5702016914498"
        assert p["ml_attributes"]["IVA"] == "21 %"             # default administrativo


def test_lote_publicar_publica_los_seleccionados(tmp_path, monkeypatch):
    """El flujo de los dos botones: Preparar completa los datos, Publicar sube."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados))
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote2.db")))
    ids = []
    for n in (1, 2):
        pid = _alta(c, marca="LEGO", titulo_ml=f"LEGO Set {n}",
                    ml_category_id="MLA1157").json()["id"]
        c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
        ids.append(pid)

    c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    r = c.post("/api/catalogo/lote/publicar", json={"ids": ids})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text
    assert len(enviados) == 2
    for pid in ids:
        assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_lote_sigue_con_los_demas_si_uno_falla(tmp_path, monkeypatch):
    """Un producto incompleto no puede frenar la tanda entera."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados))
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote3.db")))
    bueno = _alta(c, marca="LEGO", titulo_ml="LEGO Set OK",
                  ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{bueno}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    malo = _alta(c, marca="LEGO", titulo_ml="Sin foto").json()["id"]

    c.post("/api/catalogo/lote/preparar", json={"ids": [malo, bueno]})
    res = c.post("/api/catalogo/lote/publicar", json={"ids": [malo, bueno]}).json()["resultados"]
    por_id = {r["id"]: r for r in res}
    assert por_id[malo]["ok"] is False and "falta" in por_id[malo]["error"]
    assert por_id[bueno]["ok"] is True
    assert len(enviados) == 1


def test_lote_borrar(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "lote4.db")))
    ids = [_alta(c).json()["id"] for _ in range(3)]
    c.post("/api/catalogo/lote/borrar", json={"ids": ids[:2]})
    assert [p["id"] for p in c.get("/api/catalogo").json()] == [ids[2]]


def test_lote_preparar_funciona_sin_sesion_de_ml(tmp_path, monkeypatch):
    """Sin conexión a ML igual se limpia la marca y se arma el título."""
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})
    c = TestClient(crear_app(db_path=str(tmp_path / "lote5.db")))
    pid = _alta(c, marca="Visit the LEGO Store", modelo="LEGO Star Wars 75355",
                titulo_ml="").json()["id"]
    r = c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert r.json()["resultados"][0]["ok"] is True
    p = c.get(f"/api/catalogo/{pid}").json()
    assert p["marca"] == "LEGO" and p["titulo_ml"] == "LEGO Star Wars 75355"


def test_lote_prepara_los_casos_que_fallaban_en_produccion(tmp_path, monkeypatch):
    """Los dos motivos por los que los 72 productos quedaron "con problemas":
    la marca no estaba primera en el título, y el GTIN no se conseguía."""
    _con_ml(monkeypatch, _cli_lote([]))
    # El buscador de GTIN no encuentra nada, como pasó con los sets reales.
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "prod.db")))
    titulos = [
        "Set de construcción Star Wars de LEGO, Darth Vader, talla única",
        "Juguete para armar Star Wars 75050 B-Wing LEGO",
        "LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 752",
    ]
    ids = [_alta(c, marca="", modelo=t, titulo_ml=t, asin=f"B0T{i}").json()["id"]
           for i, t in enumerate(titulos)]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text

    for pid in ids:
        p = c.get(f"/api/catalogo/{pid}").json()
        assert p["marca"] == "LEGO"
        # Sin código de barras se declara el motivo, que es la vía que acepta ML.
        assert p["ml_attributes"]["EMPTY_GTIN_REASON"] == "Otra razón"

    # Y con eso ya no falta nada: el borrador queda listo para publicar.
    for pid in ids:
        c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    d = c.post(f"/api/catalogo/{ids[0]}/borrador", json={}).json()
    assert d["faltantes"] == [], d["faltantes"]


def test_lote_saca_el_gtin_del_catalogo_de_mercadolibre(tmp_path, monkeypatch):
    """El caso que dejó 61 de 63 sin publicar: MercadoLibre exige GTIN en
    MLA1157 y no le alcanza el motivo de GTIN vacío. El código sale del propio
    catálogo de ML, que ya tiene los sets cargados."""
    _con_ml(monkeypatch, _cli_lote([], gtin_catalogo={
        "gtin": "5702017155326", "product_id": "MLA123", "nombre": "LEGO 75339"}))
    # La búsqueda web por ASIN no encuentra nada, como en producción.
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "gtin.db")))
    titulo = ("LEGO Star Wars Death Star - Compactador de basura Diorama 75339 "
              "Kit de construcción (802 piezas)")
    pid = _alta(c, marca="", modelo=titulo, titulo_ml=titulo, asin="B0TEST9").json()["id"]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text

    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017155326"
    # Con GTIN de verdad, el motivo de GTIN vacío no viaja (son contradictorios).
    assert "EMPTY_GTIN_REASON" not in attrs
    # Y la cantidad de piezas sale del propio título.
    assert attrs["PIECES_NUMBER"] == "802"


def test_lote_no_pone_gtin_si_el_catalogo_no_lo_tiene(tmp_path, monkeypatch):
    """Sin código verificado no se inventa nada: mejor que falte a publicar
    el código de otro producto."""
    _con_ml(monkeypatch, _cli_lote([]))          # el catálogo no devuelve GTIN
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "singtin.db")))
    pid = _alta(c, titulo_ml="LEGO Star Wars 75339", asin="B0TESTA").json()["id"]
    c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert not (c.get(f"/api/catalogo/{pid}").json()["ml_attributes"].get("GTIN"))


def test_motivo_gtin_vacio_no_se_inventa_si_ml_no_lo_ofrece():
    """Un valor inventado ML lo descarta en silencio y después reclama el GTIN
    como si nunca se hubiera mandado: es peor que no mandarlo."""
    from mercadolibre.listing import valor_por_defecto
    assert valor_por_defecto({"id": "EMPTY_GTIN_REASON", "values": []}) == ""
    assert valor_por_defecto({"id": "EMPTY_GTIN_REASON",
                              "values": ["Es un kit", "Otra razón"]}) == "Otra razón"


def test_los_datos_se_leen_del_titulo_completo_no_del_recortado(tmp_path, monkeypatch):
    """El título de ML está recortado a 60 caracteres y ahí se pierde el final:
    el número de set queda cortado ("...Kylo Ren 752" por 75256) y la cantidad
    de piezas desaparece. Los datos se leen del título completo de Amazon."""
    consultas = []

    cli = _cli_lote([], gtin_catalogo={"gtin": "5702016909937",
                                       "product_id": "MLA77", "nombre": "LEGO 75256"})
    original = cli.ficha_de_catalogo

    def _espiar(query, debe_contener="", limit=5):
        consultas.append((query, debe_contener))
        return original(query, debe_contener, limit)

    cli.ficha_de_catalogo = _espiar
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    completo = ("LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 75256 "
                "Kit de construcción (1005 piezas)")
    c = TestClient(crear_app(db_path=str(tmp_path / "trunc.db")))
    pid = _alta(c, marca="", modelo=completo, titulo_ml=completo[:60],
                asin="B0TRUNC").json()["id"]

    c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})

    assert ("LEGO 75256", "75256") in consultas   # no "752" del título cortado
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702016909937"
    assert attrs["PIECES_NUMBER"] == "1005"


def test_publica_contra_el_catalogo_cuando_encuentra_el_producto(tmp_path, monkeypatch):
    """La vía por la que ML no pide GTIN: publicar contra su producto de
    catálogo, del que toma los atributos de su propia ficha."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados, gtin_catalogo={
        "gtin": "", "product_id": "MLA77", "nombre": "LEGO 75429"}))

    c = TestClient(crear_app(db_path=str(tmp_path / "cat.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert enviados[0]["catalog_product_id"] == "MLA77"
    assert enviados[0]["catalog_listing"] is True
    # Por esta vía no se mandan atributos: los pone MercadoLibre.
    assert "attributes" not in enviados[0]


def test_si_el_catalogo_rechaza_se_publica_por_la_via_normal(tmp_path, monkeypatch):
    """Un producto que no se puede publicar contra el catálogo (ya lo tenés
    publicado, no admite catalogación) no debe quedar trabado."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    enviados = []
    cli = _cli_lote(enviados, gtin_catalogo={"gtin": "", "product_id": "MLA77",
                                             "nombre": "LEGO 75429"})

    def _publicar(item):
        enviados.append(item)
        if item.get("catalog_listing"):
            raise MeliAPIError("no", status=400, cuerpo={"error": "not catalogable"})
        return {"id": "MLA5", "permalink": "http://ml/x"}

    cli.publicar = _publicar
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "fb.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://img/1.jpg"],
                  "ml_attributes": {"GTIN": "5702017424101", "IVA": "21 %"}})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert r.json()["ml_item_id"] == "MLA5"
    assert enviados[0].get("catalog_listing") is True      # intento por catálogo
    assert "family_name" in enviados[1]                    # y después el normal


def test_diagnostico_explica_donde_se_corta(client):
    completo = ("LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 75256 "
                "Kit de construcción (1005 piezas)")
    pid = _alta(client, modelo=completo, titulo_ml=completo[:60]).json()["id"]
    d = client.get(f"/api/catalogo/{pid}/diagnostico").json()
    assert d["numero_de_set"] == "75256"     # del completo, no del recortado
    assert d["piezas"] == "1005"
    assert d["consulta"] == "LEGO 75256"
    assert d["error"]                        # sin sesión de ML, lo dice
