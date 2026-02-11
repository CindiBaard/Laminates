import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2.service_account import Credentials
import gspread

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")
SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"

# --- 2. AUTHENTICATION & CONNECTION ---
# Note: You will need a service_account.json file from Google Cloud Console
def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    # Update 'service_account.json' with the path to your credentials file
    creds = Credentials.from_service_account_file("service_account.json", scopes=scope)
    return gspread.authorize(creds)

def load_data():
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    data = sheet.get_all_records()
    return pd.DataFrame(data), sheet

# Initialize session state
if 'df' not in st.session_state:
    st.session_state.df, _ = load_data()

st.title("📦 Laminate Stock Management - Clifford Rd")

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("Controls")
if st.sidebar.button("🔄 Sync with Google Sheets"):
    st.session_state.df, _ = load_data()
    st.sidebar.success("Data synchronized!")

# --- 4. DATA EDITOR ---
st.subheader("Monthly Stock Update")
st.info("Edit the values below to update stock levels. Totals and trends will update automatically.")

# Define month categories based on your spreadsheet headers
months = ["Jan", "Feb", "March", "April", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
selected_month = st.selectbox("Select Month to Update", months)

# Filter columns for the selected month
month_cols = [f"CliffordRd_Rolls {selected_month}", 
              f"CliffordRd_SlitRolls {selected_month}", 
              f"CliffordRd_Pallets {selected_month}", 
              f"SquareM {selected_month}"]

# Display editable dataframe for the selected month
edited_df = st.data_editor(
    st.session_state.df[["Material", "Laminate", "Code"] + month_cols],
    use_container_width=True,
    hide_index=True,
    disabled=["Material", "Laminate", "Code"]
)

# Button to save changes back to Google Sheets
if st.button("💾 Save Changes to Google Sheets"):
    client = get_gspread_client()
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    
    # Update the local dataframe with changes
    st.session_state.df.update(edited_df)
    
    # Push the entire dataframe back (starting from cell A2)
    data_to_save = [st.session_state.df.columns.values.tolist()] + st.session_state.df.values.tolist()
    sheet.update(data_to_save)
    st.success("Changes saved to the live spreadsheet!")

# --- 5. USAGE TRENDS GRAPH ---
st.divider()
st.subheader("📈 Stock Usage Trends")

selected_mat = st.selectbox("Select Material to view trend", st.session_state.df['Material'].unique())
selected_metric = st.radio("Select Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

# Prepare data for plotting
mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]

trend_values = []
for m in months:
    col_name = f"CliffordRd_{selected_metric} {m}" if selected_metric != "SquareM" else f"SquareM {m}"
    # Use 0 if the column doesn't exist for some reason
    trend_values.append(mat_row.get(col_name, 0))

plot_df = pd.DataFrame({
    'Month': months,
    'Value': trend_values
})

fig = px.line(plot_df, x='Month', y='Value', title=f"{selected_metric} Trend for {selected_mat}", markers=True)
st.plotly_chart(fig, use_container_width=True)