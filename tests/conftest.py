"""Configuración común de tests: aísla la base y evita llamadas de red."""

import os
import time

# Los tests SIEMPRE corren sobre SQLite temporal. Si el entorno tiene
# DATABASE_URL apuntando a la base real (la de producción), la quitamos: cada
# test crea y borra datos, y no debe tocar datos de verdad.
os.environ.pop("DATABASE_URL", None)

import arbitraje.cotizacion as cot

# Semilla del caché para que crear_app / obtener_cotizaciones no salgan a la red
# durante los tests (los tests específicos de cotización limpian el caché).
cot._cache["_ts"] = time.time()
cot._cache["data"] = {
    "oficial": 1300.0, "tarjeta": 1690.0, "fuente": "test",
    "online": False, "actualizado": "2026-01-01T00:00:00+00:00",
}


import pytest


@pytest.fixture(autouse=True)
def _sin_red_para_codigos(monkeypatch):
    """Los tests no salen a buscar códigos a internet.

    `_codigo_de` consulta Brickset y UPCitemdb; sin esto cada test que prepara
    borradores intentaba una conexión real, que en CI tarda hasta el timeout.
    Los tests de esas fuentes parchean `requests` por su cuenta.
    """
    import fuentes_gtin
    monkeypatch.setattr(fuentes_gtin, "gtin_de_brickset", lambda *a, **k: {})
    monkeypatch.setattr(fuentes_gtin, "gtin_de_upcitemdb", lambda *a, **k: {})
