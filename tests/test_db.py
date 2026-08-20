"""Tests del adaptador de base de datos (SQLite / PostgreSQL).

La traducción de SQL a Postgres se verificó contra un PostgreSQL 16 real; acá
se cubre el comportamiento sobre SQLite y las reglas de traducción, que es lo
que se puede probar sin depender de un servidor externo.
"""

import os

import pytest

from db import Conexion, conectar, describir


def test_sqlite_por_defecto(tmp_path):
    c = conectar(str(tmp_path / "t.db"))
    assert c.postgres is False
    assert "SQLite" in describir(c)


def test_database_url_tiene_prioridad(monkeypatch, tmp_path):
    """Si el entorno define DATABASE_URL, gana sobre la ruta local (es lo que
    hace que en la nube se use el Postgres persistente)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    c = Conexion(str(tmp_path / "t.db"))
    assert c.postgres is True
    assert c.url == "postgresql://x/y"


def test_conexion_postgres_es_perezosa(monkeypatch, tmp_path):
    """Construir la conexión NO debe conectar: si la base está dormida (Neon
    'scale to zero'), la app tiene que arrancar igual."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nadie@127.0.0.1:1/nada")
    c = Conexion()                      # no explota
    assert c._conn is None
    # Recién al usarla falla, y con un error de conexión reconocible.
    with pytest.raises(Exception) as exc:
        c.execute("SELECT 1")
    assert Conexion._es_error_de_conexion(exc.value)


def test_executescript_tolera_base_caida(monkeypatch):
    """El esquema queda registrado para aplicarse al reconectar, sin tumbar
    el arranque de la app."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nadie@127.0.0.1:1/nada")
    c = Conexion()
    c.executescript("CREATE TABLE IF NOT EXISTS x (id INTEGER PRIMARY KEY AUTOINCREMENT);")
    assert len(c._esquema) == 1  # registrado, se aplicará al conectar


def test_traduccion_de_sql_a_postgres():
    """La traducción se hace sin conectarse: se verifica sobre el método."""
    c = conectar(":memory:")
    c.postgres = True  # simulamos el dialecto solo para traducir
    sql = c._sql("INSERT INTO t (a, b) VALUES (?, datetime('now'))")
    assert "%s" in sql and "?" not in sql
    assert "now()" in sql and "datetime('now')" not in sql
    ddl = c._sql("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    assert "SERIAL PRIMARY KEY" in ddl and "AUTOINCREMENT" not in ddl


def test_insertar_devuelve_id_y_columnas(tmp_path):
    c = conectar(str(tmp_path / "t.db"))
    c.executescript("""
        CREATE TABLE IF NOT EXISTS demo (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT
        );
    """)
    c.commit()
    nid = c.insertar("INSERT INTO demo (nombre) VALUES (?)", ("uno",))
    c.commit()
    assert nid == 1
    fila = c.execute("SELECT * FROM demo WHERE id = ?", (nid,)).fetchone()
    assert fila["nombre"] == "uno"
    assert set(c.columnas("demo")) == {"id", "nombre"}


def test_los_datos_persisten_entre_conexiones(tmp_path):
    """Lo que da la persistencia: reabrir la base y encontrar los datos."""
    ruta = str(tmp_path / "t.db")
    c1 = conectar(ruta)
    c1.executescript("CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT);")
    c1.insertar("INSERT INTO demo (nombre) VALUES (?)", ("persistente",))
    c1.commit()
    c1.close()

    c2 = conectar(ruta)  # "reinicio"
    assert c2.execute("SELECT nombre FROM demo").fetchone()["nombre"] == "persistente"


def test_preparar_no_toca_la_base_hasta_que_haya_conexion(monkeypatch):
    """El arranque no debe depender de que la base esté despierta.

    Con Neon (scale to zero) la primera conexión tarda; si el esquema se
    aplicaba al construir la app, el proceso se colgaba y Render devolvía 502.
    """
    from db import Conexion

    c = Conexion.__new__(Conexion)          # sin abrir nada
    c.url, c.postgres = "postgresql://x/y", True
    c._lock = __import__("threading").RLock()
    c._conn, c._esquema, c._migraciones, c._aplicando = None, [], [], False

    llamadas = []
    c._abrir = lambda: llamadas.append("abrir")

    c.preparar("CREATE TABLE IF NOT EXISTS demo (id INTEGER);",
               migracion=lambda conn: llamadas.append("migrar"))

    assert llamadas == []                    # ni conectó ni migró
    assert len(c._esquema) == 1 and len(c._migraciones) == 1


def test_el_esquema_y_la_migracion_se_aplican_al_conectar(tmp_path):
    """Lo registrado se aplica en cuanto hay conexión, incluida la migración."""
    ruta = str(tmp_path / "t.db")
    c = conectar(ruta)
    c.executescript("CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY AUTOINCREMENT);")

    def migracion(conn):
        if "nombre" not in conn.columnas("demo"):
            conn.execute("ALTER TABLE demo ADD COLUMN nombre TEXT")

    c.preparar("CREATE TABLE IF NOT EXISTS demo2 (id INTEGER);", migracion=migracion)
    assert "nombre" in c.columnas("demo")
    assert c.execute("SELECT * FROM demo2").fetchall() == []


def test_una_migracion_que_falla_no_tumba_la_conexion(tmp_path):
    c = conectar(str(tmp_path / "t.db"))

    def rota(conn):
        raise RuntimeError("boom")

    c.preparar("CREATE TABLE IF NOT EXISTS demo (id INTEGER);", migracion=rota)
    # La app sigue funcionando: la tabla está y se puede escribir.
    c.execute("INSERT INTO demo (id) VALUES (?)", (1,))
    c.commit()
    assert c.execute("SELECT id FROM demo").fetchone()["id"] == 1
