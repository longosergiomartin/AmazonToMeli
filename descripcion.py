"""
La descripción con la que sale la publicación.

Lo que trae Amazon son sus bullets en inglés. Para el comprador argentino eso
deja sin responder justo las preguntas que lo frenan: cuánto tarda, si es
original, qué pasa si no llega. Vender importado es competir con la incertidumbre
más que con el precio, así que la descripción tiene que despejarla primero y
después contar el producto.

El bloque de la compra es texto configurable: lo que dice ahí es un compromiso
comercial y lo tiene que poder escribir el vendedor, no quedar escondido en el
código.
"""

from __future__ import annotations

# El texto por defecto. `{dias}` se reemplaza por los días de preparación
# declarados en la publicación, para que lo que dice la descripción y lo que
# MercadoLibre le promete al comprador sean el mismo número.
COMPRA_DEFAULT = (
    "CÓMO ES LA COMPRA\n"
    "• Producto 100% original y nuevo, sellado en su caja.\n"
    "• Se importa a pedido desde Estados Unidos: la entrega demora "
    "aproximadamente {dias} días hábiles desde que se acredita el pago.\n"
    "• El precio publicado ya incluye impuestos y el costo de importación. "
    "No hay que pagar nada más al recibirlo.\n"
    "• Se entrega con factura y con la garantía de MercadoLibre: si no llega "
    "en el plazo, te devuelven el dinero.\n"
    "• Ante cualquier duda escribinos antes de comprar y te respondemos."
)

LIMITE = 4900


def _ficha(marca: str = "", numero_set: str = "", piezas: str = "",
           modelo: str = "") -> str:
    """Los datos duros arriba de todo: es lo primero que se escanea."""
    filas = []
    if marca:
        filas.append(f"• Marca: {marca}")
    if numero_set:
        filas.append(f"• Número de set: {numero_set}")
    if piezas:
        filas.append(f"• Cantidad de piezas: {piezas}")
    if modelo:
        filas.append(f"• Modelo: {modelo}")
    return "\n".join(filas)


def armar(titulo: str = "", detalle: str = "", marca: str = "",
          numero_set: str = "", piezas: str = "", modelo: str = "",
          dias: int = 25, compra: str = "", limite: int = LIMITE) -> str:
    """La descripción completa, en el orden en que conviene leerla.

    Primero el título y la ficha —marca, número de set, piezas—, después el
    bloque de la compra, que es el que responde la objeción real de un producto
    importado, y al final el detalle que vino de Amazon.

    El detalle va último a propósito: es lo más largo, lo menos decisivo y lo
    único que puede venir en inglés. Si algo se recorta por el límite de
    MercadoLibre, que sea eso y no las condiciones de la compra.
    """
    compra = (compra if compra.strip() else COMPRA_DEFAULT)
    compra = compra.replace("{dias}", str(int(dias or 0)))

    bloques = []
    if titulo.strip():
        bloques.append(titulo.strip())
    ficha = _ficha(marca, numero_set, piezas, modelo)
    if ficha:
        bloques.append(ficha)
    bloques.append(compra.strip())

    cabeza = "\n\n".join(bloques)
    detalle = (detalle or "").strip()
    if not detalle:
        return cabeza[:limite]

    detalle = "SOBRE EL PRODUCTO\n" + detalle
    espacio = limite - len(cabeza) - 2
    if espacio < 60:
        return cabeza[:limite]
    if len(detalle) > espacio:
        # Se corta en el último renglón entero: cortar a mitad de una frase
        # queda peor que no incluirla.
        detalle = detalle[:espacio].rsplit("\n", 1)[0].rstrip()
    return f"{cabeza}\n\n{detalle}"
