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
    
    # Prepare dataframe and explicitly cast stock columns to float
    df_to_edit = st.session_state.df.copy()
    for col in available_cols:
        if col in df_to_edit.columns:
            df_to_edit[col] = pd.to_numeric(df_to_edit[col], errors='coerce').fillna(0.0)

    df_to_edit["Rolls Used"] = 0.0  
    
    display_cols = ["Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet", "Rolls Used"] + available_cols

    col_config = {
        "Material": st.column_config.TextColumn(pinned=True),
        "Code": st.column_config.TextColumn(disabled=True),
        "Meters_per_Roll": st.column_config.NumberColumn(disabled=True),
        "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
        "m_Square_per_pallet": st.column_config.NumberColumn(disabled=True),
        "Rolls Used": st.column_config.NumberColumn("Rolls Used (Daily)", min_value=0.0, step=0.5, format="%.1f"),
    }
    for col in available_cols:
        col_config[col] = st.column_config.NumberColumn(step=0.01, format="%.2f", disabled=("SquareM" in col))

    edited_df = st.data_editor(df_to_edit[display_cols], use_container_width=True, hide_index=True, column_config=col_config)

    # REORDER & ALERT LOGIC
    summary_list, low_stock_alerts, reorder_needed = [], [], []
    total_est_weight_kg = 0.0

    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        mat_sum = {"Material": mat_name, "Code": row["Code"]}
        edited_row = edited_df.iloc[index]
        
        m2p = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
        rp = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
        
        # Calculate Gross across all sites including live user input modifications
        for metric in ["Rolls", "Pallets", "SquareM"]:
            total = 0
            for site in site_options:
                c_name = f"{site}_{metric} {selected_month}"
                val = edited_row[c_name] if site == selected_site and c_name in edited_row else row.get(c_name, 0.0)
                
                # Apply structural modifications to the view loop so threshold counters stay sync'd
                if site == selected_site and metric in ["Rolls", "Pallets"]:
                    site_rolls_col = f"{selected_site}_Rolls {selected_month}"
                    site_pallets_col = f"{selected_site}_Pallets {selected_month}"
                    
                    s_rolls = pd.to_numeric(edited_row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
                    s_pallets = pd.to_numeric(edited_row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
                    r_used = pd.to_numeric(edited_row.get("Rolls Used", 0.0), errors='coerce') or 0.0
                    
                    if r_used > 0:
                        if s_rolls >= r_used:
                            s_rolls -= r_used
                        else:
                            deficit = r_used - s_rolls
                            s_rolls = 0.0
                            pallets_to_break = int((deficit + rp - 0.001) // rp)
                            if s_pallets >= pallets_to_break:
                                s_pallets -= pallets_to_break
                                s_rolls = (pallets_to_break * rp) - deficit
                            else:
                                s_pallets, s_rolls = 0.0, 0.0
                    
                    if s_rolls >= rp:
                        extra_pallets = int(s_rolls // rp)
                        s_pallets += extra_pallets
                        s_rolls = s_rolls % rp
                        
                    val = s_rolls if metric == "Rolls" else s_pallets
                
                try:
                    total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0
                except:
                    pass
            mat_sum[f"Gross {metric}"] = total
        
        # Recalculate dynamic square meters for gross calculations to ensure visual alerts evaluate correctly
        mat_sum["Gross SquareM"] = round((mat_sum["Gross Pallets"] * m2p) + (mat_sum["Gross Rolls"] * (m2p / rp)), 2)
        
        # Threshold Checks
        if mat_name in thresholds:
            t = thresholds[mat_name]
            cur = mat_sum[f"Gross {t['unit']}"]
            
            # Form fractional or absolute totals based on expected verification context rules
            if t['unit'] == "Pallets":
                cur_eval = mat_sum["Gross Pallets"] + (mat_sum["Gross Rolls"] / rp)
            else:
                cur_eval = mat_sum["Gross Rolls"] + (mat_sum["Gross Pallets"] * rp)
                
            if cur_eval < t['val']:
                low_stock_alerts.append(f"🚨 **{mat_name}**: {cur_eval:.2f} {t['unit']} (Min: {t['val']})")
                gap = max(0.0, float(t['target']) - float(cur_eval))
                
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
            try:
                client = get_gspread_client()
                sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                updates = []
                
                for idx, row in edited_df.iterrows():
                    real_idx = st.session_state.df.index[idx] 
                    r_p = pd.to_numeric(st.session_state.df.at[real_idx, "Rolls_on_Pallet"], errors='coerce') or 1
                    m_p = pd.to_numeric(st.session_state.df.at[real_idx, "m_Square_per_pallet"], errors='coerce') or 0
                    
                    r_used = pd.to_numeric(row.get("Rolls Used", 0.0), errors='coerce') or 0.0
                    orig_rolls = pd.to_numeric(row.get(roll_col, 0.0), errors='coerce') or 0.0
                    orig_pallets = pd.to_numeric(row.get(pallet_col, 0.0), errors='coerce') or 0.0
                    
                    final_rolls = orig_rolls
                    final_pallets = orig_pallets
                    
                    # Process loose roll asset deduction cascades
                    if r_used > 0:
                        if final_rolls >= r_used:
                            final_rolls -= r_used
                        else:
                            deficit = r_used - final_rolls
                            final_rolls = 0.0
                            pallets_to_break = int((deficit + r_p - 0.001) // r_p)
                            
                            if final_pallets >= pallets_to_break:
                                final_pallets -= pallets_to_break
                                final_rolls = (pallets_to_break * r_p) - deficit
                            else:
                                final_pallets, final_rolls = 0.0, 0.0
                    
                    # Consolidate standard rolling package limits
                    if final_rolls >= r_p:
                        extra_pallets = int(final_rolls // r_p)
                        final_pallets += extra_pallets
                        final_rolls = final_rolls % r_p
                    
                    # Calculate square meters over both dimensions independently
                    m2 = round((final_pallets * m_p) + (final_rolls * (m_p / r_p)), 2)
                    
                    # Store variables inside the change structural configuration updates mapping array
                    for c, v in [(roll_col, final_rolls), (pallet_col, final_pallets), (square_col, m2)]:
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
            except Exception as e:
                st.error(f"Save failed: {e}")

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
                            p_row['Sug_Qty'],
                            calculated_m2,
                            act_qty,
                            p_row['OrderNotes']
                        ])
                
                if rows_to_append:
                    pending_sheet.append_rows(rows_to_append)
                    st.success("Orders added to Pending tab successfully!")
                    if state_key in st.session_state:
                        del st.session_state[state_key]
                    st.rerun()
            except Exception as e:
                st.error(f"Failed to append records: {e}")

# --- MODE 2: TRENDS ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Trends (Gross)")
    
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
                    
                    current_month = datetime.now().strftime("%B") 
                    k_pallet_col = f"KPark_Pallets {current_month}"
                    k_roll_col = f"KPark_Rolls {current_month}"
                    k_square_col = f"KPark_SquareM {current_month}"
                    
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
                        cell = main_sheet.find(str(row["Code"]))
                        meta_df = st.session_state.df[st.session_state.df["Code"].astype(str).str.strip() == str(row["Code"]).strip()]
                        if meta_df.empty:
                            continue
                        
                        meta_row = meta_df.iloc[0]
                        m2p = pd.to_numeric(meta_row.get("m_Square_per_pallet", 0), errors='coerce') or 0.0
                        rp = pd.to_numeric(meta_row.get("Rolls_on_Pallet", 1), errors='coerce') or 1.0
                        mat_name = str(meta_row.get("Material", ""))
                        
                        unit_type = "Pallets"
                        if mat_name in thresholds:
                            unit_type = thresholds[mat_name].get('unit', 'Pallets')
                        
                        target_col_idx = p_col_idx
                        if unit_type == "Rolls" and k_roll_col in st.session_state.df.columns:
                            target_col_idx = st.session_state.df.columns.get_loc(k_roll_col) + 1
                        
                        current_units = float(main_sheet.cell(cell.row, target_col_idx).value or 0)
                        added_units = float(row["Final_Actual_Order"])
                        new_units = current_units + added_units
                        main_sheet.update_cell(cell.row, target_col_idx, new_units)
                        
                        current_m2 = float(main_sheet.cell(cell.row, sq_col_idx).value or 0)
                        added_m2 = added_units * (m2p if unit_type == "Pallets" else (m2p / rp))
                        new_m2 = round(current_m2 + added_m2, 2)
                        main_sheet.update_cell(cell.row, sq_col_idx, new_m2)
                    
                    remaining = receive_editor[receive_editor["Received?"] == False].drop(columns=["Received?"])
                    pending_sheet.clear()
                    pending_sheet.append_row(["Material", "Code", "Order_Qty", "Order_m2", "Final_Actual_Order", "Notes"])
                    if not remaining.empty:
                        pending_sheet.append_rows(remaining.values.tolist())
                    
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
            df_pending['Final_Actual_Order'] = pd.to_numeric(df_pending['Final_Actual_Order'], errors='coerce').fillna(0)
            
            m1, m2 = st.columns(2)
            m1.metric("Pending Line Items", len(df_pending))
            m2.metric("Total Outstanding Qty", f"{df_pending['Final_Actual_Order'].sum():,.1f}")

            st.divider()

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
                hide_index=True, use_container_width=True, key="pending_manager_editor"
            )

            col_del, col_exp = st.columns([1, 4])
            with col_del:
                if st.button("🗑️ Delete Selected", type="secondary"):
                    to_keep = edited_pending[edited_pending["Select to Delete"] == False].drop(columns=["Select to Delete"])
                    pending_sheet.clear()
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