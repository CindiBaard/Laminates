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
        key = creds_info["private_key"]
        key = key.replace("\\\\n", "\n").replace("\\n", "\n")
        creds_info["private_key"] = key.strip()
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=API_SCOPES)
    return gspread.authorize(creds)

def load_data():
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    data = sheet.get_all_records()
    return pd.DataFrame(data), sheet

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

months = ["Jan", "Feb", "March", "April", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
selected_month = st.sidebar.selectbox("Select Month", months)

if st.sidebar.button("🔄 Sync with Google Sheets"):
    with st.spinner("Fetching latest data..."):
        st.session_state.df, _ = load_data()
        st.success("Data synchronized!")
        st.rerun()

# --- 5. DATA EDITOR ---
st.subheader(f"Update: {selected_site} ({selected_month})")

if selected_site == "CliffordRd":
    month_cols = [
        f"CliffordRd_Rolls {selected_month}", 
        f"CliffordRd_SlitRolls {selected_month}", 
        f"CliffordRd_Pallets {selected_month}", 
        f"SquareM {selected_month}"
    ]
else:
    month_cols = [
        f"{selected_site}_Rolls {selected_month}", 
        f"{selected_site}_SlitRolls {selected_month}", 
        f"{selected_site}_Pallets {selected_month}",
        f"{selected_site}_SquareM {selected_month}"
    ]

available_cols = [c for c in month_cols if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

# Define which columns are editable and pinned
# Streamlit data_editor automatically freezes the header row.
edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Material": st.column_config.TextColumn(pinned=True), # Freezes the Material column
        "Laminate": st.column_config.TextColumn(disabled=True),
        "Code": st.column_config.TextColumn(disabled=True),
    },
    disabled=["Laminate", "Code"] # Specifically allows editing in the month_cols
)

if st.button("💾 Save Changes to Google Sheets"):
    with st.spinner("Updating Google Sheet..."):
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        st.session_state.df.update(edited_df)
        data_to_save = [st.session_state.df.columns.values.tolist()] + st.session_state.df.fillna('').values.tolist()
        sheet.update(range_name='A1', values=data_to_save)
        st.success(f"✅ Changes for {selected_site} saved!")

# --- 6. GROSS SUMMARY ---
st.divider()
st.subheader(f"📊 Gross Stock Summary - {selected_month}")

summary_list = []
for _, row in st.session_state.df.iterrows():
    mat_sum = {"Material": row["Material"], "Code": row["Code"]}
    for metric in ["Rolls", "SlitRolls", "Pallets", "SquareM"]:
        total = 0
        for site in site_options:
            col = f"SquareM {selected_month}" if (site == "CliffordRd" and metric == "SquareM") else f"{site}_{metric} {selected_month}"
            val = row.get(col, 0)
            try:
                clean_val = str(val).replace(',', '').strip()
                total += float(clean_val) if clean_val != "" else 0
            except (ValueError, TypeError):
                pass
        mat_sum[f"Gross {metric}"] = total
    summary_list.append(mat_sum)

st.dataframe(pd.DataFrame(summary_list), use_container_width=True)

# --- 7. TRENDS ---
st.divider()
st.subheader("📈 Stock Usage Trends (CliffordRd)")
unique_materials = st.session_state.df['Material'].unique()
selected_mat = st.selectbox("Select Material for Trend View", unique_materials)
selected_metric = st.radio("Select Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]
trend_values = []
for m in months:
    col_name = f"CliffordRd_{selected_metric} {m}" if selected_metric != "SquareM" else f"SquareM {m}"
    val = mat_data.get(col_name, 0)
    try:
        clean_v = str(val).replace(',', '').strip()
        trend_values.append(float(clean_v) if clean_v != "" else 0)
    except (ValueError, TypeError):
        trend_values.append(0)

plot_df = pd.DataFrame({'Month': months, 'Value': trend_values})
fig = px.line(plot_df, x='Month', y='Value', title=f"CliffordRd: {selected_metric} Trend", markers=True)
st.plotly_chart(fig, use_container_width=True)