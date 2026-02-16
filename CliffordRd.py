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
        
    creds = service_account.Credentials.from_service_account_info(
        creds_info, 
        scopes=API_SCOPES
    )
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

# --- 4. NAVIGATION ---
st.title("📦 Multi-Site Laminate Stock Management")

st.sidebar.header("Location & Timing")
site_options = ["CliffordRd", "KPark", "HarrisDrive"]
selected_site = st.sidebar.selectbox("Select Site to Update", site_options)

months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
selected_month = st.sidebar.selectbox("Select Month", months)

if st.sidebar.button("🔄 Sync with Google Sheets"):
    with st.spinner("Fetching latest data..."):
        st.session_state.df, _ = load_data()
        st.success("Data synchronized!")
        st.rerun()

# --- 5. DATA EDITOR ---
st.subheader(f"Update Physical Stock: {selected_site} ({selected_month})")

roll_col = f"{selected_site}_Rolls {selected_month}"
pallet_col = f"{selected_site}_Pallets {selected_month}"
square_col = f"{selected_site}_SquareM {selected_month}"

available_cols = [c for c in [roll_col, pallet_col, square_col] if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

col_config = {
    "Material": st.column_config.TextColumn(label="Material", pinned=True, width="medium"),
    "Laminate": st.column_config.TextColumn(label="Laminate", disabled=True, width="small"),
    "Code": st.column_config.TextColumn(label="Code", disabled=True, width="small"),
}

for col in available_cols:
    clean_label = col.split("_")[1].split(" ")[0]
    is_disabled = "SquareM" in col 
    col_config[col] = st.column_config.NumberColumn(
        label=clean_label, width="medium", disabled=is_disabled
    )

edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=False,
    width=1200,
    hide_index=True,
    column_config=col_config,
    key="data_editor_key"
)

# --- 6. SAVE & CALCULATION ---
if st.button("💾 Save Counts & Update Total Area"):
    try:
        with st.spinner("Updating spreadsheet..."):
            client = get_gspread_client()
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            updates = []
            
            for index, row in edited_df.iterrows():
                m_per_roll = pd.to_numeric(st.session_state.df.at[index, "Meters_per_Roll"], errors='coerce') or 0
                m2_per_pallet = pd.to_numeric(st.session_state.df.at[index, "m_Square_per_pallet"], errors='coerce') or 0
                
                new_rolls = row[roll_col]
                new_pallets = row[pallet_col]
                
                st.session_state.df.at[index, roll_col] = new_rolls
                st.session_state.df.at[index, pallet_col] = new_pallets
                
                roll_idx = st.session_state.df.columns.get_loc(roll_col) + 1
                pal_idx = st.session_state.df.columns.get_loc(pallet_col) + 1
                updates.append({'range': gspread.utils.rowcol_to_a1(index + 2, roll_idx), 'values': [[new_rolls]]})
                updates.append({'range': gspread.utils.rowcol_to_a1(index + 2, pal_idx), 'values': [[new_pallets]]})
                
                if square_col in st.session_state.df.columns:
                    # Logic: Total m2 = (Pallets * m2/Pallet) + (Rolls * Meters/Roll)
                    calc_total_m2 = round((new_pallets * m2_per_pallet) + (new_rolls * m_per_roll), 2)
                    st.session_state.df.at[index, square_col] = calc_total_m2
                    sqm_idx = st.session_state.df.columns.get_loc(square_col) + 1
                    updates.append({'range': gspread.utils.rowcol_to_a1(index + 2, sqm_idx), 'values': [[calc_total_m2]]})
            
            sheet.batch_update(updates)
            st.success("✅ Updates saved!")
            st.rerun()
    except Exception as e:
        st.error(f"❌ Error: {e}")

# --- 7. GROSS SUMMARY ---
st.divider()
st.subheader(f"📊 Gross Stock Summary - {selected_month}")

summary_list = []
for index, row in st.session_state.df.iterrows():
    # Including the specific technical columns you requested
    mat_sum = {
        "Material": row["Material"], 
        "Code": row["Code"],
        "Meters_per_Roll": row.get("Meters_per_Roll", 0),
        "Rolls_on_Pallet": row.get("Rolls_on_Pallet", 0),
        "m_Square_per_pallet": row.get("m_Square_per_pallet", 0)
    }
    
    # Calculate aggregate totals across all sites
    for metric in ["Rolls", "Pallets", "SquareM"]:
        total = 0
        for site in site_options:
            cur_month = "Feb" if (selected_month == "February" and site == "KPark" and metric == "SquareM") else selected_month
            col_name = f"{site}_{metric} {cur_month}"
            val = row.get(col_name, 0)
            try:
                total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0
            except: pass
        mat_sum[f"Gross {metric}"] = total
    
    summary_list.append(mat_sum)

summary_df = pd.DataFrame(summary_list)

# Reordering columns for better readability
final_cols = [
    "Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet",
    "Gross Rolls", "Gross Pallets", "Gross SquareM"
]

st.dataframe(
    summary_df[final_cols], 
    use_container_width=True, 
    hide_index=True,
    column_config={
        "Meters_per_Roll": st.column_config.NumberColumn(label="Mtrs/Roll"),
        "Rolls_on_Pallet": st.column_config.NumberColumn(label="Rolls/Pallet"),
        "m_Square_per_pallet": st.column_config.NumberColumn(label="m2/Pallet"),
        "Gross SquareM": st.column_config.NumberColumn(label="Total Gross m2", format="%.2f")
    }
)

# --- 8. TRENDS ---
st.divider()
st.subheader(f"📈 Trends ({selected_site})")
unique_materials = st.session_state.df['Material'].unique()
selected_mat = st.selectbox("Select Material", unique_materials)
selected_metric = st.radio("Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]
trend_values = []
for m in months:
    cur_m = "Feb" if (m == "February" and selected_site == "KPark" and selected_metric == "SquareM") else m
    col_name = f"{selected_site}_{selected_metric} {cur_m}"
    val = mat_data.get(col_name, 0)
    try:
        trend_values.append(float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0)
    except: trend_values.append(0)

st.plotly_chart(px.line(pd.DataFrame({'Month': months, 'Value': trend_values}), x='Month', y='Value', markers=True), use_container_width=True)
