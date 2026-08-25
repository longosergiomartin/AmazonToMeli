"""El agente: completa y publica el catálogo sin intervención."""

import pytest

from agente import CODIGO, NADA, PREPARAR, PUBLICAR, Agente
from arbitraje.config import Config
from catalogo import Catalogo, ProductoCatalogo
from db import conectar


@pytest.fixture()
def cat(tmp_path):
    return Catalogo(conectar(str(tmp_path / "a.db")), cfg=Config(),
                    cotizacion={"oficial": 1000.0, "tarjeta": 1300.0})


def _prod(cat, **kw):
    base = dict(asin="B0AG000001", marca="LEGO", modelo="LEGO Star Wars 75339",
                titulo_ml="LEGO Star Wars 75339", precio_usd=100.0,
                margen_deseado=0.35, stock=1)
    base.update(kw)
    return cat.agregar(ProductoCatalogo(**base))


def _agente(cat, preparar=None, codigo=None, publicar=None, faltantes=None):
    return Agente(cat,
                  preparar or (lambda p: p),
                  codigo or (lambda p: {"gtin": "", "fuente": "", "bloqueado": False}),
                  publicar or (lambda p: {"ml_item_id": "MLA1"}),
                  faltantes or (lambda p: []))


# ---- qué le falta a cada producto --------------------------------------

def test_detecta_el_paso_que_falta(cat):
    ag = _agente(cat)
    sin_categoria = _prod(cat, ml_category_id="", pictures=["http://i/1.jpg"])
    assert ag.paso_pendiente(sin_categoria) == PREPARAR

    sin_codigo = _prod(cat, asin="B0AG000002", ml_category_id="MLA1157",
                       pictures=["http://i/1.jpg"])
    assert ag.paso_pendiente(sin_codigo) == CODIGO

    listo = _prod(cat, asin="B0AG000003", ml_category_id="MLA1157",
                  pictures=["http://i/1.jpg"],
                  ml_attributes={"GTIN": "5702017155326"})
    assert ag.paso_pendiente(listo) == PUBLICAR


def test_los_publicados_no_tienen_nada_pendiente(cat):
    p = _prod(cat, ml_category_id="MLA1157", pictures=["http://i/1.jpg"])
    cat.cambiar_estado(p.id, "aprobado")
    cat.registrar_publicacion(p.id, "MLA9", "http://ml/x")
    assert _agente(cat).paso_pendiente(cat.obtener(p.id)) == NADA


# ---- el ciclo -----------------------------------------------------------

def test_apagado_no_hace_nada(cat):
    _prod(cat)
    hechos = []
    ag = _agente(cat, preparar=lambda p: hechos.append(p) or p)
    assert ag.tick()["accion"] == "apagado"
    assert not hechos


def test_prepara_y_despues_busca_el_codigo(cat):
    pasos = []
    ag = _agente(
        cat,
        preparar=lambda p: pasos.append("preparar") or cat.actualizar_publicacion(
            p.id, ml_category_id="MLA1157", pictures=["http://i/1.jpg"]),
        codigo=lambda p: pasos.append("codigo") or {"gtin": "5702017155326",
                                                    "fuente": "Brickset"})
    ag.config = {"encendido": True}
    _prod(cat, ml_category_id="", pictures=[])

    assert ag.tick()["accion"] == PREPARAR
    assert ag.tick()["accion"] == CODIGO
    assert pasos == ["preparar", "codigo"]


def test_no_publica_si_no_esta_habilitado(cat):
    """Publicar es plata: apagado por defecto, hay que habilitarlo a mano."""
    publicados = []
    ag = _agente(cat, publicar=lambda p: publicados.append(p) or {"ml_item_id": "X"})
    ag.config = {"encendido": True}
    _prod(cat, ml_category_id="MLA1157", pictures=["http://i/1.jpg"],
          ml_attributes={"GTIN": "5702017155326"})

    r = ag.tick()
    assert r["accion"] == "listo" and not publicados
    assert "habilitar" in r["detalle"]


def test_publica_cuando_esta_habilitado(cat):
    publicados = []
    ag = _agente(cat, publicar=lambda p: publicados.append(p.id) or {"ml_item_id": "MLA7"})
    ag.config = {"encendido": True, "publicar": True, "margen_minimo": 0}
    p = _prod(cat, ml_category_id="MLA1157", pictures=["http://i/1.jpg"],
              ml_attributes={"GTIN": "5702017155326"})

    r = ag.tick()
    assert r["accion"] == PUBLICAR and publicados == [p.id]
    assert "MLA7" in r["detalle"]


def test_respeta_el_tope_de_publicaciones(cat):
    """Sin tope, un error de precio se multiplicaría por todo el catálogo."""
    publicados = []
    ag = _agente(cat, publicar=lambda p: publicados.append(p.id) or {"ml_item_id": "X"})
    ag.config = {"encendido": True, "publicar": True, "margen_minimo": 0,
                 "max_publicaciones": 1}
    for i in (1, 2, 3):
        _prod(cat, asin=f"B0AGTOPE0{i}", ml_category_id="MLA1157",
              pictures=["http://i/1.jpg"], ml_attributes={"GTIN": "5702017155326"})

    for _ in range(3):
        ag.tick()
    assert len(publicados) == 1
    assert ag.config["max_publicaciones"] == 0


def test_no_publica_con_margen_bajo(cat):
    publicados = []
    ag = _agente(cat, publicar=lambda p: publicados.append(p) or {"ml_item_id": "X"})
    ag.config = {"encendido": True, "publicar": True, "margen_minimo": 90}
    p = _prod(cat, ml_category_id="MLA1157", pictures=["http://i/1.jpg"],
              ml_attributes={"GTIN": "5702017155326"})
    cat.actualizar_precio(p.id, cat.obtener(p.id).costo_total_ars * 1.05)

    r = ag.tick()
    assert r["accion"] == "trabado" and not publicados
    assert "margen" in r["detalle"]


def test_un_producto_trabado_no_frena_a_los_demas(cat):
    def _codigo(p):
        if p.asin.endswith("1"):
            return {"gtin": "", "fuente": "", "bloqueado": False}
        # Como el paso real: el código encontrado se guarda en el producto.
        cat.actualizar_publicacion(p.id, ml_attributes={"GTIN": "5702017155326"})
        return {"gtin": "5702017155326", "fuente": "Brickset"}

    ag = _agente(cat, codigo=_codigo)
    ag.config = {"encendido": True}
    _prod(cat, asin="B0AGFALLA1", ml_category_id="MLA1157", pictures=["http://i/1.jpg"])
    _prod(cat, asin="B0AGFALLA2", ml_category_id="MLA1157", pictures=["http://i/1.jpg"])

    acciones = []
    for _ in range(8):
        r = ag.tick()
        if r["accion"] == "sin_trabajo":
            break
        acciones.append(r["accion"])

    # Uno consiguió su código y el otro no, pero los dos llegaron a estar listos
    # para publicar: el que falló no frenó al agente ni se quedó pegado.
    assert "sin_codigo" in acciones and CODIGO in acciones
    assert acciones.count("listo") == 2


def test_un_error_no_tumba_al_agente(cat):
    def _explota(p):
        raise RuntimeError("MercadoLibre rechazó la publicación")

    ag = _agente(cat, preparar=_explota)
    ag.config = {"encendido": True}
    _prod(cat, ml_category_id="", pictures=[])

    r = ag.tick()
    assert r["accion"] == "error" and "rechazó" in r["detalle"]
    assert ag.tick()["accion"] == "sin_trabajo"     # queda trabado, sigue vivo


def test_encender_de_nuevo_reintenta_los_trabados(cat):
    def _explota(p):
        raise RuntimeError("MercadoLibre rechazó la publicación")

    ag = _agente(cat, preparar=_explota)
    ag.config = {"encendido": True}
    _prod(cat, ml_category_id="", pictures=[])

    assert ag.tick()["accion"] == "error"
    assert ag.tick()["accion"] == "sin_trabajo"
    ag.config = {"encendido": True}                 # apagar y encender
    assert ag.tick()["accion"] == "error"           # lo reintenta


def test_sin_codigo_igual_intenta_publicar(cat):
    """La vía del catálogo de MercadoLibre no necesita GTIN: exigirlo antes de
    intentar era bloquearse solo."""
    publicados = []
    ag = _agente(cat,
                 codigo=lambda p: {"gtin": "", "fuente": "", "bloqueado": True},
                 publicar=lambda p: publicados.append(p.id) or {"ml_item_id": "MLA3"})
    ag.config = {"encendido": True, "publicar": True, "margen_minimo": 0}
    p = _prod(cat, ml_category_id="MLA1157", pictures=["http://i/1.jpg"])

    assert ag.tick()["accion"] == "sin_codigo"
    assert ag.tick()["accion"] == PUBLICAR
    assert publicados == [p.id]


def test_la_configuracion_persiste_en_la_base(cat):
    ag = _agente(cat)
    ag.config = {"publicar": True, "max_publicaciones": 7, "margen_minimo": 25}
    otro = _agente(cat)                              # otra instancia, misma base
    assert otro.config["publicar"] is True
    assert otro.config["max_publicaciones"] == 7
    assert otro.config["margen_minimo"] == 25.0


def test_el_tope_se_limita_a_un_maximo_razonable(cat):
    ag = _agente(cat)
    ag.config = {"max_publicaciones": 9999}
    assert ag.config["max_publicaciones"] == 50


def test_estado_cuenta_lo_que_falta(cat):
    ag = _agente(cat)
    _prod(cat, asin="B0AGEST001", ml_category_id="", pictures=[])
    _prod(cat, asin="B0AGEST002", ml_category_id="MLA1157", pictures=["http://i/1.jpg"])
    e = ag.estado()
    assert e["por_preparar"] == 1 and e["sin_codigo"] == 1
    assert e["pendientes"] == 2


def test_cuenta_los_pausados_como_publicados(cat):
    """Un pausado está en MercadoLibre: la publicación existe y ML la activa
    cuando termina de revisarla."""
    ag = _agente(cat)
    p = _prod(cat, asin="B0AGPAUS01", ml_category_id="MLA1157",
              pictures=["http://i/1.jpg"])
    cat.cambiar_estado(p.id, "aprobado")
    cat.registrar_publicacion(p.id, "MLA5", "http://ml/p", "paused")

    assert cat.obtener(p.id).estado == "pausado"
    assert ag.estado()["publicados"] == 1
    assert ag.paso_pendiente(cat.obtener(p.id)) == NADA
