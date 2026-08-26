"""
Búsqueda del video del producto en YouTube.

MercadoLibre solo acepta videos de YouTube (campo `video_id`), así que el video
que trae Amazon no sirve para publicar. Acá se busca el equivalente: para la
mayoría de las marcas el fabricante publica el video oficial de cada producto en
su propio canal, que además suele ser mejor que el de Amazon.

Necesita `YOUTUBE_API_KEY` (YouTube Data API v3). La clave es de autoservicio:
se crea en la consola de Google Cloud al instante, sin pedirle permiso a nadie
ni explicar para qué. Sin clave, la búsqueda simplemente no se ofrece.

**Lo importante acá no es encontrar un video, es no poner el equivocado.**
Buscar "LEGO 21042" en YouTube devuelve reseñas, armados de terceros, y videos
de otros sets; publicar cualquiera de esos en la ficha del producto es peor que
no publicar ninguno. Por eso todo candidato tiene que pasar tres filtros antes
de ser aceptado, y ante la duda no se devuelve nada.
"""

from __future__ import annotations

import os
import re
from typing import Optional

import requests

from titulos import normalizar, numero_de_set, parecido

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/search"

# Cuánto del título del producto tiene que aparecer en el del video cuando no
# hay número de modelo para desempatar.
MINIMO_PARECIDO = 0.55

# Canales de terceros que hacen buen material de una marca. Se usan **solo como
# segunda opción**, cuando el canal oficial no tiene video del producto.
#
# Van por marca a propósito: un video de AustrianBrickFan que mencione "12345"
# no dice nada sobre un Fisher-Price con ese número de modelo. Sin esa atadura,
# confiar en un canal lo volvería una puerta abierta para las demás marcas.
CANALES_CONFIABLES: dict[str, tuple[str, ...]] = {
    "lego": ("AustrianBrickFan", "Brick Studio Architect"),
}


def configurado() -> bool:
    return bool(os.getenv("YOUTUBE_API_KEY", "").strip())


def canales_confiables(marca: str) -> tuple[str, ...]:
    """Canales de terceros aceptados para esta marca.

    Se pueden agregar sin tocar el código con `CANALES_VIDEO_CONFIABLES`, en
    formato `marca=canal|otro canal;otra marca=canal`.
    """
    extra: dict[str, tuple[str, ...]] = {}
    for bloque in os.getenv("CANALES_VIDEO_CONFIABLES", "").split(";"):
        if "=" not in bloque:
            continue
        m, canales = bloque.split("=", 1)
        nombres = tuple(c.strip() for c in canales.split("|") if c.strip())
        if nombres:
            extra.setdefault(normalizar(m).strip(), ())
            extra[normalizar(m).strip()] += nombres
    clave = normalizar(marca).strip()
    return CANALES_CONFIABLES.get(clave, ()) + extra.get(clave, ())


def _marca_en(marca: str, texto: str) -> bool:
    """Todas las palabras de la marca, cada una suelta."""
    palabras = [p for p in normalizar(marca).split() if p]
    if not palabras:
        return False
    t = normalizar(texto)
    return all(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", t) for p in palabras)


def _tiene_numero(numero: str, titulo_video: str) -> bool:
    return bool(re.search(rf"(?<!\d){re.escape(numero)}(?!\d)", titulo_video))


def _aceptable(titulo_producto: str, marca: str, numero: str,
               titulo_video: str, canal: str) -> bool:
    """¿Este video es del canal oficial de la marca y de este producto?

    Dos filtros:

      1. **El canal tiene que ser el de la marca.** Es la guarda que importa.
         Pedir la marca en el título no sirve: una reseña de un tercero también
         dice "LEGO 21042" ahí, y ese es justamente el video que no queremos.
         El canal, en cambio, separa "LEGO" de "Ladrillos y Café".
      2. Que el video sea de *este* producto y no de otro del mismo fabricante:
         si hay número de modelo, tiene que estar en el título; si no lo hay, se
         exige parecido de títulos, que es más flojo pero es lo único que queda.
    """
    if not _marca_en(marca, canal):
        return False
    if numero:
        return _tiene_numero(numero, titulo_video)
    return parecido(titulo_producto, titulo_video) >= MINIMO_PARECIDO


def _aceptable_de_confianza(marca: str, numero: str, titulo_video: str,
                            canal: str) -> bool:
    """Segunda opción: un canal de terceros que se eligió confiar para la marca.

    Acá se exige **siempre el número de modelo**, aunque el producto no lo
    tenga en la ficha. En el canal oficial el parecido de títulos alcanza
    porque todo lo que sube es de sus productos; en un canal de terceros no:
    sube de todas las marcas, y sin número no hay forma de saber de cuál es
    ese video.
    """
    canales = canales_confiables(marca)
    if not canales or not numero:
        return False
    objetivo = normalizar(canal).strip()
    if not any(normalizar(c).strip() == objetivo for c in canales):
        return False
    return _tiene_numero(numero, titulo_video)


def buscar_video(titulo: str, marca: str = "", numero_set: str = "",
                 timeout: int = 12, api_key: Optional[str] = None,
                 limite: int = 10) -> dict:
    """El video de este producto en YouTube.

    Devuelve {video_id, titulo, canal, oficial} o {} si no hay ninguno que pase
    los filtros. Que devuelva {} es un resultado correcto y frecuente: muchos
    productos no tienen video.

    Se busca en dos pasadas sobre los mismos resultados: primero el canal
    oficial de la marca, y solo si no aparece ninguno, los canales de terceros
    elegidos para esa marca. El orden importa: el oficial siempre gana, aunque
    venga más abajo en los resultados.
    """
    clave = (api_key if api_key is not None
             else os.getenv("YOUTUBE_API_KEY", "")).strip()
    marca = (marca or "").strip()
    numero = (numero_set or "").strip() or numero_de_set(titulo)
    if not clave or not (titulo or "").strip():
        return {}

    consulta = " ".join(x for x in (marca, numero, titulo) if x)[:120]
    try:
        r = requests.get(YOUTUBE_API, timeout=timeout, params={
            "key": clave, "part": "snippet", "type": "video",
            "maxResults": max(1, min(limite, 25)), "q": consulta,
        })
        if r.status_code != 200:
            return {}
        datos = r.json()
    except (requests.RequestException, ValueError):
        return {}

    candidatos = []
    for item in (datos.get("items") or []):
        vid = ((item.get("id") or {}).get("videoId") or "").strip()
        snip = item.get("snippet") or {}
        if vid:
            candidatos.append((vid, snip.get("title") or "",
                               snip.get("channelTitle") or ""))

    for vid, titulo_video, canal in candidatos:
        if _aceptable(titulo, marca, numero, titulo_video, canal):
            return {"video_id": vid, "titulo": titulo_video, "canal": canal,
                    "oficial": True}
    for vid, titulo_video, canal in candidatos:
        if _aceptable_de_confianza(marca, numero, titulo_video, canal):
            return {"video_id": vid, "titulo": titulo_video, "canal": canal,
                    "oficial": False}
    return {}
