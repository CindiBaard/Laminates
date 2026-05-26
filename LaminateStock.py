import streamlit as st
import pandas as pd
import io

# 1. Setup Page Configuration
st.set_page_config(page_title="Inventory Management System", layout="wide")

st.title("📦 Real-Time Stock Count & Update")
st.markdown("Update the **Pallets in stock** column below to confirm inventory.")

# 2. Load the Data
@st.cache_data
def load_initial_data():
    csv_data = """Material;Laminate;Code;Meters_per_Roll;Rolls_on_Pallet;m_Square_per_pallet;Pallets_in_stock;Pallets_to_order
082 PBL;PBL;LAM082HUH30017S;700;36;2066.40;0;0
082 ABL White;ABL White;LAM082HUH1337FC;880;36;2597.76;0;0
082 ABL Silver;Silver;LAM113HUH1337FCSIL;880;36;2597.76;0;0
113 PBL;PBL;LAM113HUH30017S;700;32;2531.20;0;0
113 ABL White;ABL White;LAM113HUH1337FC;880;32;3182.08;0;0
113 ABL Silver;Silver;LAM113HUH1337FCSIL;880;32;3182.08;0;0
129 PBL;PBL;LAM129HUH30017S;700;28;2528.40;0;0
129 ABL White;ABL White;LAM129HUH1337FC;880;28;3178.56;0;0
129 ABL Silver;Silver;LAM129HUH1337FCSIL;880;28;3178.56;0;0
JUMBO ROLLS PBL;PBL;LAM375HUH1337S;700;12;3675.00;0;0
JUMBO ROLLS ABL White;ABL White;LAM375HUH1337FC;880;12;3960.00;0;0
JUMBO ROLLS Silver;Silver;LAM350HUH1337FCSIL;880;12;3960.00;0;0"""
    df = pd.read_csv(io.StringIO(csv_data), sep=";")
    # Add column for the manual procurement override
    if 'Final_Procurement_Qty' not in df.columns:
        df['Final_Procurement_Qty'] = 0
    return df

# Initialize session state to store data changes
if 'inventory_df' not in st.session_state:
    st.session_state.inventory_df = load_initial_data()

# 3. Step 1: Inventory Form
with st.form("inventory_form"):
    st.subheader("1. Update Current Stock & Reorder Suggestions")
    
    edited_df = st.data_editor(
        st.session_state.inventory_df,
        column_config={
            "Pallets_in_stock": st.column_config.NumberColumn(
                "Pallets in Stock",
                help="Enter physical count in warehouse",
                min_value=0, step=1,
            ),
            "Pallets_to_order": st.column_config.NumberColumn(
                "Suggested to Order",
                help="Amount for the reorder spreadsheet",
                min_value=0,
            ),
            "Final_Procurement_Qty": st.column_config.Column(required=False, disabled=True),
        },
        disabled=["Material", "Laminate", "Code", "Meters_per_Roll", "Rolls_on_Pallet", "m_Square_per_pallet", "Final_Procurement_Qty"],
        hide_index=True,
        use_container_width=True
    )

    submit_button = st.form_submit_button("Confirm and Sync Changes")

# 4. Handle Updates (The "Missing" Section)
if submit_button:
    st.session_state.inventory_df = edited_df
    st.success("Database updated successfully!")
    
    # Low Stock Alert Logic
    low_stock = edited_df[edited_df['Pallets_in_stock'] < 2]
    if not low_stock.empty:
        st.warning(f"⚠️ Alert: {len(low_stock)} items are running low on stock!")

# 5. Step 2: Final Procurement Order Section
st.divider()
st.subheader("2. Final Procurement Override")
st.markdown("Use this section to record the **actual** amount ordered by procurement if it differs from the spreadsheet.")

# Create the editor for final quantities
final_order_df = st.data_editor(
    st.session_state.inventory_df[["Material", "Code", "Pallets_to_order", "Final_Procurement_Qty"]],
    column_config={
        "Material": st.column_config.Column(disabled=True),
        "Code": st.column_config.Column(disabled=True),
        "Pallets_to_order": st.column_config.NumberColumn("Spreadsheet Suggestion", disabled=True),
        "Final_Procurement_Qty": st.column_config.NumberColumn(
            "Final Order Qty",
            help="The actual amount Procurement is ordering",
            min_value=0, step=1
        ),
    },
    hide_index=True,
    use_container_width=True,
    key="procurement_editor"
)

# 6. Handle Final Save
if st.button("Save Final Procurement Quantities"):
    # Update only the procurement column in the main session state
    st.session_state.inventory_df["Final_Procurement_Qty"] = final_order_df["Final_Procurement_Qty"]
    st.success("Final procurement orders have been recorded.")
    
    # Show summary of what was ordered
    ordered_only = st.session_state.inventory_df[st.session_state.inventory_df["Final_Procurement_Qty"] > 0]
    if not ordered_only.empty:
        st.write("### Items Ordered Summary:")
        st.dataframe(ordered_only[["Material", "Final_Procurement_Qty"]], hide_index=True)

# 7. Sidebar Metrics
st.sidebar.header("Summary Metrics")
total_m2 = (st.session_state.inventory_df['Pallets_in_stock'] * st.session_state.inventory_df['m_Square_per_pallet']).sum()
st.sidebar.metric("Total m² in Stock", f"{total_m2:,.2f}")
st.sidebar.metric("Total Pallets", int(st.session_state.inventory_df['Pallets_in_stock'].sum()))

actual_ordered = int(st.session_state.inventory_df['Final_Procurement_Qty'].sum())
st.sidebar.metric("Total Pallets Ordered", actual_ordered)