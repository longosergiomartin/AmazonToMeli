"""Búsqueda del video del producto en YouTube.

Lo que se prueba acá no es tanto que encuentre, sino que **no acepte el video
equivocado**: publicar en la ficha de un producto el video de otro es peor que
no publicar ninguno.
"""

import pytest

import videos_youtube
from videos_youtube import buscar_video, configurado


class _RespFalsa:
    def __init__(self, items, status=200):
        self.status_code = status
        self._items = items

    def json(self):
        return {"items": self._items}


def _video(vid, titulo, canal="Un canal cualquiera"):
    return {"id": {"videoId": vid},
            "snippet": {"title": titulo, "channelTitle": canal}}


def _con(monkeypatch, items, status=200):
    pedidos = []

    def _get(url, **kw):
        pedidos.append(kw.get("params") or {})
        return _RespFalsa(items, status)

    monkeypatch.setattr(videos_youtube.requests, "get", _get)
    return pedidos


# ---- lo que tiene que encontrar ----------------------------------------

def test_encuentra_el_video_oficial_del_set(monkeypatch):
    _con(monkeypatch, [
        _video("aaaaaaaaaaa", "LEGO Icons 10282 adidas Originals Superstar",
               canal="LEGO")])
    r = buscar_video("adidas Originals Superstar Kit de construcción",
                     marca="LEGO", numero_set="10282", api_key="X")
    assert r["video_id"] == "aaaaaaaaaaa"
    assert r["canal"] == "LEGO"


def test_alcanza_con_que_la_marca_este_en_el_canal(monkeypatch):
    """El canal oficial no repite la marca en cada título."""
    _con(monkeypatch, [
        _video("bbbbbbbbbbb", "Designer Video 10282 adidas Superstar",
               canal="LEGO")])
    r = buscar_video("adidas Superstar", marca="LEGO", numero_set="10282",
                     api_key="X")
    assert r["video_id"] == "bbbbbbbbbbb"


def test_saltea_los_de_arriba_hasta_el_que_corresponde(monkeypatch):
    """El primer resultado de YouTube casi nunca es el oficial."""
    _con(monkeypatch, [
        _video("ccccccccccc", "UNBOXING! Compré 50 sets de LEGO", canal="Un youtuber"),
        _video("ddddddddddd", "LEGO 75551 Minions Brick-Built", canal="LEGO"),
    ])
    r = buscar_video("Minions El surgimiento de Gru", marca="LEGO",
                     numero_set="75551", api_key="X")
    assert r["video_id"] == "ddddddddddd"


# ---- lo que NO tiene que aceptar ---------------------------------------

def test_rechaza_el_video_de_otro_set_de_la_misma_marca(monkeypatch):
    """El error más caro: mismo fabricante, misma línea, producto distinto."""
    _con(monkeypatch, [
        _video("eeeeeeeeeee", "LEGO Architecture 21029 Buckingham Palace",
               canal="LEGO")])
    assert buscar_video("Architecture Estatua de la Libertad", marca="LEGO",
                        numero_set="21042", api_key="X") == {}


def test_rechaza_reseñas_de_terceros(monkeypatch):
    """El caso que obliga a mirar el canal y no el título: una reseña dice la
    marca y el número igual que el video oficial."""
    _con(monkeypatch, [
        _video("fffffffffff", "LEGO Architecture 21042 — mi reseña completa",
               canal="Ladrillos y Café")])
    assert buscar_video("Architecture Estatua de la Libertad", marca="LEGO",
                        numero_set="21042", api_key="X") == {}


def test_el_titulo_no_alcanza_para_dar_por_oficial_un_video(monkeypatch):
    """Aunque el título diga la marca cinco veces, si el canal no es de la
    marca no se acepta."""
    _con(monkeypatch, [
        _video("kkkkkkkkkkk", "LEGO LEGO LEGO 21042 LEGO", canal="Canal random")])
    assert buscar_video("Estatua de la Libertad", marca="LEGO",
                        numero_set="21042", api_key="X") == {}


def test_una_marca_no_matchea_con_media_palabra(monkeypatch):
    _con(monkeypatch, [
        _video("ggggggggggg", "Fisher juguete 12345", canal="Alguien")])
    assert buscar_video("Juguete", marca="Fisher Price", numero_set="12345",
                        api_key="X") == {}


def test_sin_numero_de_modelo_se_exige_parecido(monkeypatch):
    """Sin número no hay desempate fuerte: el título tiene que parecerse."""
    _con(monkeypatch, [
        _video("hhhhhhhhhhh", "HISEA Rubber Boots", canal="HISEA")])
    assert buscar_video("HISEA Waders de pesca con botas de neopreno",
                        marca="HISEA", api_key="X") == {}

    _con(monkeypatch, [
        _video("iiiiiiiiiii", "HISEA Waders de pesca con botas de neopreno",
               canal="HISEA")])
    r = buscar_video("HISEA Waders de pesca con botas de neopreno",
                     marca="HISEA", api_key="X")
    assert r["video_id"] == "iiiiiiiiiii"


# ---- que no rompa nunca -------------------------------------------------

def test_sin_clave_no_llama_a_la_api(monkeypatch):
    pedidos = _con(monkeypatch, [_video("jjjjjjjjjjj", "LEGO 10282", canal="LEGO")])
    assert buscar_video("Set", marca="LEGO", api_key="") == {}
    assert pedidos == []


def test_un_error_de_la_api_no_explota(monkeypatch):
    _con(monkeypatch, [], status=403)          # cuota agotada, clave inválida
    assert buscar_video("Set", marca="LEGO", numero_set="10282",
                        api_key="X") == {}

    def _revienta(url, **kw):
        raise videos_youtube.requests.RequestException("sin red")

    monkeypatch.setattr(videos_youtube.requests, "get", _revienta)
    assert buscar_video("Set", marca="LEGO", api_key="X") == {}


def test_sin_resultados_devuelve_vacio(monkeypatch):
    _con(monkeypatch, [])
    assert buscar_video("Set rarísimo", marca="LEGO", numero_set="99999",
                        api_key="X") == {}


def test_configurado_mira_la_variable_de_entorno(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert configurado() is False
    monkeypatch.setenv("YOUTUBE_API_KEY", "una-clave")
    assert configurado() is True


def test_el_numero_va_en_la_consulta(monkeypatch):
    """Sin el número, YouTube devuelve la línea entera y nada específico."""
    pedidos = _con(monkeypatch, [])
    buscar_video("Estatua de la Libertad", marca="LEGO", numero_set="21042",
                 api_key="X")
    assert "21042" in pedidos[0]["q"] and "LEGO" in pedidos[0]["q"]


# ---- canales de terceros elegidos para una marca ------------------------

def test_usa_un_canal_de_confianza_si_no_hay_oficial(monkeypatch):
    _con(monkeypatch, [
        _video("lllllllllll", "LEGO Architecture 21042 Statue of Liberty Speed Build",
               canal="AustrianBrickFan")])
    r = buscar_video("Architecture Estatua de la Libertad", marca="LEGO",
                     numero_set="21042", api_key="X")
    assert r["video_id"] == "lllllllllll"
    assert r["canal"] == "AustrianBrickFan"
    assert r["oficial"] is False


def test_el_canal_oficial_gana_aunque_venga_despues(monkeypatch):
    """El de confianza es segunda opción, no un empate: si LEGO tiene el video,
    va el de LEGO aunque YouTube lo devuelva más abajo."""
    _con(monkeypatch, [
        _video("mmmmmmmmmmm", "LEGO 21042 Statue of Liberty", canal="AustrianBrickFan"),
        _video("nnnnnnnnnnn", "21042 Statue of Liberty Designer Video", canal="LEGO"),
    ])
    r = buscar_video("Estatua de la Libertad", marca="LEGO", numero_set="21042",
                     api_key="X")
    assert r["video_id"] == "nnnnnnnnnnn" and r["oficial"] is True


def test_el_canal_de_confianza_no_vale_para_otra_marca(monkeypatch):
    """Un video de AustrianBrickFan que diga 12345 no dice nada sobre un
    Fisher-Price con ese número de modelo."""
    _con(monkeypatch, [
        _video("ooooooooooo", "Algo con 12345", canal="AustrianBrickFan")])
    assert buscar_video("Juguete", marca="Fisher Price", numero_set="12345",
                        api_key="X") == {}


def test_el_canal_de_confianza_exige_el_numero(monkeypatch):
    """En un canal de terceros, sin número no hay forma de saber de qué
    producto es: suben de todas las marcas."""
    _con(monkeypatch, [
        _video("ppppppppppp", "Los mejores sets del año", canal="AustrianBrickFan")])
    assert buscar_video("Estatua de la Libertad", marca="LEGO",
                        api_key="X") == {}


def test_el_segundo_canal_de_confianza_tambien_vale(monkeypatch):
    _con(monkeypatch, [
        _video("qqqqqqqqqqq", "LEGO 21029 Buckingham Palace",
               canal="Brick Studio Architect")])
    r = buscar_video("Architecture Palacio de Buckingham", marca="LEGO",
                     numero_set="21029", api_key="X")
    assert r["canal"] == "Brick Studio Architect" and r["oficial"] is False


def test_un_canal_cualquiera_sigue_sin_valer(monkeypatch):
    """La lista es corta y elegida a mano: no se abre a cualquiera."""
    _con(monkeypatch, [
        _video("rrrrrrrrrrr", "LEGO 21042 reseña", canal="Ladrillos y Café")])
    assert buscar_video("Estatua de la Libertad", marca="LEGO",
                        numero_set="21042", api_key="X") == {}


def test_se_pueden_agregar_canales_sin_tocar_el_codigo(monkeypatch):
    from videos_youtube import canales_confiables

    monkeypatch.setenv("CANALES_VIDEO_CONFIABLES",
                       "LEGO=Otro Canal;Fisher Price=Canal Bebé")
    assert "Otro Canal" in canales_confiables("LEGO")
    assert "AustrianBrickFan" in canales_confiables("LEGO")   # no pisa los fijos
    assert canales_confiables("Fisher Price") == ("Canal Bebé",)
    assert canales_confiables("Bosch") == ()


def test_el_video_oficial_dice_que_es_oficial(monkeypatch):
    _con(monkeypatch, [_video("sssssssssss", "LEGO 10282 adidas", canal="LEGO")])
    r = buscar_video("adidas Superstar", marca="LEGO", numero_set="10282",
                     api_key="X")
    assert r["oficial"] is True
