"""Tests de las rutas de Tiendanube (sin red)."""

import pytest
from fastapi.testclient import TestClient

from api.server import crear_app
from tiendanube.client import TiendanubeAPIError


def _alta(c, **kw):
    body = dict(asin="B0TN00001", marca="LEGO",
                modelo="LEGO Minecraft Frog House 21256",
                titulo_ml="LEGO Minecraft Frog House 21256",
                precio_usd=59.93, peso_kg=1.2, costo_envio_usd=25.0,
                regimen="landed", stock=3)
    body.update(kw)
    return c.post("/api/catalogo", json=body)


class _CliTN:
    """Cliente falso: registra lo que se le pide y devuelve lo que se le diga."""

    def __init__(self, crear=None, falla_crear=None):
        self.creados, self.variantes, self.publicados, self.borrados = [], [], [], []
        self._crear = crear or {"id": 555, "variants": [{"id": 777}],
                                "canonical_url": "https://mitienda/p/frog"}
        self._falla_crear = falla_crear

    def probar(self):
        return {"ok": True, "store_id": "999888", "productos_visibles": 0}

    def crear_producto(self, payload):
        if self._falla_crear:
            raise self._falla_crear
        self.creados.append(payload)
        return self._crear

    def actualizar_variante(self, pid, vid, precio=None, stock=None):
        self.variantes.append({"pid": str(pid), "vid": str(vid),
                               "precio": precio, "stock": stock})
        return {}

    def publicar(self, pid, publicado):
        self.publicados.append((str(pid), bool(publicado)))
        return {}

    def borrar_producto(self, pid):
        self.borrados.append(str(pid)); return {}


def _con_tn(monkeypatch, cli):
    import api.tiendanube_routes as rutas
    monkeypatch.setattr(rutas, "TiendanubeClient", lambda *a, **k: cli)
    monkeypatch.setattr(rutas.TiendanubeCredenciales, "configurado",
                        property(lambda self: True))
    monkeypatch.setattr(rutas.TiendanubeTokenStore, "hay_sesion", lambda self: True)


def _app(tmp_path, monkeypatch, nombre, cli=None):
    cli = cli or _CliTN()
    _con_tn(monkeypatch, cli)
    c = TestClient(crear_app(db_path=str(tmp_path / nombre)))
    pid = _alta(c).json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.patch(f"/api/catalogo/{pid}/precio", json={"precio": 300000})
    return c, cli, pid


# ---- sin sesión no se toca la tienda --------------------------------------

def test_sin_credenciales_avisa_en_vez_de_fallar(tmp_path, monkeypatch):
    import api.tiendanube_routes as rutas
    monkeypatch.setattr(rutas.TiendanubeCredenciales, "configurado",
                        property(lambda self: False))
    c = TestClient(crear_app(db_path=str(tmp_path / "sincred.db")))
    pid = _alta(c).json()["id"]

    r = c.post(f"/api/tiendanube/{pid}/publicar")

    assert r.status_code == 400
    assert "TIENDANUBE_CLIENT_ID" in r.json()["detail"]


def test_con_credenciales_pero_sin_autorizar_pide_conectar(tmp_path, monkeypatch):
    import api.tiendanube_routes as rutas
    monkeypatch.setattr(rutas.TiendanubeCredenciales, "configurado",
                        property(lambda self: True))
    monkeypatch.setattr(rutas.TiendanubeTokenStore, "hay_sesion", lambda self: False)
    c = TestClient(crear_app(db_path=str(tmp_path / "sinses.db")))
    pid = _alta(c).json()["id"]

    r = c.post(f"/api/tiendanube/{pid}/publicar")

    assert r.status_code == 401
    assert "autorizar" in r.json()["detail"]


def test_el_estado_dice_si_esta_conectada(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "est.db")))
    d = c.get("/oauth/tiendanube/status").json()
    assert d["conectado"] is False and "configurado" in d


def test_el_estado_muestra_la_url_de_autorizacion(tmp_path, monkeypatch):
    """Si el botón de conectar no lleva a ninguna parte, esta URL es lo único
    que dice por qué: el App ID va en la RUTA, así que uno equivocado da un 404
    del sitio de Tiendanube sin ningún mensaje de error que lo explique."""
    monkeypatch.setenv("TIENDANUBE_CLIENT_ID", "998877")
    monkeypatch.setenv("TIENDANUBE_CLIENT_SECRET", "sec")
    c = TestClient(crear_app(db_path=str(tmp_path / "url.db")))

    d = c.get("/oauth/tiendanube/status").json()

    assert d["url_autorizacion"].startswith(
        "https://www.tiendanube.com/apps/998877/authorize")
    assert d["client_id_es_numerico"] is True


def test_el_estado_avisa_si_el_app_id_no_es_un_numero(tmp_path, monkeypatch):
    """El error fácil del portal: copiar el «client id» alfanumérico de otra
    pantalla en vez del App ID."""
    monkeypatch.setenv("TIENDANUBE_CLIENT_ID", "abc-def-123")
    monkeypatch.setenv("TIENDANUBE_CLIENT_SECRET", "sec")
    c = TestClient(crear_app(db_path=str(tmp_path / "url2.db")))

    d = c.get("/oauth/tiendanube/status").json()

    assert d["client_id_es_numerico"] is False
    assert d["client_id_largo"] == 11


def test_sin_credenciales_no_se_arma_ninguna_url(tmp_path, monkeypatch):
    monkeypatch.delenv("TIENDANUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("TIENDANUBE_CLIENT_SECRET", raising=False)
    c = TestClient(crear_app(db_path=str(tmp_path / "url3.db")))

    assert c.get("/oauth/tiendanube/status").json()["url_autorizacion"] == ""


# ---- publicar -------------------------------------------------------------

def test_publicar_manda_el_producto_y_guarda_los_ids(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "pub.db")

    d = c.post(f"/api/tiendanube/{pid}/publicar").json()

    assert d["tn_product_id"] == "555"
    assert cli.creados[0]["name"]["es"].startswith("LEGO Minecraft")
    assert cli.creados[0]["variants"][0]["price"] == "300000.00"
    guardado = c.get(f"/api/catalogo/{pid}").json()
    assert guardado["tn_product_id"] == "555" and guardado["tn_variant_id"] == "777"
    assert guardado["tn_permalink"] == "https://mitienda/p/frog"


def test_publicar_dos_veces_no_duplica(tmp_path, monkeypatch):
    """El mismo error que ya se cometió con MercadoLibre: republicar creaba un
    ítem nuevo en vez de avisar."""
    c, cli, pid = _app(tmp_path, monkeypatch, "dup.db")
    c.post(f"/api/tiendanube/{pid}/publicar")

    r = c.post(f"/api/tiendanube/{pid}/publicar")

    assert r.status_code == 409
    assert "duplicado" in r.json()["detail"]
    assert len(cli.creados) == 1


def test_sin_foto_no_se_intenta_publicar(tmp_path, monkeypatch):
    """Gastar la llamada para que la rechacen es peor que decirlo antes."""
    cli = _CliTN()
    _con_tn(monkeypatch, cli)
    c = TestClient(crear_app(db_path=str(tmp_path / "sinfoto.db")))
    pid = _alta(c).json()["id"]
    c.patch(f"/api/catalogo/{pid}/precio", json={"precio": 300000})

    r = c.post(f"/api/tiendanube/{pid}/publicar")

    assert r.status_code == 422
    assert "al menos una foto" in r.json()["detail"]["faltantes"]
    assert cli.creados == []


def test_el_rechazo_de_tiendanube_llega_entero(tmp_path, monkeypatch):
    """El texto de la API es lo único que dice qué arreglar: no se reemplaza."""
    cli = _CliTN(falla_crear=TiendanubeAPIError(
        "Tiendanube POST /products → 422", status=422,
        cuerpo={"name": ["can't be blank"]}))
    c, _, pid = _app(tmp_path, monkeypatch, "err.db", cli)

    r = c.post(f"/api/tiendanube/{pid}/publicar")

    assert r.status_code == 502
    assert "can't be blank" in r.json()["detail"]


def test_publicar_en_lote_informa_uno_por_uno(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "lote.db")
    otro = _alta(c, asin="B0TN00002", modelo="LEGO otro").json()["id"]

    d = c.post("/api/tiendanube/lote/publicar", json={"ids": [pid, otro]}).json()

    assert d["publicados"] == 1 and d["fallas"] == 1
    falla = [r for r in d["resultados"] if not r["ok"]][0]
    assert "foto" in falla["error"]        # el segundo no tiene foto ni precio


# ---- precio de la tienda propia -------------------------------------------

def test_el_ajuste_cambia_el_precio_que_se_publica(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "ajuste.db")
    c.post("/api/tiendanube/config", json={"ajuste_pct": -15})

    c.post(f"/api/tiendanube/{pid}/publicar")

    assert cli.creados[0]["variants"][0]["price"] == "255000.00"


def test_la_previa_no_toca_la_tienda(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "previa.db")
    c.post("/api/tiendanube/config", json={"ajuste_pct": -15})

    d = c.get(f"/api/tiendanube/{pid}/previa").json()

    assert d["precio_en_ml"] == 300000.0 and d["precio_ars"] == 255000.0
    assert cli.creados == []


def test_un_ajuste_disparatado_se_rechaza(tmp_path, monkeypatch):
    c, _, _ = _app(tmp_path, monkeypatch, "aj2.db")
    assert c.post("/api/tiendanube/config",
                  json={"ajuste_pct": -95}).status_code == 422


# ---- sincronizar ----------------------------------------------------------

def test_sincronizar_empuja_precio_y_stock(tmp_path, monkeypatch):
    """Es lo que evita que los dos canales se desalineen al corregir un costo."""
    c, cli, pid = _app(tmp_path, monkeypatch, "sinc.db")
    c.post(f"/api/tiendanube/{pid}/publicar")
    c.patch(f"/api/catalogo/{pid}/precio", json={"precio": 420000})
    c.patch(f"/api/catalogo/{pid}/stock", json={"stock": 7})

    d = c.post(f"/api/tiendanube/{pid}/sincronizar").json()

    assert d["precio_ars"] == 420000.0 and d["stock"] == 7
    assert cli.variantes[-1] == {"pid": "555", "vid": "777",
                                 "precio": 420000.0, "stock": 7}


def test_lo_pausado_en_la_herramienta_se_despublica_en_la_tienda(tmp_path, monkeypatch):
    """Seguir vendiéndolo en la tienda propia es el mismo problema en otro
    canal: si se pausó porque Amazon no tiene stock, allá tampoco va."""
    c, cli, pid = _app(tmp_path, monkeypatch, "pausa.db")
    c.post(f"/api/tiendanube/{pid}/publicar")
    c.post(f"/api/catalogo/{pid}/pausar")

    c.post(f"/api/tiendanube/{pid}/sincronizar")

    assert cli.publicados[-1] == ("555", False)


def test_reactivar_en_la_herramienta_vuelve_a_publicar_alla(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "react.db")
    c.post(f"/api/tiendanube/{pid}/publicar")
    c.post(f"/api/catalogo/{pid}/pausar")
    c.post(f"/api/tiendanube/{pid}/sincronizar")
    c.post(f"/api/catalogo/{pid}/reactivar", json={})

    c.post(f"/api/tiendanube/{pid}/sincronizar")

    assert cli.publicados[-1] == ("555", True)


def test_no_se_sincroniza_lo_que_no_esta_publicado_alla(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "nosinc.db")

    r = c.post(f"/api/tiendanube/{pid}/sincronizar")

    assert r.status_code == 409
    assert cli.variantes == []


def test_sincronizar_todo_sin_ids_recorre_lo_publicado(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "sinctodo.db")
    c.post(f"/api/tiendanube/{pid}/publicar")

    d = c.post("/api/tiendanube/lote/sincronizar", json={}).json()

    assert d["sincronizados"] == 1 and d["fallas"] == 0


def test_sincronizar_sin_nada_publicado_no_es_un_error(tmp_path, monkeypatch):
    c, _, _ = _app(tmp_path, monkeypatch, "vacio.db")
    d = c.post("/api/tiendanube/lote/sincronizar", json={}).json()
    assert d["sincronizados"] == 0 and d["resultados"] == []


# ---- soltar el vínculo ----------------------------------------------------

def test_olvidar_suelta_el_vinculo_sin_tocar_la_tienda(tmp_path, monkeypatch):
    c, cli, pid = _app(tmp_path, monkeypatch, "olv.db")
    c.post(f"/api/tiendanube/{pid}/publicar")

    c.post(f"/api/tiendanube/{pid}/olvidar")

    assert c.get(f"/api/catalogo/{pid}").json()["tn_product_id"] == ""
    assert cli.borrados == []       # el producto sigue en la tienda
