import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
    "129 PBL": {"val": 10, "target": 12, "unit": "Pallets"},
    "129 ABL White": {"val": 5, "target": 7, "unit": "Pallets"},
    "113 ABL White": {"val": 10, "target": 12, "unit": "Pallets"},
    "113 PBL": {"val": 13, "target": 15, "unit": "Pallets"},
    "082 PBL": {"val": 4, "target": 6, "unit": "Pallets"},
    "082 ABL White": {"val": 3, "target": 6, "unit": "Pallets"},
    "082 ABL Silver": {"val": 20, "target": 36, "unit": "Rolls"},
    "129 ABL Silver": {"val": 20, "target": 20, "unit": "Rolls"},
    "113 ABL Silver": {"val": 20, "target": 32, "unit": "Rolls"},
    "JUMBO ROLLS PBL": {"val": 4, "target": 6, "unit": "Pallets"},
    "JUMBO ROLLS ABL White": {"val": 4, "target": 6, "unit": "Pallets"},
    "JUMBO ROLLS Silver": {"val": 1, "target": 2, "unit": "Pallets"}
}

# --- MODE 1: STOCK MANAGEMENT ---
if app_mode == "📦 Stock Management":
    st.title(f"📦 {selected_site} - {selected_month} Stock Management & Daily Usage")
    
    # --- PASSWORD AUTHENTICATION FOR EDITING ---
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False

    with st.sidebar.expander("🔒 Authorization / Admin Mode", expanded=not st.session_state.admin_authenticated):
        if not st.session_state.admin_authenticated:
            pwd_input = st.text_input("Enter Password to Enable Editing:", type="password", key="stock_edit_pwd")
            if st.button("Unlock Stock Editing"):
                # Define your secret password here (e.g., "Bowler2026") or draw from st.secrets["APP_PASSWORD"]
                MASTER_PASSWORD = st.secrets.get("APP_PASSWORD", "BowlerSecure2026")
                
                if pwd_input == MASTER_PASSWORD:
                    st.session_state.admin_authenticated = True
                    st.success("🔓 Access Granted! Editing unlocked.")
                    st.rerun()
                else:
                    st.error("❌ Incorrect Password")
        else:
            st.success("🔓 Authorized for Editing")
            if st.button("Lock Editing"):
                st.session_state.admin_authenticated = False
                st.rerun()

    is_editable = st.session_state.admin_authenticated

    if is_editable:
        st.info("💡 **Admin Mode Active:** Enter daily roll consumption in **Rolls Used Today** or adjust stock levels directly. Click save to record updates.")
    else:
        st.warning("🔒 **Read-Only Mode:** Enter the authorization password in the sidebar to modify stock counts or record daily usage.")

    # Prepare display dataframe with dedicated 'Rolls_Used_Today' column
    df_display = st.session_state.df.copy()
    if "Rolls_Used_Today" not in df_display.columns:
        df_display["Rolls_Used_Today"] = 0.0

    available_cols = [c for c in [roll_col, pallet_col, square_col] if c in df_display.columns]
    display_cols = ["Material", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet", "Rolls_Used_Today"] + available_cols

    col_config = {
        "Material": st.column_config.TextColumn(pinned=True),
        "Code": st.column_config.TextColumn(disabled=True),
        "Meters_per_Roll": st.column_config.NumberColumn(disabled=True),
        "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
        "m_Square_per_pallet": st.column_config.NumberColumn(disabled=True),
        "Rolls_Used_Today": st.column_config.NumberColumn("Rolls Used Today", min_value=0.0, step=1.0, format="%d", disabled=not is_editable, help="Enter rolls consumed today"),
    }
    
    for col in available_cols:
        if "SquareM" in col:
            col_config[col] = st.column_config.NumberColumn("m² Total", format="%.2f", disabled=True)
        elif "Rolls" in col:
            col_config[col] = st.column_config.NumberColumn("Rolls On-Hand", step=1.0, format="%d", disabled=not is_editable)
        elif "Pallets" in col:
            col_config[col] = st.column_config.NumberColumn("Pallets On-Hand", step=0.5, format="%.1f", disabled=not is_editable)

    # Data Editor (Disabled unless authorized)
    edited_df = st.data_editor(
        df_display[display_cols], 
        use_container_width=True, 
        hide_index=True, 
        column_config=col_config,
        key="daily_usage_editor"
    )

    # REORDER & ALERT LOGIC
    summary_list, low_stock_alerts, reorder_needed = [], [], []
    total_est_weight_kg = 0.0

    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        mat_sum = {"Material": mat_name, "Code": row["Code"]}
        edited_row = edited_df.iloc[index]
        
        for metric in ["Rolls", "Pallets", "SquareM"]:
            total = 0.0
            for site in site_options:
                c_name = f"{site}_{metric} {selected_month}"
                val = edited_row[c_name] if site == selected_site and c_name in edited_row else row.get(c_name, 0)
                try: 
                    total += float(str(val).replace(',', '').strip()) if str(val).strip() != "" else 0.0
                except: 
                    pass
            mat_sum[f"Gross {metric}"] = total
        
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

    # Save Button & Metrics
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    
    with c3:
        if is_editable:
            if st.button("💾 Save Live Daily Stock Counts"):
                client = get_gspread_client()
                sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                updates = []
                
                for idx, row in edited_df.iterrows():
                    real_idx = st.session_state.df.index[idx] 
                    r_p = pd.to_numeric(st.session_state.df.at[real_idx, "Rolls_on_Pallet"], errors='coerce') or 1.0
                    m_p = pd.to_numeric(st.session_state.df.at[real_idx, "m_Square_per_pallet"], errors='coerce') or 0.0
                    
                    curr_rolls = float(row[roll_col]) if str(row[roll_col]).strip() != "" else 0.0
                    curr_pallets = float(row[pallet_col]) if str(row[pallet_col]).strip() != "" else 0.0
                    used_today = float(row["Rolls_Used_Today"]) if str(row["Rolls_Used_Today"]).strip() != "" else 0.0

                    net_rolls = curr_rolls - used_today

                    if net_rolls < 0:
                        pallets_to_break = float(int(abs(net_rolls) // r_p) + 1)
                        if curr_pallets >= pallets_to_break:
                            curr_pallets -= pallets_to_break
                            net_rolls += (pallets_to_break * r_p)
                        else:
                            net_rolls = 0.0

                    new_m2 = round((curr_pallets * m_p) + (net_rolls * (m_p / r_p)), 2)

                    for c, v in [(roll_col, net_rolls), (pallet_col, curr_pallets), (square_col, new_m2)]:
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
                    st.success(f"✅ Daily usage deducted and live stock figures updated for {selected_month}!")
                    st.rerun()
        else:
            st.button("💾 Save Live Daily Stock Counts", disabled=True, help="Unlock editing in sidebar to save changes")

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
                "Final_Actual_Order": st.column_config.NumberColumn("Actual Order (Count)", min_value=0.0, step=0.5, disabled=not is_editable),
                "OrderNotes": st.column_config.TextColumn("Reason for Change", disabled=not is_editable),
                "Sug_Qty": st.column_config.NumberColumn(disabled=True),
                "Unit_Type": st.column_config.TextColumn(disabled=True),
                "m2_Per_Pallet": st.column_config.NumberColumn(disabled=True),
                "Rolls_on_Pallet": st.column_config.NumberColumn(disabled=True),
            },
            hide_index=True, use_container_width=True, key=f"edit_{state_key}"
        )

        if is_editable:
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
        else:
            st.button("✅ Save Final Order to Pending List", disabled=True)
                
# --- MODE 2: TRENDS & MONTHLY BREAKDOWN ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Analytics")
    
    # 1. COMBINED WAREHOUSE STOCK BREAKDOWN
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

    # 2. HISTORICAL MONTHLY MATERIAL USAGE & CONSUMPTION

    # --- CURRENT MONTH REAL-TIME USAGE TO-DATE ---
    st.divider()
    st.subheader(f"⚡ Live Usage To-Date ({selected_month})")
    st.info(f"Compares opening stock from the previous month against current recorded counts in **{selected_month}**.")

    # Find the previous month index to set opening baseline
    month_idx = months.index(selected_month)
    prev_month = months[month_idx - 1] if month_idx > 0 else months[0]

    live_usage_records = []
    
    for _, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        rop = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce')
        rop = rop if pd.notnull(rop) and rop > 0 else 1.0

        # Opening Baseline = Previous Month's Total Stock
        opening_pallets, opening_rolls = 0.0, 0.0
        for site in site_options:
            p_col = f"{site}_Pallets {prev_month}"
            r_col = f"{site}_Rolls {prev_month}"
            if p_col in st.session_state.df.columns:
                try: opening_pallets += float(str(row[p_col]).replace(',', '').strip()) if str(row[p_col]).strip() != "" else 0
                except: pass
            if r_col in st.session_state.df.columns:
                try: opening_rolls += float(str(row[r_col]).replace(',', '').strip()) if str(row[r_col]).strip() != "" else 0
                except: pass
        
        opening_total_eq = opening_pallets + (opening_rolls / rop)

        # Current Stock = Selected Month's Counts
        current_pallets, current_rolls = 0.0, 0.0
        for site in site_options:
            p_col = f"{site}_Pallets {selected_month}"
            r_col = f"{site}_Rolls {selected_month}"
            if p_col in st.session_state.df.columns:
                try: current_pallets += float(str(row[p_col]).replace(',', '').strip()) if str(row[p_col]).strip() != "" else 0
                except: pass
            if r_col in st.session_state.df.columns:
                try: current_rolls += float(str(row[r_col]).replace(',', '').strip()) if str(row[r_col]).strip() != "" else 0
                except: pass
        
        current_total_eq = current_pallets + (current_rolls / rop)

        # Usage To-Date (Stock Drop from Previous Month End)
        used_todate = max(0.0, opening_total_eq - current_total_eq)

        live_usage_records.append({
            "Material": mat_name,
            f"Opening Stock ({prev_month})": round(opening_total_eq, 2),
            f"Current Stock ({selected_month})": round(current_total_eq, 2),
            "Estimated Used To-Date (Eq Pallets)": round(used_todate, 2)
        })

    if live_usage_records:
        df_live = pd.DataFrame(live_usage_records)
        
        # Summary Metrics
        m1, m2 = st.columns(2)
        total_start = df_live[f"Opening Stock ({prev_month})"].sum()
        total_used = df_live["Estimated Used To-Date (Eq Pallets)"].sum()
        
        m1.metric(f"Opening Baseline Stock ({prev_month})", f"{total_start:,.1f} Pallets")
        m2.metric(f"{selected_month} Consumption To-Date", f"{total_used:,.1f} Pallets")

        # Visual Breakdown Chart
        fig_live = px.bar(
            df_live[df_live["Estimated Used To-Date (Eq Pallets)"] > 0],
            x="Material",
            y="Estimated Used To-Date (Eq Pallets)",
            title=f"Material Consumption To-Date for {selected_month} (vs. {prev_month} Baseline)",
            color_discrete_sequence=["#2ca02c"]
        )
        fig_live.update_layout(yaxis_title="Used Quantity (Equivalent Pallets)", xaxis_title="Material Type")
        st.plotly_chart(fig_live, use_container_width=True)

        # Detail Table
        st.dataframe(df_live, use_container_width=True)
        
    st.divider()
    st.subheader("📅 Monthly Material Consumption & Historical Trends")
    st.info("Tracks material consumption (stock drops) and flags months where new stock was delivered.")

    selected_months_trend = st.multiselect(
        "Select Months to Compare Usage (Select at least 2 consecutive months):", 
        months, 
        default=["June", "July"]
    )

    if st.button("📊 Calculate Monthly Material Consumption"):
        if len(selected_months_trend) < 2:
            st.warning("Please select at least two consecutive months to calculate consumption.")
        else:
            ordered_months = [m for m in months if m in selected_months_trend]
            consumption_records = []

            for i in range(1, len(ordered_months)):
                prev_m = ordered_months[i - 1]
                curr_m = ordered_months[i]

                for _, row in st.session_state.df.iterrows():
                    mat_name = str(row["Material"]).strip()
                    rop = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce')
                    rop = rop if pd.notnull(rop) and rop > 0 else 1.0

                    # Calculate Previous Month Total (Equivalent Pallets)
                    prev_p, prev_r = 0.0, 0.0
                    for site in site_options:
                        p_col = f"{site}_Pallets {prev_m}"
                        r_col = f"{site}_Rolls {prev_m}"
                        if p_col in st.session_state.df.columns:
                            try: prev_p += float(str(row[p_col]).replace(',', '').strip()) if str(row[p_col]).strip() != "" else 0
                            except: pass
                        if r_col in st.session_state.df.columns:
                            try: prev_r += float(str(row[r_col]).replace(',', '').strip()) if str(row[r_col]).strip() != "" else 0
                            except: pass
                    prev_total_eq = prev_p + (prev_r / rop)

                    # Calculate Current Month Total (Equivalent Pallets)
                    curr_p, curr_r = 0.0, 0.0
                    for site in site_options:
                        p_col = f"{site}_Pallets {curr_m}"
                        r_col = f"{site}_Rolls {curr_m}"
                        if p_col in st.session_state.df.columns:
                            try: curr_p += float(str(row[p_col]).replace(',', '').strip()) if str(row[p_col]).strip() != "" else 0
                            except: pass
                        if r_col in st.session_state.df.columns:
                            try: curr_r += float(str(row[r_col]).replace(',', '').strip()) if str(row[r_col]).strip() != "" else 0
                            except: pass
                    curr_total_eq = curr_p + (curr_r / rop)

                    # Calculate Net Change
                    delta = prev_total_eq - curr_total_eq

                    if delta > 0:
                        net_consumed = delta
                        stock_added = 0.0
                        status = "Consumed"
                    elif delta < 0:
                        net_consumed = 0.0
                        stock_added = abs(delta)
                        status = "Stock Added / Delivery"
                    else:
                        net_consumed = 0.0
                        stock_added = 0.0
                        status = "No Change"

                    consumption_records.append({
                        "Material": mat_name,
                        "Period": f"{prev_m} -> {curr_m}",
                        "Start Stock (Eq Pallets)": round(prev_total_eq, 2),
                        "End Stock (Eq Pallets)": round(curr_total_eq, 2),
                        "Net Consumption (Eq Pallets)": round(net_consumed, 2),
                        "Stock Added (Eq Pallets)": round(stock_added, 2),
                        "Movement Status": status
                    })

            if consumption_records:
                df_usage = pd.DataFrame(consumption_records)

                # Filter chart to show actual consumption
                fig_usage = px.bar(
                    df_usage[df_usage["Net Consumption (Eq Pallets)"] > 0], 
                    x="Material", 
                    y="Net Consumption (Eq Pallets)", 
                    color="Period", 
                    barmode="group",
                    title="Monthly Material Consumption (Net Pallets Consumed)"
                )
                fig_usage.update_layout(yaxis_title="Used Quantity (Equivalent Pallets)", xaxis_title="Material Type")
                st.plotly_chart(fig_usage, use_container_width=True)

                # Detailed Table with Delivery Callouts
                st.subheader("📋 Movement Ledger Details")
                st.dataframe(
                    df_usage.style.map(
                        lambda val: 'background-color: #d4edda' if val == "Stock Added / Delivery" else '', 
                        subset=['Movement Status']
                    ), 
                    use_container_width=True
                )

    # 3. STANDALONE PENDING ORDERS BAR CHART
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

    # 4. COMBINED INVENTORY + PENDING STACKED PALLETS CHART WITH TARGET MARKERS
    st.divider()
    st.subheader(f"📈 Total Projected Availability (Stock + Pending Arrivals in Pallets)")

    if st.button(f"📊 Generate Cumulative Stock & Pending Chart"):
        client = get_gspread_client()
        try:
            # Gather current warehouse metrics
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

            # Gather pipeline orders
            pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
            pending_data = pending_sheet.get_all_records()
            
            pending_pallet_breakdown = {}
            if pending_data:
                df_pend = pd.DataFrame(pending_data)
                df_pend.columns = [str(c).strip() for c in df_pend.columns]
                df_pend["Pending_Pallets"] = safe_extract_numeric(df_pend["Pending_Pallets"])
                df_pend["Pending_Rolls"] = safe_extract_numeric(df_pend["Pending_Rolls"])
                
                grouped_pend = df_pend.groupby('Material', as_index=False)[["Pending_Pallets", "Pending_Rolls"]].sum()
                for _, p_row in grouped_pend.iterrows():
                    m_name = str(p_row["Material"]).strip()
                    
                    matched_row = st.session_state.df[st.session_state.df["Material"].str.strip() == m_name]
                    rop = 1.0
                    if not matched_row.empty:
                        rop = pd.to_numeric(matched_row.iloc[0]["Rolls_on_Pallet"], errors='coerce') or 1.0
                    
                    pending_pallet_breakdown[m_name] = {
                        "Direct_Pallets": float(p_row["Pending_Pallets"]),
                        "Rolls_As_Pallets": float(p_row["Pending_Rolls"]) / rop
                    }

            # Build stacked records with Rolls-to-Pallet converted target levels
            stacked_chart_records = []
            for _, row in st.session_state.df.iterrows():
                mat_name = str(row["Material"]).strip()
                rop = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
                
                t = thresholds.get(mat_name, {"target": 0.0, "unit": "Pallets"})
                raw_target = float(t.get("target", 0.0))
                
                if t.get("unit") == "Rolls":
                    target_qty = raw_target / rop
                else:
                    target_qty = raw_target
                
                floor_pallets = warehouse_pallet_totals.get(mat_name, 0.0)
                floor_loose_rolls_as_pallets = warehouse_roll_totals.get(mat_name, 0.0) / rop
                
                pipeline_data = pending_pallet_breakdown.get(mat_name, {"Direct_Pallets": 0.0, "Rolls_As_Pallets": 0.0})
                incoming_pallets_total = pipeline_data["Direct_Pallets"] + pipeline_data["Rolls_As_Pallets"]
                
                total_projected = floor_pallets + floor_loose_rolls_as_pallets + incoming_pallets_total
                deficit = max(0.0, target_qty - total_projected)

                for comp_name, qty in [
                    ("On-Hand Pallets", floor_pallets),
                    ("On-Hand Loose Rolls (As Pallets)", floor_loose_rolls_as_pallets),
                    ("Pending Orders (As Pallets)", incoming_pallets_total)
                ]:
                    stacked_chart_records.append({
                        "Material": mat_name, 
                        "Stock Composition": comp_name, 
                        "Total Pallets": qty,
                        "Target Amount": target_qty,
                        "Deficit Below Target": deficit,
                        "Total Projected": total_projected
                    })

            df_stack = pd.DataFrame(stacked_chart_records)

            fig_stacked = px.bar(
                df_stack, x="Material", y="Total Pallets", color="Stock Composition", barmode="stack",
                title=f"Total Projected Multi-Site Volume vs. Pending Pipeline Additions ({selected_month})",
                custom_data=["Target Amount", "Deficit Below Target", "Total Projected"],
                color_discrete_map={
                    "On-Hand Loose Rolls (As Pallets)": "#ff7f0e",
                    "On-Hand Pallets": "#1f77b4",
                    "Pending Orders (As Pallets)": "#2ca02c"
                }
            )
            
            fig_stacked.update_traces(
                hovertemplate=(
                    "<b>%{x}</b><br>" +
                    "Composition: %{fullData.name}<br>" +
                    "Category Quantity: %{y:.1f}<br>" +
                    "------------------------------<br>" +
                    "🎯 <b>Target Amount:</b> %{customdata[0]:.1f}<br>" +
                    "📊 <b>Total Projected:</b> %{customdata[2]:.1f}<br>" +
                    "🚨 <b>Deficit Below Target:</b> %{customdata[1]:.1f}<br>" +
                    "<extra></extra>"
                )
            )

            df_targets = df_stack.groupby("Material", as_index=False)["Target Amount"].first()

            fig_stacked.add_trace(
                go.Scatter(
                    x=df_targets["Material"],
                    y=df_targets["Target Amount"],
                    mode="markers",
                    name="Target Level",
                    marker=dict(color="red", size=10, symbol="line-ew-open", line=dict(width=3)),
                    hovertemplate="Target Level: %{y:.1f} Pallets<extra></extra>"
                )
            )

            fig_stacked.update_layout(yaxis_title="Total Quantity (Equivalent Pallets)", xaxis_title="Material Type")
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

            df_pending[p_col] = safe_extract_numeric(df_pending[p_col])
            df_pending[r_col] = safe_extract_numeric(df_pending[r_col])
            df_pending[m2_col] = safe_extract_numeric(df_pending[m2_col])
            df_pending[act_col] = safe_extract_numeric(df_pending[act_col])
            
            display_order = ["Material", "Code", p_col, r_col, m2_col, act_col, notes_col]
            df_pending = df_pending[display_order]

            m1, m2, m3 = st.columns(3)
            m1.metric("Pending Line Items", len(df_pending))
            m2.metric("Total Pending Pallets", f"{df_pending[p_col].sum():,.1f}")
            m3.metric("Total Pending Area", f"{df_pending[m2_col].sum():,.1f} m²")

            st.divider()

            df_pending["Select to Delete"] = False
            editor_cols = ["Select to Delete"] + display_order

            edited_pending = st.data_editor(
                df_pending[editor_cols],
                column_config={
                    "Select to Delete": st.column_config.CheckboxColumn("🗑️", help="Select rows to remove"),
                    "Material": st.column_config.TextColumn("Material", disabled=True),
                    "Code": st.column_config.TextColumn("Code", disabled=True),
                    "Pending_Pallets": st.column_config.NumberColumn("Pending_Pallets", format="%.1f", disabled=True),
                    "Pending_Rolls": st.column_config.NumberColumn("Pending_Rolls", format="%.1f", disabled=True),
                    "Pending_m2": st.column_config.NumberColumn("Pending_m2", format="%.2f", disabled=True),
                    "Final_Actual_Order": st.column_config.NumberColumn("Final_Actual_Order", format="%.1f", disabled=True),
                    "Notes": st.column_config.TextColumn("Notes", width="medium", disabled=True)
                },
                hide_index=True,
                use_container_width=True,
                key="pending_manager_editor"
            )

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