import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
import io
from datetime import datetime

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Laminate Stock Manager", layout="wide")

SPREADSHEET_ID = "1Yq-sZ33JsXNUyw_UwYCvSO3CSKdpubZDUtq6_cv86Uo"
API_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

WEIGHT_FACTORS = {"Pallet_Avg_KG": 850.0, "Roll_Avg_KG": 25.0}
CONTAINER_LIMIT_KG = 18000.0

# --- 2. AUTHENTICATION ---
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
        st.error(f"⚠️ Auth Error: {e}"); st.stop()

# ADD THIS LINE HERE:
reorder_needed = []

# --- 4. SIDEBAR NAVIGATION ---
st.sidebar.header("Navigation")
# Update your navigation line to this:
app_mode = st.sidebar.radio("Select Mode", [
    "📦 Stock Management", 
    "📋 View Pending Orders",  # <--- Added this
    "📈 Stock Trends", 
    "🚛 Receive Goods (KPark)"
])

site_options = ["CliffordRd", "KPark", "HarrisDrive"]
selected_site = st.sidebar.selectbox("Select Site", site_options)
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

# --- MODE 1: STOCK MANAGEMENT ---
if app_mode == "📦 Stock Management":
    st.title(f"📦 {selected_site} - {selected_month} Management")
    
    roll_col = f"{selected_site}_Rolls {selected_month}"
    pallet_col = f"{selected_site}_Pallets {selected_month}"
    square_col = f"{selected_site}_SquareM {selected_month}"

    available_cols = [c for c in [roll_col, pallet_col, square_col] if c in st.session_state.df.columns]
    
    # 1. Prepare data frame for editing and introduce the 'Rolls Used' column
    df_to_edit = st.session_state.df.copy()
    
    # 🔥 FIX: Explicitly cast target stock columns to float to handle fractional changes safely
    for col in available_cols:
        if col in df_to_edit.columns:
            df_to_edit[col] = df_to_edit[col].astype(float)

    df_to_edit["Rolls Used"] = 0.0  # Reset daily entry to 0 on load
    
    # Place 'Rolls Used' right after the master specs columns
    display_cols = ["Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet", "Rolls Used"] + available_cols

    col_config = {
        "Material": st.column_config.TextColumn(pinned=True),
        "Code": st.column_config.TextColumn(disabled=True),
        "Meters_per_Roll": st.column_config.NumberColumn(disabled=True),
        "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
        "m_Square_per_pallet": st.column_config.NumberColumn(disabled=True),
        "Rolls Used": st.column_config.NumberColumn("Rolls Used (Daily)", min_value=0.0, step=1.0, format="%.0f"),
    }
    for col in available_cols:
        col_config[col] = st.column_config.NumberColumn(step=0.1, format="%.2f", disabled=("SquareM" in col))

    # Render the editor using our prepared DataFrame
    edited_df = st.data_editor(df_to_edit[display_cols], use_container_width=True, hide_index=True, column_config=col_config)

    # Initialize logic variables
    reorder_needed = [] 
    low_stock_alerts = []
    total_est_weight_kg = 0.0

    # 2. RUN THE CALCULATION LOOP & APPLY USAGE DEDUCTIONS
    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        edited_row = edited_df.iloc[index]
        
        # Pull structural multipliers safely
        m2p = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
        rp = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
        
        # Track usage deductions
        rolls_used = float(edited_row.get("Rolls Used", 0.0))
        
        # Calculate Current Gross across all sites
        gross_val = 0
        if mat_name in thresholds:
            t = thresholds[mat_name]
            unit = t['unit']
            
            # Sum up the specific unit (Pallets or Rolls) across all sites
            for site in site_options:
                c_name = f"{site}_{unit} {selected_month}"
                
                # Fetch baseline current value from the editor row
                val = edited_row[c_name] if site == selected_site and c_name in edited_row else row.get(c_name, 0)
                
                # Apply live usage deduction to the selected site's metrics directly
                if site == selected_site and rolls_used > 0:
                    current_rolls = float(edited_row.get(roll_col, 0.0))
                    
                    # Deduct usage from current site totals
                    if unit == "Rolls":
                        # If this specific variant tracks stock by Rolls
                        val = max(0.0, current_rolls - rolls_used)
                    elif unit == "Pallets":
                        # If tracking by Pallets, convert remaining rolls back into Pallets
                        remaining_rolls = max(0.0, current_rolls - rolls_used)
                        val = max(0.0, remaining_rolls / rp)
                
                try: gross_val += float(val)
                except: pass
            
            # Check against threshold
            if gross_val < t['val']:
                low_stock_alerts.append(f"🚨 **{mat_name}**: {gross_val:.1f} {unit} (Min: {t['val']})")
                gap = max(0.0, float(t['target']) - float(gross_val))
                
                weight = gap * (WEIGHT_FACTORS["Pallet_Avg_KG"] if unit=="Pallets" else WEIGHT_FACTORS["Roll_Avg_KG"])
                total_est_weight_kg += weight
                
                reorder_needed.append({
                    "Material": mat_name, 
                    "Code": row["Code"],
                    "Order Qty": f"{gap:.1f} {unit}",
                    "Order m²": round(gap * (m2p if unit=="Pallets" else m2p/rp), 2)
                })

    # 3. Display Top Metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    with c3:
        if st.button("💾 Save Counts to Sheet"):
            try:
                client = get_gspread_client()
                main_sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                
                # Update our baseline local session state with the edited/deducted rows before committing
                for idx, row in edited_df.iterrows():
                    m_code = str(row["Code"]).strip()
                    r_used = float(row.get("Rolls Used", 0.0))
                    
                    if r_used > 0:
                        # Grab existing values from editor
                        orig_rolls = float(row.get(roll_col, 0.0))
                        rp_val = pd.to_numeric(st.session_state.df.iloc[idx]["Rolls_on_Pallet"], errors='coerce') or 1.0
                        m2p_val = pd.to_numeric(st.session_state.df.iloc[idx]["m_Square_per_pallet"], errors='coerce') or 0.0
                        
                        # Process deductions
                        new_rolls = max(0.0, orig_rolls - r_used)
                        new_pallets = max(0.0, new_rolls / rp_val)
                        new_square_m = round(new_pallets * m2p_val, 2)
                        
                        # Apply down to the temporary dataframe being saved
                        edited_df.at[idx, roll_col] = new_rolls
                        edited_df.at[idx, pallet_col] = new_pallets
                        edited_df.at[idx, square_col] = new_square_m
                
                # Write back the calculated month metrics to Google Sheets
                for col in available_cols:
                    col_idx = st.session_state.df.columns.get_loc(col) + 1
                    for idx, row in edited_df.iterrows():
                        main_sheet.update_cell(idx + 2, col_idx, row[col]) # idx + 2 accounts for 1-based index and header
                
                # 🔥 FIX: Clear the old cached data so the app pulls fresh numbers on rerun
                if 'df' in st.session_state:
                    del st.session_state['df']
                st.success("Stock and Daily Usage Deducted successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")

    if low_stock_alerts:
        with st.expander("🚩 View Low Stock Flags", expanded=True):
            for alert in low_stock_alerts: st.write(alert)

# --- MODE 2: TRENDS ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Trends (Gross)")
    
    # Calculate Gross for all months for a specific material
    target_mat = st.selectbox("Select Material to Track", st.session_state.df["Material"].unique())
    trend_data = []
    
    row = st.session_state.df[st.session_state.df["Material"] == target_mat].iloc[0]
    for m in months:
        gross_pallets = 0
        for site in site_options:
            col = f"{site}_Pallets {m}"
            if col in st.session_state.df.columns:
                try: gross_pallets += float(row[col])
                except: pass
        trend_data.append({"Month": m, "Pallets": gross_pallets})
    
    df_trend = pd.DataFrame(trend_data)
    fig = px.line(df_trend, x="Month", y="Pallets", title=f"Gross Pallet Stock Trend: {target_mat}", markers=True)
    st.plotly_chart(fig, use_container_width=True)

# --- MODE 3: RECEIVE GOODS ---
elif app_mode == "🚛 Receive Goods (KPark)":
    st.title("🚛 Goods Receiving (KPark)")
    st.info("Check items that have arrived to automatically add them to KPark stock.")
    
    client = get_gspread_client()
    try:
        pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
        pending_data = pending_sheet.get_all_records()
        
        if pending_data:
            pending_df = pd.DataFrame(pending_data)
            pending_df["Received?"] = False
            
            receive_editor = st.data_editor(
                pending_df,
                column_config={"Received?": st.column_config.CheckboxColumn("Confirm Arrived")},
                hide_index=True, use_container_width=True
            )
            
            if st.button("🚛 Confirm Arrival & Update KPark Inventory"):
                received = receive_editor[receive_editor["Received?"] == True]
                if not received.empty:
                    main_sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                    
                    # Target current live month column metrics
                    current_month = datetime.now().strftime("%B") 
                    k_pallet_col = f"KPark_Pallets {current_month}"
                    k_roll_col = f"KPark_Rolls {current_month}"
                    k_square_col = f"KPark_SquareM {current_month}"
                    
                    # Verify baseline columns exist in session state master columns
                    if k_pallet_col in st.session_state.df.columns:
                        p_col_idx = st.session_state.df.columns.get_loc(k_pallet_col) + 1
                    else:
                        st.error(f"Could not find column '{k_pallet_col}' in the main sheet.")
                        st.stop()
                        
                    if k_square_col in st.session_state.df.columns:
                        sq_col_idx = st.session_state.df.columns.get_loc(k_square_col) + 1
                    else:
                        st.error(f"Could not find column '{k_square_col}' in the main sheet.")
                        st.stop()

                    for _, row in received.iterrows():
                        # Find matching material row in Google Sheet via unique Code
                        cell = main_sheet.find(str(row["Code"]))
                        
                        # Gather baseline structural specs from main master dataframe 
                        meta_df = st.session_state.df[st.session_state.df["Code"].astype(str).str.strip() == str(row["Code"]).strip()]
                        if meta_df.empty:
                            continue
                        
                        meta_row = meta_df.iloc[0]
                        m2p = pd.to_numeric(meta_row.get("m_Square_per_pallet", 0), errors='coerce') or 0.0
                        rp = pd.to_numeric(meta_row.get("Rolls_on_Pallet", 1), errors='coerce') or 1.0
                        mat_name = str(meta_row.get("Material", ""))
                        
                        # Determine tracking unit type rule setup in threshold dictionaries
                        unit_type = "Pallets"
                        if mat_name in thresholds:
                            unit_type = thresholds[mat_name].get('unit', 'Pallets')
                        
                        # Select appropriate tracking balance unit index destination
                        target_col_idx = p_col_idx
                        if unit_type == "Rolls" and k_roll_col in st.session_state.df.columns:
                            target_col_idx = st.session_state.df.columns.get_loc(k_roll_col) + 1
                        
                        # 1. Calculate and update primary metrics (Pallets or Rolls)
                        current_units = float(main_sheet.cell(cell.row, target_col_idx).value or 0)
                        added_units = float(row["Final_Actual_Order"])
                        new_units = current_units + added_units
                        main_sheet.update_cell(cell.row, target_col_idx, new_units)
                        
                        # 2. Calculate and update complementary Square Meters (m²) automatically
                        current_m2 = float(main_sheet.cell(cell.row, sq_col_idx).value or 0)
                        added_m2 = added_units * (m2p if unit_type == "Pallets" else (m2p / rp))
                        new_m2 = round(current_m2 + added_m2, 2)
                        main_sheet.update_cell(cell.row, sq_col_idx, new_m2)
                    
                    # Cleanup Pending list rows safely
                    remaining = receive_editor[receive_editor["Received?"] == False].drop(columns=["Received?"])
                    pending_sheet.clear()
                    pending_sheet.append_row(["Material", "Code", "Order_Qty", "Order_m2", "Final_Actual_Order", "Notes"])
                    if not remaining.empty:
                        pending_sheet.append_rows(remaining.values.tolist())
                    
                    # Clear local dataframe state cache to reflect live update fields
                    if 'df' in st.session_state:
                        del st.session_state['df']
                    
                    st.success("KPark stock units and square meters updated successfully!")
                    st.rerun()
        else:
            st.write("No pending orders currently in the system.")
            
    except Exception as e:
        st.error(f"Error accessing 'Pending_Orders' tab: {e}")

# --- MODE 4: PENDING ORDER DASHBOARD ---
elif app_mode == "📋 View Pending Orders":
    st.title("📋 Current Pending Orders")
    st.info("View, export, or remove outstanding orders from the system.")

    client = get_gspread_client()
    try:
        pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
        pending_data = pending_sheet.get_all_records()
        
        if pending_data:
            df_pending = pd.DataFrame(pending_data)
            
            # --- KPI METRICS ---
            df_pending['Final_Actual_Order'] = pd.to_numeric(df_pending['Final_Actual_Order'], errors='coerce').fillna(0)
            m1, m2 = st.columns(2)
            m1.metric("Pending Line Items", len(df_pending))
            m2.metric("Total Outstanding Qty", f"{df_pending['Final_Actual_Order'].sum():,.1f}")

            st.divider()

            # --- EDITABLE TABLE FOR DELETION ---
            # We add a temporary column for selection
            df_pending["Select to Delete"] = False
            
            edited_pending = st.data_editor(
                df_pending,
                column_config={
                    "Select to Delete": st.column_config.CheckboxColumn("🗑️", help="Select rows to remove"),
                    "Material": st.column_config.TextColumn("Material", disabled=True),
                    "Code": st.column_config.TextColumn("Code", disabled=True),
                    "Final_Actual_Order": st.column_config.NumberColumn("Qty Ordered", format="%.1f", disabled=True),
                    "Notes": st.column_config.TextColumn("Notes", width="large", disabled=True)
                },
                hide_index=True,
                use_container_width=True,
                key="pending_manager_editor"
            )

            # --- ACTIONS: DELETE & EXPORT ---
            col_del, col_exp = st.columns([1, 4])
            
            with col_del:
                if st.button("🗑️ Delete Selected", type="secondary"):
                    # Keep only rows that WERE NOT selected for deletion
                    to_keep = edited_pending[edited_pending["Select to Delete"] == False].drop(columns=["Select to Delete"])
                    
                    pending_sheet.clear()
                    # Rewrite headers
                    pending_sheet.append_row(["Material", "Code", "Order_Qty", "Order_m2", "Final_Actual_Order", "Notes"])
                    
                    if not to_keep.empty:
                        pending_sheet.append_rows(to_keep.values.tolist())
                    
                    st.warning("Selected orders removed from the pending list.")
                    st.rerun()

            with col_exp:
                csv = df_pending.drop(columns=["Select to Delete"]).to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export Pending List (CSV)",
                    data=csv,
                    file_name=f"Pending_Orders_{datetime.now().strftime('%Y-%m-%d')}.csv",
                    mime='text/csv',
                )

        else:
            st.success("✨ All orders have been cleared or received.")
            
    except Exception as e:
        st.error(f"Error accessing 'Pending_Orders': {e}")