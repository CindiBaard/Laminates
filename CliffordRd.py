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
st.subheader(f"Update: {selected_site} ({selected_month})")

month_cols = [
    f"{selected_site}_Rolls {selected_month}", 
    f"{selected_site}_Pallets {selected_month}",
    f"{selected_site}_SquareM {selected_month}"
]

available_cols = [c for c in month_cols if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

col_config = {
    "Material": st.column_config.TextColumn(label="Material", pinned=True, width="medium"),
    "Laminate": st.column_config.TextColumn(label="Laminate", disabled=True, width="small"),
    "Code": st.column_config.TextColumn(label="Code", disabled=True, width="small"),
}

for col in available_cols:
    clean_label = col.split("_")[1].split(" ")[0]
    col_config[col] = st.column_config.NumberColumn(
        label=clean_label,
        width="medium", 
        disabled=False,
        required=False
    )

edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=False,
    width=1200,
    hide_index=True,
    column_config=col_config,
    key="data_editor_key"
)

# --- 6. GROSS SUMMARY ---
st.divider()
st.subheader(f"📊 Gross Stock Summary - {selected_month}")

summary_list = []
for _, row in st.session_state.df.iterrows():
    # Base row data
    mat_sum = {
        "Material": row["Material"], 
        "Code": row["Code"],
        "Meters/Roll": row.get("Meters_per_Roll", 0),
        "Rolls/Pallet": row.get("Rolls_on_Pallet", 0),
        "m2/Pallet": row.get("m_Square_per_pallet", 0)
    }
    
    # Calculate Gross totals for each metric
    for metric in ["Rolls", "Pallets", "SquareM"]:
        total = 0
        for site in site_options:
            # Handle the specific KPark Feb typo in your sheet
            cur_month = "Feb" if (selected_month == "February" and site == "KPark" and metric == "SquareM") else selected_month
            col_name = f"{site}_{metric} {cur_month}"
            
            val = row.get(col_name, 0)
            try:
                # Cleaning string values like "1,200" into floats
                clean_val = str(val).replace(',', '').strip()
                total += float(clean_val) if clean_val != "" else 0
            except (ValueError, TypeError):
                pass
        
        # Add the sum to our dictionary
        mat_sum[f"Gross {metric}"] = total
    
    summary_list.append(mat_sum)

# Create DataFrame and display
summary_df = pd.DataFrame(summary_list)

# Formatting Gross Rolls to be prominent
st.dataframe(
    summary_df, 
    use_container_width=True, 
    hide_index=True,
    column_config={
        "Gross Rolls": st.column_config.NumberColumn(format="%d", help="Sum of Rolls across all 3 sites"),
        "Gross Pallets": st.column_config.NumberColumn(format="%.1f"),
        "Gross SquareM": st.column_config.NumberColumn(format="%.2f")
    }
)

# --- 7. TRENDS ---
st.divider()
st.subheader(f"📈 Stock Usage Trends ({selected_site})")
unique_materials = st.session_state.df['Material'].unique()
selected_mat = st.selectbox("Select Material for Trend View", unique_materials)
selected_metric = st.radio("Select Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]
trend_values = []
for m in months:
    cur_m = "Feb" if (m == "February" and selected_site == "KPark" and selected_metric == "SquareM") else m
    col_name = f"{selected_site}_{selected_metric} {cur_m}"
    
    val = mat_data.get(col_name, 0)
    try:
        clean_v = str(val).replace(',', '').strip()
        trend_values.append(float(clean_v) if clean_v != "" else 0)
    except (ValueError, TypeError):
        trend_values.append(0)

plot_df = pd.DataFrame({'Month': months, 'Value': trend_values})
fig = px.line(plot_df, x='Month', y='Value', title=f"{selected_site}: {selected_metric} Trend", markers=True)
st.plotly_chart(fig, use_container_width=True)