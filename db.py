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
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence


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
        self.url = (os.environ.get("DATABASE_URL") or url or "data/arbitraje.db").strip()
        self.postgres = _es_postgres(self.url)
        # Los endpoints sincrónicos de FastAPI corren en un pool de hilos: una
        # única conexión compartida necesita exclusión mutua.
        self._lock = threading.RLock()
        self._conn = None
        # Scripts de creación de tablas (idempotentes). Se reaplican al
        # reconectar, así el esquema queda listo aunque la base estuviera
        # dormida cuando arrancó la app.
        self._esquema: list[str] = []
        # La conexión se abre perezosamente: si la base todavía está
        # despertando (Neon "scale to zero"), la app no muere al arrancar.
        if not self.postgres:
            self._abrir()

    def _abrir(self) -> None:
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            # autocommit evita que un error deje la transacción abortada y
            # haga fallar todas las consultas siguientes.
            self._conn = psycopg.connect(self.url, row_factory=dict_row,
                                         autocommit=True, connect_timeout=15)
        else:
            ruta = Path(self.url)
            ruta.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(ruta), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._aplicar_esquema()

    def _aplicar_esquema(self) -> None:
        """Reaplica los CREATE TABLE IF NOT EXISTS sobre la conexión nueva."""
        for script in self._esquema:
            self._ejecutar_script(self._conn, script)

    def _ejecutar_script(self, conn, script: str) -> None:
        if self.postgres:
            cur = conn.cursor()
            for sentencia in script.split(";"):
                if sentencia.strip():
                    cur.execute(self._sql(sentencia))
        else:
            conn.executescript(script)

    def _vivo(self) -> bool:
        if self._conn is None:
            return False
        return not (self.postgres and getattr(self._conn, "closed", False))

    def _reintentar(self, operacion: Callable[[Any], Any], intentos: int = 3) -> Any:
        """Ejecuta `operacion(conn)` reconectando si la conexión se cayó.

        Es lo que hace que la app sobreviva a que la base se duerma: Neon y
        Render cierran las conexiones inactivas, y sin esto toda petición
        posterior devolvía 500 para siempre.
        """
        with self._lock:
            ultimo = None
            for intento in range(intentos):
                try:
                    if not self._vivo():
                        self._abrir()
                    return operacion(self._conn)
                except Exception as e:  # noqa: BLE001 - reconectamos ante cualquier fallo de conexión
                    ultimo = e
                    if not self._es_error_de_conexion(e):
                        raise
                    try:
                        if self._conn is not None:
                            self._conn.close()
                    except Exception:
                        pass
                    self._conn = None
                    if intento < intentos - 1:
                        time.sleep(1.5 * (intento + 1))  # la base puede estar despertando
            raise ultimo

    @staticmethod
    def _es_error_de_conexion(e: Exception) -> bool:
        nombre = type(e).__name__
        if nombre in ("OperationalError", "InterfaceError", "AdminShutdown",
                      "ConnectionException", "ProgrammingError"):
            texto = str(e).lower()
            # ProgrammingError también aparece al usar una conexión ya cerrada.
            if nombre == "ProgrammingError" and "closed" not in texto:
                return False
            return True
        return False

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
        p = tuple(params)

        def _op(conn):
            if self.postgres:
                cur = conn.cursor()
                cur.execute(sql_t, p)
                return _Cursor(cur)
            return _Cursor(conn.execute(sql_t, p))

        return self._reintentar(_op)

    def insertar(self, sql: str, params: Sequence[Any] = (),
                 columna_id: str = "id") -> int:
        """INSERT que devuelve el id generado (equivalente a lastrowid)."""
        p = tuple(params)

        def _op(conn):
            if self.postgres:
                cur = conn.cursor()
                cur.execute(self._sql(sql) + f" RETURNING {columna_id}", p)
                fila = cur.fetchone()
                return fila[columna_id] if fila else 0
            return conn.execute(sql, p).lastrowid

        return self._reintentar(_op)

    def executescript(self, script: str) -> None:
        """Aplica un script de esquema y lo recuerda para reaplicarlo al
        reconectar. Si la base está caída (Neon dormido), no revienta la app:
        el script queda registrado y se aplica en cuanto haya conexión."""
        with self._lock:
            if script not in self._esquema:
                self._esquema.append(script)
        try:
            self._reintentar(lambda conn: self._ejecutar_script(conn, script))
        except Exception as e:  # noqa: BLE001
            if not self._es_error_de_conexion(e):
                raise

    def columnas(self, tabla: str) -> list[str]:
        """Nombres de columna de una tabla (para migraciones)."""
        def _op(conn):
            if self.postgres:
                cur = conn.cursor()
                cur.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = %s", (tabla,))
                return [r["column_name"] for r in cur.fetchall()]
            return [r[1] for r in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]

        return self._reintentar(_op)

    def commit(self) -> None:
        # En Postgres usamos autocommit, así que no hay nada que confirmar.
        if self.postgres:
            return
        with self._lock:
            if self._vivo():
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    @property
    def row_factory(self):  # compatibilidad con código que lo consulta
        return getattr(self._conn, "row_factory", None)

    @row_factory.setter
    def row_factory(self, valor):
        if not self.postgres and self._conn is not None:
            self._conn.row_factory = valor


def conectar(url: Optional[str] = None) -> Conexion:
    return Conexion(url)


def describir(conn: Conexion) -> str:
    """Texto corto para mostrar en el panel qué almacenamiento se está usando."""
    return ("PostgreSQL (los datos sobreviven a los reinicios)" if conn.postgres
            else "SQLite local (en la nube gratis se borra al reiniciar)")
