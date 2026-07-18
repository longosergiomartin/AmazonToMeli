"""Configuración común de tests: evita llamadas de red a la cotización."""

import time

import arbitraje.cotizacion as cot

# Semilla del caché para que crear_app / obtener_cotizaciones no salgan a la red
# durante los tests (los tests específicos de cotización limpian el caché).
cot._cache["_ts"] = time.time()
cot._cache["data"] = {
    "oficial": 1300.0, "tarjeta": 1690.0, "fuente": "test",
    "online": False, "actualizado": "2026-01-01T00:00:00+00:00",
}
