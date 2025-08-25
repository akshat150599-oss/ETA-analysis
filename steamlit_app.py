import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="ETA Analysis • Shipment Level", layout="wide")
st.title("ETA Analysis – Shipment Level")
st.caption("Filter by Stop, Accuracy Buckets, and Shipment Lane. Outputs one row per BILL_OF_LADING.")

# --- File input
st.sidebar.header("1) Load data")
uploaded = st.sidebar.file_uploader("Upload CSV (shipment-level aggregates present)", type=["csv"]) 

# Helper: safe int conversion for STOP_NUMBER, leave non-numeric as NaN
@st.cache_data
def load_df(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    # Normalize column names by stripping whitespace
    df.columns = [c.strip() for c in df.columns]

    # Ensure STOP_NUMBER is numeric if present
    if "STOP_NUMBER" in df.columns:
        df["STOP_NUMBER"] = pd.to_numeric(df["STOP_NUMBER"], errors="coerce")

    # Ensure ACCURACY_* are numeric (0/1) if present
    for col in df.columns:
        if col.upper().startswith("ACCURACY_") or col.upper().startswith("COUNT_OF_ACCURATE_PREDICTIONS_"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df

if uploaded is None:
    st.info("👆 Upload your CSV to begin. The app expects shipment-level aggregates already computed in the file.")
    st.stop()

df_raw = load_df(uploaded)

# --- Identify key columns (case-insensitive matching fallback)
cols = {c.upper(): c for c in df_raw.columns}

# Required / commonly used fields
BOL = cols.get("BILL_OF_LADING", "BILL_OF_LADING")
CARRIER_NAME = cols.get("CARRIER_NAME", None)
SHIPMENT_LANE = cols.get("SHIPMENT_LANE", None)
STOP_NUMBER = cols.get("STOP_NUMBER", None)
PING_COVERAGE = None
ARRIVAL_WITHIN_APPT = None
TOTAL_PREDICTIONS = None

# Try to match common variants
for k in cols:
    if PING_COVERAGE is None and k in ("PING_COVERAGE", "AVG_PING_COVERAGE", "PING_COVERAGE_%"):
        PING_COVERAGE = cols[k]
    if ARRIVAL_WITHIN_APPT is None and (k == "ARRIVAL_WITHIN_APPOINTMENT_WINDOW" or k == "ARRIVAL_WITHIN_APPT_WINDOW" or k == "ARRIVAL_WITHIN_WINDOW"):
        ARRIVAL_WITHIN_APPT = cols[k]
    if TOTAL_PREDICTIONS is None and (k == "TOTAL_PREDICTIONS" or k == "N_PREDICTIONS" or k == "COUNT_PREDICTIONS"):
        TOTAL_PREDICTIONS = cols[k]

# Accuracy bucket columns (support multiple naming styles)
CANDIDATE_BUCKETS = [
    ("ACCURACY_30_MINS", ["ACCURACY_30_MINS", "COUNT_OF_ACCURATE_PREDICTIONS_30_MINS", "COUNT_30", "ACCURATE_30"]),
    ("ACCURACY_45_MINS", ["ACCURACY_45_MINS", "COUNT_OF_ACCURATE_PREDICTIONS_45_MINS", "COUNT_45", "ACCURATE_45"]),
    ("ACCURACY_60_MINS", ["ACCURACY_60_MINS", "COUNT_OF_ACCURATE_PREDICTIONS_60_MINS", "COUNT_60", "ACCURATE_60"]),
    ("ACCURACY_90_MINS", ["ACCURACY_90_MINS", "COUNT_OF_ACCURATE_PREDICTIONS_90_MINS", "COUNT_90", "ACCURATE_90"]),
    ("ACCURACY_120_MINS", ["ACCURACY_120_MINS", "COUNT_OF_ACCURATE_PREDICTIONS_120_MINS", "COUNT_120", "ACCURATE_120"]),
]

bucket_map = {}
for friendly, aliases in CANDIDATE_BUCKETS:
    for alias in aliases:
        if alias.upper() in cols:
            bucket_map[friendly] = cols[alias.upper()]
            break

# --- Sidebar filters
st.sidebar.header("2) Filters")

# STOP_NUMBER filter (single or range) if available
if STOP_NUMBER and STOP_NUMBER in df_raw.columns:
    mode = st.sidebar.radio("Stop Number selection", ["Single", "Range"], horizontal=True)
    valid_stops = sorted(df_raw[STOP_NUMBER].dropna().unique())
    if len(valid_stops) == 0:
        st.sidebar.caption("No numeric STOP_NUMBER values found.")
        stop_filter = None
    else:
        if mode == "Single":
            stop_val = st.sidebar.selectbox("STOP_NUMBER", options=valid_stops)
            stop_filter = ("single", stop_val)
        else:
            min_s, max_s = min(valid_stops), max(valid_stops)
            s_min, s_max = st.sidebar.slider("STOP_NUMBER range (inclusive)", min_value=int(min_s), max_value=int(max_s), value=(int(min_s), int(max_s)))
            stop_filter = ("range", (s_min, s_max))
else:
    stop_filter = None
    if STOP_NUMBER is None:
        st.sidebar.caption("STOP_NUMBER column not found – skipping stop filter.")

# Accuracy Bucket filter (multi-select)
available_buckets = [b for b in ["ACCURACY_30_MINS", "ACCURACY_45_MINS", "ACCURACY_60_MINS", "ACCURACY_90_MINS", "ACCURACY_120_MINS"] if b in bucket_map]
selected_buckets = st.sidebar.multiselect("Accuracy buckets to show", options=available_buckets, default=available_buckets)

# Shipment Lane filter (multi-select)
if SHIPMENT_LANE and SHIPMENT_LANE in df_raw.columns:
    lanes = sorted([x for x in df_raw[SHIPMENT_LANE].dropna().unique()])
    selected_lanes = st.sidebar.multiselect("Shipment lane(s)", options=lanes, default=lanes)
else:
    selected_lanes = None
    if SHIPMENT_LANE is None:
        st.sidebar.caption("SHIPMENT_LANE column not found – skipping lane filter.")

# --- Apply filters (no re-aggregation; file is already at shipment-level)
df = df_raw.copy()

# STOP_NUMBER filter
if stop_filter and STOP_NUMBER in df.columns:
    mode, val = stop_filter
    if mode == "Single":
        df = df[df[STOP_NUMBER] == val]
    else:
        lo, hi = val
        df = df[(df[STOP_NUMBER] >= lo) & (df[STOP_NUMBER] <= hi)]

# SHIPMENT_LANE filter
if selected_lanes is not None and SHIPMENT_LANE in df.columns:
    df = df[df[SHIPMENT_LANE].isin(selected_lanes)]

# In case multiple rows per BOL exist (identical shipment-level metrics repeated), keep first occurrence
if BOL in df.columns:
    df = df.sort_values(by=[BOL]).drop_duplicates(subset=[BOL], keep="first")

# --- Build output table
fixed_cols = [c for c in [BOL, CARRIER_NAME, SHIPMENT_LANE, PING_COVERAGE, ARRIVAL_WITHIN_APPT, TOTAL_PREDICTIONS] if c is not None and c in df.columns]

# Map selected buckets to actual column names present
bucket_cols = [bucket_map[b] for b in selected_buckets if b in bucket_map]

show_cols = fixed_cols + bucket_cols

missing_cols = [c for c in show_cols if c not in df.columns]
if missing_cols:
    st.warning(f"Some expected columns are missing in the file and will be skipped: {missing_cols}")
    show_cols = [c for c in show_cols if c in df.columns]

st.subheader("Results")
st.write("Filtered, shipment-level view (one row per BILL_OF_LADING).")

if not show_cols:
    st.error("No displayable columns found after selection. Please adjust filters or verify your CSV headers.")
    st.stop()

# Rename columns to friendly labels where appropriate
rename_map = {}
if ARRIVAL_WITHIN_APPT and ARRIVAL_WITHIN_APPT in show_cols:
    rename_map[ARRIVAL_WITHIN_APPT] = "ARRIVAL_WITHIN_APPOINTMENT_WINDOW"
if TOTAL_PREDICTIONS and TOTAL_PREDICTIONS in show_cols:
    rename_map[TOTAL_PREDICTIONS] = "TOTAL_PREDICTIONS"

for friendly, col in bucket_map.items():
    if col in show_cols:
        rename_map[col] = f"COUNT_OF_ACCURATE_PREDICTIONS_{friendly.split('_')[1]}_MINS" if friendly != "ACCURACY_45_MINS" else "COUNT_OF_ACCURATE_PREDICTIONS_45_MINS"

out = df[show_cols].rename(columns=rename_map)

# Basic top-line KPIs
k1, k2, k3 = st.columns(3)
with k1:
    st.metric("Shipments (unique BOL)", value=f"{out[BOL].nunique() if BOL in out.columns else len(out):,}")
with k2:
    if PING_COVERAGE and PING_COVERAGE in out.columns:
        try:
            st.metric("Avg Ping Coverage", value=f"{pd.to_numeric(out[PING_COVERAGE], errors='coerce').mean():.2f}")
        except Exception:
            st.metric("Avg Ping Coverage", value="—")
    else:
        st.metric("Avg Ping Coverage", value="—")
with k3:
    if TOTAL_PREDICTIONS and TOTAL_PREDICTIONS in out.columns:
        try:
            st.metric("Total Predictions (sum)", value=f"{pd.to_numeric(out[TOTAL_PREDICTIONS], errors='coerce').sum():,}")
        except Exception:
            st.metric("Total Predictions (sum)", value="—")
    else:
        st.metric("Total Predictions (sum)", value="—")

st.dataframe(out, use_container_width=True)

# --- Download button
csv_bytes = out.to_csv(index=False).encode("utf-8")
st.download_button(
    label="Download filtered table as CSV",
    data=csv_bytes,
    file_name="eta_shipment_level_filtered.csv",
    mime="text/csv",
)
