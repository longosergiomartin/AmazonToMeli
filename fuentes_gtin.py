"""
Fuentes de códigos de barras con API de verdad.

Hasta acá el GTIN se buscaba leyendo páginas (Amazon, buscadores). Eso funciona
desde una PC hogareña, pero **desde un servidor en la nube no**: Amazon y los
buscadores bloquean las IP de datacenter, así que en Render la búsqueda fallaba
siempre y sin ese código MercadoLibre no deja publicar.

Estas fuentes son APIs públicas, pensadas para consultarse por programa. No
bloquean servidores, responden JSON y no hay que interpretar HTML:

  - **Brickset** (brickset.com/api): la base de datos de referencia de LEGO.
    Se consulta por número de set y devuelve el EAN y el UPC de la caja. Es la
    fuente autoritativa para este rubro. Necesita una API key gratuita.
  - **UPCitemdb**: base de códigos de barras genérica, sirve para cualquier
    rubro. Tiene un nivel de prueba sin registro (con límite diario).

Todo código se valida con el dígito verificador antes de devolverlo.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

import requests

from gtin_lookup import validar_gtin
from titulos import parecido

BRICKSET_API = "https://brickset.com/api/v3.asmx/getSets"
UPCITEMDB_API = "https://api.upcitemdb.com/prod/trial/search"


def _primero_valido(*codigos) -> str:
    for c in codigos:
        c = re.sub(r"\D", "", str(c or ""))
        if validar_gtin(c):
            return c
    return ""


def brickset_configurado() -> bool:
    return bool(os.getenv("BRICKSET_API_KEY", "").strip())


def gtin_de_brickset(numero_set: str, timeout: int = 12,
                     api_key: Optional[str] = None) -> dict:
    """Código de barras de un set de LEGO según Brickset.

    Es la fuente más confiable para LEGO: la base la mantiene la comunidad a
    partir de las cajas reales, y se consulta por número de set, que es
    inequívoco. Devuelve {gtin, nombre, fuente} o {}.
    """
    numero = re.sub(r"\D", "", numero_set or "")
    clave = (api_key if api_key is not None else os.getenv("BRICKSET_API_KEY", "")).strip()
    if not numero or not clave:
        return {}
    # Brickset identifica los sets como "75339-1" (número + variante).
    params = {"apiKey": clave, "userHash": "",
              "params": json.dumps({"setNumber": f"{numero}-1"})}
    try:
        resp = requests.get(BRICKSET_API, params=params, timeout=timeout)
        datos = resp.json()
    except (requests.RequestException, ValueError):
        return {}
    if datos.get("status") != "success":
        return {}
    for s in (datos.get("sets") or []):
        codigo = s.get("barcode") or {}
        gtin = _primero_valido(codigo.get("EAN"), codigo.get("UPC"))
        if gtin:
            return {"gtin": gtin, "nombre": s.get("name", ""), "fuente": "Brickset"}
    return {}


def gtin_de_upcitemdb(consulta: str, parecido_a: str = "",
                      minimo_parecido: float = 0.5, timeout: int = 12) -> dict:
    """Código de barras buscando por nombre en UPCitemdb.

    Sirve para cualquier rubro. `parecido_a` es la guarda contra quedarse con
    otro producto: si el nombre del resultado no se parece lo suficiente al que
    buscamos, se descarta.
    """
    consulta = (consulta or "").strip()
    if len(consulta) < 4:
        return {}
    try:
        resp = requests.get(UPCITEMDB_API, params={"s": consulta[:120]},
                            timeout=timeout)
        if resp.status_code != 200:
            return {}
        datos = resp.json()
    except (requests.RequestException, ValueError):
        return {}
    for item in (datos.get("items") or []):
        nombre = item.get("title", "")
        if parecido_a and parecido(parecido_a, nombre) < minimo_parecido:
            continue
        gtin = _primero_valido(item.get("ean"), item.get("upc"),
                               item.get("gtin"))
        if gtin:
            return {"gtin": gtin, "nombre": nombre, "fuente": "UPCitemdb"}
    return {}
