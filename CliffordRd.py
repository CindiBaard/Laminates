import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
from datetime import datetime

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")

SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"
API_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# --- 2. AUTH & DATA ---
def get_gspread_client():
    creds_info = dict(st.secrets["gcp_service_account"])
    if "private_key" in creds_info:
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=API_SCOPES)
    return gspread.authorize(creds)

def load_data():
    client = get_gspread_client()
    ss = client.open_by_key(SPREADSHEET_ID)
    df = pd.DataFrame(ss.sheet1.get_all_records())
    df.columns = [str(c).strip() for c in df.columns]
    # Ensure numeric columns are actually numeric to prevent math errors
    numeric_cols = df.columns.drop(['Material', 'Code'])
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df, ss.sheet1, ss

# --- 3. SESSION STATE ---
if 'df' not in st.session_state:
    st.session_state.df, _, _ = load_data()

# --- 4. NAVIGATION ---
st.sidebar.header("Control Panel")
site_options = ["CliffordRd", "KPark", "HarrisDrive"]
selected_site = st.sidebar.selectbox("Active Update Site", site_options)
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
selected_month = st.sidebar.selectbox("Active Month", months)

thresholds = {
    "129 PBL": {"min": 5, "target": 15, "unit": "Pallets"},
    "129 ABL White": {"min": 3, "target": 10, "unit": "Pallets"},
    "113 ABL White": {"min": 7, "target": 20, "unit": "Pallets"},
    "113 PBL": {"min": 5, "target": 15, "unit": "Pallets"},
    "082 PBL": {"min": 5, "target": 15, "unit": "Pallets"},
    "082 ABL White": {"min": 2, "target": 8, "unit": "Pallets"},
    "082 ABL Silver": {"min": 20, "target": 100, "unit": "Rolls"},
    "129 ABL Silver": {"min": 20, "target": 100, "unit": "Rolls"},
    "113 ABL Silver": {"min": 20, "target": 100, "unit": "Rolls"}
}

tab_update, tab_summary, tab_reorder, tab_trends = st.tabs([
    "📝 Update Stock", "📊 Projections", "🛒 Reorder Report", "📈 Trends"
])

# --- 5. UPDATE LOGIC (Batch Update) ---
with tab_update:
    roll_col = f"{selected_site}_Rolls {selected_month}"
    pal_col = f"{selected_site}_Pallets {selected_month}"
    sq_col = f"{selected_site}_SquareM {selected_month}"
    
    display_cols = ["Material", "Code", roll_col, pal_col, sq_col]
    
    # ADJUSTED FOR HALF ROLLS: Configure the editor to allow 0.5 increments
    col_config = {
        roll_col: st.column_config.NumberColumn(
            f"Rolls ({selected_month})",
            help="Enter rolls (e.g., 1.5 for a roll and a half)",
            min_value=0,
            step=0.5,
            format="%.1f"
        ),
        pal_col: st.column_config.NumberColumn(
            f"Pallets ({selected_month})",
            min_value=0,
            step=0.5,
            format="%.1f"
        ),
        sq_col: st.column_config.NumberColumn(
            "SquareM (Auto)",
            disabled=True,
            format="%.2f"
        )
    }

    edited_df = st.data_editor(
        st.session_state.df[display_cols], 
        use_container_width=True, 
        hide_index=True,
        column_config=col_config
    )

    if st.button("💾 Save All Changes"):
        with st.spinner("Syncing..."):
            _, sheet, ss = load_data()
            updates = []
            for idx, row in edited_df.iterrows():
                # Precision Math remains consistent
                r_p = float(st.session_state.df.at[idx, "Rolls_on_Pallet"] or 1.0)
                m_p = float(st.session_state.df.at[idx, "m_Square_per_pallet"] or 0.0)
                
                nr = float(row[roll_col])
                np_val = float(row[pal_col])
                
                # Math: (Full Pallets * Area) + (Rolls * (Area / Rolls per Pallet))
                n_sq = round((np_val * m_p) + (nr * (m_p / r_p)), 2)
                
                updates.append({'range': gspread.utils.rowcol_to_a1(idx+2, st.session_state.df.columns.get_loc(roll_col)+1), 'values': [[nr]]})
                updates.append({'range': gspread.utils.rowcol_to_a1(idx+2, st.session_state.df.columns.get_loc(pal_col)+1), 'values': [[np_val]]})
                updates.append({'range': gspread.utils.rowcol_to_a1(idx+2, st.session_state.df.columns.get_loc(sq_col)+1), 'values': [[n_sq]]})
            
            sheet.batch_update(updates)
            st.session_state.df, _, _ = load_data()
            st.success("Stock updated with half-roll precision.")
            st.rerun()

# --- 6. REORDER REPORT TAB ---
with tab_reorder:
    st.subheader("📋 Required Reorder Quantities")
    reorder_list = []
    for _, row in st.session_state.df.iterrows():
        name = row["Material"]
        if name in thresholds:
            unit = thresholds[name]['unit']
            current_gross = sum([float(row.get(f"{s}_{unit} {selected_month}", 0)) for s in site_options])
            
            if current_gross < thresholds[name]['min']:
                needed = thresholds[name]['target'] - current_gross
                reorder_list.append({
                    "Material": name,
                    "Current Total": round(current_gross, 1),
                    "Target": thresholds[name]['target'],
                    "ORDER QUANTITY": f"{round(needed, 1)} {unit}"
                })
    
    if reorder_list:
        st.table(pd.DataFrame(reorder_list))
    else:
        st.success("All stock levels healthy.")

# --- 7. SUMMARY & TRENDS ---
with tab_summary:
    st.info("Projections are calculated based on monthly consumption rates.")

with tab_trends:
    st.write("Visualizing site-by-site performance.")