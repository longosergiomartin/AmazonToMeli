"""Conversor ASIN ⇄ código de barras (EAN/UPC/ISBN)."""

import pytest

from codigos import ASIN, GTIN, convertir, convertir_lote, tipo_de


@pytest.mark.parametrize("entrada, esperado", [
    ("B075SDMMMV", ASIN),
    ("b075sdmmmv", ASIN),
    ("https://www.amazon.com/dp/B0BBHHT8LY", ASIN),
    ("https://www.amazon.com/gp/product/B0BBHHT8LY", ASIN),
    ("5702016914498", GTIN),      # EAN-13
    ("673419281423", GTIN),       # UPC-12
    ("5702016914498 ", GTIN),
    ("570-2016-914498", GTIN),
    ("", ""),
    ("no soy un codigo", ""),
    ("12345", ""),                # ni ASIN ni largo válido de GTIN
])
def test_detecta_el_tipo(entrada, esperado):
    assert tipo_de(entrada) == esperado


def test_asin_a_gtin():
    r = convertir("B075SDMMMV",
                  buscar_gtin_fn=lambda a: {"ok": True, "gtin": "5702016914498",
                                            "fuente": "amazon", "mensaje": "listo"})
    assert r["ok"] and r["asin"] == "B075SDMMMV" and r["gtin"] == "5702016914498"


def test_gtin_a_asin():
    r = convertir("5702016914498",
                  buscar_asin_fn=lambda g: {"ok": True, "asin": "B075SDMMMV",
                                            "titulo": "LEGO Star Wars",
                                            "bloqueado": False, "mensaje": ""})
    assert r["ok"] and r["asin"] == "B075SDMMMV" and r["gtin"] == "5702016914498"
    assert r["titulo"] == "LEGO Star Wars"


def test_entrada_invalida_no_sale_a_la_red():
    llamadas = []

    def _no_llamar(x):
        llamadas.append(x)
        return {}

    r = convertir("cualquier cosa", buscar_gtin_fn=_no_llamar,
                  buscar_asin_fn=_no_llamar)
    assert r["ok"] is False and not llamadas
    assert "no parece" in r["mensaje"].lower()


class _CatalogoFalso:
    def __init__(self, productos):
        self._p = productos

    def todos(self):
        return self._p


class _Prod:
    def __init__(self, asin, gtin, titulo="Producto"):
        self.asin, self.titulo_ml, self.modelo = asin, titulo, titulo
        self.ml_attributes = {"GTIN": gtin} if gtin else {}


def test_el_catalogo_propio_se_consulta_primero():
    """Si ya lo tenemos cargado, no se gasta un pedido a Amazon."""
    llamadas = []
    cat = _CatalogoFalso([_Prod("B075SDMMMV", "5702016914498", "LEGO X-Wing")])
    r = convertir("B075SDMMMV", catalogo=cat,
                  buscar_gtin_fn=lambda a: llamadas.append(a) or {})
    assert r["ok"] and r["gtin"] == "5702016914498"
    assert r["fuente"] == "tu catálogo" and not llamadas


def test_el_catalogo_propio_tambien_va_al_reves():
    cat = _CatalogoFalso([_Prod("B075SDMMMV", "5702016914498")])
    r = convertir("5702016914498", catalogo=cat,
                  buscar_asin_fn=lambda g: {"ok": False})
    assert r["asin"] == "B075SDMMMV" and r["fuente"] == "tu catálogo"


class _MLFalso:
    def __init__(self, gtin):
        self.gtin, self.consultas = gtin, []

    def gtin_de_catalogo(self, query, debe_contener="", limit=5):
        self.consultas.append(query)
        return {"gtin": self.gtin, "nombre": "LEGO 75304"} if self.gtin else {}


def test_usa_el_catalogo_de_mercadolibre_antes_que_amazon():
    llamadas = []
    cat = _CatalogoFalso([_Prod("B0LEGO0001", "", "LEGO Star Wars 75304 Kit")])
    ml = _MLFalso("5702016914498")
    r = convertir("B0LEGO0001", catalogo=cat, cliente_ml=ml,
                  buscar_gtin_fn=lambda a: llamadas.append(a) or {})
    assert r["ok"] and r["gtin"] == "5702016914498"
    assert r["fuente"] == "catálogo de MercadoLibre"
    assert ml.consultas == ["75304"] and not llamadas


def test_lote_no_repite_entradas_duplicadas():
    llamadas = []

    def _buscar(a):
        llamadas.append(a)
        return {"ok": True, "gtin": "5702016914498", "fuente": "amazon"}

    d = convertir_lote(["B075SDMMMV", "B075SDMMMV", " "], buscar_gtin_fn=_buscar,
                       dormir=lambda s: None)
    assert d["total"] == 1 and llamadas == ["B075SDMMMV"]


def test_lote_frena_si_amazon_nos_limita():
    """Misma disciplina que la cola de importación: ante un bloqueo se frena."""
    def _buscar_asin(g):
        return {"ok": False, "asin": "", "bloqueado": True,
                "mensaje": "Amazon respondió 503."}

    d = convertir_lote(["5702016914498", "673419281423", "5702016616989"],
                       buscar_asin_fn=_buscar_asin, dormir=lambda s: None)
    assert d["detenido"] is True
    assert d["total"] == 1 and d["pendientes"] == 2


def test_lote_hace_pausa_entre_consultas_a_la_red():
    pausas = []
    d = convertir_lote(["B075SDMMMV", "B0BBHHT8LY"], pausa_seg=1.5,
                       buscar_gtin_fn=lambda a: {"ok": True, "gtin": "5702016914498"},
                       dormir=pausas.append)
    assert pausas == [1.5] and d["convertidos"] == 2


def test_lote_no_pausa_cuando_sale_del_catalogo():
    """Sin salir a la red no hay a quién cuidar: no se pierde tiempo."""
    pausas = []
    cat = _CatalogoFalso([_Prod("B075SDMMMV", "5702016914498"),
                          _Prod("B0BBHHT8LY", "673419281423")])
    convertir_lote(["B075SDMMMV", "B0BBHHT8LY"], catalogo=cat, dormir=pausas.append)
    assert pausas == []
