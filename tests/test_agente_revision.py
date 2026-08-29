"""El agente que mantiene al día lo ya publicado.

Publicar congela un precio. Entre que se publica y que alguien compra pueden
pasar semanas, y en ese tiempo Amazon sube el precio o se queda sin stock. Se
descubre cuando ya se vendió, que es tarde.
"""

import pytest

from db import conectar
from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from agente_revision import AgenteRevision


@pytest.fixture()
def cat():
    return Catalogo(conectar(":memory:"), cfg=Config())


def _pub(cat, asin="B0TEST0001", precio=31.69):
    p = cat.agregar(ProductoCatalogo(
        asin=asin, amazon_link=f"https://amazon.com/dp/{asin}", marca="LEGO",
        modelo=f"LEGO Set {asin}", titulo_ml=f"Set LEGO {asin}",
        precio_usd=precio, regimen="landed", margen_deseado=0.30, stock=1))
    cat.cambiar_estado(p.id, "aprobado")
    return cat.registrar_publicacion(p.id, f"MLA{asin}", "http://ml/x")


def _agente(cat, respuesta, pausados=None, margen_minimo=0.0):
    pausados = pausados if pausados is not None else []
    return AgenteRevision(cat, lambda url: dict(respuesta),
                          lambda p: pausados.append(p.id),
                          margen_minimo=margen_minimo)


def test_un_precio_nuevo_recalcula_el_costo(cat):
    p = _pub(cat)
    ag = _agente(cat, {"ok": True, "precio_usd": 53.00, "disponible": True})
    costo_antes = p.costo_total_ars

    r = ag.tick()

    assert r["accion"] in ("revisar", "margen_bajo")
    assert cat.obtener(p.id).precio_usd == 53.00
    assert cat.obtener(p.id).costo_total_ars > costo_antes


def test_sin_stock_pausa_la_publicacion(cat):
    """Lo único que este agente cambia en MercadoLibre, y va en la dirección
    segura: no se puede vender lo que no se puede comprar."""
    p = _pub(cat)
    pausados = []
    ag = _agente(cat, {"ok": True, "precio_usd": 59.99, "disponible": False},
                 pausados)

    r = ag.tick()

    assert r["accion"] == "pausado"
    assert pausados == [p.id]
    assert ag.estado()["sin_stock"] == 1


def test_lo_que_no_se_pudo_leer_no_se_pausa(cat):
    """Amazon rechaza seguido. Tomar un rechazo por "sin stock" sacaría de venta
    publicaciones que están perfectas."""
    p = _pub(cat)
    pausados = []
    ag = _agente(cat, {"ok": False, "precio_usd": None, "disponible": None,
                       "bloqueado": True, "mensaje": "Amazon nos bloqueó"},
                 pausados)

    r = ag.tick()

    assert r["accion"] == "no_leido" and r["bloqueado"] is True
    assert pausados == []
    assert cat.obtener(p.id).estado == "publicado"
    assert cat.obtener(p.id).disponibilidad == "in_stock"


def test_avisa_cuando_el_margen_al_precio_publicado_se_hundio(cat):
    """Lo que importa no es que Amazon suba, sino si el precio YA PUBLICADO
    sigue dejando ganancia."""
    p = _pub(cat, precio=31.69)
    ag = _agente(cat, {"ok": True, "precio_usd": 90.00, "disponible": True},
                 margen_minimo=30.0)

    r = ag.tick()

    assert r["accion"] == "margen_bajo"
    assert ag.estado()["en_perdida"] == 1
    assert ag.estado()["encarecidos"] == 1


def test_va_de_a_uno_y_no_repite(cat):
    a, b = _pub(cat, "B0AAAAAAAA"), _pub(cat, "B0BBBBBBBB")
    ag = _agente(cat, {"ok": True, "precio_usd": 40.0, "disponible": True})

    vistos = {ag.tick()["id"], ag.tick()["id"]}
    assert vistos == {a.id, b.id}
    assert ag.tick()["accion"] == "sin_trabajo"


def test_lo_que_fallo_no_se_reintenta_en_la_misma_corrida(cat):
    """Si Amazon nos está rechazando, insistir en el mismo producto no cambia
    nada y deja al agente girando en falso."""
    _pub(cat)
    ag = _agente(cat, {"ok": False, "precio_usd": None, "disponible": None})

    assert ag.tick()["accion"] == "no_leido"
    assert ag.tick()["accion"] == "sin_trabajo"


def test_reiniciar_vuelve_a_habilitar_todo(cat):
    _pub(cat)
    ag = _agente(cat, {"ok": True, "precio_usd": 40.0, "disponible": True})
    ag.tick()
    assert ag.estado()["por_revisar"] == 0

    ag.reiniciar()
    assert ag.estado()["por_revisar"] == 1 and ag.estado()["revisados"] == 0


def test_si_no_se_puede_pausar_lo_dice_y_sigue(cat):
    """Que MercadoLibre rechace la pausa no puede frenar la corrida ni quedar
    en silencio: sería creer que se sacó de venta algo que sigue publicado."""
    _pub(cat)

    def _falla(p):
        raise RuntimeError("MercadoLibre dijo que no")

    ag = AgenteRevision(cat, lambda url: {"ok": True, "precio_usd": 50.0,
                                          "disponible": False}, _falla)
    r = ag.tick()
    assert r["accion"] == "error" and "no se pudo pausar" in r["detalle"]


def test_solo_mira_lo_publicado(cat):
    cat.agregar(ProductoCatalogo(asin="B0BORRADOR", marca="LEGO",
                                 modelo="Sin publicar", precio_usd=20.0,
                                 amazon_link="https://amazon.com/dp/B0BORRADOR"))
    _pub(cat)
    ag = _agente(cat, {"ok": True, "precio_usd": 40.0, "disponible": True})
    assert ag.estado()["por_revisar"] == 1
