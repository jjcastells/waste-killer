import streamlit as st
import pandas as pd
import numpy as np
import re
import unicodedata
from io import BytesIO
from datetime import datetime

# =====================
# Configuración página
# =====================
st.set_page_config(page_title="BidForest Hygiene — Keyword Pauser", page_icon="🧹", layout="wide")
st.title("🧹 BidForest Hygiene — Pausar Keywords que dañan el ACOS")

st.caption(
    "Lead magnet mini-tool: subes bulksheet/performance → (opcional) filtras → detecto keywords/targets para pausar "
    "por ACOS extremo o por cero ventas con muchos clics → exporto bulksheet con pausas."
)

# =====================
# Defaults
# =====================
DEFAULT_ACOS_MULTIPLIER = 4.0     # conservador
DEFAULT_NO_SALES_CLICKS = 50      # clásico
DEFAULT_MIN_CLICKS_FOR_ACOS_RULE = 10  # evita ruido en ACOS alto con pocos clics

# =====================
# Helpers
# =====================
def strip_accents(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", str(s))
        if not unicodedata.combining(ch)
    )

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def _clean(x):
        x = str(x)
        x = x.replace("\ufeff", "")   # BOM
        x = x.replace("\u200b", "")   # zero-width
        x = x.replace("\xa0", " ")    # NBSP
        x = re.sub(r"\s+", " ", x)    # colapsa espacios
        return x.strip()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join([str(c) for c in col if str(c) != "nan"]).strip() for col in df.columns]

    df.columns = [_clean(c) for c in df.columns]
    return df

def normalize_id_series(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    s = s.replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})
    s = s.str.replace(r"\.0$", "", regex=True)  # 12345.0 -> 12345
    s = s.str.replace(r"[^\d]", "", regex=True) # solo dígitos
    return s

def to_float_euaware(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    if (s.str.contains(",").mean() > 0.5):
        s = s.str.replace(r"[^\d,.\-]", "", regex=True)
        s = s.str.replace(".", "", regex=False)
        s = s.str.replace(",", ".", regex=False)
    else:
        s = s.str.replace(r"[^\d.\-]", "", regex=True)
        s = s.str.replace(",", "", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def find_col(df: pd.DataFrame, options):
    if isinstance(options, str):
        options = [options]

    def norm(x: str) -> str:
        x = str(x)
        x = x.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
        x = re.sub(r"\s+", " ", x).strip().lower()
        x = strip_accents(x)
        return x

    cols = list(df.columns)
    cols_norm = {norm(c): c for c in cols}

    for opt in options:
        optn = norm(opt)
        if optn in cols_norm:
            return cols_norm[optn]
        for cn, original in cols_norm.items():
            if cn.startswith(optn + "."):
                return original
    return None

def find_col_contains(df: pd.DataFrame, needles: list[str]):
    def norm(x: str) -> str:
        x = str(x)
        x = x.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
        x = re.sub(r"\s+", " ", x).strip().lower()
        x = strip_accents(x)
        return x

    needles_n = [norm(n) for n in needles]
    for c in df.columns:
        cn = norm(c)
        if any(n in cn for n in needles_n):
            return c
    return None

def sanitize_sheet_name(name: str) -> str:
    cleaned = re.sub(r'[:\\/?*\[\]]', '-', name)
    cleaned = cleaned.strip() or 'HYGIENE'
    return cleaned[:31]

def canon_entity_value(v: str) -> str:
    s = str(v).strip().lower()
    s = strip_accents(s)
    s = re.sub(r"\s+", " ", s)

    mapping = {
        "keyword": "keyword",
        "palabra clave": "keyword",
        "palabras clave": "keyword",

        "product targeting": "product targeting",
        "segmentacion por productos": "product targeting",
        "segmentacion de productos": "product targeting",
        "segmentacion por producto": "product targeting",
        "segmentacion": "product targeting",
    }
    return mapping.get(s, s)

def canon_product_value(v: str) -> str:
    s = str(v).strip().lower()
    s = strip_accents(s)
    s = re.sub(r"\s+", " ", s)

    mapping = {
        "sponsored products": "sponsored products",
        "productos patrocinados": "sponsored products",

        "sponsored brands": "sponsored brands",
        "marcas patrocinadas": "sponsored brands",

        "sponsored display": "sponsored display",
        "display patrocinado": "sponsored display",
    }
    return mapping.get(s, s)

@st.cache_data(show_spinner=False)
def load_file(file):
    if file is None:
        return None
    name = file.name.lower()
    if name.endswith(".xlsx"):
        xls = pd.ExcelFile(file)
        return {s: pd.read_excel(xls, sheet_name=s, dtype=str) for s in xls.sheet_names}
    elif name.endswith(".csv"):
        try:
            return pd.read_csv(file, dtype=str)
        except Exception:
            file.seek(0)
            return pd.read_csv(file, sep=";", dtype=str)
    return None

def is_target_row(row) -> bool:
    ent = canon_entity_value(row.get("Entity", ""))
    if ent in ("keyword", "product targeting"):
        return True
    rt = canon_entity_value(row.get("Record Type", ""))
    if rt in ("keyword", "product targeting"):
        return True
    return bool(str(row.get("Texto de palabra clave", "")).strip())

def id_col_for_row(base_row: pd.Series, df_all_cols: pd.DataFrame):
    ent = canon_entity_value(base_row.get("Entity", ""))
    if ent == "keyword":
        if "Keyword ID" in df_all_cols.columns and str(base_row.get("Keyword ID", "")).strip() != "":
            return "Keyword ID"
    if ent == "product targeting":
        for cand in ["Product Targeting ID", "Targeting ID", "Target ID"]:
            if cand in df_all_cols.columns and str(base_row.get(cand, "")).strip() != "":
                return cand
    for cand in ["Keyword ID", "Product Targeting ID", "Targeting ID", "Target ID"]:
        if cand in df_all_cols.columns and str(base_row.get(cand, "")).strip() != "":
            return cand
    return None

# =====================
# Upload
# =====================
uploaded_file = st.file_uploader("📤 Sube tu archivo (Bulksheet Excel o CSV rendimiento)", type=["xlsx", "csv"])
if uploaded_file is None:
    st.stop()

loaded = load_file(uploaded_file)

selected_sheet = None
if isinstance(loaded, dict):
    sheets = list(loaded.keys())
    default_idx = 1 if len(sheets) > 1 else 0
    selected_sheet = st.selectbox("📑 Selecciona hoja (Excel)", options=sheets, index=default_idx)
    df = loaded[selected_sheet]
else:
    df = loaded

if df is None or df.empty:
    st.error("No se pudo leer el archivo o está vacío.")
    st.stop()

df = clean_columns(df)

# =====================
# Mapeos de columnas (performance + bulksheet)
# =====================
# performance → canónico
col_kw      = find_col(df, ["Texto de palabra clave", "Palabra clave", "Segmentación"])
col_spend   = find_col(df, ["Gasto(EUR)", "Inversión (convertido)", "Inversion (convertido)", "Inversión", "Inversion", "Spend"])
col_sales   = find_col(df, ["Ventas(EUR)", "Ventas (convertido)", "Ventas", "Sales"])
col_clicks  = find_col(df, ["Clics", "Clicks"])
col_orders  = find_col(df, ["Pedidos", "Orders"])
col_acos    = find_col(df, ["ACOS", "Acos"])

rename_map = {}
if col_kw:     rename_map[col_kw]     = "Texto de palabra clave"
if col_spend:  rename_map[col_spend]  = "Spend"
if col_sales:  rename_map[col_sales]  = "Sales"
if col_clicks: rename_map[col_clicks] = "Clicks"
if col_orders: rename_map[col_orders] = "Orders"
if col_acos:   rename_map[col_acos]   = "ACOS"
if rename_map:
    df = df.rename(columns=rename_map)

# bulksheet → canónico
BULK_CANON = {
    "Product": ["Product", "Producto"],
    "Entity": ["Entity", "Entidad"],
    "Operation": ["Operation", "Operación"],
    "State": ["State", "Estado"],

    "Campaign ID": ["Campaign ID", "Campaign Id", "CampaignId", "ID de campaña", "Id de campaña", "ID campaña", "ID de la campaña"],
    "Ad Group ID": ["Ad Group ID", "Ad group ID", "Ad Group Id", "AdGroup ID", "Adgroup Id", "ID del grupo de anuncios", "ID de grupo de anuncios", "ID grupo de anuncios"],

    "Keyword ID": ["Keyword ID", "Keyword Id", "KeywordId", "ID de palabra clave", "ID palabra clave"],
    "Product Targeting ID": ["Product Targeting ID", "Product Targeting Id", "ProductTargeting ID", "Targeting ID", "Target ID",
                             "ID de segmentación por productos", "ID de segmentación de producto", "ID de segmentación", "ID objetivo de producto"],

    "Campaign Name": ["Campaign Name", "Nombre de campaña", "Nombre de la campaña (Solo informativo)", "Campaña", "Campaign"],
    "Ad Group Name": ["Ad Group Name", "Nombre del grupo de anuncios (Solo informativo)", "Nombre del grupo de anuncios", "Grupo de anuncios", "Ad Group"],
}

bulk_rename = {}
for canon, opts in BULK_CANON.items():
    found = find_col(df, opts)
    if found and found != canon:
        bulk_rename[found] = canon
if bulk_rename:
    df = df.rename(columns=bulk_rename)

# Fallback final por "contains"
if "Spend" not in df.columns:
    maybe = find_col_contains(df, ["inversion", "gasto", "spend"])
    if maybe:
        df = df.rename(columns={maybe: "Spend"})
if "Sales" not in df.columns:
    maybe = find_col_contains(df, ["ventas", "sales"])
    if maybe:
        df = df.rename(columns={maybe: "Sales"})

# Normalizar valores clave
if "Entity" in df.columns:
    df["Entity"] = df["Entity"].apply(canon_entity_value)
if "Product" in df.columns:
    df["Product"] = df["Product"].apply(canon_product_value)

# Forward fill jerarquía (para excels con celdas vacías abajo)
fill_cols = ["Campaign Name", "Campaign ID", "Ad Group Name", "Ad Group ID"]
for fc in fill_cols:
    if fc in df.columns:
        df[fc] = df[fc].replace("", np.nan).ffill()

# IDs texto
ID_COLS = ["Campaign ID", "Ad Group ID", "Keyword ID", "Product Targeting ID"]
for c in ID_COLS:
    if c in df.columns:
        df[c] = normalize_id_series(df[c])

# Parse numérico métricas
for c in ["Spend", "Sales"]:
    if c in df.columns:
        df[c] = to_float_euaware(df[c])
for c in ["Clicks", "Orders", "ACOS"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

# Canon de texto
if "Texto de palabra clave" in df.columns:
    df["Keyword/Target"] = df["Texto de palabra clave"].astype(str)

# =====================
# Vista previa
# =====================
st.subheader("📄 Vista previa del archivo")
st.dataframe(df.head(50), use_container_width=True)

# =====================
# Filtro (Opcional)
# =====================
st.divider()
st.subheader("🔍 Filtrar datos (Opcional)")

if "filter_active" not in st.session_state:
    st.session_state.filter_active = False
if "filter_query" not in st.session_state:
    st.session_state.filter_query = ""
if "filter_type" not in st.session_state:
    st.session_state.filter_type = "Nombre de campaña"

with st.form("filter_form", clear_on_submit=False):
    f_col1, f_col2 = st.columns([1, 2])
    with f_col1:
        f_type = st.radio(
            "Filtrar por:",
            ["Nombre de campaña", "Nombre de grupo de anuncios", "Keyword/ASIN"],
            index=0,
            horizontal=True,
            key="filter_type",
        )
    with f_col2:
        f_query = st.text_input(
            "Texto a buscar (deja en blanco para no filtrar):",
            key="filter_query",
        )
    submitted_filter = st.form_submit_button("Filtrar")

if submitted_filter:
    st.session_state.filter_active = True

df_global = df.copy()
df_filtered = df.copy()

if st.session_state.filter_active and st.session_state.filter_query.strip():
    query_str = st.session_state.filter_query.strip()
    ft = st.session_state.filter_type

    if ft == "Nombre de campaña":
        col_camp = find_col(df_filtered, ["Campaign Name", "Nombre de campaña", "Campaña", "Campaign"])
        if col_camp and col_camp in df_filtered.columns:
            df_filtered = df_filtered[df_filtered[col_camp].astype(str).str.contains(query_str, case=False, na=False, regex=False)]
        else:
            st.warning("⚠️ No encuentro columna de Nombre de Campaña para filtrar.")

    elif ft == "Nombre de grupo de anuncios":
        col_adg = find_col(df_filtered, ["Ad Group Name", "Nombre del grupo de anuncios", "Grupo de anuncios", "Ad Group"])
        if not col_adg:
            col_adg = find_col(df_filtered, ["Ad Group ID", "ID del grupo de anuncios"])
        if col_adg and col_adg in df_filtered.columns:
            df_filtered = df_filtered[df_filtered[col_adg].astype(str).str.contains(query_str, case=False, na=False, regex=False)]
        else:
            st.warning("⚠️ No encuentro columna de Grupo de Anuncios para filtrar.")

    elif ft == "Keyword/ASIN":
        cols_to_search = []
        if "Keyword/Target" in df_filtered.columns:
            cols_to_search.append("Keyword/Target")
        if cols_to_search:
            mask = pd.Series(False, index=df_filtered.index)
            for c in cols_to_search:
                mask |= df_filtered[c].astype(str).str.contains(query_str, case=False, na=False, regex=False)
            df_filtered = df_filtered[mask]
        else:
            st.warning("⚠️ No encuentro columna Keyword/Target para filtrar.")

    st.success(f"Filtro aplicado: **{ft}** contiene '**{query_str}**'.")

df = df_filtered

# =====================
# Controles de higiene
# =====================
st.divider()
st.subheader("🧼 Reglas de Higiene (inputs)")

r1, r2, r3 = st.columns(3)
with r1:
    acos_multiplier = st.number_input(
        "Multiplicador de ACOS (pausa si ACOS ≥ X × ACOS_ref)",
        min_value=1.0, max_value=50.0,
        value=float(DEFAULT_ACOS_MULTIPLIER),
        step=0.5
    )
with r2:
    no_sales_clicks = st.number_input(
        "Clicks mínimos sin ventas (pausar)",
        min_value=1, max_value=10000,
        value=int(DEFAULT_NO_SALES_CLICKS),
        step=5
    )
with r3:
    min_clicks_acos_rule = st.number_input(
        "Clicks mínimos para aplicar regla ACOS alto",
        min_value=0, max_value=10000,
        value=int(DEFAULT_MIN_CLICKS_FOR_ACOS_RULE),
        step=5
    )

ref_scope = st.radio(
    "Referencia para ACOS medio (ACOS_ref):",
    ["Sponsored Products (recomendado)", "Toda la hoja (filtrada)"],
    index=0,
    horizontal=True
)

with st.form("run_hygiene_form", clear_on_submit=False):
    run_clicked = st.form_submit_button("🚀 Generar pausas", use_container_width=True)

if not run_clicked:
    st.stop()

# =====================
# Validaciones mínimas
# =====================
if not {"Spend", "Sales"}.issubset(df.columns):
    st.error("No encuentro Spend/Sales en la selección. Necesito Gasto y Ventas.")
    st.info("Columnas actuales: " + " | ".join(df.columns.astype(str).tolist()))
    st.stop()

# Trabajamos solo con targets (keyword / product targeting)
df_targets = df[df.apply(is_target_row, axis=1)].copy()
if df_targets.empty:
    st.error("No detecto filas Keyword/Product Targeting en la selección.")
    st.stop()

# ACOS por fila (derivado si falta o si es 0)
acos_row = np.where(df_targets["Sales"] > 0, df_targets["Spend"] / df_targets["Sales"], np.nan)
if "ACOS" not in df_targets.columns:
    df_targets["ACOS"] = acos_row
else:
    acos_existing = pd.to_numeric(df_targets["ACOS"], errors="coerce")
    fill_mask = acos_existing.isna() | (acos_existing <= 0)
    df_targets.loc[fill_mask, "ACOS"] = acos_row[fill_mask]

df_targets["Clicks"] = pd.to_numeric(df_targets.get("Clicks", 0), errors="coerce").fillna(0.0)
df_targets["Orders"] = pd.to_numeric(df_targets.get("Orders", 0), errors="coerce").fillna(0.0)

# =====================
# ACOS_ref (media ponderada por ventas / equivalente a Spend/Sales)
# =====================
if ref_scope.startswith("Sponsored Products") and "Product" in df_targets.columns:
    ref_df = df_targets[df_targets["Product"].astype(str).str.lower().eq("sponsored products")].copy()
    if ref_df.empty:
        ref_df = df_targets.copy()
        st.warning("No encuentro filas de Sponsored Products en la selección. Uso toda la selección como referencia.")
else:
    ref_df = df_targets.copy()

ref_spend = float(ref_df["Spend"].sum())
ref_sales = float(ref_df["Sales"].sum())
acos_ref = (ref_spend / ref_sales) if ref_sales > 0 else 0.0

# =====================
# Reglas de pausa
# =====================
# Regla A: sin ventas + clicks >= threshold
rule_no_sales = (df_targets["Orders"] <= 0) & (df_targets["Clicks"] >= float(no_sales_clicks))

# Regla B: ACOS extremadamente alto vs referencia
acos_val = pd.to_numeric(df_targets["ACOS"], errors="coerce")
rule_acos_high = (
    (acos_ref > 0) &
    (acos_val > 0) &
    (df_targets["Clicks"] >= float(min_clicks_acos_rule)) &
    (acos_val >= float(acos_multiplier) * float(acos_ref))
)

df_targets["Pausar"] = rule_no_sales | rule_acos_high

def reason(row):
    reasons = []
    if bool(row.get("Pausar", False)):
        if bool(rule_no_sales.loc[row.name]):
            reasons.append(f"Sin ventas ≥ {int(no_sales_clicks)} clics")
        if bool(rule_acos_high.loc[row.name]):
            reasons.append(f"ACOS ≥ {acos_multiplier:.1f}× ACOS_ref")
    return " · ".join(reasons) if reasons else ""

df_targets["Motivo"] = df_targets.apply(reason, axis=1)

# =====================
# Resultados
# =====================
st.subheader("✅ Sugerencias de Pausa")

m1, m2, m3, m4 = st.columns(4)
m1.metric("ACOS_ref", f"{acos_ref*100:.2f} %" if acos_ref > 0 else "N/A")
m2.metric("Targets analizados", f"{len(df_targets)}")
m3.metric("Pausas sugeridas", f"{int(df_targets['Pausar'].sum())}")
pct_pause = 100.0 * float(df_targets["Pausar"].mean()) if len(df_targets) else 0.0
m4.metric("% Pausa", f"{pct_pause:.1f}%")

cols_show = [
    "Keyword/Target", "Texto de palabra clave",
    "Motivo", "Pausar",
    "Clicks", "Orders", "Spend", "Sales", "ACOS",
    "Product", "Entity", "Campaign Name", "Ad Group Name",
    "Campaign ID", "Ad Group ID", "Keyword ID", "Product Targeting ID"
]
cols_show = [c for c in cols_show if c in df_targets.columns]

st.dataframe(
    df_targets.sort_values(["Pausar", "Spend"], ascending=[False, False])[cols_show],
    use_container_width=True
)

# =====================
# Export bulksheet (solo pausas)
# =====================
st.divider()
st.subheader("💾 Exportar bulksheet (solo pausas)")

required_common = ["Product", "Entity", "Campaign ID", "Ad Group ID"]
if not all(col in df_global.columns for col in required_common):
    st.error(
        "Para exportar un bulksheet de cambios necesito columnas oficiales: "
        "Product, Entity, Campaign ID, Ad Group ID y el ID (Keyword/Product Targeting)."
    )
    st.stop()

allowed_entities = {"keyword", "product targeting"}
rows = []

df_pause = df_targets[df_targets["Pausar"]].copy()
if df_pause.empty:
    st.warning("No hay pausas sugeridas con las reglas actuales.")
    st.stop()

for _, base in df_pause.iterrows():
    ent_l = canon_entity_value(base.get("Entity", ""))
    if ent_l not in allowed_entities:
        continue

    idc = id_col_for_row(base, df_global)
    if idc is None or str(base.get(idc, "")).strip() == "":
        continue

    out = {
        "Product": base.get("Product"),
        "Entity": base.get("Entity"),
        "Operation": "Update",
        "Campaign ID": base.get("Campaign ID"),
        "Ad Group ID": base.get("Ad Group ID"),
        idc: base.get(idc),
        "State": "paused",
    }
    # Campo extra (no molesta si lo dejas, pero Amazon a veces lo ignora):
    # out["Notes"] = base.get("Motivo", "")

    rows.append(out)

if not rows:
    st.error("No he podido construir filas exportables (faltan IDs en las filas a pausar).")
    st.stop()

bulk_out = pd.DataFrame(rows)

# blindaje IDs
for c in ["Campaign ID", "Ad Group ID", "Keyword ID", "Product Targeting ID"]:
    if c in bulk_out.columns:
        bulk_out[c] = normalize_id_series(bulk_out[c])

ordered = ["Product", "Entity", "Operation", "Campaign ID", "Ad Group ID", "Keyword ID", "Product Targeting ID", "State"]
present = [c for c in ordered if c in bulk_out.columns]
bulk_out = bulk_out[present]

sheet_name = sanitize_sheet_name(f"HYGIENE {datetime.now().strftime('%d-%m-%Y')}")
output = BytesIO()
with pd.ExcelWriter(output, engine="openpyxl") as writer:
    bulk_out.to_excel(writer, index=False, sheet_name=sheet_name)

st.dataframe(bulk_out.head(50), use_container_width=True)

st.download_button(
    label="⬇️ Descargar BidForest_Hygiene_Pause.xlsx",
    data=output.getvalue(),
    file_name="BidForest_Hygiene_Pause.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
