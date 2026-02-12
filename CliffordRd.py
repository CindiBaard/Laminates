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

# --- 3. SESSION STATE INITIALIZATION ---
if 'df' not in st.session_state:
    try:
        st.session_state.df, _ = load_data()
    except Exception as e:
        st.error(f"⚠️ Authentication Error: {e}")
        st.stop()

# --- 4. NAVIGATION & FILTERS ---
st.title("📦 Multi-Site Laminate Stock Management")

st.sidebar.header("Location & Timing")
# Site Selection
site_options = ["CliffordRd", "KPark", "HarrisDrive"]
selected_site = st.sidebar.selectbox("Select Site to Update", site_options)

# Month Selection
months = ["Jan", "Feb", "March", "April", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
selected_month = st.sidebar.selectbox("Select Month", months)

if st.sidebar.button("🔄 Sync with Google Sheets"):
    with st.spinner("Fetching latest data..."):
        st.session_state.df, _ = load_data()
        st.success("Data synchronized!")
        st.rerun()

# --- 5. DATA EDITOR SECTION ---
st.subheader(f"Update Stock for: {selected_site}")
st.info(f"Showing columns for {selected_site} in {selected_month}.")

# Logic for CliffordRd columns remains identical; others follow the same naming pattern
if selected_site == "CliffordRd":
    month_cols = [
        f"CliffordRd_Rolls {selected_month}", 
        f"CliffordRd_SlitRolls {selected_month}", 
        f"CliffordRd_Pallets {selected_month}", 
        f"SquareM {selected_month}"
    ]
else:
    # Standardizing naming for KPark and HarrisDrive
    month_cols = [
        f"{selected_site}_Rolls {selected_month}", 
        f"{selected_site}_SlitRolls {selected_month}", 
        f"{selected_site}_Pallets {selected_month}",
        f"{selected_site}_SquareM {selected_month}" # Assuming SquareM is also site-specific
    ]

available_cols = [c for c in month_cols if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=True,
    hide_index=True,
    disabled=["Material", "Laminate", "Code"]
)

if st.button("💾 Save Changes to Google Sheets"):
    with st.spinner("Updating Google Sheet..."):
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        st.session_state.df.update(edited_df)
        data_to_save = [st.session_state.df.columns.values.tolist()] + st.session_state.df.fillna('').values.tolist()
        sheet.update(range_name='A1', values=data_to_save)
        st.success(f"✅ {selected_site} changes saved!")

# --- 6. GROSS STOCK SUMMARY (ALL SITES COMBINED) ---
st.divider()
st.subheader(f"📊 Gross Stock Total - {selected_month}")

# Define metrics to sum
metrics = ["Rolls", "SlitRolls", "Pallets", "SquareM"]
summary_data = []

for _, row in st.session_state.df.iterrows():
    mat_sum = {"Material": row["Material"]}
    for metric in metrics:
        # Sum values across all sites for this material/month
        total = 0
        for site in site_options:
            col_name = f"{site}_{metric} {selected_month}"
            if site == "CliffordRd" and metric == "SquareM":
                col_name = f"SquareM {selected_month}"
            
            val = row.get(col_name, 0)
            try:
                total += float(val) if val != "" else 0
            except:
                pass
        mat_sum[f"Total {metric}"] = total
    summary_data.append(mat_sum)

st.table(pd.DataFrame(summary_data))

# --- 7. USAGE TRENDS GRAPH ---
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
    trend_values.append(val if val != "" else 0)

plot_df = pd.DataFrame({'Month': months, 'Value': trend_values})
fig = px.line(plot_df, x='Month', y='Value', title=f"CliffordRd {selected_metric} Trend", markers=True)
st.plotly_chart(fig, use_container_width=True)
=f"{selected_metric} Trend for {selected_mat}", 
    markers=True,
    line_shape="linear"
)
st.plotly_chart(fig, use_container_width=True)