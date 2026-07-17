"""
Interfaz de línea de comandos.

Ejemplos:
    # Evaluar los productos del CSV bajo régimen courier
    python -m arbitraje.cli --csv data/productos.example.csv

    # Comparar courier vs general y exportar a CSV
    python -m arbitraje.cli --csv data/productos.example.csv \
        --regimen courier general --export resultados.csv

    # Usar precios manuales sin tocar la API de MeLi (offline)
    python -m arbitraje.cli --csv data/productos.example.csv --sin-api

    # Con una config propia (tipo de cambio, alícuotas, comisiones)
    python -m arbitraje.cli --csv data/productos.example.csv --config mi_config.json
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List

from .config import Config, CONFIG_DEFAULT
from .amazon import ManualProvider
from .evaluador import evaluar_muchos
from .models import Oportunidad


def _fmt_ars(v: float) -> str:
    return f"${v:,.0f}"


def _imprimir_oportunidad(op: Oportunidad, cfg: Config) -> None:
    p = op.producto
    print(f"\n{'=' * 72}")
    print(f"{p.nombre}   [{op.regimen}]")
    print('=' * 72)
    if op.resultados_meli:
        print("  Referencias en MercadoLibre (confirmá el match real):")
        for i, r in enumerate(op.resultados_meli, 1):
            print(f"    {i}. {_fmt_ars(r.precio)} — {r.titulo}")
            if r.link:
                print(f"       {r.link}")
    if op.regimen == "landed":
        print(f"\n  Costo puesto (Total de Amazon): USD {p.precio_landed_usd:,.2f}")
    else:
        print(f"\n  Precio Amazon:              USD {p.precio_amazon_usd:,.2f}  ({p.peso_kg} kg)")
    print(f"  Dólar de compra (tarjeta):  ${cfg.tc_compra():,.0f}  "
          f"(oficial ${cfg.tipo_cambio_oficial:,.0f} + {cfg.recargo_tarjeta_pct:.0%})")
    print(f"  Costo puesto en Argentina:  {_fmt_ars(op.costo.total_ars)}  "
          f"(USD {op.costo.total_usd:,.2f})")
    print(f"  Precio de venta MeLi (ref): {_fmt_ars(op.precio_venta_ars)}")
    print(f"  Neto de la venta:           {_fmt_ars(op.venta.neto_ars)}")
    print(f"  {'-' * 50}")
    veredicto = op.veredicto(cfg.umbral_margen_bueno_pct)
    print(f"  MARGEN NETO: {_fmt_ars(op.margen_ars)}  ({op.margen_pct:.1f}%)  >>> {veredicto}")


def _tabla_resumen(oportunidades: List[Oportunidad]) -> None:
    print(f"\n{'=' * 72}")
    print("RANKING DE OPORTUNIDADES (mayor a menor margen)")
    print('=' * 72)
    print(f"{'Producto':<34}{'Rég.':<9}{'Margen ARS':>14}{'Margen %':>10}")
    print('-' * 72)
    for op in oportunidades:
        nombre = (op.producto.nombre[:31] + '...') if len(op.producto.nombre) > 34 else op.producto.nombre
        print(f"{nombre:<34}{op.regimen:<9}{_fmt_ars(op.margen_ars):>14}{op.margen_pct:>9.1f}%")


def _exportar_csv(oportunidades: List[Oportunidad], ruta: str) -> None:
    if not oportunidades:
        return
    campos = list(oportunidades[0].fila_resumen().keys())
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for op in oportunidades:
            writer.writerow(op.fila_resumen())
    print(f"\nExportado a {ruta}")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detector de arbitraje Amazon -> MercadoLibre Argentina",
    )
    parser.add_argument("--csv", help="CSV con productos a evaluar (ver data/productos.example.csv)")
    parser.add_argument("--regimen", nargs="+", default=["courier"],
                        choices=["courier", "general"],
                        help="Régimen(es) de importación a calcular (default: courier)")
    parser.add_argument("--config", help="Archivo JSON con la config (tipo de cambio, alícuotas...)")
    parser.add_argument("--recargo-tarjeta", type=float, default=None,
                        help="Recargo del dólar tarjeta sobre la compra, ej: 0.30 (pisa la config)")
    parser.add_argument("--export", help="Exportar el ranking a un CSV")
    parser.add_argument("--sin-api", action="store_true",
                        help="No consultar la API de MeLi; usar solo precios manuales")
    parser.add_argument("--token", help="Access token OAuth de MercadoLibre (opcional)")
    args = parser.parse_args(argv)

    cfg = Config.desde_json(args.config) if args.config else Config()
    if args.recargo_tarjeta is not None:
        cfg.recargo_tarjeta_pct = args.recargo_tarjeta

    if not args.csv:
        parser.error("Falta --csv con los productos a evaluar.")
    if not os.path.exists(args.csv):
        parser.error(f"No existe el archivo: {args.csv}")

    productos = ManualProvider.desde_csv(args.csv).cargar()
    if not productos:
        print("El CSV no tiene productos.")
        return 1

    oportunidades = evaluar_muchos(
        productos,
        regimenes=args.regimen,
        cfg=cfg,
        access_token=args.token,
        usar_api=not args.sin_api,
    )

    if not oportunidades:
        print("No se pudo evaluar ningún producto. Si la API de MeLi falló, "
              "cargá 'precio_meli_manual' en el CSV o usá --sin-api.")
        return 1

    for op in oportunidades:
        _imprimir_oportunidad(op, cfg)
    _tabla_resumen(oportunidades)

    if args.export:
        _exportar_csv(oportunidades, args.export)

    return 0


if __name__ == "__main__":
    sys.exit(main())
