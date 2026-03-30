import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
import io

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")

SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"
API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# Weight and Container Constants
WEIGHT_FACTORS = {
    "Pallet_Avg_KG": 850.0,
    "Roll_Avg_KG": 25.0
}
CONTAINER_LIMIT_KG = 18000.0  # Max KG for a standard 20ft container load
CONTAINER_LIMIT_PALLETS = 10.0 # Max pallet spaces for a 20ft container

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
    "129 PBL": {"val": 7, "target": 10, "unit": "Pallets"},
    "129 ABL White": {"val": 5, "target": 7, "unit": "Pallets"},
    "113 ABL White": {"val": 9, "target": 11, "unit": "Pallets"},
    "113 PBL": {"val": 8, "target": 15, "unit": "Pallets"},
    "082 PBL": {"val": 4, "target": 6, "unit": "Pallets"},
    "082 ABL White": {"val": 2, "target": 6, "unit": "Pallets"},
    "082 ABL Silver": {"val": 20, "target": 36, "unit": "Rolls"},
    "129 ABL Silver": {"val": 20, "target": 20, "unit": "Rolls"},
    "113 ABL Silver": {"val": 20, "target": 32, "unit": "Rolls"},
    "JUMBO ROLLS PBL": {"val": 4, "target": 6, "unit": "Pallets"},
    "JUMBO ROLLS ABL White": {"val": 4, "target": 6, "unit": "Pallets"},
    "JUMBO ROLLS Silver": {"val": 1, "target": 2, "unit": "Pallets"}
}

# --- 5. DATA EDITOR ---
st.title("📦 Multi-Site Laminate Stock Management")
st.subheader(f"Update Physical Stock: {selected_site} ({selected_month})")

roll_col = f"{selected_site}_Rolls {selected_month}"
pallet_col = f"{selected_site}_Pallets {selected_month}"
square_col = f"{selected_site}_SquareM {selected_month}"

available_cols = [c for c in [roll_col, pallet_col, square_col] if c in st.session_state.df.columns]

# UNHIDDEN COLUMNS
display_cols = ["Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet"] + available_cols

col_config = {
    "Material": st.column_config.TextColumn(label="Material", pinned=True),
    "Code": st.column_config.TextColumn(label="Code", disabled=True),
    "Meters_per_Roll": st.column_config.NumberColumn(label="m/Roll", disabled=True),
    "Rolls_on_Pallet": st.column_config.NumberColumn(label="Rolls/Pallet", disabled=True),
    "m_Square_per_pallet": st.column_config.NumberColumn(label="m²/Pallet", disabled=True),
}
for col in available_cols:
    col_config[col] = st.column_config.NumberColumn(step=0.5, format="%.1f", disabled=("SquareM" in col))

edited_df = st.data_editor(st.session_state.df[display_cols], use_container_width=True, hide_index=True, column_config=col_config)

# --- 6. LIVE DATA PROCESSING ---
summary_list = []
low_stock_alerts = []
reorder_needed = []
total_est_weight_kg = 0.0
total_pallets_to_order = 0.0

for index, row in st.session_state.df.iterrows():
    mat_name = str(row["Material"]).strip()
    mat_sum = {"Material": mat_name, "Code": row["Code"]}
    edited_row = edited_df.iloc[index]
    
    for metric in ["Rolls", "Pallets", "SquareM"]:
        total = 0
        for site in site_options:
            col_name = f"{site}_{metric} {selected_month}"
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
            gap = max(0.0, float(t_info['target']) - float(current_val))
            
            item_weight = 0.0
            order_sqm = 0.0
            m2_per_p = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0
            r_on_p = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1

            if t_info['unit'] == "Pallets":
                item_weight = gap * WEIGHT_FACTORS["Pallet_Avg_KG"]
                total_pallets_to_order += gap
                order_sqm = gap * m2_per_p
            else:
                item_weight = gap * WEIGHT_FACTORS["Roll_Avg_KG"]
                order_sqm = gap * (m2_per_p / r_on_p)
            
            total_est_weight_kg += item_weight

            reorder_needed.append({
                "Material": mat_name,
                "Current": current_val,
                "Target": t_info['target'],
                "Order Qty": f"{gap:.1f} {t_info['unit']}",
                "Order m²": round(order_sqm, 2),
                "Est. Weight (KG)": round(item_weight, 1)
            })
    summary_list.append(mat_sum)

summary_df = pd.DataFrame(summary_list)

# Sidebar Alerts
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

# --- 8. REORDER REPORT & FREIGHT PLANNING ---
if reorder_needed:
    st.divider()
    col_a, col_b = st.columns([2, 1])
    
    with col_a:
        st.subheader("🛒 Reorder Report")
        reorder_df = pd.DataFrame(reorder_needed)
        st.table(reorder_df)
    
    with col_b:
        st.subheader("🚛 Freight Planning (20ft Load)")
        
        weight_cap = min(total_est_weight_kg / CONTAINER_LIMIT_KG, 1.0)
        pallet_cap = min(total_pallets_to_order / CONTAINER_LIMIT_PALLETS, 1.0)
        
        st.write(f"**Weight Capacity ({total_est_weight_kg/1000:.2f}T / {CONTAINER_LIMIT_KG/1000:.0f}T)**")
        st.progress(weight_cap)
        
        st.write(f"**Pallet Space ({total_pallets_to_order:.1f} / {CONTAINER_LIMIT_PALLETS:.0f})**")
        st.progress(pallet_cap)
        
        if weight_cap >= 1.0 or pallet_cap >= 1.0:
            st.error("🚨 Container capacity exceeded!")
        elif weight_cap > 0.8 or pallet_cap > 0.8:
            st.warning("⚠️ Container almost full.")
        else:
            st.info("🟢 Capacity available.")

    # --- Excel Export Logic ---
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        reorder_df.to_excel(writer, index=False, sheet_name='Reorder List')
    
    st.download_button(
        label="📥 Download Reorder List (Excel)",
        data=buffer.getvalue(),
        file_name=f"Reorder_Report_{selected_month}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# --- 9. GROSS SUMMARY TABLE ---
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

# --- 10. MONTHLY TREND CHART ---
st.divider()
st.subheader("📈 Monthly Stock Trends by Code")

trend_data = []
sqm_cols = [c for c in st.session_state.df.columns if "SquareM" in c]

for m in months:
    month_sqm_cols = [c for c in sqm_cols if m in c]
    if month_sqm_cols:
        for idx, row in st.session_state.df.iterrows():
            code = row["Code"]
            total_m2 = pd.to_numeric(row[month_sqm_cols], errors='coerce').fillna(0).sum()
            trend_data.append({"Month": m, "Code": code, "Total m²": total_m2})

if trend_data:
    trend_df = pd.DataFrame(trend_data)
    fig = px.line(
        trend_df, 
        x="Month", 
        y="Total m²", 
        color="Code", 
        markers=True,
        category_orders={"Month": months},
        title="Stock Level History (m²) per Material Code"
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No data available to plot trends.")

# ... (Keep all your existing code from sections 1 through 10) ...

# --- 11. FINAL PROCUREMENT OVERRIDE SECTION ---
st.divider()
st.subheader("📝 Final Procurement Confirmation")
st.info("The quantities below are suggested based on your stock levels. If Procurement ordered different amounts, please update the 'Final Actual Order' column.")

# Check if there are actually items that need reordering
if reorder_needed:
    # Convert the list of dicts to a clean DataFrame for the editor
    # We use only the essential columns to avoid KeyError
    current_reorder_df = pd.DataFrame(reorder_needed)
    
    # Standardize columns to ensure they exist before indexing
    required_cols = ["Material", "Code", "Order Qty", "Order m²"]
    
    # Initialize or Update session state
    # We check if the Material list has changed to reset the override table if needed
    if 'final_procurement_data' not in st.session_state:
        # Create the initial override table
        override_df = current_reorder_df[required_cols].copy()
        override_df['Final Actual Order (Qty)'] = 0.0
        override_df['Notes/Reason for Change'] = ""
        st.session_state.final_procurement_data = override_df

    # Create the editor using the session state
    procurement_editor = st.data_editor(
        st.session_state.final_procurement_data,
        column_config={
            "Material": st.column_config.TextColumn(disabled=True),
            "Code": st.column_config.TextColumn(disabled=True),
            "Order Qty": st.column_config.TextColumn("Suggested Qty", disabled=True),
            "Order m²": st.column_config.NumberColumn("Suggested m²", disabled=True, format="%.2f"),
            "Final Actual Order (Qty)": st.column_config.NumberColumn(
                "Final Actual Order",
                help="The exact quantity procurement actually purchased",
                min_value=0.0,
                step=0.1,
            ),
            "Notes/Reason for Change": st.column_config.TextColumn(
                "Notes",
                help="e.g., Supplier out of stock, budget constraints, etc."
            )
        },
        hide_index=True,
        use_container_width=True,
        key="procurement_override_editor"
    )

    col_save_1, col_save_2, col_save_3 = st.columns([1, 1, 3])
    
    with col_save_1:
        if st.button("✅ Confirm Order"):
            st.session_state.final_procurement_data = procurement_editor
            st.success("Order Saved!")
            
    with col_save_2:
        if st.button("🔄 Reset Form"):
            if 'final_procurement_data' in st.session_state:
                del st.session_state.final_procurement_data
            st.rerun()
    
    with col_save_3:
        # Export logic
        final_buffer = io.BytesIO()
        with pd.ExcelWriter(final_buffer, engine='openpyxl') as writer:
            procurement_editor.to_excel(writer, index=False, sheet_name='Final Order')
        
        st.download_button(
            label="📥 Download FINAL Procurement List (Excel)",
            data=final_buffer.getvalue(),
            file_name=f"FINAL_Procurement_{selected_month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
else:
    st.write("🟢 No reorders currently required. Final procurement section is empty.")

# --- SIDEBAR UPDATE ---
if 'final_procurement_data' in st.session_state:
    actual_sum = st.session_state.final_procurement_data['Final Actual Order (Qty)'].sum()
    st.sidebar.metric("Confirmed Order Total", f"{actual_sum:.1f}")