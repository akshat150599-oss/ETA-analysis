import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="ETA Analysis • Shipment Level", layout="wide")
st.title("ETA Analysis – Shipment Level")
st.caption("Filter by Stop, Accuracy Buckets, and Shipment Lane. Outputs one row per BILL_OF_LADING.")

# --- File input
st.sidebar.header("1) Load data")
uploaded = st.sidebar.file_uploader("Upload CSV (prediction rows; shipment-level metrics repeated)", type=["csv"]) 

@st.cache_data
def load_df(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    df.columns = [c.strip() for c in df.columns]
    # Normalize likely numeric cols
    for c in ["STOP_NUMBER", "TOTAL_PREDICTIONS"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Accuracy & count columns numeric
    for c in df.columns:
        if c.upper().startswith("ACCURACY_") or c.upper().startswith("COUNT_OF_ACCURATE_PREDICTIONS_"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

if uploaded is None:
    st.info("👆 Upload your CSV to begin. The app assumes **each row is a prediction** and shipment-level fields repeat.")
    st.stop()

df_raw = load_df(uploaded)
cols = {c.upper(): c for c in df_raw.columns}

# Canonical columns
BOL = cols.get("BILL_OF_LADING", "BILL_OF_LADING")
CARRIER_NAME = cols.get("CARRIER_NAME", None)
SHIPMENT_LANE = cols.get("SHIPMENT_LANE", None)
STOP_NUMBER = cols.get("STOP_NUMBER", None)
PING_COVERAGE = cols.get("PING_COVERAGE", None)
TOTAL_PREDICTIONS = cols.get("TOTAL_PREDICTIONS", None)

# Accuracy buckets: map to BOTH count & accuracy columns
BUCKET_KEYS = [30, 45, 60, 90, 120]
count_map = {}
acc_map = {}
for m in BUCKET_KEYS:
    cnt_name = f"COUNT_OF_ACCURATE_PREDICTIONS_{m}_MINS"
    acc_name = f"ACCURACY_{m}_MINS"
    if cnt_name in df_raw.columns:
        count_map[m] = cnt_name
    if acc_name in df_raw.columns:
        acc_map[m] = acc_name

# --- Sidebar filters
st.sidebar.header("2) Filters")

# STOP_NUMBER range selection only (removed single option)
stop_filter = None
if STOP_NUMBER and STOP_NUMBER in df_raw.columns:
    valid_stops = sorted(df_raw[STOP_NUMBER].dropna().unique())
    if len(valid_stops) > 0:
        s_min, s_max = int(min(valid_stops)), int(max(valid_stops))
        lo, hi = st.sidebar.slider("STOP_NUMBER range (inclusive)", min_value=s_min, max_value=s_max, value=(s_min, s_max))
        stop_filter = ("range", (lo, hi))
    else:
        st.sidebar.caption("No numeric STOP_NUMBER values found.")
else:
    st.sidebar.caption("STOP_NUMBER column not found – skipping stop filter.")

# Accuracy bucket multi-select (choose which pairs to show)
available_buckets = [m for m in BUCKET_KEYS if (m in count_map and m in acc_map)]
selected_buckets = st.sidebar.multiselect(
    "Accuracy buckets to show",
    options=available_buckets,
    default=available_buckets,
    format_func=lambda x: f"{x} mins",
)

# Shipment Lane filter
selected_lanes = None
if SHIPMENT_LANE and SHIPMENT_LANE in df_raw.columns:
    lanes = sorted([x for x in df_raw[SHIPMENT_LANE].dropna().unique()])
    if len(lanes) > 0:
        selected_lanes = st.sidebar.multiselect("Shipment lane(s)", options=lanes, default=lanes)

# --- Apply filters to PREDICTION-LEVEL rows (no aggregation yet) (no aggregation yet)
df_f = df_raw.copy()

if stop_filter and STOP_NUMBER in df_f.columns:
    mode, val = stop_filter
    if mode == "Single":
        df_f = df_f[df_f[STOP_NUMBER] == val]
    else:
        lo, hi = val
        df_f = df_f[(df_f[STOP_NUMBER] >= lo) & (df_f[STOP_NUMBER] <= hi)]

if selected_lanes is not None and SHIPMENT_LANE in df_f.columns:
    df_f = df_f[df_f[SHIPMENT_LANE].isin(selected_lanes)]

# If no rows remain, show a friendly message
if df_f.empty:
    st.warning("No rows match the current filters. Adjust filters to see results.")
    st.stop()

# --- Shipment-level output: one row per BOL (take first occurrence)
if BOL in df_f.columns:
    # Coerce to string to avoid mixed-type sort errors
    df_f[BOL] = df_f[BOL].astype(str)
    try:
        df_ship = df_f.sort_values(by=[BOL]).drop_duplicates(subset=[BOL], keep="first")
    except Exception:
        df_ship = df_f.drop_duplicates(subset=[BOL], keep="first")
else:
    st.error("BILL_OF_LADING column not found in the uploaded CSV.")
    st.stop()

# Fixed columns for output (exclude ARRIVAL_WITHIN_APPOINTMENT_WINDOW as requested)
fixed_cols = [c for c in [BOL, CARRIER_NAME, SHIPMENT_LANE, PING_COVERAGE, TOTAL_PREDICTIONS] if c and c in df_ship.columns]

# Dynamic bucket columns: for each selected bucket, show COUNT then ACCURACY next to it
bucket_cols = []
for m in selected_buckets:
    if m in count_map:
        bucket_cols.append(count_map[m])
    if m in acc_map:
        bucket_cols.append(acc_map[m])

show_cols = fixed_cols + bucket_cols

missing = [c for c in show_cols if c not in df_ship.columns]
if missing:
    st.warning(f"Some selected columns are missing and will be skipped: {missing}")
    show_cols = [c for c in show_cols if c in df_ship.columns]

st.subheader("Results")
st.write("Filtered view at **shipment level** (one row per BILL_OF_LADING). Accuracy columns are shown as pairs: **count** then **percentage**.")

out = df_ship[show_cols].copy()

# KPI row
k1, k2, k3 = st.columns(3)
with k1:
    st.metric("Shipments (unique BOL)", value=f"{df_ship[BOL].nunique():,}")
with k2:
    if PING_COVERAGE and PING_COVERAGE in out.columns:
        try:
            st.metric("Avg Ping Coverage", value=f"{pd.to_numeric(out[PING_COVERAGE], errors='coerce').mean():.2f}")
        except Exception:
            st.metric("Avg Ping Coverage", value="—")
    else:
        st.metric("Avg Ping Coverage", value="—")
with k3:
    # IMPORTANT: Total predictions should reflect prediction **rows** after filters
    st.metric("Total Predictions (rows)", value=f"{len(df_f):,}")

st.dataframe(out, use_container_width=True)

# Download
csv_bytes = out.to_csv(index=False).encode("utf-8")
st.download_button(
    label="Download filtered table as CSV",
    data=csv_bytes,
    file_name="eta_shipment_level_filtered.csv",
    mime="text/csv",
)
