import streamlit as st
from modules import sales, attendance, salary, expenses, fa_sales

from utils.access import get_user_access
from utils.auth import ensure_google_login

st.set_page_config(page_title="Dashboard", layout="wide")




st.sidebar.title("📊 Dashboard")
page = st.sidebar.radio("Navigate to", ["Sales", "Attendance", "Salary", "Expenses", "FA Sales"])

# ✅ Single global login (only here)
email = ensure_google_login(get_user_access)

# ✅ Without login show message (but sidebar login stays visible)
if not email:
    st.title("Sales Team Dashboard")
    st.write("Login first from Sidebar")
    st.stop()

# Route
if page == "Sales":
    sales.show()
elif page == "Attendance":
    attendance.show()
elif page == "Salary":
    salary.show()
elif page == "Expenses":
    expenses.show()
elif page == "FA Sales":    
    fa_sales.show()