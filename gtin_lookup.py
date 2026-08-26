"""
Búsqueda automática del GTIN (código de barras EAN/UPC) a partir del ASIN.

Hace lo que uno haría a mano en herramientas tipo ASINScope/Analyzer.Tools,
pero automático y con verificación matemática:

  1. Lee la página del producto en Amazon buscando menciones de EAN/UPC/GTIN.
  2. Busca el ASIN en un buscador web y junta los números candidatos de los
     resultados.
  3. Valida cada candidato con el dígito verificador del estándar GTIN
     (GTIN-8/12/13/14): los códigos de barras reales cumplen una cuenta
     matemática, así se descarta el ruido.
  4. Devuelve el candidato más repetido que pase la validación.

Es best-effort: desde servidores (nube) los sitios pueden bloquear; en ese
caso devuelve ok=False y el usuario lo carga a mano (el panel muestra un link
de búsqueda como plan B).
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from typing import Optional

import requests

import descarga
from titulos import numero_de_set  # noqa: F401 - se reexporta por comodidad

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def validar_gtin(codigo: str) -> bool:
    """Valida el dígito verificador de un GTIN-8/12/13/14."""
    if not re.fullmatch(r"\d{8}|\d{12}|\d{13}|\d{14}", codigo or ""):
        return False
    digitos = [int(c) for c in codigo]
    verificador = digitos[-1]
    cuerpo = digitos[:-1][::-1]  # desde el más cercano al verificador
    suma = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(cuerpo))
    return (10 - suma % 10) % 10 == verificador


def _candidatos(texto: str) -> list[str]:
    """Números de 8-14 dígitos que pasan la validación GTIN."""
    crudos = re.findall(r"(?<!\d)(\d{12,14}|\d{8})(?!\d)", texto or "")
    return [c for c in crudos if validar_gtin(c)]


def _de_amazon(asin: str, timeout: int) -> tuple[list[str], bool]:
    """EAN/UPC/GTIN mencionados en la página del producto.

    Devuelve (candidatos, bloqueado). Distinguir "no encontré" de "me
    bloquearon" es lo que permite frenar un lote a tiempo en vez de seguir
    golpeando a Amazon.
    """
    url = f"https://www.amazon.com/dp/{asin}"
    resp, _ = descarga.bajar(url, timeout, headers={"User-Agent": _UA})
    if resp.status_code != 200:
        return [], resp.status_code in (403, 429, 503)
    if "captcha" in resp.text[:4000].lower() and "productTitle" not in resp.text:
        return [], True
    # Solo números que aparecen cerca de una etiqueta EAN/UPC/GTIN.
    cerca = re.findall(r"(?:EAN|UPC|GTIN|C[óo]digo de barras)[^0-9]{0,60}(\d{8,14})",
                       resp.text, re.I)
    return [c for c in cerca if validar_gtin(c)], False


def _de_buscador(asin: str, timeout: int) -> list[str]:
    """Busca '<ASIN> EAN UPC' en la web y junta candidatos validados."""
    url = "https://html.duckduckgo.com/html/"
    resp, _ = descarga.bajar(url, timeout, params={"q": f"{asin} EAN UPC barcode"},
                             headers={"User-Agent": _UA})
    if resp.status_code != 200:
        return []
    return _candidatos(resp.text)


def buscar_asin(gtin: str, timeout: int = 12) -> dict:
    """ASIN a partir del código de barras: busca el código en Amazon.

    Es el camino inverso de `buscar_gtin`. Amazon indexa los EAN/UPC, así que
    buscar el número devuelve la ficha del producto. Devuelve
    {ok, asin, titulo, bloqueado, mensaje}.
    """
    gtin = re.sub(r"\D", "", gtin or "")
    if not validar_gtin(gtin):
        return {"ok": False, "asin": "", "titulo": "", "bloqueado": False,
                "mensaje": "Código inválido: no pasa el dígito verificador "
                           "(EAN/UPC de 8, 12, 13 o 14 números)."}
    try:
        resp, _ = descarga.bajar("https://www.amazon.com/s", timeout,
                                 params={"k": gtin},
                                 headers={"User-Agent": _UA,
                                          "Accept-Language": "en-US,en;q=0.9"})
    except requests.RequestException as e:
        return {"ok": False, "asin": "", "titulo": "", "bloqueado": False,
                "mensaje": f"No se pudo consultar Amazon ({e})."}

    if resp.status_code != 200:
        bloqueado = resp.status_code in (403, 429, 503)
        return {"ok": False, "asin": "", "titulo": "", "bloqueado": bloqueado,
                "mensaje": f"Amazon respondió {resp.status_code}."
                           + (" Conviene frenar y seguir más tarde."
                              if bloqueado else "")}

    texto = resp.text
    if "captcha" in texto[:4000].lower():
        return {"ok": False, "asin": "", "titulo": "", "bloqueado": True,
                "mensaje": "Amazon pidió verificación (captcha). Frenar y seguir "
                           "más tarde."}

    m = re.search(r'data-asin="([A-Z0-9]{10})"[^>]*data-component-type="s-search-result"',
                  texto) or re.search(r'data-component-type="s-search-result"[^>]*'
                                      r'data-asin="([A-Z0-9]{10})"', texto)
    if not m:
        m = re.search(r'data-asin="([A-Z0-9]{10})"', texto)
    if not m:
        return {"ok": False, "asin": "", "titulo": "", "bloqueado": False,
                "mensaje": "Amazon no devolvió resultados para ese código."}

    asin = m.group(1)
    t = re.search(r'data-asin="' + asin + r'".*?<h2[^>]*>.*?<span[^>]*>(.*?)</span>',
                  texto, re.S)
    titulo = re.sub(r"\s+", " ", _html.unescape(t.group(1))).strip() if t else ""
    return {"ok": True, "asin": asin, "titulo": titulo[:200], "bloqueado": False,
            "mensaje": f"ASIN encontrado en Amazon ({asin}). Verificá que sea el "
                       "producto correcto."}


def buscar_gtin(asin: str, timeout: int = 12) -> dict:
    """Devuelve {ok, gtin, candidatos, fuente, mensaje} para un ASIN."""
    asin = (asin or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        return {"ok": False, "gtin": "", "candidatos": [], "fuente": "",
                "bloqueado": False,
                "mensaje": "ASIN inválido (deben ser 10 caracteres, ej: B075SDMMMV)."}

    todos: list[str] = []
    fuente = ""
    bloqueado = False
    try:
        encontrados, bloqueado = _de_amazon(asin, timeout)
    except requests.RequestException:
        encontrados = []
    if encontrados:
        todos.extend(encontrados)
        fuente = "amazon"
    if not bloqueado:
        try:
            web = _de_buscador(asin, timeout)
        except requests.RequestException:
            web = []
        if web:
            todos.extend(web)
            fuente = fuente or "busqueda web"

    if not todos:
        return {"ok": False, "gtin": "", "candidatos": [], "fuente": "",
                "bloqueado": bloqueado,
                "mensaje": ("Amazon nos está limitando: conviene frenar y seguir "
                            "más tarde." if bloqueado else
                            "No pude encontrar el GTIN automáticamente. Usá el "
                            "link de búsqueda manual o mirá el código de barras "
                            "de la caja.")}

    conteo = Counter(todos)
    gtin, veces = conteo.most_common(1)[0]
    candidatos = [c for c, _ in conteo.most_common(5)]
    return {"ok": True, "gtin": gtin, "candidatos": candidatos, "fuente": fuente,
            "bloqueado": False,
            "mensaje": (f"GTIN encontrado ({fuente}, visto {veces} vez/veces y "
                        "verificado matemáticamente). Confirmalo contra la caja "
                        "o la ficha del producto.")}
