"""Tests de la integración con Tiendanube (sin red).

Vale una aclaración sobre el alcance de estos tests: el contrato de la API de
Tiendanube **no se pudo verificar contra una llamada real** —el entorno donde se
escribió esto bloquea la salida a `api.tiendanube.com`—. Lo que sí verifican es
que el cliente mande lo que la documentación pública describe: el header
`Authentication` (no `Authorization`), el `User-Agent` obligatorio, los precios
como texto y el id de tienda en la URL. Si la primera llamada real desmiente
algo, el test que lo fija dice exactamente qué cambiar.
"""

import json

import pytest

from db import conectar
from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from tiendanube.client import (TiendanubeClient, TiendanubeAPIError,
                               describir_error, precio_texto)
from tiendanube.listing import (construir_producto, faltantes_para_publicar,
                                precio_para_tiendanube, titulo_para_tiendanube,
                                vista_previa)
from tiendanube.oauth import TiendanubeCredenciales, TiendanubeTokenStore


# ---- doble de la red ------------------------------------------------------

class _Respuesta:
    def __init__(self, status=200, cuerpo=None, headers=None, texto=None):
        self.status_code = status
        self._cuerpo = {} if cuerpo is None else cuerpo
        self.headers = headers or {}
        self.text = texto if texto is not None else json.dumps(self._cuerpo)

    def json(self):
        if isinstance(self._cuerpo, str):
            raise ValueError("no es json")
        return self._cuerpo


class _Sesion:
    """Registra cada llamada en vez de salir a la red."""

    def __init__(self, respuestas=None):
        self.llamadas = []
        self._respuestas = list(respuestas or [])

    def request(self, metodo, url, headers=None, timeout=None, **kw):
        self.llamadas.append({"metodo": metodo, "url": url,
                              "headers": headers or {}, **kw})
        if self._respuestas:
            return self._respuestas.pop(0)
        return _Respuesta(200, {})


def _cli(respuestas=None, sesion=None):
    ses = sesion or _Sesion(respuestas)
    return TiendanubeClient(token_provider=lambda: "TOK123",
                            store_provider=lambda: "999888",
                            user_agent="MiApp (yo@ejemplo.com)",
                            session=ses), ses


def _prod(**kw):
    base = dict(asin="B0TN00001", marca="LEGO",
                modelo="LEGO Minecraft The Frog House 21256",
                titulo_ml="LEGO Minecraft Frog House 21256",
                precio_usd=59.93, peso_kg=1.2, stock=3,
                pictures=["http://img/1.jpg"], descripcion="Un set de LEGO.",
                precio_publicado_ars=300000.0)
    base.update(kw)
    return ProductoCatalogo(**base)


# ---- lo que más fácil se hace mal en esta API ------------------------------

def test_el_header_de_auth_se_llama_authentication_no_authorization():
    """Es el error nº1 de esta API: se parece a todas las demás pero el header
    tiene otro nombre, y usar el habitual da 401 sin explicar por qué."""
    cli, ses = _cli([_Respuesta(200, [])])
    cli.listar_productos()

    h = ses.llamadas[0]["headers"]
    assert h["Authentication"] == "bearer TOK123"
    assert "Authorization" not in h


def test_manda_el_user_agent_que_tiendanube_exige():
    """Sin User-Agent identificando la app, Tiendanube rechaza la llamada."""
    cli, ses = _cli([_Respuesta(200, [])])
    cli.listar_productos()

    assert ses.llamadas[0]["headers"]["User-Agent"] == "MiApp (yo@ejemplo.com)"


def test_el_id_de_tienda_va_en_la_url():
    cli, ses = _cli([_Respuesta(200, [])])
    cli.listar_productos()

    assert ses.llamadas[0]["url"] == "https://api.tiendanube.com/v1/999888/products"


def test_los_precios_viajan_como_texto_con_dos_decimales():
    """Mandarlos como número puede perder centavos al serializar."""
    assert precio_texto(1234.5) == "1234.50"
    assert precio_texto(0) == "0.00"

    cli, ses = _cli([_Respuesta(200, {})])
    cli.actualizar_variante("11", "22", precio=99999.456, stock=4)

    cuerpo = ses.llamadas[0]["json"]
    assert cuerpo["price"] == "99999.46"
    assert cuerpo["stock"] == 4
    assert isinstance(cuerpo["price"], str)


def test_precio_y_stock_van_a_la_variante_no_al_producto():
    """En Tiendanube el producto no tiene precio: lo tiene cada variante."""
    cli, ses = _cli([_Respuesta(200, {})])
    cli.actualizar_variante("11", "22", precio=1000, stock=2)

    assert ses.llamadas[0]["url"].endswith("/products/11/variants/22")
    assert ses.llamadas[0]["metodo"] == "PUT"


# ---- límite de ritmo ------------------------------------------------------

def test_reintenta_cuando_tiendanube_limita_el_ritmo(monkeypatch):
    esperas = []
    monkeypatch.setattr("tiendanube.client.time.sleep", lambda s: esperas.append(s))
    cli, ses = _cli([_Respuesta(429, {}, {"X-Rate-Limit-Reset": "3000"}),
                     _Respuesta(200, [])])

    cli.listar_productos()

    assert len(ses.llamadas) == 2
    # El reset viene en milisegundos: 3000ms son 3s, más que los 2s nuestros.
    assert esperas == [3.0]


def test_el_reintento_se_rinde_y_avisa(monkeypatch):
    monkeypatch.setattr("tiendanube.client.time.sleep", lambda s: None)
    cli, _ = _cli([_Respuesta(429, {}) for _ in range(6)])

    with pytest.raises(TiendanubeAPIError) as e:
        cli.listar_productos()
    assert e.value.status == 429


# ---- los errores se muestran crudos ---------------------------------------

def test_el_error_de_validacion_se_lee_entero():
    """Tiendanube contesta {"campo": ["motivo"]} y ese detalle es lo único que
    dice qué hay que arreglar."""
    assert "name" in describir_error({"name": ["can't be blank"]})
    assert "can't be blank" in describir_error({"name": ["can't be blank"]})
    assert describir_error({"message": "algo"}) == "algo"
    assert describir_error("texto pelado") == "texto pelado"


def test_un_error_de_la_api_llega_con_su_cuerpo():
    cli, _ = _cli([_Respuesta(422, {"name": ["can't be blank"]})])

    with pytest.raises(TiendanubeAPIError) as e:
        cli.crear_producto({})
    assert e.value.status == 422
    assert e.value.cuerpo == {"name": ["can't be blank"]}


def test_una_respuesta_sin_cuerpo_no_rompe():
    """DELETE contesta 204 sin JSON; pedirle .json() explotaría."""
    cli, _ = _cli([_Respuesta(204, {}, texto="")])
    assert cli.borrar_producto("11") == {}


# ---- armado del producto --------------------------------------------------

def test_el_nombre_y_la_descripcion_van_por_idioma():
    """Son diccionarios, no strings: un string pelado lo rechaza."""
    item = construir_producto(_prod())

    assert item["name"] == {"es": "LEGO Minecraft The Frog House 21256"}
    assert item["description"]["es"] == "Un set de LEGO."


def test_usa_el_nombre_largo_y_no_el_titulo_recortado_de_ml():
    """El recorte a 60 caracteres es un límite de MercadoLibre que acá no rige:
    aplicarlo igual sería perder texto sin ningún motivo."""
    p = _prod(modelo="LEGO Minecraft The Frog House Building Toy for Kids 21256",
              titulo_ml="LEGO Minecraft Frog House 21256")
    assert titulo_para_tiendanube(p).startswith("LEGO Minecraft The Frog House Building")


def test_el_producto_lleva_una_variante_con_precio_y_stock():
    item = construir_producto(_prod(stock=3))

    assert len(item["variants"]) == 1
    assert item["variants"][0]["price"] == "300000.00"
    assert item["variants"][0]["stock"] == 3


def test_el_peso_va_solo_si_esta_cargado():
    assert "weight" in construir_producto(_prod(peso_kg=1.2))["variants"][0]
    assert "weight" not in construir_producto(_prod(peso_kg=0))["variants"][0]


def test_las_fotos_van_como_lista_de_src():
    item = construir_producto(_prod(pictures=["http://a/1.jpg", "http://a/2.jpg"]))
    assert item["images"] == [{"src": "http://a/1.jpg"}, {"src": "http://a/2.jpg"}]


# ---- el precio de la tienda propia ----------------------------------------

def test_sin_ajuste_el_precio_es_el_mismo_que_en_mercadolibre():
    """El default no toma una decisión comercial por el usuario."""
    assert precio_para_tiendanube(_prod(precio_publicado_ars=300000.0)) == 300000.0


def test_el_ajuste_corrige_el_precio_de_mercadolibre():
    p = _prod(precio_publicado_ars=300000.0)
    assert precio_para_tiendanube(p, -15) == 255000.0
    assert precio_para_tiendanube(p, 10) == 330000.0


def test_sin_precio_publicado_se_usa_el_sugerido():
    p = _prod(precio_publicado_ars=None, precio_sugerido_ars=280000.0)
    assert precio_para_tiendanube(p) == 280000.0


def test_falta_lo_que_impide_publicar():
    assert faltantes_para_publicar(_prod()) == []
    assert "al menos una foto" in faltantes_para_publicar(_prod(pictures=[]))
    assert "precio de venta" in faltantes_para_publicar(
        _prod(precio_publicado_ars=0, precio_sugerido_ars=0))


def test_la_previa_muestra_la_diferencia_con_mercadolibre():
    """Es la decisión que se está tomando: cuánto más barato se vende en la
    tienda propia. Verla antes de publicar evita enterarse después."""
    v = vista_previa(_prod(precio_publicado_ars=300000.0), -15)

    assert v["precio_en_ml"] == 300000.0
    assert v["precio_ars"] == 255000.0
    assert v["diferencia_ars"] == -45000.0


# ---- credenciales y token -------------------------------------------------

def test_las_credenciales_salen_del_entorno(monkeypatch):
    monkeypatch.setenv("TIENDANUBE_CLIENT_ID", "1234")
    monkeypatch.setenv("TIENDANUBE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("TIENDANUBE_USER_AGENT", "App (a@b.com)")
    cred = TiendanubeCredenciales.desde_entorno()

    assert cred.configurado and cred.user_agent == "App (a@b.com)"


def test_sin_credenciales_no_esta_configurado(monkeypatch):
    monkeypatch.delenv("TIENDANUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("TIENDANUBE_CLIENT_SECRET", raising=False)
    assert TiendanubeCredenciales.desde_entorno().configurado is False


def test_la_url_de_autorizacion_lleva_el_app_id_en_la_ruta():
    """No va como parámetro, que es lo que uno esperaría de un OAuth."""
    from tiendanube.oauth import TiendanubeOAuth
    conn = conectar(":memory:")
    cred = TiendanubeCredenciales("4321", "sec", "https://x/cb", "App (a@b.com)")
    url = TiendanubeOAuth(cred, TiendanubeTokenStore(conn)).url_autorizacion()

    assert url.startswith("https://www.tiendanube.com/apps/4321/authorize?")


def test_el_token_guarda_el_id_de_tienda():
    """Viene como `user_id` pero es el id de tienda, y sin él no hay URL a la
    que pegarle: un token sin tienda no es una sesión."""
    store = TiendanubeTokenStore(conectar(":memory:"))
    assert store.hay_sesion() is False

    store.guardar({"access_token": "T", "user_id": 999888, "scope": "write_products"})

    fila = store.leer()
    assert fila["store_id"] == "999888" and store.hay_sesion() is True


def test_un_token_sin_tienda_no_cuenta_como_sesion():
    store = TiendanubeTokenStore(conectar(":memory:"))
    store.guardar({"access_token": "T", "user_id": None})
    assert store.hay_sesion() is False


# ---- vínculo con el catálogo ----------------------------------------------

@pytest.fixture()
def cat():
    return Catalogo(conectar(":memory:"), cfg=Config())


def test_registrar_guarda_los_dos_ids(cat):
    """Sin el de variante no se puede tocar después ni precio ni stock."""
    p = cat.agregar(_prod())

    p2 = cat.registrar_tiendanube(p.id, 555, 777, "https://mitienda/p/1")

    assert p2.tn_product_id == "555" and p2.tn_variant_id == "777"
    assert cat.obtener(p.id).tn_permalink == "https://mitienda/p/1"


def test_olvidar_suelta_el_vinculo(cat):
    p = cat.agregar(_prod())
    cat.registrar_tiendanube(p.id, 555, 777)

    p2 = cat.olvidar_tiendanube(p.id)

    assert p2.tn_product_id == "" and p2.tn_variant_id == ""
    assert cat.en_tiendanube() == []


def test_en_tiendanube_lista_solo_los_publicados_alla(cat):
    a = cat.agregar(_prod(asin="B0TN00002"))
    cat.agregar(_prod(asin="B0TN00003"))
    cat.registrar_tiendanube(a.id, 555, 777)

    assert [p.id for p in cat.en_tiendanube()] == [a.id]


def test_el_vinculo_sobrevive_al_guardado(cat):
    p = cat.agregar(_prod())
    cat.registrar_tiendanube(p.id, 555, 777)
    cat.actualizar_stock(p.id, 9)

    assert cat.obtener(p.id).tn_product_id == "555"


def test_el_ajuste_se_guarda_y_se_valida(cat):
    assert cat.tn_ajuste_pct == 0.0
    cat.tn_ajuste_pct = -15
    assert cat.tn_ajuste_pct == -15.0
    with pytest.raises(ValueError):
        cat.tn_ajuste_pct = -95
    with pytest.raises(ValueError):
        cat.tn_ajuste_pct = "mucho"
