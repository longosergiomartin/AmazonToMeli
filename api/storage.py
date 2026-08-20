"""
Almacenamiento de productos capturados + histórico de precios.

Funciona sobre SQLite (local) o PostgreSQL (nube, para que los datos
sobrevivan a los reinicios). Ver `db.conectar`.

Esquema:
  productos(asin PK, titulo, link, categoria, peso_kg, creado)
  precios(id PK, asin, ts, fuente, precio_usd, landed_usd, precio_ars)

`fuente` identifica de dónde salió cada punto de precio: "amazon" (bookmarklet),
"meli" (bookmarklet en MercadoLibre), "canopy" (API externa), "manual".
El histórico nunca se pisa: cada captura agrega una fila nueva con timestamp,
lo que permite ver la evolución del precio de cada producto.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from db import conectar


class Almacen:
    def __init__(self, ruta: str | Path = "data/arbitraje.db"):
        # `ruta` puede ser un archivo SQLite o una URL de Postgres; si está
        # definida DATABASE_URL, esa gana (ver db.conectar).
        self.conn = conectar(str(ruta) if ruta else None)
        self._crear_tablas()

    def _crear_tablas(self) -> None:
        self.conn.preparar("""
            CREATE TABLE IF NOT EXISTS productos (
                asin TEXT PRIMARY KEY,
                titulo TEXT NOT NULL,
                link TEXT,
                categoria TEXT NOT NULL DEFAULT 'default',
                peso_kg REAL NOT NULL DEFAULT 0.5,
                creado TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS precios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asin TEXT NOT NULL,
                ts TEXT NOT NULL,
                fuente TEXT NOT NULL,
                precio_usd REAL,
                landed_usd REAL,
                precio_ars REAL
            );
            CREATE INDEX IF NOT EXISTS idx_precios_asin ON precios(asin, ts);
        """)
        self.conn.commit()

    @staticmethod
    def _ahora() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---- escritura -------------------------------------------------------

    def guardar_producto(self, asin: str, titulo: str, link: Optional[str] = None,
                         categoria: str = "default", peso_kg: float = 0.5) -> None:
        """Alta o actualización de un producto (el título/link se refrescan)."""
        self.conn.execute(
            """INSERT INTO productos (asin, titulo, link, categoria, peso_kg, creado)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(asin) DO UPDATE SET
                 titulo = excluded.titulo,
                 link = COALESCE(excluded.link, productos.link)""",
            (asin, titulo, link, categoria, peso_kg, self._ahora()),
        )
        self.conn.commit()

    def registrar_precio(self, asin: str, fuente: str,
                         precio_usd: Optional[float] = None,
                         landed_usd: Optional[float] = None,
                         precio_ars: Optional[float] = None) -> None:
        self.conn.execute(
            """INSERT INTO precios (asin, ts, fuente, precio_usd, landed_usd, precio_ars)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (asin, self._ahora(), fuente, precio_usd, landed_usd, precio_ars),
        )
        self.conn.commit()

    # ---- lectura ---------------------------------------------------------

    def _ultimo_precio(self, asin: str, campo: str) -> Optional[dict]:
        return self.conn.execute(
            f"""SELECT * FROM precios WHERE asin = ? AND {campo} IS NOT NULL
                ORDER BY ts DESC, id DESC LIMIT 1""",
            (asin,),
        ).fetchone()

    def producto(self, asin: str) -> Optional[dict]:
        """Producto + últimos precios conocidos de cada lado."""
        row = self.conn.execute(
            "SELECT * FROM productos WHERE asin = ?", (asin,)
        ).fetchone()
        if row is None:
            return None
        amazon = self._ultimo_precio(asin, "precio_usd")
        landed = self._ultimo_precio(asin, "landed_usd")
        meli = self._ultimo_precio(asin, "precio_ars")
        return {
            "asin": row["asin"],
            "titulo": row["titulo"],
            "link": row["link"],
            "categoria": row["categoria"],
            "peso_kg": row["peso_kg"],
            "precio_amazon_usd": amazon["precio_usd"] if amazon else None,
            "precio_landed_usd": landed["landed_usd"] if landed else None,
            "precio_meli_ars": meli["precio_ars"] if meli else None,
            "ts_amazon": amazon["ts"] if amazon else None,
            "ts_meli": meli["ts"] if meli else None,
        }

    def buscar(self, q: str) -> list[dict]:
        filas = self.conn.execute(
            "SELECT asin FROM productos WHERE titulo LIKE ? ORDER BY creado DESC",
            (f"%{q}%",),
        ).fetchall()
        return [self.producto(f["asin"]) for f in filas]

    def todos(self) -> list[dict]:
        filas = self.conn.execute(
            "SELECT asin FROM productos ORDER BY creado DESC"
        ).fetchall()
        return [self.producto(f["asin"]) for f in filas]

    def historial(self, asin: str) -> list[dict]:
        filas = self.conn.execute(
            "SELECT ts, fuente, precio_usd, landed_usd, precio_ars FROM precios "
            "WHERE asin = ? ORDER BY ts, id", (asin,),
        ).fetchall()
        return [dict(f) for f in filas]

    # ---- export ----------------------------------------------------------

    def filas_csv(self) -> list[dict]:
        """Filas en el formato que consume el CLI de arbitraje
        (data/productos.example.csv)."""
        filas = []
        for p in self.todos():
            filas.append({
                "nombre": p["titulo"][:120],
                "query_meli": p["titulo"][:60],
                "precio_amazon_usd": p["precio_amazon_usd"] or "",
                "peso_kg": p["peso_kg"],
                "categoria": p["categoria"],
                "arancel_pct": "",
                "precio_meli_manual": p["precio_meli_ars"] or "",
                "precio_landed_usd": p["precio_landed_usd"] or "",
                "cantidad": 1,
                "precio_landed_lote_usd": "",
                "link_amazon": p["link"] or "",
            })
        return filas
