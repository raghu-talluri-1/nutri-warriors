"""
migrate_to_sheets.py
---------------------
One-time script to migrate module1_output.xlsx into Google Sheets.
Safe to run multiple times — skips duplicates automatically.

Usage:
  python3 migrate_to_sheets.py
"""

import uuid
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from sheets_connector import bulk_append, COLUMNS, test_connection

SOURCE_FILE = "module1_output.xlsx"
SHEET_NAME  = "Module1_Nutrition_Raw"

# Map Module 1 column names → master schema names
M1_RENAME = {
    "TotalSugar_g":   "Sugar_Total_g",
    "AddedSugar_g":   "Sugar_Added_g",
    "Fat_g":          "Fat_Total_g",
    "SatFat_g":       "Fat_Saturated_g",
    "TransFat_g":     "Fat_Trans_g",
    "Confidence":     "AI_Confidence",
}


def clean(val):
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def make_id():
    return str(uuid.uuid4())[:8].upper()


def load_source():
    if not Path(SOURCE_FILE).exists():
        raise FileNotFoundError(f"{SOURCE_FILE} not found in current folder.")

    df = pd.read_excel(SOURCE_FILE, sheet_name=SHEET_NAME, header=0)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns=M1_RENAME)
    print(f"  📂 Loaded {len(df)} rows from {SOURCE_FILE}")
    return df


def df_to_records(df: pd.DataFrame) -> list[dict]:
    records = []
    today = datetime.now().strftime("%Y-%m-%d")

    for _, row in df.iterrows():
        record = {}
        for col in COLUMNS:
            record[col] = clean(row.get(col))

        if not record.get("Food_ID"):
            record["Food_ID"] = make_id()
        if not record.get("Date_Added"):
            record["Date_Added"] = today
        if not record.get("Source_Module"):
            record["Source_Module"] = "Module1"

        if not record.get("Food_Name"):
            continue

        records.append(record)

    return records


def main():
    print("\n🚀 Migrating data to Google Sheets...\n")

    print("  🔌 Testing Google Sheets connection...")
    ok, msg = test_connection()
    if not ok:
        print(f"  ❌ {msg}")
        return
    print(f"  ✅ {msg}\n")

    df = load_source()
    records = df_to_records(df)
    print(f"  📋 Prepared {len(records)} records\n")

    print("  ☁️  Writing to Google Sheets...")
    result = bulk_append(records)

    print(f"\n✅ Migration complete!")
    print(f"   Added:   {len(result['added'])} foods")
    print(f"   Skipped: {len(result['skipped'])} duplicates")

    if result["added"]:
        print(f"\n   Foods added:")
        for name in result["added"]:
            print(f"   + {name}")

    print(f"\n   🔗 View your sheet:")
    print(f"   https://docs.google.com/spreadsheets/d/1wuFdlPlvmU-1_ZwPOLfsdcLbbOlcyx7BfqbFhHlhvjg/edit\n")


if __name__ == "__main__":
    main()
