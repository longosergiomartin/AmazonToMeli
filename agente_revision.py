"""
Agente de revisión: mantiene al día lo que ya está publicado.

Es el hermano del agente de publicación, y va al revés. Aquel toma borradores y
los saca a la venta; este recorre lo que ya está vendiéndose y verifica que
todavía se pueda cumplir: que el producto siga en Amazon, a qué precio, y si el
precio de venta sigue dejando ganancia.

Existe porque publicar congela un precio. Entre que se publica y que alguien
compra pueden pasar semanas, y en ese tiempo Amazon sube el precio o se queda
sin stock. Se descubre cuando ya se vendió, que es tarde: hay que comprar más
caro, o cancelar.

Lo que hace en cada paso, sobre un producto por vez:
  1. Lee la ficha de Amazon.
  2. Guarda el precio nuevo y recalcula costo, precio sugerido y margen.
  3. Si Amazon se quedó sin stock, **pausa la publicación** en MercadoLibre.
  4. Si el margen al precio publicado quedó por debajo del mínimo, lo avisa.

Va de a uno y guarda por dónde iba (`revisado_en`), así una corrida cortada no
empieza de cero ni repite lo que ya miró.
"""

from __future__ import annotations

from typing import Callable, Optional

REVISAR = "revisar"
NADA = "nada"


class AgenteRevision:
    """Recorre lo publicado verificando precio y stock en Amazon.

    `leer` recibe la URL y devuelve el dict de `importar_desde_url`. `pausar`
    recibe el producto. Los dos se inyectan para que este módulo no dependa de
    la red ni de MercadoLibre.
    """

    def __init__(self, cat, leer: Callable, pausar: Callable,
                 margen_minimo: float = 0.0):
        self.cat = cat
        self._leer = leer
        self._pausar = pausar
        self.margen_minimo = margen_minimo
        # Lo visto en esta corrida, para el resumen del panel.
        self.revisados: list[dict] = []
        # Productos que no se pudieron leer. No se reintentan en la misma
        # corrida: si Amazon nos está rechazando, insistir no cambia nada.
        self.fallados: set[int] = set()

    # ---- estado ----------------------------------------------------------

    def _pendientes(self) -> list:
        """Publicados con link, que no se hayan mirado ya en esta corrida."""
        return [p for p in self.cat.a_revisar(limite=10_000)
                if p.id not in self.fallados
                and p.id not in {r["id"] for r in self.revisados}]

    def estado(self) -> dict:
        pend = self._pendientes()
        return {
            "por_revisar": len(pend),
            "revisados": len(self.revisados),
            "sin_stock": sum(1 for r in self.revisados if r.get("pausado")),
            "en_perdida": sum(1 for r in self.revisados if r.get("en_perdida")),
            "no_leidos": len(self.fallados),
            "encarecidos": sum(1 for r in self.revisados if r.get("subio")),
        }

    def reiniciar(self) -> dict:
        """Arranca una corrida nueva. Lo ya revisado vuelve a estar disponible."""
        self.revisados.clear()
        self.fallados.clear()
        return self.estado()

    # ---- una unidad de trabajo -------------------------------------------

    def tick(self) -> dict:
        """Revisa **un** producto. Devuelve qué encontró y qué hizo."""
        pend = self._pendientes()
        if not pend:
            return {"accion": "sin_trabajo", **self.estado()}

        p = pend[0]
        nombre = p.titulo_ml or p.modelo or p.asin
        url = p.amazon_link or (f"https://www.amazon.com/dp/{p.asin}"
                                if p.asin else "")
        if not url:
            self.fallados.add(p.id)
            return {"accion": "error", "id": p.id, "nombre": nombre,
                    "detalle": "no tiene link de Amazon", **self.estado()}

        try:
            d = self._leer(url) or {}
        except Exception as e:  # noqa: BLE001 - un producto no frena la corrida
            self.fallados.add(p.id)
            return {"accion": "error", "id": p.id, "nombre": nombre,
                    "detalle": str(e)[:200], **self.estado()}

        precio_nuevo = d.get("precio_usd")
        disponible = d.get("disponible")
        if precio_nuevo is None and disponible is None:
            # Ni precio ni disponibilidad: la página no se leyó. No se toca
            # nada, porque marcar "sin stock" acá pausaría un producto que
            # probablemente esté perfecto.
            self.fallados.add(p.id)
            return {"accion": "no_leido", "id": p.id, "nombre": nombre,
                    "bloqueado": bool(d.get("bloqueado")),
                    "detalle": d.get("mensaje") or "no se pudo leer la ficha",
                    **self.estado()}

        antes = p.precio_usd
        p = self.cat.marcar_revisado(p.id, precio_nuevo, disponible)
        fila = {"id": p.id, "nombre": nombre,
                "precio_antes": antes, "precio_ahora": precio_nuevo,
                "subio": bool(precio_nuevo and antes and precio_nuevo > antes),
                "pausado": False, "en_perdida": False}

        # Sin stock: se pausa. Es lo único que este agente cambia en
        # MercadoLibre, y va en la dirección segura — no se puede vender lo que
        # no se puede comprar. Se pausa, no se borra: vuelve el stock y se
        # reactiva conservando antigüedad y visitas.
        if disponible is False:
            if p.estado != "pausado":
                try:
                    self._pausar(p)
                    fila["pausado"] = True
                except Exception as e:  # noqa: BLE001
                    self.revisados.append(fila)
                    return {"accion": "error", "id": p.id, "nombre": nombre,
                            "detalle": f"sin stock, pero no se pudo pausar: {e}",
                            **self.estado()}
            self.revisados.append(fila)
            return {"accion": "pausado", "id": p.id, "nombre": nombre,
                    "detalle": "Amazon se quedó sin stock: publicación pausada",
                    **self.estado()}

        # Con stock: lo que importa es si el precio YA PUBLICADO sigue dejando
        # ganancia con el costo de hoy.
        fila["en_perdida"] = p.margen_pct < self.margen_minimo
        self.revisados.append(fila)
        if fila["en_perdida"]:
            return {"accion": "margen_bajo", "id": p.id, "nombre": nombre,
                    "detalle": (f"margen {p.margen_pct:.0f}% al precio publicado"
                                + (f" (Amazon pasó de US${antes:.2f} a "
                                   f"US${precio_nuevo:.2f})"
                                   if fila["subio"] else "")),
                    **self.estado()}
        return {"accion": REVISAR, "id": p.id, "nombre": nombre,
                "detalle": ("sin cambios" if not fila["subio"]
                            else f"Amazon subió a US${precio_nuevo:.2f}, "
                                 f"margen {p.margen_pct:.0f}%"),
                **self.estado()}
