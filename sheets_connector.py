"""
sheets_connector.py
--------------------
Central module for all Google Sheets read/write operations.
Used by the Streamlit app, Module 2, and the migration script.

Requires:
  - credentials.json  (service account key file — keep this private, never commit to git)
  - SHEET_ID          (set below)

Install deps:
  pip install gspread google-auth
"""

import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import pandas as pd
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID        = "1wuFdlPlvmU-1_ZwPOLfsdcLbbOlcyx7BfqbFhHlhvjg"
CREDS_FILE      = "credentials.json"   # path to your downloaded service account JSON
MAIN_WORKSHEET  = "Master_Food_Data"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Full column schema (must match module3) ───────────────────────────────────
COLUMNS = [
    # D1 Identity
    "Food_ID", "Food_Name", "Brand", "Category", "Source_Kid",
    "Data_Type", "Date_Added", "Source_Module",
    # D2 Macronutrients per 100g
    "Energy_kcal", "Protein_g", "Carbohydrate_g", "Sugar_Total_g",
    "Sugar_Added_g", "Fat_Total_g", "Fat_Saturated_g", "Fat_Trans_g",
    "Fibre_g", "Sodium_mg", "Cholesterol_mg",
    # D3 Per Serving
    "Serving_Size_g", "Energy_per_Serving_kcal", "Protein_per_Serving_g",
    "Carbs_per_Serving_g", "Fat_per_Serving_g",
    # D4 Micronutrients
    "Calcium_mg", "Iron_mg", "VitaminC_mg", "VitaminD_mcg", "Potassium_mg",
    # D5 Processing
    "NOVA_Class", "NOVA_Verified", "Ingredient_Count", "Ingredient_List",
    "Has_Preservatives", "Preservative_List", "Has_Artificial_Colors",
    "Color_List", "Has_Emulsifiers", "Emulsifier_List",
    "Has_Artificial_Sweeteners", "Sweetener_List", "Has_MSG",
    "Refined_Grain", "AI_Confidence",
    # D6 CGM
    "CGM_Glucose_Baseline_mgdL", "CGM_Glucose_Peak_mgdL",
    "CGM_Glucose_Delta", "CGM_AUC", "CGM_Spike_Duration_min",
    "CGM_Tested_By", "CGM_Test_Date", "CGM_Score",
    # D7 Glycemic Index
    "GI_Value", "GI_Source", "GI_Confidence", "GL_Value",
    "Personal_GI_Equivalent",
    # Quality
    "Data_Completeness_pct", "Needs_Review", "Notes",
]

# Fields that count toward completeness
KEY_FIELDS = [
    "Food_Name", "Category", "Energy_kcal", "Protein_g", "Carbohydrate_g",
    "Fat_Total_g", "Sodium_mg", "Serving_Size_g", "NOVA_Class",
    "Has_Preservatives", "Has_Artificial_Colors", "Has_Emulsifiers",
]


# ── Connection ────────────────────────────────────────────────────────────────
def get_client():
    """Return authenticated gspread client."""
    if not Path(CREDS_FILE).exists():
        raise FileNotFoundError(
            f"credentials.json not found.\n"
            f"Download your service account JSON from Google Cloud Console\n"
            f"and save it as '{CREDS_FILE}' in your project folder."
        )
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet():
    """Return the main worksheet, creating it with headers if it doesn't exist."""
    client = get_client()
    sheet  = client.open_by_key(SHEET_ID)

    try:
        ws = sheet.worksheet(MAIN_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(MAIN_WORKSHEET, rows=1000, cols=len(COLUMNS))
        _write_headers(ws)

    return ws


def _write_headers(ws):
    """Write the header row to a fresh worksheet."""
    ws.update("A1", [COLUMNS])
    # Bold the header row
    ws.format("A1:BZ1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.18, "green": 0.25, "blue": 0.34},
        "horizontalAlignment": "CENTER",
    })


# ── Read ──────────────────────────────────────────────────────────────────────
def read_all() -> pd.DataFrame:
    """Read entire master sheet into a DataFrame."""
    ws  = get_worksheet()
    data = ws.get_all_records(expected_headers=COLUMNS)
    if not data:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(data)
    # Ensure all schema columns present
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[COLUMNS]


def get_existing_keys() -> set:
    """Return set of (food_name_lower, source_kid_lower) for duplicate detection."""
    try:
        ws   = get_worksheet()
        rows = ws.get_all_records(expected_headers=COLUMNS)
        return {
            (str(r.get("Food_Name","")).lower().strip(),
             str(r.get("Source_Kid","")).lower().strip())
            for r in rows
        }
    except Exception:
        return set()


# ── Write ─────────────────────────────────────────────────────────────────────
def append_food(record: dict) -> tuple[bool, str]:
    """
    Append a single food record to the Sheet.
    Returns (success: bool, message: str)
    Automatically checks for duplicates.
    """
    existing_keys = get_existing_keys()
    key = (
        str(record.get("Food_Name","")).lower().strip(),
        str(record.get("Source_Kid","")).lower().strip(),
    )

    if key in existing_keys:
        return False, f"'{record.get('Food_Name')}' already exists for {record.get('Source_Kid')} — skipped"

    # Calculate per-serving values
    record = _add_per_serving(record)

    # Calculate completeness
    record = _add_completeness(record)

    # Build row in column order
    row = [_safe(record.get(col)) for col in COLUMNS]

    ws = get_worksheet()
    ws.append_row(row, value_input_option="USER_ENTERED")

    food_name = record.get("Food_Name", "Unknown")
    return True, f"✅ '{food_name}' added to master database"


def bulk_append(records: list[dict]) -> dict:
    """Append multiple records, skipping duplicates. Returns summary."""
    existing_keys = get_existing_keys()
    ws = get_worksheet()

    added, skipped = [], []
    rows_to_write = []

    for record in records:
        key = (
            str(record.get("Food_Name","")).lower().strip(),
            str(record.get("Source_Kid","")).lower().strip(),
        )
        if key in existing_keys:
            skipped.append(record.get("Food_Name","?"))
            continue

        record = _add_per_serving(record)
        record = _add_completeness(record)
        rows_to_write.append([_safe(record.get(col)) for col in COLUMNS])
        existing_keys.add(key)
        added.append(record.get("Food_Name","?"))

    if rows_to_write:
        ws.append_rows(rows_to_write, value_input_option="USER_ENTERED")

    return {"added": added, "skipped": skipped}


def update_cell(food_id: str, column: str, value) -> bool:
    """Update a specific cell by Food_ID and column name."""
    ws = get_worksheet()
    try:
        col_idx  = COLUMNS.index(column) + 1
        id_col   = COLUMNS.index("Food_ID") + 1
        id_vals  = ws.col_values(id_col)
        if food_id in id_vals:
            row_idx = id_vals.index(food_id) + 1
            ws.update_cell(row_idx, col_idx, value)
            return True
    except Exception:
        pass
    return False


# ── Helpers ───────────────────────────────────────────────────────────────────
def _add_per_serving(record: dict) -> dict:
    """Calculate per-serving values from per-100g + serving size."""
    s = _to_float(record.get("Serving_Size_g"))
    if s:
        for nutrient, serving_col in [
            ("Energy_kcal",    "Energy_per_Serving_kcal"),
            ("Protein_g",      "Protein_per_Serving_g"),
            ("Carbohydrate_g", "Carbs_per_Serving_g"),
            ("Fat_Total_g",    "Fat_per_Serving_g"),
        ]:
            val = _to_float(record.get(nutrient))
            record[serving_col] = round(val * s / 100, 2) if val is not None else None
    return record


def _add_completeness(record: dict) -> dict:
    """Calculate what % of key fields are filled."""
    filled = sum(1 for f in KEY_FIELDS if record.get(f) not in (None, "", "None"))
    record["Data_Completeness_pct"] = round(filled / len(KEY_FIELDS) * 100, 1)
    record["Needs_Review"] = "⚠️ Review" if filled / len(KEY_FIELDS) < 0.6 else "OK"
    return record


def _to_float(val):
    try:
        return float(val) if val not in (None, "", "None") else None
    except (TypeError, ValueError):
        return None


def _safe(val):
    """Convert value to a Sheets-safe type."""
    if val is None:
        return ""
    if isinstance(val, float):
        import math
        if math.isnan(val) or math.isinf(val):
            return ""
        return round(val, 4)
    return val


# ── Test connection ───────────────────────────────────────────────────────────
def test_connection() -> tuple[bool, str]:
    """Quick connectivity test — call this on app startup."""
    try:
        ws = get_worksheet()
        title = ws.spreadsheet.title
        rows  = ws.row_count
        return True, f"Connected to '{title}' ({rows} rows capacity)"
    except FileNotFoundError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Connection failed: {e}"


if __name__ == "__main__":
    ok, msg = test_connection()
    print("✅" if ok else "❌", msg)
    if ok:
        df = read_all()
        print(f"   {len(df)} rows in master sheet")
        print(f"   Columns: {len(df.columns)}")
