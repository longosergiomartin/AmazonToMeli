"""
Datos que se pueden leer del propio título del producto.

Los títulos de Amazon traen, además del nombre, identificadores que
MercadoLibre pide como atributos: el número de set del fabricante y la cantidad
de piezas. Sacarlos de acá evita tener que cargarlos a mano uno por uno.
"""

from __future__ import annotations

import re
import unicodedata


def numero_de_set(titulo: str) -> str:
    """Número de set del fabricante ("LEGO Star Wars 75339" → "75339").

    Es el identificador con el que el producto está cargado en el catálogo de
    MercadoLibre, así que sirve para encontrarlo sin ambigüedad. Los sets tienen
    4 o 5 dígitos; los años (19xx/20xx) y las cantidades ("802 piezas") se
    descartan para no confundirlos con el set.
    """
    texto = titulo or ""
    candidatos = []
    for m in re.finditer(r"\b(\d{4,5})\b", texto):
        num = m.group(1)
        if len(num) == 4 and (num.startswith("19") or num.startswith("20")):
            continue  # un año, no un set
        resto = texto[m.end():m.end() + 12].lower()
        if re.match(r"\s*(piezas|pcs|pieces|bloques|ml\b|g\b|cm)", resto):
            continue
        candidatos.append(num)
    # El número de set suele ser el último token numérico del título.
    return candidatos[-1] if candidatos else ""


# Arranques de marketing que Amazon le pone a los títulos traducidos y que no
# aportan nada en MercadoLibre.
_PREFIJOS_MARKETING = (
    r"set de construcci[óo]n( de)?", r"juego de construcci[óo]n( de)?",
    r"juguete para armar", r"kit de construcci[óo]n( de)?",
    r"juego de", r"set de", r"kit de", r"building (kit|set|toy)",
)

# Desde acá hasta el final, el título de Amazon es puro argumento de venta:
# a quién regalarlo, para qué edad, qué lindo queda en el estante. En 60
# caracteres ese texto se come el nombre del set, que es lo que el comprador
# busca. Se corta en la primera marca y se tira todo lo que sigue.
_COLAS_MARKETING = (
    r"\b(great |perfect |ideal )?(gift|present)s? (for|idea)\b",
    r"\b(birthday|christmas|holiday|xmas)\b",
    r"\bfor (kids|boys|girls|children|adults|teens|fans)\b",
    r"\bboys and girls\b", r"\bkids and adults\b",
    r"\bages?\s*\d+\s*(\+|plus|and up|y m[áa]s)?\b", r"\b\d+\+\s*years?\b",
    r"\bpara (ni[ñn]os|ni[ñn]as|chicos|adultos|fan[áa]ticos)\b",
    r"\bidea(l)? (de|para) regalo\b", r"\bregalo (para|de)\b",
    r"\bcumplea[ñn]os\b", r"\bnavidad\b",
    r"\bhome (or )?office d[ée]cor\b", r"\bd[ée]cor(ation|ativo)?\b",
    r"\bcollectible\b", r"\bbuildable\b",
)

# Palabras que dicen lo que ya dice "Set LEGO": que es de armar. En un título
# de 60 caracteres cada una de estas cuesta el nombre de un personaje.
_GENERICAS = (
    r"\bbuilding (kit|set|toys?|blocks?|pack)\b",
    r"\bconstruction (toy|set|kit)s?\b",
    r"\bjuguete de construcci[óo]n\b", r"\bbloques de construcci[óo]n\b",
    r"\b(set|kit) pack\b", r"\bplayset\b", r"\bmodel kit\b",
    r"\btoy (set|kit|figures?)\b",
)

# Un título que termina en preposición o artículo quedó cortado a la mitad
# ("...Farm with", "...Alex Creeper and 2"). Se recorta hasta la última palabra
# que se sostenga sola.
_CONECTORES_FINALES = {
    "with", "and", "for", "the", "a", "an", "in", "of", "to", "or", "on",
    "de", "del", "la", "el", "los", "las", "un", "una", "con", "y", "para",
    "por", "en", "que", "su", "sus", "al",
}


def _sin_cola_de_marketing(texto: str) -> str:
    """Corta el título en el primer argumento de venta y tira el resto."""
    corte = len(texto)
    for patron in _COLAS_MARKETING:
        m = re.search(patron, texto, flags=re.I)
        if m and m.start() < corte:
            corte = m.start()
    # No se corta tan al principio que quede solo la marca: si el argumento de
    # venta aparece en las primeras palabras, es parte del nombre del producto.
    return texto[:corte] if corte >= 12 else texto


# "3 in 1" / "2 en 1" es el nombre del producto, no una frase cortada: el número
# final no se toca aunque venga después de una preposición.
_N_EN_N = re.compile(r"\b\d+\s+(in|en)\s+\d+\s*$", re.I)


def _sin_conector_final(texto: str) -> str:
    """Saca del final las palabras que no se sostienen solas.

    Recortar a 60 caracteres deja colgado un "with" o un "and 2": queda leyendo
    como una frase partida al medio.
    """
    def limpio(w: str) -> str:
        return normalizar(w.strip(".,-–—:"))

    palabras = texto.split()
    while palabras:
        if _N_EN_N.search(" ".join(palabras)):
            break
        if limpio(palabras[-1]) in _CONECTORES_FINALES:
            palabras.pop()
            continue
        # Un número suelto al final solo sobra si quedó colgado de un conector
        # ("…Alex Creeper and 2"). Si no, puede ser parte del nombre.
        if (len(palabras) >= 2 and limpio(palabras[-1]).isdigit()
                and limpio(palabras[-2]) in _CONECTORES_FINALES):
            palabras.pop()
            palabras.pop()
            continue
        break
    return " ".join(palabras)


def titulo_para_ml(marca: str, titulo: str, numero_set: str = "",
                   limite: int = 60, piezas: str = "",
                   tipo: str = "") -> str:
    """Título para MercadoLibre, armado para que se encuentre y se entienda.

    MercadoLibre recomienda *producto + marca + modelo + especificaciones*, sin
    palabras promocionales ni repeticiones. Acá eso queda:

        [tipo] MARCA nombre-del-set NÚMERO [N piezas]

    El título de Amazon no sirve tal cual: es marketing traducido ("Set de
    construcción Star Wars de LEGO, Darth Vader, talla única"), viene con la
    marca en el medio y con una cola de argumentos de venta que en 60 caracteres
    se come el nombre del set y el número, que es justo lo que el comprador
    busca. Y recortado a lo bruto queda partido al medio ("...Farm with").

    `piezas` se agrega solo si sobra lugar: es una especificación real que el
    comprador compara, pero nunca a costa del nombre ni del número.
    """
    base = re.sub(r"\s+", " ", (titulo or "")).strip()
    base = _sin_cola_de_marketing(base)
    for p in _PREFIJOS_MARKETING:
        base = re.sub(r"^" + p + r"\s*", "", base, flags=re.I).strip()
    marca = (marca or "").strip()
    if marca:
        # La marca va una sola vez y adelante: en los títulos traducidos aparece
        # en el medio ("...Star Wars de LEGO, Darth Vader").
        base = re.sub(r"\bde\s+" + re.escape(marca) + r"\b", "", base, flags=re.I)
        base = re.sub(r"\b" + re.escape(marca) + r"\b", "", base, flags=re.I)
    # El número de set se saca del cuerpo y se vuelve a poner al final: si se
    # deja donde estaba, el recorte a 60 caracteres se lo come justo a él, que
    # es el dato con el que después se busca el producto en el catálogo.
    if numero_set:
        base = re.sub(r"[#nN]?[°º]?\s*" + re.escape(numero_set) + r"\b", "", base)
    # La cantidad de piezas va como atributo aparte; en el título solo gasta
    # caracteres y, al quitar el número de set, deja restos ("Set # – 1 103").
    base = re.sub(r"\(?\s*[\d.,]+\s*(piezas|pzas|pcs|pieces|bloques)\b\)?",
                  "", base, flags=re.I)
    # Decir "building kit" es gastar 12 caracteres en repetir lo que ya dice
    # "Set LEGO", y encima en inglés: nadie lo busca así en Argentina.
    for g in _GENERICAS:
        base = re.sub(g, " ", base, flags=re.I)
    if tipo:
        # Sin esto queda "Set LEGO ... Farm Set 21181": la misma palabra dos
        # veces, y una de ellas ocupando el lugar del nombre.
        base = re.sub(r"\b" + re.escape(tipo) + r"\b", " ", base, flags=re.I)
        base = re.sub(r"\bpack\b", " ", base, flags=re.I)
    base = re.sub(r"\s*[,;·|]\s*", " ", base)
    base = re.sub(r"[#]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" ,-–—:")

    # Solo se agrega si parece un número de set del fabricante (4 a 6 dígitos).
    # Amazon a veces declara un código interno de 7 dígitos (6474652) que no
    # identifica nada para el comprador ni sirve para buscar: en el título solo
    # ensucia.
    sufijo = f" {numero_set}" if re.fullmatch(r"\d{4,6}", numero_set or "") else ""
    tipo = (tipo or "").strip()
    prefijo = " ".join(x for x in (tipo, marca) if x)
    prefijo = f"{prefijo} " if prefijo else ""
    espacio = limite - len(prefijo) - len(sufijo)
    if espacio < 1:
        return (prefijo + sufijo.strip())[:limite].strip()
    if len(base) > espacio:
        base = base[:espacio].rsplit(" ", 1)[0].strip(" ,-–—")
    base = _sin_conector_final(base)

    armado = (prefijo + base + sufijo).strip()
    # El lugar que sobra se aprovecha con la cantidad de piezas, que es lo que
    # el comprador usa para comparar dos sets parecidos.
    if piezas and re.fullmatch(r"\d{2,6}", str(piezas)):
        extra = f" {piezas} Piezas"
        if len(armado) + len(extra) <= limite:
            armado += extra
    return armado


# Palabras que aparecen en cualquier título y no distinguen un producto de otro.
_VACIAS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "con", "para", "por",
    "y", "a", "en", "the", "of", "for", "with", "and", "to",
    "set", "sets", "kit", "kits", "juego", "juegos", "juguete", "juguetes",
    "building", "toy", "toys", "block", "blocks", "bloques", "construccion",
    "piezas", "pieces", "pzas", "pcs", "modelo", "model", "coleccionable",
    "regalo", "gift", "nuevo", "new", "edicion", "edition", "adultos", "adults",
}


def _tokens(texto: str) -> set:
    palabras = re.findall(r"[0-9a-zA-ZÁÉÍÓÚÜÑáéíóúüñ]+", texto or "")
    return {p for p in (normalizar(w) for w in palabras)
            if len(p) >= 3 and p not in _VACIAS}


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()


def parecido(referencia: str, candidato: str) -> float:
    """Qué proporción de las palabras distintivas de `referencia` aparece en
    `candidato`. Sirve para decidir si dos títulos son el mismo producto.

    Si los dos traen número de modelo y **no coinciden**, devuelve 0: son
    productos distintos por más que compartan todas las palabras (dos sets de
    Star Wars comparten casi todo el título menos el número, que es justo lo
    único que los distingue).
    """
    a, b = _tokens(referencia), _tokens(candidato)
    if not a or not b:
        return 0.0
    na, nb = numero_de_set(referencia), numero_de_set(candidato)
    if na and nb and na != nb:
        return 0.0
    return len(a & b) / len(a)


def piezas_del_titulo(titulo: str) -> str:
    """Cantidad de piezas anunciada en el título ("(802 piezas)" → "802").

    MercadoLibre la pide para los sets de construcción; sin ella la publicación
    sale igual pero peor matcheada con su catálogo.
    """
    m = re.search(r"\b(\d{1,6})\s*(?:piezas|piezas\.|pzas|pcs|pieces|bloques)\b",
                  titulo or "", re.I)
    return m.group(1) if m else ""
