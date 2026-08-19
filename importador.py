"""
Cola de importación por lote: carga muchos productos de Amazon sin trabajo
repetitivo.

Cómo se usa (y por qué así):
  1. El usuario navega Amazon y busca lo que le interesa ("LEGO Star Wars").
  2. Con un botón captura los ASIN de la página que ya tiene abierta en su
     navegador, y quedan encolados acá.
  3. La cola los procesa **de a uno y despacio**, autocompletando lo que se
     puede leer de cada ficha, y deja cada producto como BORRADOR para que el
     usuario lo revise y apruebe.

Regla importante: si Amazon empieza a limitar o pide verificación, la cola
**se detiene y guarda el progreso**. No se insiste ni se intenta esquivar el
bloqueo: se continúa otro día con `reactivar_bloqueados()`. Frenar ante un
límite es lo correcto y además protege la cuenta de comprador.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from amazon_import import extraer_asin, importar_desde_url
from catalogo import Catalogo, ProductoCatalogo

# Estados de cada ítem de la cola.
PENDIENTE = "pendiente"
LISTO = "listo"
ERROR = "error"
BLOQUEADO = "bloqueado"   # Amazon nos limitó: reintentar más tarde


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ColaImportacion:
    def __init__(self, conn, cat: Catalogo):
        self.conn = conn
        self.cat = cat
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS cola_importacion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asin TEXT, url TEXT,
                estado TEXT NOT NULL, mensaje TEXT,
                creado TEXT NOT NULL, procesado TEXT
            );
        """)
        self.conn.commit()

    # ---- encolar ---------------------------------------------------------

    def _ya_conocido(self, asin: str) -> bool:
        """Evita duplicados: ni en el catálogo ni pendiente en la cola."""
        if not asin:
            return False
        en_catalogo = self.conn.execute(
            "SELECT 1 FROM catalogo WHERE asin = ?", (asin,)).fetchone()
        if en_catalogo:
            return True
        en_cola = self.conn.execute(
            "SELECT 1 FROM cola_importacion WHERE asin = ? AND estado <> ?",
            (asin, ERROR)).fetchone()
        return bool(en_cola)

    def encolar(self, entradas: list[str]) -> dict:
        """Encola links de Amazon o ASIN sueltos. Devuelve cuántos entraron."""
        nuevos = duplicados = invalidos = 0
        for entrada in entradas:
            entrada = (entrada or "").strip()
            if not entrada:
                continue
            if entrada.startswith("http"):
                asin, url = extraer_asin(entrada), entrada
            else:
                asin = entrada.upper()
                url = f"https://www.amazon.com/dp/{asin}"
            if not asin or len(asin) != 10:
                invalidos += 1
                continue
            if self._ya_conocido(asin):
                duplicados += 1
                continue
            self.conn.execute(
                """INSERT INTO cola_importacion (asin, url, estado, mensaje, creado)
                   VALUES (?, ?, ?, '', ?)""", (asin, url, PENDIENTE, _ahora()))
            nuevos += 1
        self.conn.commit()
        return {"nuevos": nuevos, "duplicados": duplicados,
                "invalidos": invalidos, **self.estado()}

    # ---- procesar --------------------------------------------------------

    def _marcar(self, item_id: int, estado: str, mensaje: str) -> None:
        self.conn.execute(
            "UPDATE cola_importacion SET estado = ?, mensaje = ?, procesado = ? WHERE id = ?",
            (estado, mensaje[:300], _ahora(), item_id))
        self.conn.commit()

    def _crear_producto(self, datos: dict) -> ProductoCatalogo:
        """Arma el borrador con todo lo que se pudo leer de la ficha."""
        p = ProductoCatalogo(
            amazon_link=datos.get("amazon_link", ""),
            asin=datos.get("asin", ""),
            marca=datos.get("marca", ""),
            modelo=datos.get("modelo", "") or datos.get("asin", ""),
            precio_usd=float(datos.get("precio_usd") or 0.0),
            peso_kg=float(datos.get("peso_kg") or 0.5),
            regimen="landed",          # el envío+importación se estima con el %
            titulo_ml=(datos.get("modelo") or "")[:60],
            descripcion=datos.get("descripcion", ""),
            pictures=list(datos.get("imagenes") or []),
        )
        return self.cat.agregar(p)

    def procesar_uno(self, importador: Callable[[str], dict] = importar_desde_url) -> dict:
        """Procesa el próximo pendiente. Devuelve el resultado y si hay que
        frenar (`detener`) porque Amazon nos está limitando."""
        fila = self.conn.execute(
            "SELECT * FROM cola_importacion WHERE estado = ? ORDER BY id LIMIT 1",
            (PENDIENTE,)).fetchone()
        if not fila:
            return {"hecho": False, "detener": True, "motivo": "vacia", **self.estado()}

        item_id, url, asin = fila["id"], fila["url"], fila["asin"]
        datos = importador(url)

        if datos.get("bloqueado"):
            # Frenamos: el ítem queda para retomar más tarde, no se pierde.
            self._marcar(item_id, BLOQUEADO, datos.get("mensaje", "Amazon nos limitó"))
            return {"hecho": False, "detener": True, "motivo": "bloqueado",
                    "asin": asin, "mensaje": datos.get("mensaje", ""), **self.estado()}

        if not datos.get("precio_usd"):
            # Sin precio no sirve para calcular margen: queda como error para
            # revisar a mano, pero la cola sigue con el resto.
            self._marcar(item_id, ERROR, datos.get("mensaje", "Sin precio"))
            return {"hecho": False, "detener": False, "motivo": "sin_precio",
                    "asin": asin, "mensaje": datos.get("mensaje", ""), **self.estado()}

        p = self._crear_producto(datos)
        self._marcar(item_id, LISTO, f"Cargado como borrador #{p.id}")
        return {"hecho": True, "detener": False, "motivo": "ok", "asin": asin,
                "producto_id": p.id, "titulo": p.modelo, **self.estado()}

    def procesar_lote(self, maximo: int = 5, pausa_seg: float = 2.0,
                      importador: Callable[[str], dict] = importar_desde_url,
                      dormir: Callable[[float], None] = time.sleep) -> dict:
        """Procesa hasta `maximo` productos, con una pausa entre cada uno para
        no golpear el sitio. Corta apenas Amazon nos limita."""
        resultados = []
        detener = False
        motivo = "ok"
        for i in range(max(1, maximo)):
            r = self.procesar_uno(importador=importador)
            resultados.append(r)
            if r["detener"]:
                detener = True
                motivo = r["motivo"]
                break
            if i < maximo - 1:
                dormir(pausa_seg)
        return {"procesados": resultados, "detener": detener, "motivo": motivo,
                **self.estado()}

    # ---- estado ----------------------------------------------------------

    def estado(self) -> dict:
        filas = self.conn.execute(
            "SELECT estado, COUNT(*) AS n FROM cola_importacion GROUP BY estado").fetchall()
        conteo = {f["estado"]: f["n"] for f in filas}
        return {"pendientes": conteo.get(PENDIENTE, 0),
                "listos": conteo.get(LISTO, 0),
                "errores": conteo.get(ERROR, 0),
                "bloqueados": conteo.get(BLOQUEADO, 0)}

    def items(self, limite: int = 30) -> list[dict]:
        filas = self.conn.execute(
            "SELECT asin, estado, mensaje, creado, procesado FROM cola_importacion "
            "ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(f) for f in filas]

    def reactivar_bloqueados(self) -> dict:
        """Vuelve a poner en cola lo que quedó frenado (para continuar otro día)."""
        self.conn.execute(
            "UPDATE cola_importacion SET estado = ?, mensaje = '' WHERE estado = ?",
            (PENDIENTE, BLOQUEADO))
        self.conn.commit()
        return self.estado()

    def limpiar_terminados(self) -> dict:
        self.conn.execute("DELETE FROM cola_importacion WHERE estado IN (?, ?)",
                          (LISTO, ERROR))
        self.conn.commit()
        return self.estado()
