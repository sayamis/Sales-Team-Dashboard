import time
import hashlib
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file("service_account.json", scopes=scope)
client = gspread.authorize(creds)

SPREADSHEET_URL = "PASTE_YOUR_SHEET_URL_HERE"

sh = client.open_by_url("https://docs.google.com/spreadsheets/d/1uCHPSSdK4J4Ag-iXq-JkjDCQI8e5hM5OAZa7XUnJywg/edit?gid=0#gid=0")
ws = sh.worksheet("Sheet1")   # yahan tab/worksheet ka exact naam


def fetch_df():
    records = ws.get_all_records()
    return pd.DataFrame(records)

def df_hash(df: pd.DataFrame) -> str:
    # stable hash for change detection
    raw = df.to_csv(index=False).encode("utf-8")
    return hashlib.md5(raw).hexdigest()

last_hash = None

while True:
    df = fetch_df()
    h = df_hash(df)

    if h != last_hash:
        last_hash = h
        print("\n✅ Sheet updated! Latest preview:")
        print(df.head())
        # yahin se aap apna dashboard refresh call karoge (next step)

    time.sleep(5)  # har 5 sec me check
