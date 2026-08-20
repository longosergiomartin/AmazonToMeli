"""
Autocompletado de datos de un producto de Amazon a partir de su link.

Estrategia (legítima, sin scraping masivo):
  1. El ASIN se extrae del propio link (siempre funciona, sin red).
  2. Se intenta leer la página del producto con un User-Agent de navegador para
     completar título, marca, precio y peso. Amazon bloquea servidores de
     datacenter, pero desde una PC hogareña (IP residencial) suele responder.
  3. Si la lectura falla o falta algún dato, se devuelve lo que se pudo (al
     menos ASIN + link) y el resto se completa a mano.

No obtiene el "Total puesto en Argentina" (envío + importación): ese dato vive
en el checkout, no en la página del producto.
"""

from __future__ import annotations

import html as _html
import re
from typing import Optional

import requests

from marcas import limpiar_marca

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_LBS_A_KG = 0.453592


def extraer_asin(url: str) -> str:
    """Saca el ASIN del link (formatos /dp/ASIN, /gp/product/ASIN, ?...&asin=)."""
    for patron in (r"/dp/([A-Z0-9]{10})", r"/gp/product/([A-Z0-9]{10})",
                   r"/product/([A-Z0-9]{10})", r"[?&]asin=([A-Z0-9]{10})"):
        m = re.search(patron, url, re.I)
        if m:
            return m.group(1).upper()
    return ""


def _buscar(patrones, texto) -> Optional[str]:
    for p in patrones:
        m = re.search(p, texto, re.I | re.S)
        if m:
            return _html.unescape(m.group(1)).strip()
    return None


def _parse_precio(texto: str) -> Optional[float]:
    val = _buscar([
        r'"priceAmount"\s*:\s*([0-9]+\.?[0-9]*)',
        r'id="corePrice[^"]*"[^>]*>.*?class="a-offscreen"[^>]*>\s*\$?\s*([0-9.,]+)',
        r'class="a-price-whole"[^>]*>\s*([0-9.,]+)',
        r'class="a-offscreen"[^>]*>\s*\$\s*([0-9.,]+)',
    ], texto)
    if not val:
        return None
    val = val.replace(",", "")
    try:
        return round(float(val), 2)
    except ValueError:
        return None


def _texto(fragmento: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", fragmento)
    return re.sub(r"\s+", " ", _html.unescape(txt)).strip()


def _parse_descripcion(texto: str) -> str:
    """Arma una descripción con los bullets ('Sobre este artículo') y la
    descripción larga del producto."""
    partes = []
    m = re.search(r'id="feature-bullets"(.*?)</div>\s*</div>', texto, re.S | re.I)
    if m:
        for li in re.findall(r'<li[^>]*>(.*?)</li>', m.group(1), re.S | re.I):
            t = _texto(li)
            if t and len(t) > 2:
                partes.append("• " + t)
    m2 = re.search(r'id="productDescription"(.*?)</div>', texto, re.S | re.I)
    if m2:
        t = _texto(m2.group(1))
        if t:
            partes.append(t)
    return "\n".join(partes)[:4900]


def _parse_imagenes(texto: str, limite: int = 8) -> list:
    """Saca las URLs de las fotos del producto (alta resolución si están)."""
    urls: list = []
    for patron in (r'"hiRes":"(https:[^"]+)"', r'"large":"(https:[^"]+)"'):
        for u in re.findall(patron, texto):
            u = u.replace("\\/", "/")
            if "media-amazon" in u and u not in urls:
                urls.append(u)
        if urls:
            break
    if not urls:
        m = re.search(r'id="landingImage"[^>]*\ssrc="(https:[^"]+)"', texto)
        if m:
            urls.append(m.group(1))
    return urls[:limite]


def _parse_detalles(texto: str) -> dict:
    """Tabla de "Detalles del producto" / "Product information" de Amazon.

    Es la fuente buena de los datos duros: el título es marketing traducido y a
    veces omite el número de set, pero acá el fabricante los declara con
    etiqueta. Devuelve {etiqueta_normalizada: valor}.
    """
    detalles: dict = {}

    def _guardar(etiqueta: str, valor: str) -> None:
        et = re.sub(r"\s+", " ", _texto(etiqueta)).strip(" :‎‏").lower()
        val = re.sub(r"\s+", " ", _texto(valor)).strip(" :‎‏")
        if et and val and et not in detalles:
            detalles[et] = val

    # Tablas (productDetails_detailBullets_sections1, techSpec, etc.).
    for th, td in re.findall(r"<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                             texto, re.S | re.I):
        _guardar(th, td)
    # Lista de viñetas (detailBullets_feature_div).
    for li in re.findall(r"<li[^>]*>(.*?)</li>", texto, re.S | re.I):
        m = re.search(r'class="a-text-bold"[^>]*>(.*?)</span>(.*)', li, re.S | re.I)
        if m:
            _guardar(m.group(1), m.group(2))
    return detalles


# Cómo llama Amazon al número de modelo del fabricante, que en LEGO es el
# número de set (75304) — el dato con el que MercadoLibre tiene cargados los
# productos en su catálogo.
_ETIQUETAS_MODELO = (
    "numero de modelo del articulo", "número de modelo del artículo",
    "item model number", "numero de parte del fabricante",
    "número de pieza del fabricante", "manufacturer part number",
    "numero de modelo", "modelo",
)
_ETIQUETAS_MARCA = ("marca", "brand", "fabricante", "manufacturer")


def _de_detalles(detalles: dict, etiquetas) -> str:
    for et in etiquetas:
        clave = _norm_etiqueta(et)
        for k, v in detalles.items():
            if _norm_etiqueta(k) == clave:
                return v
    return ""


def _numero_de_modelo(valor: str) -> str:
    """Limpia el número de modelo que declara el fabricante.

    Viene con ruido según el vendedor: "75304", "LEGO 75304", "75304-1",
    "6379837". Nos quedamos con el bloque de dígitos, que es lo que después se
    busca en el catálogo de MercadoLibre.
    """
    valor = (valor or "").strip()
    if not valor:
        return ""
    m = re.search(r"\b(\d{4,7})\b", valor)
    return m.group(1) if m else valor[:30]


def _norm_etiqueta(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def _parse_peso_kg(texto: str) -> Optional[float]:
    m = re.search(r'(?:Item Weight|Peso del (?:producto|art[íi]culo))[^0-9]{0,40}'
                  r'([0-9]+\.?[0-9]*)\s*(pounds|libras|lb|lbs|kilograms|kg|onzas|ounces|oz)',
                  texto, re.I)
    if not m:
        return None
    val, unidad = float(m.group(1)), m.group(2).lower()
    if unidad.startswith(("pound", "libra", "lb")):
        return round(val * _LBS_A_KG, 2)
    if unidad.startswith(("ounce", "onza", "oz")):
        return round(val * _LBS_A_KG / 16, 2)
    return round(val, 2)


def importar_desde_url(url: str, timeout: int = 12) -> dict:
    """Devuelve los datos que se pudieron obtener del producto de Amazon.
    Siempre incluye asin (si está en el link) y amazon_link; `ok` indica si se
    pudo leer la página."""
    url = (url or "").strip()
    datos = {"asin": extraer_asin(url), "amazon_link": url, "ok": False,
             "marca": "", "modelo": "", "modelo_fabricante": "", "detalles": {},
             "precio_usd": None, "peso_kg": None,
             "descripcion": "", "imagenes": [], "mensaje": "",
             # `status` y `bloqueado` permiten a la cola de importación
             # distinguir "no pude leer la página" de "Amazon me está
             # limitando", que es cuando hay que frenar y seguir otro día.
             "status": None, "bloqueado": False}
    if not url.startswith("http"):
        datos["mensaje"] = "Pegá un link válido de Amazon."
        return datos
    try:
        resp = requests.get(url, headers={"User-Agent": _UA,
                            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"},
                            timeout=timeout)
    except requests.RequestException as e:
        datos["mensaje"] = f"No se pudo leer la página ({e}). Completá a mano."
        return datos

    datos["status"] = resp.status_code
    if resp.status_code != 200:
        # 429/503 = nos está limitando; 403 = nos bloqueó. En esos casos la
        # cola tiene que parar, no insistir.
        datos["bloqueado"] = resp.status_code in (403, 429, 503)
        datos["mensaje"] = (f"Amazon respondió {resp.status_code} (suele pasar en "
                            "servidores). El ASIN quedó cargado; completá el resto a mano.")
        return datos

    texto = resp.text
    # Amazon a veces responde 200 con la página de "no soy un robot".
    if "captcha" in texto[:4000].lower() and "productTitle" not in texto:
        datos["bloqueado"] = True
        datos["mensaje"] = ("Amazon pidió verificación (captcha). Conviene frenar "
                            "y continuar más tarde.")
        return datos
    titulo = _buscar([r'id="productTitle"[^>]*>(.*?)</span>',
                      r'<title>(.*?)</title>'], texto)
    # El byline trae la marca envuelta en texto y en el idioma del sitio
    # ("Visit the LEGO Store"): `limpiar_marca` deja solo el nombre, que es lo
    # único que MercadoLibre acepta como valor de BRAND.
    # `[^<]` para que la captura no se lleve etiquetas ni comentarios HTML por
    # delante: eso terminaba guardado como marca y MercadoLibre lo rechazaba.
    marca = _buscar([r'id="bylineInfo"[^>]*>([^<]{2,80})<',
                     r'"brand"\s*:\s*"([^"]{2,80})"',
                     r'>\s*(?:Marca|Brand)\s*</span>.*?<span[^>]*>([^<]{2,80})<'], texto)
    # La ficha del producto gana sobre el byline y sobre el título: ahí el
    # fabricante declara marca y número de modelo con etiqueta, sin traducciones
    # ni recortes de marketing.
    detalles = _parse_detalles(texto)
    datos["detalles"] = detalles
    datos["modelo_fabricante"] = _numero_de_modelo(
        _de_detalles(detalles, _ETIQUETAS_MODELO))
    datos["modelo"] = (titulo or "")[:120]
    datos["marca"] = (limpiar_marca(_de_detalles(detalles, _ETIQUETAS_MARCA))
                      or limpiar_marca(marca or ""))
    datos["precio_usd"] = _parse_precio(texto)
    datos["peso_kg"] = _parse_peso_kg(texto)
    datos["descripcion"] = _parse_descripcion(texto)
    datos["imagenes"] = _parse_imagenes(texto)
    datos["ok"] = bool(titulo or datos["precio_usd"] or datos["imagenes"])
    if not datos["ok"]:
        datos["mensaje"] = ("Se cargó el ASIN pero no pude leer los detalles. "
                            "Completá precio/peso a mano (mirá el checkout para el Total).")
    else:
        datos["mensaje"] = (
            f"Traído de Amazon: {len(datos['imagenes'])} foto/s"
            + (", descripción" if datos["descripcion"] else "")
            + ". Revisá y agregá el envío+importación desde el checkout.")
    return datos
