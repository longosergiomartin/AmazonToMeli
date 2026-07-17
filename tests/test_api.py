"""Tests de la API local (SQLite en directorio temporal, sin red)."""

import pytest
from fastapi.testclient import TestClient

from api.server import crear_app


@pytest.fixture()
def client(tmp_path):
    app = crear_app(db_path=str(tmp_path / "test.db"))
    return TestClient(app)


def _capturar_amazon(client, asin="B0TEST1234"):
    return client.get("/capture", params={
        "site": "amazon", "asin": asin,
        "titulo": "Waders de prueba HISEA",
        "precio_usd": "118.99", "landed_usd": "203.34",
        "link": "https://www.amazon.com/dp/B0TEST1234",
    })


def test_captura_amazon_y_lectura(client):
    assert _capturar_amazon(client).status_code == 200
    p = client.get("/product/B0TEST1234").json()
    assert p["titulo"] == "Waders de prueba HISEA"
    assert p["precio_amazon_usd"] == 118.99
    assert p["precio_landed_usd"] == 203.34
    assert p["precio_meli_ars"] is None


def test_captura_meli_se_asocia_al_asin(client):
    _capturar_amazon(client)
    r = client.get("/capture", params={
        "site": "meli", "asin": "B0TEST1234", "precio_ars": "717999",
    })
    assert r.status_code == 200
    p = client.get("/product/B0TEST1234").json()
    assert p["precio_meli_ars"] == 717999.0


def test_historial_acumula_puntos_fechados(client):
    _capturar_amazon(client)
    client.get("/capture", params={"site": "amazon", "asin": "B0TEST1234",
                                   "titulo": "Waders", "precio_usd": "110.00"})
    h = client.get("/history/B0TEST1234").json()
    assert len(h) == 2
    assert all("ts" in punto for punto in h)
    # El último precio conocido es el más reciente.
    p = client.get("/product/B0TEST1234").json()
    assert p["precio_amazon_usd"] == 110.00


def test_search_por_titulo(client):
    _capturar_amazon(client)
    assert client.get("/search", params={"q": "waders"}).json() != []
    assert client.get("/search", params={"q": "inexistente"}).json() == []


def test_export_csv_compatible_con_cli(client, tmp_path):
    _capturar_amazon(client)
    client.get("/capture", params={"site": "meli", "asin": "B0TEST1234",
                                   "precio_ars": "717999"})
    csv_texto = client.get("/export.csv").text
    ruta = tmp_path / "export.csv"
    ruta.write_text(csv_texto, encoding="utf-8")

    # El CSV exportado tiene que poder entrar directo al pipeline existente.
    from arbitraje.amazon import ManualProvider
    from arbitraje.evaluador import evaluar_muchos
    productos = ManualProvider.desde_csv(ruta).cargar()
    assert len(productos) == 1
    assert productos[0].precio_landed_usd == 203.34
    ops = evaluar_muchos(productos, usar_api=False)
    assert len(ops) == 1
    assert ops[0].regimen == "landed"


def test_producto_inexistente_404(client):
    assert client.get("/product/NOEXISTE99").status_code == 404


def test_capture_site_invalido_400(client):
    r = client.get("/capture", params={"site": "ebay", "asin": "X"})
    assert r.status_code == 400
