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


--- MODE 1: STOCK MANAGEMENT ---
if app_mode == "📦 Stock Management":
    st.title(f"📦 {selected_site} - {selected_month} Management")

    # 1. Define your secure password (keep this safe!)
    # You can change "BowlerSecure2026" to whatever password you want
    SECRET_PASSWORD = "BowlerSecure2026" 
    
    # 2. Add the password input field to the Sidebar
    user_password = st.sidebar.text_input(
        "🔑 Enter Editor Password", 
        type="password", 
        help="Type the password to unlock saving and editing features."
    )
    
    # 3. Check if the password matches
    is_editor = (user_password == SECRET_PASSWORD)

    # 4. Show a visual warning if they are in read-only mode
    if not is_editor:
        st.sidebar.info("🔒 App is locked.")
        st.warning("⚠️ You are in **Read-Only** mode. Please enter the password in the sidebar to edit or save counts.")

    # Inform unauthorized users they are in read-only mode
    if not is_editor:
        st.warning("⚠️ You are in **Read-Only** mode. Only authorized users can edit quantities or save changes.")
    
    roll_col = f"{selected_site}_Rolls {selected_month}"
    pallet_col = f"{selected_site}_Pallets {selected_month}"
    square_col = f"{selected_site}_SquareM {selected_month}"

    available_cols = [c for c in [roll_col, pallet_col, square_col] if c in st.session_state.df.columns]
    
    df_to_edit = st.session_state.df.copy()
    for col in available_cols:
        if col in df_to_edit.columns:
            df_to_edit[col] = df_to_edit[col].astype(float)

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

    edited_df = st.data_editor(
        df_to_edit[display_cols], 
        use_container_width=True, 
        hide_index=True, 
        column_config=col_config,
        key="stock_editor",
        disabled=not is_editor  # <-- Add this: locks the whole grid if not an editor
    )

    reorder_needed = [] 
    low_stock_alerts = []
    total_est_weight_kg = 0.0

    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        edited_row = edited_df.iloc[index]
        
        m2p = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
        rp = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
        
        gross_val = 0
        if mat_name in thresholds:
            t = thresholds[mat_name]
            unit = t['unit']
            
            for site in site_options:
                site_rolls_col = f"{site}_Rolls {selected_month}"
                site_pallets_col = f"{site}_Pallets {selected_month}"
                
                s_rolls = edited_row[site_rolls_col] if site == selected_site and site_rolls_col in edited_row else row.get(site_rolls_col, 0.0)
                s_pallets = edited_row[site_pallets_col] if site == selected_site and site_pallets_col in edited_row else row.get(site_pallets_col, 0.0)
                
                try:
                    s_rolls = float(s_rolls)
                    s_pallets = float(s_pallets)
                except:
                    s_rolls, s_pallets = 0.0, 0.0

                if site == selected_site:
                    r_used = float(edited_row.get("Rolls Used", 0.0))
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

                if unit == "Rolls":
                    gross_val += s_rolls + (s_pallets * rp)
                elif unit == "Pallets":
                    gross_val += s_pallets + (s_rolls / rp)
            
            if gross_val < t['val']:
                low_stock_alerts.append(f"🚨 **{mat_name}**: {gross_val:.2f} {unit} (Min: {t['val']})")
                gap = max(0.0, float(t['target']) - float(gross_val))
                
                weight = gap * (WEIGHT_FACTORS["Pallet_Avg_KG"] if unit=="Pallets" else WEIGHT_FACTORS["Roll_Avg_KG"])
                total_est_weight_kg += weight
                
                reorder_needed.append({
                    "Material": mat_name, 
                    "Code": row["Code"],
                    "Order Qty": f"{gap:.2f} {unit}",
                    "Order m²": round(gap * (m2p if unit=="Pallets" else m2p/rp), 2)
                })

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    with c3:
        if st.button("💾 Save Counts to Sheet", disabled=not is_editor):
            try:
                client = get_gspread_client()
                main_sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                
                for idx, row in edited_df.iterrows():
                    r_used = pd.to_numeric(row.get("Rolls Used", 0.0), errors='coerce') or 0.0
                    orig_rolls = pd.to_numeric(row.get(roll_col, 0.0), errors='coerce') or 0.0
                    orig_pallets = pd.to_numeric(row.get(pallet_col, 0.0), errors='coerce') or 0.0
                    
                    rp_val = pd.to_numeric(st.session_state.df.iloc[idx]["Rolls_on_Pallet"], errors='coerce') or 1.0
                    m2p_val = pd.to_numeric(st.session_state.df.iloc[idx]["m_Square_per_pallet"], errors='coerce') or 0.0
                    m2_per_roll = m2p_val / rp_val
                    
                    final_rolls = orig_rolls
                    final_pallets = orig_pallets
                    
                    if r_used > 0:
                        if final_rolls >= r_used:
                            final_rolls -= r_used
                        else:
                            deficit = r_used - final_rolls
                            final_rolls = 0.0
                            pallets_to_break = int((deficit + rp_val - 0.001) // rp_val)
                            
                            if final_pallets >= pallets_to_break:
                                final_pallets -= pallets_to_break
                                final_rolls = (pallets_to_break * rp_val) - deficit
                            else:
                                final_pallets, final_rolls = 0.0, 0.0
                    
                    if final_rolls >= rp_val:
                        extra_pallets = int(final_rolls // rp_val)
                        final_pallets += extra_pallets
                        final_rolls = final_rolls % rp_val
                    
                    final_square_m = round((final_pallets * m2p_val) + (final_rolls * m2_per_roll), 2)
                    
                    edited_df.at[idx, roll_col] = final_rolls
                    edited_df.at[idx, pallet_col] = final_pallets
                    edited_df.at[idx, square_col] = final_square_m

                cells_to_update = []
                for col in available_cols:
                    col_idx = st.session_state.df.columns.get_loc(col) + 1
                    for idx, row in edited_df.iterrows():
                        row_idx = idx + 2
                        cell = gspread.cell.Cell(row=row_idx, col=col_idx, value=float(row[col]))
                        cells_to_update.append(cell)
                
                if cells_to_update:
                    main_sheet.update_cells(cells_to_update)
                
                if 'df' in st.session_state:
                    del st.session_state['df']
                    
                st.success("Stock counts and Alerts updated successfully!")
                st.rerun()
                
            except Exception as e:
                st.error(f"Save failed: {e}")

    if low_stock_alerts:
        with st.expander("🚩 View Low Stock Flags & Reorder Requirements", expanded=True):
            for alert in low_stock_alerts: 
                st.write(alert)
            
            st.divider()
            st.subheader("📋 Items Requiring Reorder Summary")
            
            df_reorder_summary = pd.DataFrame(reorder_needed)
            
            st.dataframe(
                df_reorder_summary,
                column_config={
                    "Material": st.column_config.TextColumn("Material Description"),
                    "Code": st.column_config.TextColumn("Item Code"),
                    "Order Qty": st.column_config.TextColumn("Deficit (To Target)"),
                    "Order m²": st.column_config.NumberColumn("Required Area (m²)", format="%.2f")
                },
                use_container_width=True,
                hide_index=True
            )
            
            csv_summary = df_reorder_summary.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Order List (CSV)",
                data=csv_summary,
                file_name=f"Reorder_Requirements_{selected_site}_{selected_month}.csv",
                mime='text/csv',
                key="btn_quick_reorder_export"
            )

        st.divider()
        st.subheader("➕ Queue New Pending Procurement Order")
        st.info("Select a flagged low-stock item below to adjust and lock in the definitive quantity being ordered.")

        flagged_materials = df_reorder_summary["Material"].tolist()
        
        c_form1, c_form2 = st.columns(2)
        with c_form1:
            chosen_material = st.selectbox("Select Material to Order", flagged_materials, key="order_mat_select")
            chosen_code = df_reorder_summary[df_reorder_summary["Material"] == chosen_material]["Code"].values[0]
            st.text_input("Item Code Identifier", value=chosen_code, disabled=True)
            
        with c_form2:
            input_pallets = st.number_input("Confirmed Pallets to Order", min_value=0.0, step=1.0, format="%.1f")
            input_rolls = st.number_input("Confirmed Loose Rolls to Order", min_value=0.0, step=1.0, format="%.1f")

        c_form3, c_form4 = st.columns(2)
        with c_form3:
            matched_meta = st.session_state.df[st.session_state.df["Material"] == chosen_material]
            m2p_factor = float(matched_meta["m_Square_per_pallet"].values[0]) if not matched_meta.empty else 0.0
            rop_factor = float(matched_meta["Rolls_on_Pallet"].values[0]) if not matched_meta.empty else 1.0
            m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
            
            calculated_m2 = round((input_pallets * m2p_factor) + (input_rolls * m2_per_roll), 2)
            input_m2 = st.number_input("Total Area to Order (m²)", min_value=0.0, value=calculated_m2, step=0.01, format="%.2f")
            
        with c_form4:
            calculated_weight = (input_pallets * WEIGHT_FACTORS["Pallet_Avg_KG"]) + (input_rolls * WEIGHT_FACTORS["Roll_Avg_KG"])
            input_weight = st.number_input("Calculated Weight (KG)", min_value=0.0, value=calculated_weight, step=1.0, format="%.1f", disabled=True)

        input_notes = st.text_input("Procurement Notes / PO Number", placeholder="e.g., PO-100234, Supplier X")

        if st.button("🚀 Commit to Pending Orders Pipeline", type="primary", disabled=not is_editor):
            if input_pallets == 0 and input_rolls == 0:
                st.error("Please specify a valid quantity of Pallets or Rolls to log.")
            else:
                try:
                    client = get_gspread_client()
                    pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
                    
                    new_order_row = [
                        chosen_material,
                        chosen_code,
                        input_pallets,
                        input_rolls,
                        input_m2,
                        input_weight, 
                        input_notes
                    ]
                    
                    pending_sheet.append_row(new_order_row)
                    st.success(f"Successfully logged {chosen_material} ({input_weight:,} KG) into the Pending Pipeline!")
                    
                    if 'df' in st.session_state:
                        del st.session_state['df']
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Failed to submit pending allocation line item: {e}")

# --- MODE 2: VIEW PENDING ORDERS moved to Mode  ---


# --- MODE 3: TRENDS & MONTHLY BREAKDOWN ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Analytics")

    st.caption(f"Reviewing inventory allocations and rolling trends for target timeline: **{selected_month}**")

    # =========================================================================
    # ⚙️ GLOBAL UNIT SELECTOR (Applies to current stock view & historical charts)
    # =========================================================================
    st.info("💡 Use the selector below to toggle how all inventory metrics are rendered across this dashboard page.")
    reporting_unit = st.radio(
        "Select Reporting Display Unit:", 
        ["Rolls", "Pallets", "Square Meters (m²)"], 
        horizontal=True,
        key="global_trend_reporting_unit"
    )
    
# =========================================================================
    # FEATURE 1: LIVE SITE STOCK LEVEL SUMMARY (Current Month)
    # =========================================================================
    st.subheader(f"📊 Warehouse Balances for {selected_site} ({selected_month})")
    
    current_stock_records = []
    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        item_code = str(row["Code"])
        rop_factor = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
        m2p_factor = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
        m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
        
        # Read raw on-hand balances for the current month
        site_rolls_col = f"{selected_site}_Rolls {selected_month}"
        site_pallets_col = f"{selected_site}_Pallets {selected_month}"
        
        s_rolls = pd.to_numeric(row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
        s_pallets = pd.to_numeric(row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
        
        # Apply the conversion math based on user preference
        if reporting_unit == "Rolls":
            current_volume = s_rolls + (s_pallets * rop_factor)
        elif reporting_unit == "Pallets":
            current_volume = s_pallets + (s_rolls / rop_factor) if rop_factor > 0 else 0.0
        else:  # Square Meters (m²)
            current_volume = (s_pallets * m2p_factor) + (s_rolls * m2_per_roll)
            
        current_stock_records.append({
            "Material": mat_name,
            "Code": item_code,
            f"Stock Balance ({reporting_unit})": round(current_volume, 2)
        })

    df_current_stock = pd.DataFrame(current_stock_records)

    # Render Bar Chart for Individual Material Types
    fig_current = px.bar(
        df_current_stock,
        x="Material",
        y=f"Stock Balance ({reporting_unit})",
        color="Material",
        title=f"On-Hand Stock Volumes by Material Type at {selected_site}",
        labels={f"Stock Balance ({reporting_unit})": f"Available Stock ({reporting_unit})"},
        color_discrete_sequence=px.colors.qualitative.Plotly
    )
    st.plotly_chart(fig_current, width="stretch")

    # Data Grid View
    with st.expander("📋 View Live Balance Sheet Data Grid", expanded=True):
        st.dataframe(df_current_stock, width="stretch", hide_index=True)

    # === FEATURE 2: STANDALONE PENDING ORDERS BAR CHART ===
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

    # === FEATURE 3: COMBINED INVENTORY + PENDING STACKED PALLETS CHART ===
    st.divider()
    st.subheader(f"📈 Total Projected Availability (Stock + Pending Arrivals in Pallets)")
    if st.button(f"📊 Generate Cumulative Stock & Pending Chart"):
        client = get_gspread_client()
        try:
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

            stacked_chart_records = []
            for _, row in st.session_state.df.iterrows():
                mat_name = str(row["Material"]).strip()
                rop = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
                
                floor_pallets = warehouse_pallet_totals.get(mat_name, 0.0)
                floor_loose_rolls_as_pallets = warehouse_roll_totals.get(mat_name, 0.0) / rop
                
                pipeline_data = pending_pallet_breakdown.get(mat_name, {"Direct_Pallets": 0.0, "Rolls_As_Pallets": 0.0})
                incoming_pallets_total = pipeline_data["Direct_Pallets"] + pipeline_data["Rolls_As_Pallets"]
                
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "On-Hand Pallets", "Total Pallets": floor_pallets})
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "On-Hand Loose Rolls (As Pallets)", "Total Pallets": floor_loose_rolls_as_pallets})
                stacked_chart_records.append({"Material": mat_name, "Stock Composition": "Pending Orders (As Pallets)", "Total Pallets": incoming_pallets_total})
                
            df_stack = pd.DataFrame(stacked_chart_records)
            
            fig_stacked = px.bar(
                df_stack, x="Material", y="Total Pallets", color="Stock Composition", barmode="stack",
                title=f"Total Projected Multi-Site Volume vs. Pending Pipeline Additions ({selected_month})",
                color_discrete_map={
                    "On-Hand Loose Rolls (As Pallets)": "#ff7f0e",   
                    "On-Hand Pallets": "#1f77b4",                    
                    "Pending Orders (As Pallets)": "#2ca02c"          
                }
            )
            fig_stacked.update_layout(yaxis_title="Total Quantity (Equivalent Pallets)", xaxis_title="Material Type")
            st.plotly_chart(fig_stacked, use_container_width=True)
            
        except Exception as e:
            st.error(f"Error compiling cumulative stacked data metrics: {e}")

# === FEATURE 4: SAVED MATERIAL CONSUMPTION & BUFFER ANALYTICS ===
    st.divider()
    st.subheader("⏱️ Saved Material Consumption & Buffer Analytics")
    st.caption("Summarizes stock depletion vs. target thresholds alongside active warehouse safety buffers.")

    if st.button("📊 Calculate Saved Production Consumption"):
        usage_records = []
        buffer_records = []
        
        for index, row in st.session_state.df.iterrows():
            mat_name = str(row["Material"]).strip()
            item_code = str(row["Code"])
            
            if mat_name in thresholds:
                t = thresholds[mat_name]
                unit = t['unit']
                target_qty = float(t['target'])
                
                current_gross_val = 0.0
                rop_factor = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
                m2p_factor = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
                m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
                
                for site in site_options:
                    site_rolls_col = f"{site}_Rolls {selected_month}"
                    site_pallets_col = f"{site}_Pallets {selected_month}"
                    
                    s_rolls = pd.to_numeric(row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
                    s_pallets = pd.to_numeric(row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
                    
                    if unit == "Rolls":
                        current_gross_val += s_rolls + (s_pallets * rop_factor)
                    elif unit == "Pallets":
                        current_gross_val += s_pallets + (s_rolls / rop_factor)
                
                # --- CASE 1: STOCK DEFICIT (Below Target) ---
                if current_gross_val < target_qty:
                    deficit = target_qty - current_gross_val
                    if unit == "Pallets":
                        rolls_consumed = deficit * rop_factor
                        area_consumed = deficit * m2p_factor
                    else:
                        rolls_consumed = deficit
                        area_consumed = deficit * m2_per_roll
                        
                    weight_consumed = rolls_consumed * WEIGHT_FACTORS["Roll_Avg_KG"]
                    
                    usage_records.append({
                        "Material": mat_name,
                        "Item Code": item_code,
                        "Rolls Consumed (Deficit)": round(rolls_consumed, 1),
                        "Area Deficit (m²)": round(area_consumed, 2),
                        "Est. Missing Weight (KG)": round(weight_consumed, 1)
                    })
                
                # --- CASE 2: STOCK BUFFER (Above Target) ---
                elif current_gross_val > target_qty:
                    surplus = current_gross_val - target_qty
                    if unit == "Pallets":
                        rolls_buffer = surplus * rop_factor
                        area_buffer = surplus * m2p_factor
                    else:
                        rolls_buffer = surplus
                        area_buffer = surplus * m2_per_roll
                        
                    weight_buffer = rolls_buffer * WEIGHT_FACTORS["Roll_Avg_KG"]
                    
                    buffer_records.append({
                        "Material": mat_name,
                        "Item Code": item_code,
                        "Excess Rolls (Buffer)": round(rolls_buffer, 1),
                        "Surplus Area (m²)": round(area_buffer, 2),
                        "Buffer Weight (KG)": round(weight_buffer, 1)
                    })
                        
        # --- DISPLAY DEFICIT GRAPH ---
        st.markdown("### 🚨 Critical Deficits (Below Target)")
        if usage_records:
            df_usage_summary = pd.DataFrame(usage_records)
            
            m_c1, m_c2, m_c3 = st.columns(3)
            m_c1.metric("Total Rolls Below Target", f"{df_usage_summary['Rolls Consumed (Deficit)'].sum():,.1f} Rolls", delta_color="inverse")
            m_c2.metric("Total Surface Area Deficit", f"{df_usage_summary['Area Deficit (m²)'].sum():,.2f} m²")
            m_c3.metric("Total Required Mass Weight", f"{df_usage_summary['Est. Missing Weight (KG)'].sum():,.1f} KG")
            
            df_chart = df_usage_summary.sort_values(by="Rolls Consumed (Deficit)", ascending=True)
            fig_consumption = px.bar(
                df_chart, x="Rolls Consumed (Deficit)", y="Material", orientation='h',
                title="Total Material Volume Below Target (Rolls Consumed)",
                labels={"Rolls Consumed (Deficit)": "Rolls Below Target", "Material": "Material Description"},
                color="Rolls Consumed (Deficit)", color_continuous_scale="Reds"
            )
            fig_consumption.update_layout(showlegend=False, height=max(250, len(df_chart) * 35), margin=dict(l=5, r=5, t=40, b=20))    
            st.plotly_chart(fig_consumption, use_container_width=True)
        else:
            st.success("✨ Optimal Stock Levels Maintained! No items are currently in deficit.")

        # --- DISPLAY BUFFER GRAPH ---
        st.write("")
        st.markdown("### 🟢 Healthy Runways (Safety Stock Buffers)")
        if buffer_records:
            df_buffer_summary = pd.DataFrame(buffer_records)
            
            b_c1, b_c2, b_c3 = st.columns(3)
            b_c1.metric("Total Excess Rolls", f"{df_buffer_summary['Excess Rolls (Buffer)'].sum():,.1f} Rolls")
            b_c2.metric("Total Surplus Area", f"{df_buffer_summary['Surplus Area (m²)'].sum():,.2f} m²")
            b_c3.metric("Total Buffer Weight", f"{df_buffer_summary['Buffer Weight (KG)'].sum():,.1f} KG")
            
            df_buf_chart = df_buffer_summary.sort_values(by="Excess Rolls (Buffer)", ascending=True)
            fig_buffer = px.bar(
                df_buf_chart, x="Excess Rolls (Buffer)", y="Material", orientation='h',
                title="Available Safety Stock Buffers (Rolls Above Target Limit)",
                labels={"Excess Rolls (Buffer)": "Extra Rolls on Hand", "Material": "Material Description"},
                color="Excess Rolls (Buffer)", color_continuous_scale="Greens" # Green gradient for healthy stock
            )
            fig_buffer.update_layout(showlegend=False, height=max(250, len(df_buf_chart) * 35), margin=dict(l=5, r=5, t=40, b=20))    
            st.plotly_chart(fig_buffer, use_container_width=True)
        else:
            st.info("No items currently exceed safety targets. Runways are operating precisely at baseline targets.")

# === NEW: CONFIGURABLE REPORTING UNIT SELECTION ===
    st.divider()
    st.subheader("⚙️ Analytics Display Preferences")
    reporting_unit = st.radio(
        "Select Reporting Display Unit:", 
        ["Rolls", "Pallets", "Square Meters (m²)"], 
        horizontal=True
    )

    # Determine past 3 months based on selection
    current_idx = months.index(selected_month)
    past_months = [months[(current_idx - i) % 12] for i in range(1, 4)]


    # === FEATURE 5: THREE-MONTH HISTORICAL SITE ANALYTICS ===
    st.subheader(f"🗓️ Rolling 3-Month History ({selected_site})")
    st.caption(f"Displays stock metrics configured in **{reporting_unit}** for the three months preceding {selected_month}.")

    if st.button(f"📊 Calculate Past 3 Months for {selected_site}"):
        history_records = []
        
        for index, row in st.session_state.df.iterrows():
            mat_name = str(row["Material"]).strip()
            item_code = str(row["Code"])
            rop_factor = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
            m2p_factor = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
            m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
            
            for m in past_months:
                site_rolls_col = f"{selected_site}_Rolls {m}"
                site_pallets_col = f"{selected_site}_Pallets {m}"
                
                s_rolls = pd.to_numeric(row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
                s_pallets = pd.to_numeric(row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
                
                # Dynamic Conversion Logic Matrix
                if reporting_unit == "Rolls":
                    converted_val = s_rolls + (s_pallets * rop_factor)
                elif reporting_unit == "Pallets":
                    converted_val = s_pallets + (s_rolls / rop_factor) if rop_factor > 0 else 0.0
                else:  # Square Meters
                    converted_val = (s_pallets * m2p_factor) + (s_rolls * m2_per_roll)
                
                history_records.append({
                    "Material": mat_name,
                    "Code": item_code,
                    "Month": m,
                    "Value": round(converted_val, 2)
                })
        
        if history_records:
            df_history = pd.DataFrame(history_records)
            df_history["Month"] = pd.Categorical(df_history["Month"], categories=reversed(past_months), ordered=True)
            df_history = df_history.sort_values(["Material", "Month"])
            
            fig_history = px.bar(
                df_history, x="Month", y="Value", color="Material", barmode="group",
                title=f"Material Stock History ({reporting_unit}) at {selected_site}",
                labels={"Value": f"Quantity ({reporting_unit})", "Month": "Historical Timeline"},
                color_discrete_sequence=px.colors.qualitative.G10
            )
            st.plotly_chart(fig_history, width="stretch")
        else:
            st.warning("No structural profile matching layout configurations found.")


    # === FEATURE 6: COMBINED 3-MONTH GLOBAL MULTI-SITE ANALYTICS ===
    st.divider()
    st.subheader("🌐 Global 3-Month Cross-Warehouse Summary")
    st.caption(f"Aggregates total network volume metrics in **{reporting_unit}** across all sites.")

    if st.button("📊 Calculate Global Multi-Site Volume"):
        global_history_records = []
        
        for index, row in st.session_state.df.iterrows():
            mat_name = str(row["Material"]).strip()
            rop_factor = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
            m2p_factor = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
            m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
            
            for m in past_months:
                total_converted_network = 0.0
                
                for site in site_options:
                    site_rolls_col = f"{site}_Rolls {m}"
                    site_pallets_col = f"{site}_Pallets {m}"
                    
                    s_rolls = pd.to_numeric(row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
                    s_pallets = pd.to_numeric(row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
                    
                    # Convert to target metric per site before adding to total sum
                    if reporting_unit == "Rolls":
                        total_converted_network += s_rolls + (s_pallets * rop_factor)
                    elif reporting_unit == "Pallets":
                        total_converted_network += s_pallets + (s_rolls / rop_factor) if rop_factor > 0 else 0.0
                    else:  # Square Meters
                        total_converted_network += (s_pallets * m2p_factor) + (s_rolls * m2_per_roll)
                
                global_history_records.append({
                    "Material": mat_name,
                    "Month": m,
                    "Global Value": round(total_converted_network, 2)
                })
        
        if global_history_records:
            df_global = pd.DataFrame(global_history_records)
            df_global["Month"] = pd.Categorical(df_global["Month"], categories=reversed(past_months), ordered=True)
            df_global = df_global.sort_values(["Material", "Month"])
            
            fig_global = px.bar(
                df_global, x="Material", y="Global Value", color="Month", barmode="group",
                title=f"Total Dynamic Network Volume Over Time ({reporting_unit})",
                labels={"Global Value": f"Network Sum ({reporting_unit})", "Material": "Material Type"},
                color_discrete_sequence=px.colors.qualitative.Safe
            )
            st.plotly_chart(fig_global, width="stretch")
            
            with st.expander("📋 View Consolidated Network Matrix Table", expanded=False):
                df_pivot = df_global.pivot(index="Material", columns="Month", values="Global Value")
                st.dataframe(df_pivot, width="stretch")
        else:
            st.warning("No dynamic column structures found matching historical configurations.")

# --- MODE 4: RECEIVE GOODS ---
elif app_mode == "🚛 Receive Goods (KPark)":
    st.title("🚛 Goods Receiving (KPark)")
    st.subheader("📥 Process Inbound Substrate Shipments")
    
    client = get_gspread_client()
    try:
        # 1. Fetch current pending orders to see what can be received
        pending_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Pending_Orders")
        pending_data = pending_sheet.get_all_records()
        
        if not pending_data:
            st.info("✨ No pending orders found in the pipeline to receive.")
        else:
            df_pending = pd.DataFrame(pending_data)
            df_pending.columns = [str(c).strip() for c in df_pending.columns]
            
            # Create a clean label for a dropdown selection
            df_pending["Dropdown_Label"] = df_pending.apply(
                lambda r: f"{r['Material']} | Pallets: {r.get('Pending_Pallets', 0)} | Rolls: {r.get('Pending_Rolls', 0)} | Notes: {r.get('Notes', '')}", 
                axis=1
            )
            
            st.markdown("### 1. Select Incoming Shipment")
            selected_order_label = st.selectbox("Choose a pending line item to receive:", df_pending["Dropdown_Label"].tolist())
            
            # Extract the selected row's data
            selected_row = df_pending[df_pending["Dropdown_Label"] == selected_order_label].iloc[0]
            selected_material = str(selected_row["Material"]).strip()
            pending_index = df_pending[df_pending["Dropdown_Label"] == selected_order_label].index[0]
            
            # Read counts safely
            p_to_receive = float(safe_extract_numeric(pd.Series([selected_row.get("Pending_Pallets", 0)]))[0])
            r_to_receive = float(safe_extract_numeric(pd.Series([selected_row.get("Pending_Rolls", 0)]))[0])
            
            # Display summary of what is being processed
            col_rec1, col_rec2 = st.columns(2)
            with col_rec1:
                st.metric("Pallets to Add", f"{p_to_receive:.1f}")
            with col_rec2:
                st.metric("Loose Rolls to Add", f"{r_to_receive:.1f}")
                
            st.markdown("### 2. Finalize Intake Allocation")
            st.caption(f"Clicking the button below will remove this line item from 'Pending_Orders' and automatically add these quantities into your active **KPark** {selected_month} stock ledger counts.")
            
            if st.button("🚛 Accept Delivery & Update Stock Sheets", type="primary"):
                with st.spinner("Processing intake manifests..."):
                    # 2. Update Main Inventory Sheet
                    main_sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                    main_data = main_sheet.get_all_records()
                    df_main = pd.DataFrame(main_data)
                    df_main.columns = [str(c).strip() for c in df_main.columns]
                    
                    # Target columns for KPark site
                    kpark_pallet_col = f"KPark_Pallets {selected_month}"
                    kpark_roll_col = f"KPark_Rolls {selected_month}"
                    kpark_square_col = f"KPark_SquareM {selected_month}"
                    
                    # Find matching row index in main sheet
                    match_mask = df_main["Material"].str.strip() == selected_material
                    if not df_main[match_mask].empty:
                        main_idx = df_main[match_mask].index[0]
                        row_num_in_sheet = main_idx + 2 # account for headers
                        
                        # Fetch conversion ratios
                        rop_val = pd.to_numeric(df_main.iloc[main_idx]["Rolls_on_Pallet"], errors='coerce') or 1.0
                        m2p_val = pd.to_numeric(df_main.iloc[main_idx]["m_Square_per_pallet"], errors='coerce') or 0.0
                        m2_per_roll = m2p_val / rop_val if rop_val > 0 else 0.0
                        
                        # Get current stock allocations on the floor
                        current_pallets = pd.to_numeric(df_main.iloc[main_idx].get(kpark_pallet_col, 0.0), errors='coerce') or 0.0
                        current_rolls = pd.to_numeric(df_main.iloc[main_idx].get(kpark_roll_col, 0.0), errors='coerce') or 0.0
                        
                        # Add new inventory amounts
                        new_pallets = current_pallets + p_to_receive
                        new_rolls = current_rolls + r_to_receive
                        
                        # Balance loose rolls into full pallets if they exceed standard configuration metrics
                        if new_rolls >= rop_val:
                            extra_pallets = int(new_rolls // rop_val)
                            new_pallets += extra_pallets
                            new_rolls = new_rolls % rop_val
                            
                        new_square_m = round((new_pallets * m2p_val) + (new_rolls * m2_per_roll), 2)
                        
                        # Batch update cell values on main sheet
                        pallet_col_idx = df_main.columns.get_loc(kpark_pallet_col) + 1
                        roll_col_idx = df_main.columns.get_loc(kpark_roll_col) + 1
                        square_col_idx = df_main.columns.get_loc(kpark_square_col) + 1
                        
                        main_sheet.update_cells([
                            gspread.cell.Cell(row=row_num_in_sheet, col=pallet_col_idx, value=float(new_pallets)),
                            gspread.cell.Cell(row=row_num_in_sheet, col=roll_col_idx, value=float(new_rolls)),
                            gspread.cell.Cell(row=row_num_in_sheet, col=square_col_idx, value=float(new_square_m))
                        ])
                        
                        # 3. Strip line item out of the pending log tracker
                        # Row index starts at 2, add pending_index
                        pending_sheet.delete_rows(int(pending_index) + 2)
                        
                        # Reset internal stream memory states
                        if 'df' in st.session_state:
                            del st.session_state['df']
                            
                        st.success(f"✅ Received successfully! {selected_material} has been updated under KPark inventory manifests.")
                        st.rerun()
                    else:
                        st.error(f"Could not find matching material profile name '{selected_material}' inside main tracking ledger tab setup.")
                        
    except Exception as e:
        st.error(f"Inventory intake process failure: {e}")

   
# --- MODE 5: PENDING ORDER DASHBOARD ---
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
            act_col = "Total Weight (KG)"
            
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
                    "Pending_Pallets": st.column_config.NumberColumn("Pending_Pallets", format="%.1f", disabled=True),
                    "Pending_Rolls": st.column_config.NumberColumn("Pending_Rolls", format="%.1f", disabled=True),
                    "Pending_m2": st.column_config.NumberColumn("Pending_m2", format="%.2f", disabled=True),
                    "Total Weight (KG)": st.column_config.NumberColumn("Total Weight (KG)", format="%.1f", disabled=True),
                    "Notes": st.column_config.TextColumn("Notes", width="medium", disabled=True)
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
                    pending_sheet.append_row(["Material", "Code", "Pending_Pallets", "Pending_Rolls", "Pending_m2", "Total Weight (KG)", "Notes"])
                    
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