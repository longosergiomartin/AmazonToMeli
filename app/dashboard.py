"""
Dashboard web opcional (Streamlit).

Interfaz visual para cargar productos, elegir régimen y ver el ranking de
oportunidades ordenado por margen. Es un extra por encima del CLI: el motor de
cálculo es exactamente el mismo (paquete `arbitraje`).

Requiere: pip install streamlit pandas
Ejecutar:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Permitir importar el paquete cuando se corre desde la raíz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from arbitraje.config import Config
from arbitraje.models import Producto
from arbitraje.evaluador import evaluar_muchos


st.set_page_config(page_title="Arbitraje Amazon → MeLi", layout="wide")
st.title("🛒 Arbitraje Amazon (EEUU) → MercadoLibre Argentina")
st.caption("Detecta productos con buen margen para traer de Amazon y revender en MeLi.")

with st.sidebar:
    st.header("Parámetros")
    tc = st.number_input("Tipo de cambio (ARS/USD)", value=1300.0, step=10.0)
    regimenes = st.multiselect(
        "Régimen(es) de importación", ["courier", "general"], default=["courier"],
    )
    usar_api = st.checkbox("Buscar precios en la API de MercadoLibre", value=False,
                           help="Si está apagado, usá la columna 'precio_meli_manual'.")
    umbral = st.slider("Umbral de 'buena oportunidad' (%)", 0, 100, 30)

cfg = Config(tipo_cambio_oficial=tc, umbral_margen_bueno_pct=float(umbral))

st.subheader("Productos a evaluar")
st.write("Cargá o editá los productos (precio y peso los ves en Amazon):")

df_inicial = pd.DataFrame([
    {"nombre": "Auriculares XYZ 123", "query_meli": "auriculares bluetooth XYZ",
     "precio_amazon_usd": 45.0, "peso_kg": 0.3, "categoria": "electronica",
     "arancel_pct": 0.16, "precio_meli_manual": 150000.0},
])
df = st.data_editor(df_inicial, num_rows="dynamic", use_container_width=True)

if st.button("Evaluar oportunidades", type="primary"):
    productos = []
    for _, fila in df.iterrows():
        if not str(fila.get("nombre", "")).strip():
            continue
        pm = fila.get("precio_meli_manual")
        productos.append(Producto(
            nombre=str(fila["nombre"]),
            query_meli=str(fila.get("query_meli") or fila["nombre"]),
            precio_amazon_usd=float(fila.get("precio_amazon_usd") or 0),
            peso_kg=float(fila.get("peso_kg") or 0.5),
            categoria=str(fila.get("categoria") or "default"),
            arancel_pct=float(fila.get("arancel_pct") or 0.16),
            precio_meli_manual=(float(pm) if pm and not pd.isna(pm) else None),
        ))

    ops = evaluar_muchos(productos, regimenes=regimenes or ["courier"],
                         cfg=cfg, usar_api=usar_api)
    if not ops:
        st.warning("No se pudo evaluar. Cargá 'precio_meli_manual' o activá la API.")
    else:
        st.subheader("Ranking de oportunidades")
        tabla = pd.DataFrame([o.fila_resumen() for o in ops])

        def _color(v):
            return "color: green" if v > 0 else "color: red"

        st.dataframe(
            tabla.style.map(_color, subset=["margen_ars", "margen_pct"]),
            use_container_width=True,
        )
        mejor = ops[0]
        if mejor.margen_ars > 0:
            st.success(f"💡 Mejor oportunidad: **{mejor.producto.nombre}** "
                       f"({mejor.regimen}) — margen ${mejor.margen_ars:,.0f} "
                       f"({mejor.margen_pct:.1f}%)")
