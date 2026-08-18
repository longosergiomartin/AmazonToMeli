"""
Conexión a la base de datos, con SQLite o PostgreSQL.

Por qué existe: en la nube (Render plan gratis) el disco es efímero — cada vez
que la app se duerme o se redeploya se pierde el archivo SQLite, y con él la
sesión de MercadoLibre, el catálogo y el historial. Configurando `DATABASE_URL`
con un Postgres externo (Neon, Supabase, Render Postgres), los datos sobreviven
y la conexión con MercadoLibre queda enganchada para siempre.

Uso:
    conn = conectar()                      # usa DATABASE_URL si está, si no SQLite
    conn = conectar("data/arbitraje.db")   # SQLite explícito

El resto del código sigue escribiendo SQL con `?` y nombres de columna; este
módulo traduce lo específico de cada motor.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional, Sequence


def _es_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


class _Cursor:
    """Cursor con la misma interfaz mínima que usa la app."""

    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class Conexion:
    """Envuelve SQLite o Postgres con una interfaz común.

    Las diferencias que traduce:
      - marcadores de parámetros: `?` (SQLite) vs `%s` (Postgres)
      - autoincremental: `INTEGER PRIMARY KEY AUTOINCREMENT` vs `SERIAL`
      - fecha actual: `datetime('now')` vs `now()`
      - listado de columnas: `PRAGMA table_info` vs `information_schema`
    """

    def __init__(self, url: Optional[str] = None):
        # DATABASE_URL tiene prioridad: si está configurada (deploy en la nube),
        # manda sobre la ruta local que pase el código.
        url = (os.environ.get("DATABASE_URL") or url or "data/arbitraje.db").strip()
        self.postgres = _es_postgres(url)
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        else:
            ruta = Path(url)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(ruta), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    # ---- traducción de SQL ----------------------------------------------

    def _sql(self, sql: str) -> str:
        if not self.postgres:
            return sql
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = sql.replace("datetime('now')", "now()")
        # Los parámetros van como %s en Postgres. Nuestro SQL no usa '?' dentro
        # de literales, así que la sustitución directa es segura.
        sql = sql.replace("?", "%s")
        # `%` literales (no hay hoy) romperían el formateo; se escapan aparte.
        return sql

    # ---- API que usa la app ---------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        sql_t = self._sql(sql)
        if self.postgres:
            cur = self._conn.cursor()
            cur.execute(sql_t, tuple(params))
            return _Cursor(cur)
        return _Cursor(self._conn.execute(sql_t, tuple(params)))

    def insertar(self, sql: str, params: Sequence[Any] = (),
                 columna_id: str = "id") -> int:
        """INSERT que devuelve el id generado (equivalente a lastrowid)."""
        if self.postgres:
            cur = self._conn.cursor()
            cur.execute(self._sql(sql) + f" RETURNING {columna_id}", tuple(params))
            fila = cur.fetchone()
            return fila[columna_id] if fila else 0
        cur = self._conn.execute(sql, tuple(params))
        return cur.lastrowid

    def executescript(self, script: str) -> None:
        if self.postgres:
            cur = self._conn.cursor()
            for sentencia in script.split(";"):
                if sentencia.strip():
                    cur.execute(self._sql(sentencia))
            return
        self._conn.executescript(script)

    def columnas(self, tabla: str) -> list[str]:
        """Nombres de columna de una tabla (para migraciones)."""
        if self.postgres:
            cur = self._conn.cursor()
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = %s", (tabla,))
            return [r["column_name"] for r in cur.fetchall()]
        return [r[1] for r in self._conn.execute(f"PRAGMA table_info({tabla})").fetchall()]

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @property
    def row_factory(self):  # compatibilidad: la app a veces lo setea
        return getattr(self._conn, "row_factory", None)

    @row_factory.setter
    def row_factory(self, valor):
        if not self.postgres:
            self._conn.row_factory = valor


def conectar(url: Optional[str] = None) -> Conexion:
    return Conexion(url)


def describir(conn: Conexion) -> str:
    """Texto corto para mostrar en el panel qué almacenamiento se está usando."""
    return ("PostgreSQL (los datos sobreviven a los reinicios)" if conn.postgres
            else "SQLite local (en la nube gratis se borra al reiniciar)")
