"""
Procesador de la cola de importación, para correr en TU PC.

Por qué existe: Amazon bloquea las IPs de los servidores en la nube (Render y
similares), así que desde ahí la lectura de fichas falla siempre. Desde una
conexión hogareña, en cambio, funciona.

Como la base de datos es compartida (la misma DATABASE_URL que usa el panel en
la nube), este script ve la MISMA cola: encolás desde donde quieras y procesás
desde tu casa. Los productos cargados aparecen en el panel al instante.

Uso (en la carpeta del proyecto):

    set DATABASE_URL=postgresql://...        (Windows; el mismo de Render)
    py procesar_cola.py

    py procesar_cola.py --maximo 40 --pausa 4     # más lento y prudente
    py procesar_cola.py --reintentar              # retomar los frenados

Frena solo si Amazon empieza a limitar: no insiste ni intenta esquivar el
bloqueo. Al día siguiente se continúa con --reintentar.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from arbitraje.config import CONFIG_DEFAULT
from arbitraje.cotizacion import obtener_cotizaciones
from catalogo import Catalogo
from db import conectar, describir
from importador import ColaImportacion


def main() -> int:
    p = argparse.ArgumentParser(description="Procesa la cola de importación desde tu PC")
    p.add_argument("--maximo", type=int, default=25,
                   help="cuántos productos procesar como máximo (default 25)")
    p.add_argument("--pausa", type=float, default=3.0,
                   help="segundos de espera entre productos (default 3)")
    p.add_argument("--reintentar", action="store_true",
                   help="vuelve a poner en cola los que quedaron frenados")
    args = p.parse_args()

    conn = conectar()
    print(f"Base: {describir(conn)}")
    if not getattr(conn, "postgres", False) and not os.environ.get("DATABASE_URL"):
        print("Ojo: estás usando la base LOCAL (SQLite). Si querés procesar la cola\n"
              "     del panel en la nube, definí DATABASE_URL con el mismo valor\n"
              "     que pusiste en Render.\n")

    cat = Catalogo(conn, cfg=CONFIG_DEFAULT,
                   cotizacion=obtener_cotizaciones(CONFIG_DEFAULT))
    cola = ColaImportacion(conn, cat)

    if args.reintentar:
        e = cola.reactivar_bloqueados()
        print(f"Frenados devueltos a la cola. Pendientes: {e['pendientes']}")

    e = cola.estado()
    print(f"Cola: {e['pendientes']} pendientes · {e['listos']} cargados · "
          f"{e['errores']} con error · {e['bloqueados']} frenados\n")
    if not e["pendientes"]:
        print("No hay nada pendiente.")
        return 0

    hechos = fallidos = 0
    for i in range(args.maximo):
        r = cola.procesar_uno()
        if r["motivo"] == "vacia":
            print("\nCola vacía: terminó todo. ✓")
            break
        if r["detener"]:
            print(f"\n⚠ Amazon nos limitó ({r.get('mensaje', '')[:80]}).")
            print("  La cola quedó frenada y NO se perdió nada.")
            print(f"  Quedan {r['pendientes']} pendientes + {r['bloqueados']} frenados.")
            print("  Continuá más tarde o mañana con:  py procesar_cola.py --reintentar")
            break
        if r["hecho"]:
            hechos += 1
            print(f"  ✓ {r['asin']}  {r.get('titulo', '')[:60]}")
        else:
            fallidos += 1
            print(f"  · {r['asin']}  sin datos ({r.get('mensaje', '')[:50]})")
        if i < args.maximo - 1:
            time.sleep(args.pausa)

    e = cola.estado()
    print(f"\nResumen: {hechos} cargados, {fallidos} sin datos. "
          f"Quedan {e['pendientes']} pendientes.")
    print("Revisalos y publicalos desde el panel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
