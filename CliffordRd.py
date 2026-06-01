import streamlit as st
import pandas as pd
import plotly.express as px
from google.oauth2 import service_account
import gspread
import io
import re
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

# Helper function to extract numerical values safely from text strings
def safe_extract_numeric(series):
    return series.astype(str).str.extract(r'([-+]?\d*\.\d+|\d+)')[0].astype(float).fillna(0.0)

# --- 3. SESSION STATE ---
if 'df' not in st.session_state:
    try:
        st.session_state.df, _ = load_data()
    except Exception as e:
        st.error(f"⚠️ Auth Error: {e}")
        st.stop()

# --- 4. SIDEBAR NAVIGATION ---
st.sidebar.header("Navigation")
app_mode = st.sidebar.radio("Select Mode", [
    "📦 Stock Management", 
    "📋 View Pending Orders",
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
                try: 
                    total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0
                except: 
                    pass
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
                    "Material": mat_name, 
                    "Code": row["Code"],
                    "Suggested Order": f"{gap:.1f} {t['unit']}",
                    "Sug_Qty": gap,
                    "Unit_Type": t['unit'],
                    "m2_Per_Pallet": m2p,
                    "Rolls_on_Pallet": rp
                })
        summary_list.append(mat_sum)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    
    with c3:
        if st.button("💾 Save Counts to Sheet"):
            client = get_gspread_client()
            sheet = client.open_by_key(SPREADSHEET_ID).sheet1
            updates = []
            
            for idx, row in edited_df.iterrows():
                real_idx = st.session_state.df.index[idx] 
                r_p = pd.to_numeric(st.session_state.df.at[real_idx, "Rolls_on_Pallet"], errors='coerce') or 1
                m_p = pd.to_numeric(st.session_state.df.at[real_idx, "m_Square_per_pallet"], errors='coerce') or 0
                
                m2 = round((row[pallet_col] * m_p) + (row[roll_col] * (m_p / r_p)), 2)
                
                for c, v in [(roll_col, row[roll_col]), (pallet_col, row[pallet_col]), (square_col, m2)]:
                    if c in st.session_state.df.columns:
                        col_idx = st.session_state.df.columns.get_loc(c) + 1
                        updates.append({
                            'range': gspread.utils.rowcol_to_a1(real_idx + 2, col_idx), 
                            'values': [[float(v)]] 
                        })
            
            if updates:
                sheet.batch_update(updates)
                st.cache_data.clear()
                st.session_state.df, _ = load_data()
                st.success(f"Stock Updated for {selected_month}!")
                st.rerun()

    if low_stock_alerts:
        with st.expander("🚩 View Low Stock Flags", expanded=True):
            for alert in low_stock_alerts: 
                st.write(alert)

    # --- PROCUREMENT OVERRIDE ---
    st.divider()
    st.subheader("📝 Final Procurement Confirmation")
    if reorder_needed:
        state_key = f"proc_vFinal_{selected_site}_{selected_month}"
        if state_key not in st.session_state:
            df_over = pd.DataFrame(reorder_needed)
            df_over['Final_Actual_Order'] = df_over['Sug_Qty']
            df_over['OrderNotes'] = ""
            st.session_state[state_key] = df_over

        proc_editor = st.data_editor(
            st.session_state[state_key],
            column_config={
                "Material": st.column_config.TextColumn(disabled=True),
                "Code": st.column_config.TextColumn(disabled=True),
                "Suggested Order": st.column_config.TextColumn("System Suggestion", disabled=True),
                "Final_Actual_Order": st.column_config.NumberColumn("Actual Order (Count)", min_value=0.0, step=0.5),
                "OrderNotes": st.column_config.TextColumn("Reason for Change"),
                "Sug_Qty": st.column_config.NumberColumn(disabled=True),
                "Unit_Type": st.column_config.TextColumn(disabled=True),
                "m2_Per_Pallet": st.column_config.NumberColumn(disabled=True),
                "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True, use_container_width=True, key=f"edit_{state_key}"
        )

        if st.button("✅ Save Final Order to Pending List"):
            client = get_gspread_client()
            try:
                pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
                
                rows_to_append = []
                for _, p_row in proc_editor.iterrows():
                    act_qty = float(p_row['Final_Actual_Order'])
                    if act_qty > 0:
                        p_count = act_qty if p_row['Unit_Type'] == "Pallets" else 0.0
                        r_count = act_qty if p_row['Unit_Type'] == "Rolls" else 0.0
                        
                        m2p = float(p_row['m2_Per_Pallet'])
                        rop = float(p_row['Rolls_on_Pallet']) if float(p_row['Rolls_on_Pallet']) > 0 else 1
                        calculated_m2 = round(p_count * m2p + r_count * (m2p / rop), 2)
                        
                        rows_to_append.append([
                            p_row['Material'],
                            p_row['Code'],
                            p_count,
                            r_count,
                            calculated_m2,
                            act_qty,  
                            p_row['OrderNotes']
                        ])
                
                if rows_to_append:
                    pending_sheet.append_rows(rows_to_append)
                    st.success("Order added to Pending List successfully!")
                else:
                    st.warning("Please enter at least one quantity.")
            except Exception as e:
                st.error(f"Error saving order: {e}")

# --- MODE 2: TRENDS & MONTHLY BREAKDOWN ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Analytics")
    
    st.subheader(f"📊 Combined Warehouse Stock Breakdown ({selected_month})")
    if st.button(f"🔄 Generate Combined Chart for {selected_month}"):
        combined_data = []
        for _, row in st.session_state.df.iterrows():
            mat_name = str(row["Material"]).strip()
            total_pallets, total_rolls = 0.0, 0.0
            for site in site_options:
                pallet_col = f"{site}_Pallets {selected_month}"
                roll_col = f"{site}_Rolls {selected_month}"
                if pallet_col in st.session_state.df.columns:
                    try: 
                        total_pallets += float(str(row[pallet_col]).replace(',', '').strip()) if str(row[pallet_col]).strip() != "" else 0
                    except: 
                        pass
                if roll_col in st.session_state.df.columns:
                    try: 
                        total_rolls += float(str(row[roll_col]).replace(',', '').strip()) if str(row[roll_col]).strip() != "" else 0
                    except: 
                        pass
            
            combined_data.append({"Material": mat_name, "Unit Type": "Pallets", "Quantity": total_pallets})
            combined_data.append({"Material": mat_name, "Unit Type": "Rolls", "Quantity": total_rolls})
            
        df_combined = pd.DataFrame(combined_data)
        fig_combined = px.bar(
            df_combined, x="Material", y="Quantity", color="Unit Type", barmode="group",
            title=f"Total Pallets & Rolls across All Warehouses ({selected_month})",
            color_discrete_map={"Pallets": "#1f77b4", "Rolls": "#ff7f0e"}
        )
        st.plotly_chart(fig_combined, use_container_width=True)

    # --- STANDALONE PENDING ORDERS BAR CHART ---
    st.divider()
    st.subheader(f"⏳ Standalone Pending Orders Chart ({selected_month})")

    if st.button(f"📊 Generate Standalone Pending Chart for {selected_month}"):
        client = get_gspread_client()
        try:
            pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
            pending_data = pending_sheet.get_all_records()
            
            if pending_data:
                df_pending = pd.DataFrame(pending_data)
                df_pending.columns = [str(c).strip() for c in df_pending.columns]
                
                p_col = "Pending_Pallets"
                r_col = "Pending_Rolls"
                
                if p_col in df_pending.columns and r_col in df_pending.columns:
                    df_pending[p_col] = safe_extract_numeric(df_pending[p_col])
                    df_pending[r_col] = safe_extract_numeric(df_pending[r_col])
                    
                    grouped_p = df_pending.groupby('Material', as_index=False)[[p_col, r_col]].sum()
                    
                    pending_graph_data = []
                    for _, p_row in grouped_p.iterrows():
                        mat_name = p_row["Material"]
                        pending_graph_data.append({"Material": mat_name, "Unit Type": "Pallets", "Quantity": float(p_row[p_col])})
                        pending_graph_data.append({"Material": mat_name, "Unit Type": "Rolls", "Quantity": float(p_row[r_col])})
                        
                    df_pending_graph = pd.DataFrame(pending_graph_data)
                    fig_standalone_pending = px.bar(
                        df_pending_graph, x="Material", y="Quantity", color="Unit Type", barmode="group",
                        title="Pending Materials Outstanding (All Warehouses Combined)",
                        color_discrete_map={"Pallets": "#2ca02c", "Rolls": "#9467bd"}
                    )
                    st.plotly_chart(fig_standalone_pending, use_container_width=True)
            else:
                st.info("The 'Pending_Orders' sheet is currently empty.")
        except Exception as e:
            st.error(f"Could not read 'Pending_Orders' tab: {e}")

    # --- NEW CHART: COMBINED INVENTORY + PENDING STACKED ROLLS CHART ---
    st.divider()
    st.subheader(f"📈 Total Projected Availability (Stock + Pending Arrivals)")

    if st.button(f"📊 Generate Cumulative Stock & Pending Chart"):
        client = get_gspread_client()
        try:
            # 1. Gather current warehouse metrics
            warehouse_roll_totals = {}
            warehouse_pallet_totals = {}
            
            for _, row in st.session_state.df.iterrows():
                mat_name = str(row["Material"]).strip()
                t_pallets, t_rolls = 0.0, 0.0
                for site in site_options:
                    p_col = f"{site}_Pallets {selected_month}"
                    r_col = f"{site}_Rolls {selected_month}"
                    if p_col in st.session_state.df.columns:
                        try: t_pallets += float(str(row[p_col]).replace(',', '').strip()) if str(row[p_col]).strip() != "" else 0
                        except: pass
                    if r_col in st.session_state.df.columns:
                        try: t_rolls += float(str(row[r_col]).replace(',', '').strip()) if str(row[r_col]).strip() != "" else 0
                        except: pass
                warehouse_roll_totals[mat_name] = t_rolls
                warehouse_pallet_totals[mat_name] = t_pallets

            # 2. Gather matching metrics from pipeline orders tab
            pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
            pending_data = pending_sheet.get_all_records()
            
            pending_roll_totals = {}
            if pending_data:
                df_pend = pd.DataFrame(pending_data)
                df_pend.columns = [str(c).strip() for c in df_pend.columns]
                df_pend["Pending_Pallets"] = safe_extract_numeric(df_pend["Pending_Pallets"])
                df_pend["Pending_Rolls"] = safe_extract_numeric(df_pend["Pending_Rolls"])
                
                # Group data to accommodate multiple duplicate raw entry line items safely
                grouped_pend = df_pend.groupby('Material', as_index=False)[["Pending_Pallets", "Pending_Rolls"]].sum()
                for _, p_row in grouped_pend.iterrows():
                    m_name = str(p_row["Material"]).strip()
                    
                    # Convert incoming pallets to standalone rolls using original dataframe reference metadata
                    matched_row = st.session_state.df[st.session_state.df["Material"].str.strip() == m_name]
                    rop = 1.0
                    if not matched_row.empty:
                        rop = pd.to_numeric(matched_row.iloc[0]["Rolls_on_Pallet"], errors='coerce') or 1.0
                    
                    converted_rolls = (float(p_row["Pending_Pallets"]) * rop) + float(p_row["Pending_Rolls"])
                    pending_roll_totals[m_name] = converted_rolls

            # 3. Restructure layout for a unified stacked data frame matrix
            stacked_chart_records = []
            for _, row in st.session_state.df.iterrows():
                mat_name = str(row["Material"]).strip()
                rop = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
                
                # Translate physical floor pallets into base rolls for uniform stacked tracking
                floor_pallet_rolls = warehouse_pallet_totals.get(mat_name, 0.0) * rop
                floor_loose_rolls = warehouse_roll_totals.get(mat_name, 0.0)
                incoming_pipeline_rolls = pending_roll_totals.get(mat_name, 0.0)
                
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "On-Hand Rolls", "Total Rolls": floor_loose_rolls})
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "On-Hand Pallets (As Rolls)", "Total Rolls": floor_pallet_rolls})
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "Pending Order (As Rolls)", "Total Rolls": incoming_pipeline_rolls})
                
            df_stack = pd.DataFrame(stacked_chart_records)
            
            # 4. Generate the final Plotly stacked configuration
            fig_stacked = px.bar(
                df_stack, x="Material", y="Total Rolls", color="Stock Composition", barmode="stack",
                title=f"Total Projected Multi-Site Roll Volume vs. Pending Pipeline Additions ({selected_month})",
                color_discrete_map={
                    "On-Hand Rolls": "#ff7f0e",               # Orange
                    "On-Hand Pallets (As Rolls)": "#1f77b4",   # Blue
                    "Pending Order (As Rolls)": "#2ca02c"      # Green
                }
            )
            fig_stacked.update_layout(yaxis_title="Total Quantity (Equivalent Rolls)", xaxis_title="Material Type")
            st.plotly_chart(fig_stacked, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error compiling cumulative stacked data metrics: {e}")

# --- MODE 3: RECEIVE GOODS ---
elif app_mode == "🚛 Receive Goods (KPark)":
    st.title("🚛 Goods Receiving (KPark)")
    st.info("Check items that have arrived to update KPark stock metrics automatically.")
    
    client = get_gspread_client()
    try:
        pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
        pending_data = pending_sheet.get_all_records()
        
        if pending_data:
            pending_df = pd.DataFrame(pending_data)
            pending_df.columns = [str(c).strip() for c in pending_df.columns]
            
            p_col = "Pending_Pallets"
            r_col = "Pending_Rolls"
            m2_col = "Pending_m2"
            act_col = "Final_Actual_Order"
            
            if p_col in pending_df.columns:
                pending_df[p_col] = safe_extract_numeric(pending_df[p_col])
            if r_col in pending_df.columns:
                pending_df[r_col] = safe_extract_numeric(pending_df[r_col])
            if m2_col in pending_df.columns:
                pending_df[m2_col] = safe_extract_numeric(pending_df[m2_col])
            if act_col in pending_df.columns:
                pending_df[act_col] = safe_extract_numeric(pending_df[act_col])
                
            if "OrderNotes" in pending_df.columns:
                pending_df.rename(columns={"OrderNotes": "Notes"}, inplace=True)
                
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
                    
                    kp_pallet_col = f"KPark_Pallets {selected_month}"
                    kp_roll_col = f"KPark_Rolls {selected_month}"
                    kp_m2_col = f"KPark_SquareM {selected_month}"
                    
                    idx_p = st.session_state.df.columns.get_loc(kp_pallet_col) + 1
                    idx_r = st.session_state.df.columns.get_loc(kp_roll_col) + 1
                    idx_m = st.session_state.df.columns.get_loc(kp_m2_col) + 1
                    
                    for _, row in received.iterrows():
                        cell = main_sheet.find(str(row["Code"]))
                        
                        incoming_pallets = float(row.get("Pending_Pallets", 0))
                        incoming_rolls = float(row.get("Pending_Rolls", 0))
                        incoming_m2 = float(row.get("Pending_m2", 0))
                        
                        cur_p = float(main_sheet.cell(cell.row, idx_p).value or 0)
                        cur_r = float(main_sheet.cell(cell.row, idx_r).value or 0)
                        cur_m = float(main_sheet.cell(cell.row, idx_m).value or 0)
                        
                        main_sheet.update_cell(cell.row, idx_p, cur_p + incoming_pallets)
                        main_sheet.update_cell(cell.row, idx_r, cur_r + incoming_rolls)
                        main_sheet.update_cell(cell.row, idx_m, cur_m + incoming_m2)
                    
                    # Cleanup Pending list
                    remaining = receive_editor[receive_editor["Received?"] == False].drop(columns=["Received?"])
                    pending_sheet.clear()
                    pending_sheet.append_row(["Material", "Code", "Pending_Pallets", "Pending_Rolls", "Pending_m2", "Final_Actual_Order", "Notes"])
                    if not remaining.empty:
                        pending_sheet.append_rows(remaining.values.tolist())
                    
                    st.success("KPark stock records incremented correctly!")
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
            df_pending.columns = [str(c).strip() for c in df_pending.columns]
            
            p_col = "Pending_Pallets"
            r_col = "Pending_Rolls"
            m2_col = "Pending_m2"
            act_col = "Final_Actual_Order"
            
            # Harmonize column names between Notes and OrderNotes
            if "OrderNotes" in df_pending.columns:
                df_pending.rename(columns={"OrderNotes": "Notes"}, inplace=True)
            elif "Notes" not in df_pending.columns:
                df_pending["Notes"] = ""
                
            notes_col = "Notes"
                
            missing_cols = [c for c in ["Material", "Code", p_col, r_col, m2_col, act_col, notes_col] if c not in df_pending.columns]
            if missing_cols:
                st.error(f"⚠️ Missing columns in Google Sheet: {missing_cols}")
                st.info("Please check that the column headers on your 'Pending_Orders' tab match perfectly.")
                st.stop()

            # Clean and parse metrics by extracting first occurring digit patterns safely
            df_pending[p_col] = safe_extract_numeric(df_pending[p_col])
            df_pending[r_col] = safe_extract_numeric(df_pending[r_col])
            df_pending[m2_col] = safe_extract_numeric(df_pending[m2_col])
            df_pending[act_col] = safe_extract_numeric(df_pending[act_col])
            
            display_order = ["Material", "Code", p_col, r_col, m2_col, act_col, notes_col]
            df_pending = df_pending[display_order]

            # --- KPI METRICS ---
            m1, m2, m3 = st.columns(3)
            m1.metric("Pending Line Items", len(df_pending))
            m2.metric("Total Pending Pallets", f"{df_pending[p_col].sum():,.1f}")
            m3.metric("Total Pending Area", f"{df_pending[m2_col].sum():,.1f} m²")

            st.divider()

            # --- EDITABLE TABLE FOR DELETION ---
            df_pending["Select to Delete"] = False
            editor_cols = ["Select to Delete"] + display_order

            edited_pending = st.data_editor(
                df_pending[editor_cols],
                column_config={
                    "Select to Delete": st.column_config.CheckboxColumn("🗑️", help="Select rows to remove"),
                    "Material": st.column_config.TextColumn("Material", disabled=True),
                    "Code": st.column_config.TextColumn("Code", disabled=True),
                    p_col: st.column_config.NumberColumn("Pending_Pallets", format="%.1f", disabled=True),
                    r_col: st.column_config.NumberColumn("Pending_Rolls", format="%.1f", disabled=True),
                    m2_col: st.column_config.NumberColumn("Pending_m2", format="%.2f", disabled=True),
                    act_col: st.column_config.NumberColumn("Final_Actual_Order", format="%.1f", disabled=True),
                    notes_col: st.column_config.TextColumn("Notes", width="medium", disabled=True)
                },
                hide_index=True,
                use_container_width=True,
                key="pending_manager_editor"
            )

            # --- ACTIONS: DELETE & EXPORT ---
            col_del, col_exp = st.columns([1, 4])
            
            with col_del:
                if st.button("🗑️ Delete Selected", type="secondary"):
                    to_keep = edited_pending[edited_pending["Select to Delete"] == False].drop(columns=["Select to Delete"])
                    pending_sheet.clear()
                    pending_sheet.append_row(["Material", "Code", "Pending_Pallets", "Pending_Rolls", "Pending_m2", "Final_Actual_Order", "Notes"])
                    
                    if not to_keep.empty:
                        pending_sheet.append_rows(to_keep.values.tolist())
                    
                    st.warning("Selected records stripped from the pending ledger tracker.")
                    st.rerun()

            with col_exp:
                csv = df_pending.drop(columns=["Select to Delete"]).to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export Pending List (CSV)",
                    data=csv,
                    file_name=f"Detailed_Pending_Orders_{datetime.now().strftime('%Y-%m-%d')}.csv",
                    mime='text/csv',
                )
        else:
            st.success("✨ All orders have been cleared or received.")
            
    except Exception as e:
        st.error(f"Error accessing 'Pending_Orders': {e}")