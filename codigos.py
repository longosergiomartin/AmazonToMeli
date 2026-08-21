"""
Conversor de identificadores de producto: ASIN ⇄ GTIN (EAN/UPC/ISBN).

Hace lo mismo que los conversores online, pero sin límite diario y usando
primero lo que ya sabemos:

  1. El catálogo propio: si el producto ya está cargado con su código, la
     respuesta es instantánea y no se consulta nada afuera.
  2. El catálogo de MercadoLibre: los productos ya están cargados ahí con su
     GTIN, y es una API oficial en la que estamos autenticados.
  3. Amazon y la web, como último recurso.

Cada código encontrado se valida con el dígito verificador del estándar GTIN
antes de devolverlo: los códigos de barras reales cumplen una cuenta
matemática, así no se cuelan números sueltos de la página.

Si Amazon empieza a limitar, el lote **se detiene** y avisa: no se insiste ni
se intenta esquivar el bloqueo.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

from gtin_lookup import buscar_asin, buscar_gtin, validar_gtin
from titulos import numero_de_set

ASIN = "asin"
GTIN = "gtin"


def tipo_de(entrada: str) -> str:
    """¿Es un ASIN o un código de barras? Devuelve "asin", "gtin" o ""."""
    v = (entrada or "").strip().upper()
    # Puede venir pegado un link de Amazon.
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", v, re.I)
    if m:
        return ASIN
    solo_digitos = re.sub(r"\D", "", v)
    if len(solo_digitos) in (8, 12, 13, 14) and solo_digitos == v.replace("-", "").replace(" ", ""):
        return GTIN
    if re.fullmatch(r"[A-Z0-9]{10}", v):
        return ASIN
    return ""


def _normalizar(entrada: str, tipo: str) -> str:
    v = (entrada or "").strip().upper()
    if tipo == ASIN:
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", v, re.I)
        return (m.group(1) if m else v).upper()
    return re.sub(r"\D", "", v)


def convertir(entrada: str, catalogo=None, cliente_ml=None,
              buscar_gtin_fn: Callable[[str], dict] = buscar_gtin,
              buscar_asin_fn: Callable[[str], dict] = buscar_asin) -> dict:
    """Convierte una entrada suelta. Devuelve siempre la misma forma:
    {entrada, tipo, asin, gtin, titulo, fuente, ok, bloqueado, mensaje}."""
    tipo = tipo_de(entrada)
    r = {"entrada": (entrada or "").strip(), "tipo": tipo, "asin": "", "gtin": "",
         "titulo": "", "fuente": "", "ok": False, "bloqueado": False, "mensaje": ""}
    if not tipo:
        r["mensaje"] = ("No parece un ASIN (10 caracteres) ni un código de barras "
                        "(8, 12, 13 o 14 números).")
        return r

    valor = _normalizar(entrada, tipo)
    r[tipo] = valor

    # 1) Lo que ya tenemos cargado: gratis e instantáneo.
    guardado = _del_catalogo(catalogo, tipo, valor)
    if guardado:
        r.update(guardado, ok=True, fuente="tu catálogo",
                 mensaje="Ya estaba en tu catálogo.")
        return r

    if tipo == ASIN:
        # 2) El catálogo de MercadoLibre, por número de modelo del título.
        del_ml = _de_mercadolibre(cliente_ml, r["titulo"] or "", valor, catalogo)
        if del_ml:
            r.update(del_ml, ok=True, fuente="catálogo de MercadoLibre",
                     mensaje="Código tomado de la ficha oficial en MercadoLibre.")
            return r
        # 3) Amazon y la web.
        res = buscar_gtin_fn(valor)
        r["ok"] = bool(res.get("ok"))
        r["gtin"] = res.get("gtin", "")
        r["fuente"] = res.get("fuente", "")
        r["mensaje"] = res.get("mensaje", "")
        return r

    res = buscar_asin_fn(valor)
    r["ok"] = bool(res.get("ok"))
    r["asin"] = res.get("asin", "")
    r["titulo"] = res.get("titulo", "")
    r["bloqueado"] = bool(res.get("bloqueado"))
    r["fuente"] = "amazon" if res.get("ok") else ""
    r["mensaje"] = res.get("mensaje", "")
    return r


def _del_catalogo(catalogo, tipo: str, valor: str) -> Optional[dict]:
    """Busca la equivalencia entre los productos ya cargados."""
    if catalogo is None:
        return None
    for p in catalogo.todos():
        gtin = (p.ml_attributes or {}).get("GTIN", "")
        if tipo == ASIN and p.asin.upper() == valor and gtin:
            return {"gtin": gtin, "titulo": p.titulo_ml or p.modelo}
        if tipo == GTIN and gtin == valor and p.asin:
            return {"asin": p.asin, "titulo": p.titulo_ml or p.modelo}
    return None


def _de_mercadolibre(cliente_ml, titulo: str, asin: str, catalogo) -> Optional[dict]:
    """GTIN desde el catálogo de MercadoLibre, por número de modelo.

    Solo sirve si conocemos el título del producto (porque de ahí sale el número
    de modelo del fabricante), así que se usa cuando el ASIN ya está en el
    catálogo propio aunque todavía no tenga código cargado.
    """
    if cliente_ml is None:
        return None
    if not titulo and catalogo is not None:
        for p in catalogo.todos():
            if p.asin.upper() == asin:
                titulo = p.modelo or p.titulo_ml
                break
    numero = numero_de_set(titulo)
    if not numero:
        return None
    try:
        ficha = cliente_ml.gtin_de_catalogo(numero, debe_contener=numero)
    except Exception:  # noqa: BLE001 - es una fuente más, no puede tumbar nada
        return None
    gtin = (ficha or {}).get("gtin", "")
    if gtin and validar_gtin(gtin):
        return {"gtin": gtin, "titulo": ficha.get("nombre", "")}
    return None


def convertir_lote(entradas: list[str], catalogo=None, cliente_ml=None,
                   maximo: int = 25, pausa_seg: float = 1.5,
                   dormir: Callable[[float], None] = time.sleep,
                   **kw) -> dict:
    """Convierte varias entradas, con una pausa entre las que salen a la red.

    Corta apenas Amazon nos limita: lo que quedó sin convertir se puede
    reintentar más tarde.
    """
    resultados = []
    detenido = False
    vistas = []
    for entrada in entradas:
        if (entrada or "").strip() and entrada.strip() not in vistas:
            vistas.append(entrada.strip())
    for i, entrada in enumerate(vistas[:maximo]):
        r = convertir(entrada, catalogo=catalogo, cliente_ml=cliente_ml, **kw)
        resultados.append(r)
        if r["bloqueado"]:
            detenido = True
            break
        # Sin pausa cuando la respuesta salió del catálogo: no se consultó nada.
        if i < len(vistas[:maximo]) - 1 and r["fuente"] != "tu catálogo":
            dormir(pausa_seg)
    return {"resultados": resultados, "detenido": detenido,
            "convertidos": sum(1 for r in resultados if r["ok"]),
            "total": len(resultados),
            "pendientes": max(0, len(vistas) - len(resultados))}
