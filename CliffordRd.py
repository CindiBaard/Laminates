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
    
    # --- FIXED: PENDING ORDERS CHART LOADS AUTOMATICALLY WITHOUT A BUTTON WRAPPER ---
    st.subheader(f"⏳ Standalone Pending Orders Pipeline Overview")
    
    try:
        client = get_gspread_client()
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
                    df_pending_graph, 
                    x="Material", 
                    y="Quantity", 
                    color="Unit Type", 
                    barmode="group",
                    title="Pending Materials Outstanding (All Warehouses Combined)",
                    color_discrete_map={"Pallets": "#2ca02c", "Rolls": "#9467bd"}
                )
                st.plotly_chart(fig_standalone_pending, use_container_width=True)
            else:
                st.warning("⚠️ Pending_Orders sheet structure missing required 'Pending_Pallets' or 'Pending_Rolls' columns.")
        else:
            st.info("ℹ️ The 'Pending_Orders' tab is currently empty. No active procurement orders on file.")
            
    except Exception as e:
        st.error(f"Could not load 'Pending_Orders' sheet data: {e}")