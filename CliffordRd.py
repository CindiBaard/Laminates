import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")

SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"
API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- 2. AUTHENTICATION & CONNECTION ---
def get_gspread_client():
    creds_info = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_info:
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=API_SCOPES)
    return gspread.authorize(creds)

def load_data():
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    df.columns = [str(c).strip() for c in df.columns]
    return df, sheet

# --- 3. SESSION STATE ---
if 'df' not in st.session_state:
    try:
        st.session_state.df, _ = load_data()
    except Exception as e:
        st.error(f"⚠️ Authentication Error: {e}")
        st.stop()

# --- 4. NAVIGATION & SIDEBAR ---
st.sidebar.header("Location & Timing")
site_options = ["CliffordRd", "KPark", "HarrisDrive"]
selected_site = st.sidebar.selectbox("Select Site to Update", site_options)
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
selected_month = st.sidebar.selectbox("Select Month", months)

thresholds = {
    "129 PBL": {"val": 5, "target": 10, "unit": "Pallets"},
    "129 ABL White": {"val": 3, "target": 6, "unit": "Pallets"},
    "113 ABL White": {"val": 7, "target": 20, "unit": "Pallets"},
    "113 PBL": {"val": 5, "target": 15, "unit": "Pallets"},
    "082 PBL": {"val": 4, "target": 6, "unit": "Pallets"},
    "082 ABL White": {"val": 2, "target": 8, "unit": "Pallets"},
    "082 ABL Silver": {"val": 20, "target": 36, "unit": "Rolls"},
    "129 ABL Silver": {"val": 20, "target": 32, "unit": "Rolls"},
    "113 ABL Silver": {"val": 20, "target": 32, "unit": "Rolls"},
    "JUMBO ROLLS PBL": {"val": 3, "target": 5, "unit": "Pallets"},
    "JUMBO ROLLS ABL White": {"val": 2, "target": 8, "unit": "Pallets"},
    "JUMBO ROLLS Silver": {"val": 1, "target": 2, "unit": "Pallets"}
}

# --- 5. DATA EDITOR (Moved up so edited values are available for calc) ---
st.title("📦 Multi-Site Laminate Stock Management")
st.subheader(f"Update Physical Stock: {selected_site} ({selected_month})")

roll_col = f"{selected_site}_Rolls {selected_month}"
pallet_col = f"{selected_site}_Pallets {selected_month}"
square_col = f"{selected_site}_SquareM {selected_month}"

available_cols = [c for c in [roll_col, pallet_col, square_col] if c in st.session_state.df.columns]
display_cols = ["Material", "Code"] + available_cols

col_config = {
    "Material": st.column_config.TextColumn(label="Material", pinned=True),
    "Code": st.column_config.TextColumn(label="Code", disabled=True),
}
for col in available_cols:
    col_config[col] = st.column_config.NumberColumn(step=0.5, format="%.1f", disabled=("SquareM" in col))

# capturing live edits
edited_df = st.data_editor(st.session_state.df[display_cols], use_container_width=True, hide_index=True, column_config=col_config)

# --- 6. DATA PROCESSING & REORDER LOGIC ---
summary_list = []
low_stock_alerts = []
reorder_needed = []

for index, row in st.session_state.df.iterrows():
    mat_name = str(row["Material"]).strip()
    mat_sum = {"Material": mat_name, "Code": row["Code"]}
    
    # Use edited_df values for the active site, otherwise use session_state data
    edited_row = edited_df.iloc[index]
    
    for metric in ["Rolls", "Pallets", "SquareM"]:
        total = 0
        for site in site_options:
            col_name = f"{site}_{metric} {selected_month}"
            # If this is the site we are currently editing, take the value from the table
            if site == selected_site and col_name in edited_row:
                val = edited_row[col_name]
            else:
                val = row.get(col_name, 0)
                
            try: total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0
            except: pass
        mat_sum[f"Gross {metric}"] = total
    
    if mat_name in thresholds:
        t_info = thresholds[mat_name]
        current_val = mat_sum[f"Gross {t_info['unit']}"]
        if current_val < t_info['val']:
            low_stock_alerts.append(f"**{mat_name}**: {current_val} {t_info['unit']} (Min: {t_info['val']})")
            gap = max(0, t_info['target'] - current_val)
            reorder_needed.append({
                "Material": mat_name,
                "Current": current_val,
                "Target": t_info['target'],
                "Order Qty": f"{gap} {t_info['unit']}"
            })
    summary_list.append(mat_sum)

summary_df = pd.DataFrame(summary_list)

# Sidebar UI
if low_stock_alerts:
    st.sidebar.warning("⚠️ **Low Stock Alerts**")
    for alert in low_stock_alerts: st.sidebar.write(f"- {alert}")
else:
    st.sidebar.success("✅ All stock levels healthy")

# --- 7. SAVE BUTTON ---
if st.button("💾 Save Counts"):
    try:
        with st.spinner("Updating..."):
            client = get_gspread_client()
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            updates = []
            for index, row in edited_df.iterrows():
                r_on_p = pd.to_numeric(st.session_state.df.at[index, "Rolls_on_Pallet"], errors='coerce') or 1
                m2_p_p = pd.to_numeric(st.session_state.df.at[index, "m_Square_per_pallet"], errors='coerce') or 0
                calc_total_m2 = round((row[pallet_col] * m2_p_p) + (row[roll_col] * (m2_p_p / r_on_p)), 2)
                
                for c, val in [(roll_col, row[roll_col]), (pallet_col, row[pallet_col]), (square_col, calc_total_m2)]:
                    if c in st.session_state.df.columns:
                        col_idx = st.session_state.df.columns.get_loc(c) + 1
                        updates.append({'range': gspread.utils.rowcol_to_a1(index + 2, col_idx), 'values': [[val]]})
            sheet.batch_update(updates)
            st.session_state.df, _ = load_data()
            st.success("✅ Saved!")
            st.rerun()
    except Exception as e: st.error(f"Error: {e}")

# --- 8. REORDER REPORT & SUMMARY ---
if reorder_needed:
    st.divider()
    st.subheader("🛒 Reorder Report (Required to hit Targets)")
    st.table(pd.DataFrame(reorder_needed))

st.divider()
st.subheader(f"📊 Gross Stock Summary - {selected_month}")
def highlight_low_stock(row):
    material = str(row["Material"]).strip()
    if material in thresholds:
        t_info = thresholds[material]
        if row[f"Gross {t_info['unit']}"] < t_info['val']:
            return ['background-color: #ff4b4b; color: white'] * len(row)
    return [''] * len(row)

st.dataframe(summary_df.style.apply(highlight_low_stock, axis=1), use_container_width=True, hide_index=True)