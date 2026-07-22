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

# --- 2. AUTHENTICATION (GOOGLE SHEETS) ---
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

# --- 3. SESSION STATE & GLOBAL APP AUTHENTICATION ---
if 'df' not in st.session_state:
    try:
        st.session_state.df, _ = load_data()
    except Exception as e:
        st.error(f"⚠️ Auth Error: {e}")
        st.stop()

if "is_authenticated" not in st.session_state:
    st.session_state["is_authenticated"] = False

SECRET_PASSWORD = "BowlerSecure2026" 

# --- SIDEBAR LOGIN CONTROLS (GLOBAL) ---
st.sidebar.markdown("---")
if st.session_state["is_authenticated"]:
    st.sidebar.success("🔓 Edit Access Granted")
    if st.sidebar.button("🚪 Log Out", use_container_width=True):
        st.session_state["is_authenticated"] = False
        st.rerun()
else:
    st.sidebar.info("🔒 App in Read-Only Mode")
    with st.sidebar.form("sidebar_login_form"):
        user_password = st.text_input(
            "🔑 Enter Passcode to Edit", 
            type="password", 
            key="sidebar_passcode_input",
            help="Type password to unlock saving, editing, and order creation."
        ).strip()
        
        if st.form_submit_button("Unlock Edit Mode", use_container_width=True):
            if user_password == SECRET_PASSWORD:
                st.session_state["is_authenticated"] = True
                st.rerun()
            else:
                st.sidebar.error("😕 Incorrect passcode.")

# Global flag available to ALL pages/modes
is_editor = st.session_state["is_authenticated"]
st.sidebar.markdown("---")


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

    if not is_editor:
        st.warning("⚠️ You are viewing in **Read-Only** mode. Enter the passcode in the sidebar to enable editing and saving.")

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
        disabled=not is_editor  # Locks editor in read-only mode
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

    c1, c2, c3, c4 = st.columns(4) 
    c1.metric("Total Order Weight", f"{total_est_weight_kg:,.0f} KG")
    c2.metric("Container Capacity", f"{(total_est_weight_kg/CONTAINER_LIMIT_KG)*100:.1f}%")
    
    with c3:
        if st.button("💾 Save Counts to Sheet", disabled=not is_editor, use_container_width=True):
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
                    
                st.success("Stock counts updated successfully!")
                st.rerun()
                
            except Exception as e:
                st.error(f"Save failed: {e}")

    with c4:
        is_first_of_month = (datetime.now().day == 1)
        allow_rollover = is_editor and is_first_of_month
        
        if not is_first_of_month:
            help_text = f"🔒 Rollover disabled. Today is day {datetime.now().day}. Unlocks on the 1st day of the month."
        elif not is_editor:
            help_text = "🔒 Enter Passcode to unlock."
        else:
            help_text = "Copies current month's ending balances directly into the next month's columns."

        if st.button(
            "🔄 Roll Over to Next Month", 
            disabled=not allow_rollover, 
            use_container_width=True, 
            help=help_text
        ):
            current_month_idx = months.index(selected_month)
            if current_month_idx == 11:
                st.error("Cannot automatically roll over past December.")
            else:
                next_month = months[current_month_idx + 1]
                next_roll_col = f"{selected_site}_Rolls {next_month}"
                next_pallet_col = f"{selected_site}_Pallets {next_month}"
                next_square_col = f"{selected_site}_SquareM {next_month}"
                
                all_cols = list(st.session_state.df.columns)
                
                if next_roll_col not in all_cols or next_pallet_col not in all_cols or next_square_col not in all_cols:
                    st.error(f"Ensure columns for **{next_month}** exist in your Google Sheet.")
                else:
                    try:
                        with st.spinner(f"Rolling totals forward to {next_month}..."):
                            client = get_gspread_client()
                            main_sheet = client.open_by_key(SPREADSHEET_ID).sheet1
                            
                            next_month_cells = []
                            next_roll_idx = all_cols.index(next_roll_col) + 1
                            next_pallet_idx = all_cols.index(next_pallet_col) + 1
                            next_square_idx = all_cols.index(next_square_col) + 1
                            
                            for idx, row in edited_df.iterrows():
                                row_idx = idx + 2
                                current_rolls = float(row.get(roll_col, 0.0))
                                current_pallets = float(row.get(pallet_col, 0.0))
                                current_square = float(row.get(square_col, 0.0))
                                
                                next_month_cells.append(gspread.cell.Cell(row=row_idx, col=next_roll_idx, value=current_rolls))
                                next_month_cells.append(gspread.cell.Cell(row=row_idx, col=next_pallet_idx, value=current_pallets))
                                next_month_cells.append(gspread.cell.Cell(row=row_idx, col=next_square_idx, value=current_square))
                            
                            if next_month_cells:
                                main_sheet.update_cells(next_month_cells)
                                
                            if 'df' in st.session_state:
                                del st.session_state['df']
                                
                            st.success(f"Success! Balances rolled clean into **{next_month}**.")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Rollover update failed: {e}")

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
                    st.error(f"Failed to submit pending allocation: {e}")


# --- MODE 3: TRENDS & MONTHLY BREAKDOWN ---
elif app_mode == "📈 Stock Trends":
    st.title("📈 Stock Level Analytics")
    st.caption(f"Reviewing inventory allocations and rolling trends for target timeline: **{selected_month}**")

    st.info("💡 Use the selector below to toggle how all inventory metrics are rendered across this dashboard page.")
    reporting_unit = st.radio(
        "Select Reporting Display Unit:", 
        ["Rolls", "Pallets", "Square Meters (m²)"], 
        horizontal=True,
        key="global_trend_reporting_unit"
    )
    
    st.subheader(f"📊 Warehouse Balances for {selected_site} ({selected_month})")
    
    current_stock_records = []
    for index, row in st.session_state.df.iterrows():
        mat_name = str(row["Material"]).strip()
        item_code = str(row["Code"])
        rop_factor = pd.to_numeric(row["Rolls_on_Pallet"], errors='coerce') or 1.0
        m2p_factor = pd.to_numeric(row["m_Square_per_pallet"], errors='coerce') or 0.0
        m2_per_roll = m2p_factor / rop_factor if rop_factor > 0 else 0.0
        
        site_rolls_col = f"{selected_site}_Rolls {selected_month}"
        site_pallets_col = f"{selected_site}_Pallets {selected_month}"
        
        s_rolls = pd.to_numeric(row.get(site_rolls_col, 0.0), errors='coerce') or 0.0
        s_pallets = pd.to_numeric(row.get(site_pallets_col, 0.0), errors='coerce') or 0.0
        
        if reporting_unit == "Rolls":
            current_volume = s_rolls + (s_pallets * rop_factor)
        elif reporting_unit == "Pallets":
            current_volume = s_pallets + (s_rolls / rop_factor) if rop_factor > 0 else 0.0
        else:
            current_volume = (s_pallets * m2p_factor) + (s_rolls * m2_per_roll)
            
        current_stock_records.append({
            "Material": mat_name,
            "Code": item_code,
            f"Stock Balance ({reporting_unit})": round(current_volume, 2)
        })

    df_current_stock = pd.DataFrame(current_stock_records)

    fig_current = px.bar(
        df_current_stock,
        x="Material",
        y=f"Stock Balance ({reporting_unit})",
        color="Material",
        title=f"On-Hand Stock Volumes by Material Type at {selected_site}",
        labels={f"Stock Balance ({reporting_unit})": f"Available Stock ({reporting_unit})"},
        color_discrete_sequence=px.colors.qualitative.Plotly
    )
    st.plotly_chart(fig_current, use_container_width=True)

    with st.expander("📋 View Live Balance Sheet Data Grid", expanded=True):
        st.dataframe(df_current_stock, use_container_width=True, hide_index=True)

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