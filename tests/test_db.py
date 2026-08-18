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
    # No conectamos de verdad: solo comprobamos qué motor elegiría.
    with pytest.raises(Exception):
        Conexion(str(tmp_path / "t.db"))  # falla al conectar, no al elegir


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
