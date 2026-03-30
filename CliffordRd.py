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
    display_cols = ["Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet"] + available_cols

    col_config = {
        "Material": st.column_config.TextColumn(pinned=True),
        "Code": st.column_config.TextColumn(disabled=True),
        "Meters_per_Roll": st.column_config.NumberColumn(disabled=True),
        "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
        "m_Square_per_pallet": st.column_config.NumberColumn(disabled=True),
    }
    for col in available_cols:
        col_config[col] = st.column_config.NumberColumn(step=0.5, format="%.1f", disabled=("SquareM" in col))

    edited_df = st.data_editor(st.session_state.df[display_cols], use_container_width=True, hide_index=True, column_config=col_config)

    # REORDER & ALERT LOGIC
    summary_list, low_stock_alerts, reorder_needed = [], [], []
    total_est_weight_kg = 0.0

    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        mat_sum = {"Material": mat_name, "Code": row["Code"]}
        edited_row = edited_df.iloc[index]
        
        # Calculate Gross across all sites
        for metric in ["Rolls", "Pallets", "SquareM"]:
            total = 0
            for site in site_options:
                c_name = f"{site}_{metric} {selected_month}"
                val = edited_row[c_name] if site == selected_site and c_name in edited_row else row.get(c_name, 0)
                try: total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0
                except: pass
            mat_sum[f"Gross {metric}"] = total
        
        # Threshold Checks
        if mat_name in thresholds:
            t = thresholds[mat_name]
            cur = mat_sum[f"Gross {t['unit']}"]
            if cur < t['val']:
                low_stock_alerts.append(f"🚨 **{mat_name}**: {cur} {t['unit']} (Min: {t['val']})")
                gap = max(0.0, float(t['target']) - float(cur))
                m2p = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0
                rp = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1
                
                weight = gap * (WEIGHT_FACTORS["Pallet_Avg_KG"] if t['unit']=="Pallets" else WEIGHT_FACTORS["Roll_Avg_KG"])
                total_est_weight_kg += weight
                
                reorder_needed.append({
                    "Material": mat_name, "Code": row["Code"],
                    "Order Qty": f"{gap:.1f} {t['unit']}",
                    "Order m²": round(gap * (m2p if t['unit']=="Pallets" else m2p/rp), 2)
                })
        summary_list.append(mat_sum)

    # Top Metrics
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    with c3:
        if st.button("💾 Save Counts to Sheet"):
            client = get_gspread_client()
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            updates = []
            for idx, row in edited_df.iterrows():
                r_p = pd.to_numeric(st.session_state.df.at[idx, "Rolls_on_Pallet"], errors='coerce') or 1
                m_p = pd.to_numeric(st.session_state.df.at[idx, "m_Square_per_pallet"], errors='coerce') or 0
                m2 = round((row[pallet_col] * m_p) + (row[roll_col] * (m_p / r_p)), 2)
                for c, v in [(roll_col, row[roll_col]), (pallet_col, row[pallet_col]), (square_col, m2)]:
                    if c in st.session_state.df.columns:
                        col_idx = st.session_state.df.columns.get_loc(c) + 1
                        updates.append({'range': gspread.utils.rowcol_to_a1(idx+2, col_idx), 'values': [[v]]})
            sheet.batch_update(updates)
            st.session_state.df, _ = load_data()
            st.success("Stock Updated!"); st.rerun()

    if low_stock_alerts:
        with st.expander("🚩 View Low Stock Flags", expanded=True):
            for alert in low_stock_alerts: st.write(alert)

    # --- PROCUREMENT OVERRIDE ---
    st.divider()
    st.subheader("📝 Final Procurement Confirmation")
    if reorder_needed:
        state_key = f"proc_vFinal_{selected_site}_{selected_month}"
        if state_key not in st.session_state:
            df_over = pd.DataFrame(reorder_needed)
            df_over['Final Actual Order (Qty)'] = 0.0
            df_over['Notes/Reason for Change'] = ""
            st.session_state[state_key] = df_over

        proc_editor = st.data_editor(
            st.session_state[state_key],
            column_config={
                "Material": st.column_config.TextColumn(disabled=True),
                "Code": st.column_config.TextColumn(disabled=True),
                "Order Qty": st.column_config.TextColumn("Suggested", disabled=True),
                "Order m²": st.column_config.NumberColumn("Sug. m²", disabled=True),
                "Final Actual Order (Qty)": st.column_config.NumberColumn("Actual Order", min_value=0.0),
                "Notes/Reason for Change": st.column_config.TextColumn("Reason for Change")
            },
            hide_index=True, use_container_width=True, key=f"edit_{state_key}"
        )

        if st.button("✅ Save Final Order to Pending List"):
            client = get_gspread_client()
            try:
                pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
                valid_orders = proc_editor[proc_editor['Final Actual Order (Qty)'] > 0].values.tolist()
                if valid_orders:
                    pending_sheet.append_rows(valid_orders)
                    st.success("Order added to Pending List! Ready for receiving mode.")
                else:
                    st.warning("Please enter at least one quantity.")
            except:
                st.error("Error: Ensure a tab named 'Pending_Orders' exists.")

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
                    k_col_name = f"KPark_Pallets {selected_month}"
                    k_col_idx = st.session_state.df.columns.get_loc(k_col_name) + 1
                    
                    for _, row in received.iterrows():
                        cell = main_sheet.find(str(row["Code"]))
                        current_val = float(main_sheet.cell(cell.row, k_col_idx).value or 0)
                        # Add the "Final Actual Order" amount to current stock
                        new_val = current_val + float(row["Final_Actual_Order"])
                        main_sheet.update_cell(cell.row, k_col_idx, new_val)
                    
                    # Cleanup Pending list
                    remaining = receive_editor[receive_editor["Received?"] == False].drop(columns=["Received?"])
                    pending_sheet.clear()
                    pending_sheet.append_row(["Material", "Code", "Order_Qty", "Order_m2", "Final_Actual_Order", "Notes"])
                    if not remaining.empty:
                        pending_sheet.append_rows(remaining.values.tolist())
                    
                    st.success("KPark stock updated successfully!"); st.rerun()
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