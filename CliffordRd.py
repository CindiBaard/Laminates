import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")
SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"

# Define scopes for Google Sheets and Drive
API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- 2. AUTHENTICATION & CONNECTION ---
def get_gspread_client():
    # Convert Streamlit secrets to a standard dictionary
    creds_info = dict(st.secrets["gcp_service_account"])
    
    # Fix formatting for the private key
    if "private_key" in creds_info:
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
    
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=API_SCOPES)
    return gspread.authorize(creds)

def load_data():
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    data = sheet.get_all_records()
    return pd.DataFrame(data), sheet

# Initialize session state
if 'df' not in st.session_state:
    try:
        st.session_state.df, _ = load_data()
    except Exception as e:
        st.error(f"Authentication Error: {e}")
        st.stop()

st.title("📦 Laminate Stock Management - Clifford Rd")

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("Controls")
if st.sidebar.button("🔄 Sync with Google Sheets"):
    st.session_state.df, _ = load_data()
    st.rerun()

# --- 4. DATA EDITOR ---
st.subheader("Monthly Stock Update")
months = ["Jan", "Feb", "March", "April", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
selected_month = st.selectbox("Select Month to Update", months)

month_cols = [f"CliffordRd_Rolls {selected_month}", 
              f"CliffordRd_SlitRolls {selected_month}", 
              f"CliffordRd_Pallets {selected_month}", 
              f"SquareM {selected_month}"]

# Safety check: only use columns that actually exist in the sheet
available_cols = [c for c in month_cols if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=True,
    hide_index=True,
    disabled=["Material", "Laminate", "Code"]
)

if st.button("💾 Save Changes to Google Sheets"):
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    
    # Update local state
    st.session_state.df.update(edited_df)
    
    # Prepare data for upload (headers + values)
    # Using .fillna('') to prevent JSON errors with NaN values
    data_to_save = [st.session_state.df.columns.values.tolist()] + st.session_state.df.fillna('').values.tolist()
    
    # gspread update syntax (A1 is the starting cell)
    sheet.update(range_name='A1', values=data_to_save)
    st.success("Changes saved successfully!")

# --- 5. USAGE TRENDS GRAPH ---
st.divider()
st.subheader("📈 Stock Usage Trends")

selected_mat = st.selectbox("Select Material", st.session_state.df['Material'].unique())
selected_metric = st.radio("Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]

trend_values = []
for m in months:
    col_name = f"CliffordRd_{selected_metric} {m}" if selected_metric != "SquareM" else f"SquareM {m}"
    trend_values.append(mat_data.get(col_name, 0))

plot_df = pd.DataFrame({'Month': months, 'Value': trend_values})
fig = px.line(plot_df, x='Month', y='Value', title=f"{selected_metric} Trend", markers=True)
st.plotly_chart(fig, use_container_width=True)
