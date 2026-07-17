"""
=======================================================================
 VALIDADOR DE MARGEN: Amazon (EEUU) -> MercadoLibre Argentina
=======================================================================
Objetivo de este script (fase MVP / validación de la idea):

  1. Buscar en la API pública de MercadoLibre cuál es el precio de venta
     real de un producto en Argentina.
  2. Calcular el "costo puesto en Argentina" del mismo producto importado
     desde Amazon, bajo el régimen de IMPORTADOR REGISTRADO (régimen
     general), no el régimen simplificado courier.
  3. Calcular cuánto te queda neto en el bolsillo si lo vendés en MeLi,
     descontando comisión, IVA, costo fijo, IIBB, Ganancias, envío.
  4. Mostrarte el margen neto en pesos y en % para que puedas juzgar si
     vale la pena.

QUÉ TE FALTA COMPLETAR VOS (a propósito, para que valides con datos
reales de tu propio análisis, no con supuestos míos):

  - PRECIO Y PESO en Amazon: por ahora los cargás a mano en
    `productos_a_evaluar` (más abajo). La búsqueda automática en Amazon
    es la Fase 2 del proyecto (requiere elegir una API paga tipo
    Rainforest/Keepa, o el Product Advertising API con cuenta de afiliado).
  - ARANCEL (derecho de importación) por NCM: cambia según el producto
    específico, no según la categoría genérica. Tenés que cargarlo vos
    por producto (o dejar el default y ajustarlo). Se puede consultar
    en el Nomenclador Común del Mercosur (NCM) / TARAR de ARCA.
  - Tipo de cambio, alícuotas de IIBB de tu jurisdicción y tu condición
    fiscal (Responsable Inscripto / Monotributo) también son variables
    que definís en la sección CONFIG.

Requiere: pip install requests
=======================================================================
"""

import requests

# =======================================================================
# CONFIG — ajustá estos valores a tu situación real. Todo lo que dice
# "VERIFICAR" son datos que cambian con el tiempo y conviene chequear
# antes de tomar una decisión con plata real.
# =======================================================================

TIPO_CAMBIO_OFICIAL = 1300.0  # ARS por USD — VERIFICAR cotización del día

# --- Costos de IMPORTACIÓN (régimen general / importador registrado) ---
IMPORT_CONFIG = {
    "tasa_estadistica_pct": 0.03,      # 3% sobre valor FOB (histórico; VERIFICAR tope vigente)
    "iva_pct": 0.21,                    # IVA general
    "percepcion_iva_pct": 0.10,         # 10% si sos Responsable Inscripto (20% si no) — VERIFICAR
    "percepcion_ganancias_pct": 0.06,   # 6% si sos RI (11% si no) — VERIFICAR
    "percepcion_iibb_pct": 0.025,       # depende de tu jurisdicción — AJUSTAR
    "despachante_pct": 0.02,            # honorarios despachante de aduana, aprox. sobre CIF — AJUSTAR
    "despachante_minimo_usd": 80,       # muchos despachantes cobran un mínimo fijo por operación
    "flete_seguro_pct": 0.15,           # flete internacional + seguro, aprox. % sobre FOB para paquetes chicos — AJUSTAR
    "gastos_portuarios_usd": 60,        # estimado fijo por despacho — AJUSTAR
}

# --- Costos de VENTA en MercadoLibre por categoría (comisión Premium) ---
# Fuente: tarifas publicadas por MeLi, actualizadas periódicamente.
# VERIFICAR en tu cuenta de vendedor antes de decidir, cambian cada
# pocos meses.
MELI_COMISIONES = {
    "electronica":       {"comision_pct": 0.1714, "costo_fijo": 2810},
    "computacion":       {"comision_pct": 0.1500, "costo_fijo": 2810},
    "hogar":             {"comision_pct": 0.1400, "costo_fijo": 2300},
    "default":           {"comision_pct": 0.1500, "costo_fijo": 2300},
}
MELI_IVA_SOBRE_COMISION = 0.21
MELI_IIBB_PCT = 0.03          # sobre el precio de venta — AJUSTAR según tu jurisdicción
MELI_GANANCIAS_PCT = 0.06     # si sos RI — AJUSTAR
COSTO_ENVIO_ESTIMADO_ARS = 6000  # si ofrecés envío gratis (Premium), estimalo por peso/volumen


# =======================================================================
# PRODUCTOS A EVALUAR (cargá acá los candidatos, a mano, por ahora)
# =======================================================================
productos_a_evaluar = [
    {
        "nombre": "Ejemplo: Auricular XYZ modelo 123",
        "query_meli": "auricular XYZ 123",  # término de búsqueda en MeLi
        "categoria": "electronica",
        "precio_fob_usd": 45.0,   # precio en Amazon, sin envío
        "arancel_pct": 0.16,      # VERIFICAR NCM específico del producto
    },
    # Agregá más productos acá con la misma estructura...
]


# =======================================================================
# LÓGICA
# =======================================================================

def buscar_precio_meli(query, site="MLA"):
    """Busca en la API pública de MercadoLibre y devuelve una lista de
    (título, precio, permalink) de los primeros resultados, para que
    puedas confirmar manualmente cuál es el producto equivalente real."""
    url = f"https://api.mercadolibre.com/sites/{site}/search"
    resp = requests.get(url, params={"q": query, "limit": 5}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    resultados = []
    for item in data.get("results", []):
        resultados.append({
            "titulo": item.get("title"),
            "precio": item.get("price"),
            "link": item.get("permalink"),
        })
    return resultados


def calcular_costo_importado(precio_fob_usd, arancel_pct, cfg=IMPORT_CONFIG,
                              tc=TIPO_CAMBIO_OFICIAL):
    """Calcula el costo puesto en Argentina (en ARS) de un producto
    importado bajo régimen general, a partir de su valor FOB en USD."""
    fob = precio_fob_usd
    flete_seguro = fob * cfg["flete_seguro_pct"]
    cif = fob + flete_seguro  # Cost + Insurance + Freight

    derechos_importacion = cif * arancel_pct
    tasa_estadistica = cif * cfg["tasa_estadistica_pct"]

    base_iva = cif + derechos_importacion + tasa_estadistica
    iva = base_iva * cfg["iva_pct"]
    percepcion_iva = base_iva * cfg["percepcion_iva_pct"]
    percepcion_ganancias = base_iva * cfg["percepcion_ganancias_pct"]
    percepcion_iibb = base_iva * cfg["percepcion_iibb_pct"]

    despachante = max(cif * cfg["despachante_pct"], cfg["despachante_minimo_usd"])
    gastos_portuarios = cfg["gastos_portuarios_usd"]

    total_usd = (cif + derechos_importacion + tasa_estadistica + iva +
                 percepcion_iva + percepcion_ganancias + percepcion_iibb +
                 despachante + gastos_portuarios)

    total_ars = total_usd * tc

    return {
        "total_usd": round(total_usd, 2),
        "total_ars": round(total_ars, 2),
        "detalle_usd": {
            "fob": round(fob, 2),
            "flete_seguro": round(flete_seguro, 2),
            "cif": round(cif, 2),
            "derechos_importacion": round(derechos_importacion, 2),
            "tasa_estadistica": round(tasa_estadistica, 2),
            "iva": round(iva, 2),
            "percepcion_iva": round(percepcion_iva, 2),
            "percepcion_ganancias": round(percepcion_ganancias, 2),
            "percepcion_iibb": round(percepcion_iibb, 2),
            "despachante": round(despachante, 2),
            "gastos_portuarios": gastos_portuarios,
        }
    }


def calcular_neto_venta_meli(precio_venta_ars, categoria):
    """Calcula cuánto te queda neto (en ARS) de una venta en MeLi,
    descontando comisión + IVA sobre comisión + costo fijo + IIBB +
    Ganancias + envío estimado."""
    cfg = MELI_COMISIONES.get(categoria, MELI_COMISIONES["default"])

    comision = precio_venta_ars * cfg["comision_pct"]
    costo_fijo = cfg["costo_fijo"] if precio_venta_ars < 33000 else 0
    iva_sobre_comision = (comision + costo_fijo) * MELI_IVA_SOBRE_COMISION
    iibb = precio_venta_ars * MELI_IIBB_PCT
    ganancias = precio_venta_ars * MELI_GANANCIAS_PCT

    total_descuentos = (comision + costo_fijo + iva_sobre_comision +
                         iibb + ganancias + COSTO_ENVIO_ESTIMADO_ARS)

    neto = precio_venta_ars - total_descuentos

    return {
        "neto_ars": round(neto, 2),
        "detalle_ars": {
            "comision": round(comision, 2),
            "costo_fijo": costo_fijo,
            "iva_sobre_comision": round(iva_sobre_comision, 2),
            "iibb": round(iibb, 2),
            "ganancias": round(ganancias, 2),
            "envio_estimado": COSTO_ENVIO_ESTIMADO_ARS,
        }
    }


def evaluar_producto(producto):
    print(f"\n{'='*70}\n{producto['nombre']}\n{'='*70}")

    resultados_meli = buscar_precio_meli(producto["query_meli"])
    if not resultados_meli:
        print("  No se encontraron resultados en MercadoLibre para esta búsqueda.")
        return

    print("  Resultados encontrados en MercadoLibre (confirmá cuál es el match real):")
    for i, r in enumerate(resultados_meli, 1):
        print(f"    {i}. ${r['precio']:,.0f} — {r['titulo']}")
        print(f"       {r['link']}")

    # Por ahora, tomamos el primer resultado como referencia.
    # En la Fase 2 (matching automático) esto se reemplaza por lógica real.
    precio_venta_ref = resultados_meli[0]["precio"]

    costo = calcular_costo_importado(
        producto["precio_fob_usd"], producto["arancel_pct"]
    )
    venta = calcular_neto_venta_meli(precio_venta_ref, producto["categoria"])

    margen_ars = venta["neto_ars"] - costo["total_ars"]
    margen_pct = (margen_ars / costo["total_ars"]) * 100 if costo["total_ars"] else 0

    print(f"\n  Precio de referencia en MeLi: ${precio_venta_ref:,.0f}")
    print(f"  Costo puesto en Argentina:    ${costo['total_ars']:,.0f} "
          f"(USD {costo['total_usd']:,.2f})")
    print(f"  Neto de la venta en MeLi:     ${venta['neto_ars']:,.0f}")
    print(f"  --------------------------------------------------")
    print(f"  MARGEN NETO: ${margen_ars:,.0f}  ({margen_pct:.1f}%)")

    if margen_pct >= 30:
        print("  >>> Oportunidad interesante, revisar en detalle.")
    elif margen_pct >= 0:
        print("  >>> Margen positivo pero ajustado.")
    else:
        print("  >>> No conviene con estos números.")


if __name__ == "__main__":
    for p in productos_a_evaluar:
        evaluar_producto(p)
