import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")

# The ID of your Google Sheet
SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"

# Permissions required for the app
API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- 2. AUTHENTICATION & CONNECTION ---

def get_gspread_client():
    """Authenticates and returns a gspread client."""
    # 1. Convert Streamlit secrets to a standard dictionary
    creds_info = dict(st.secrets["gcp_service_account"])
    
    # 2. Fix formatting for the private key (Resolves MalformedFraming/PEM errors)
    if "private_key" in creds_info:
        key = creds_info["private_key"]
        # Handle literal backslashes and strip whitespace
        key = key.replace("\\\\n", "\n").replace("\\n", "\n")
        creds_info["private_key"] = key.strip()
    
    # 3. Create credentials and authorize client
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=API_SCOPES)
    return gspread.authorize(creds)

def load_data():
    """Fetches data from Google Sheets and returns a DataFrame and the sheet object."""
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
        st.info("Check your Streamlit Secrets for formatting issues (private_key newlines).")
        st.stop()

# --- 4. APP UI - HEADER ---
st.title("📦 Laminate Stock Management - Clifford Rd")

# --- 5. SIDEBAR CONTROLS ---
st.sidebar.header("Controls")
if st.sidebar.button("🔄 Sync with Google Sheets"):
    with st.spinner("Fetching latest data..."):
        st.session_state.df, _ = load_data()
        st.success("Data synchronized!")
        st.rerun()

# --- 6. DATA EDITOR SECTION ---
st.subheader("Monthly Stock Update")
st.info("Edit the values below to update stock levels. Totals and trends will update automatically.")

# Months configuration
months = ["Jan", "Feb", "March", "April", "May", "June", "July", "Aug", "Sep", "Oct", "Nov", "Dec"]
selected_month = st.selectbox("Select Month to Update", months)

# Define column names based on spreadsheet structure
month_cols = [
    f"CliffordRd_Rolls {selected_month}", 
    f"CliffordRd_SlitRolls {selected_month}", 
    f"CliffordRd_Pallets {selected_month}", 
    f"SquareM {selected_month}"
]

# Ensure only columns that exist in the DataFrame are shown
available_cols = [c for c in month_cols if c in st.session_state.df.columns]
display_cols = ["Material", "Laminate", "Code"] + available_cols

# Editable dataframe
edited_df = st.data_editor(
    st.session_state.df[display_cols],
    use_container_width=True,
    hide_index=True,
    disabled=["Material", "Laminate", "Code"]
)

# Save Logic
if st.button("💾 Save Changes to Google Sheets"):
    with st.spinner("Updating Google Sheet..."):
        client = get_gspread_client()
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        
        # Update local session state
        st.session_state.df.update(edited_df)
        
        # Prepare data for upload (Headers + Data)
        # Using .fillna('') ensures that empty cells are handled correctly by the Google API
        data_to_save = [st.session_state.df.columns.values.tolist()] + st.session_state.df.fillna('').values.tolist()
        
        # Push to Google Sheets (Range 'A1' starts at the top-left)
        sheet.update(range_name='A1', values=data_to_save)
        st.success("✅ Changes saved to the live spreadsheet!")

# --- 7. USAGE TRENDS GRAPH ---
st.divider()
st.subheader("📈 Stock Usage Trends")

# Filter controls for visualization
unique_materials = st.session_state.df['Material'].unique()
selected_mat = st.selectbox("Select Material to view trend", unique_materials)
selected_metric = st.radio("Select Metric", ["Rolls", "Pallets", "SquareM"], horizontal=True)

# Find the specific row for the selected material
mat_data = st.session_state.df[st.session_state.df['Material'] == selected_mat].iloc[0]

# Build trend data points
trend_values = []
for m in months:
    col_name = f"CliffordRd_{selected_metric} {m}" if selected_metric != "SquareM" else f"SquareM {m}"
    # Default to 0 if the column is missing or value is empty
    val = mat_data.get(col_name, 0)
    trend_values.append(val if val != "" else 0)

# Create plotting DataFrame
plot_df = pd.DataFrame({
    'Month': months,
    'Value': trend_values
})

# Display line chart
fig = px.line(
    plot_df, 
    x='Month', 
    y='Value', 
    title=f"{selected_metric} Trend for {selected_mat}", 
    markers=True,
    line_shape="linear"
)
st.plotly_chart(fig, use_container_width=True)