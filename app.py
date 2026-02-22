"""
app.py — Hi Tech Nutri Warriors
---------------------------------
Streamlit web app. Runs at nutri-warriors.streamlit.app

Features:
  Tab 1 — 📸 Scan Food       (Module 2 logic — photo → Claude Vision → Google Sheets)
  Tab 2 — 📊 Food Database   (live view of master sheet with filters)
  Tab 3 — 🏆 Leaderboard     (who scanned most, healthiest/worst foods)
  Tab 4 — ℹ️  How to Use     (guide for the kids)

Run locally:
  streamlit run app.py

Deploy:
  Push to GitHub → connect at share.streamlit.io → add secrets in dashboard
"""

import os
import json
import base64
import re
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st
from module4_scoring import compute_health_score, get_rda_breakdown
import pandas as pd
import anthropic

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Hi Tech Nutri Warriors",
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Constants ─────────────────────────────────────────────────────────────────
KIDS = ["Aditya", "Vihaan", "Samar", "Shashvath"]

NUTRIENT_KEYS = [
    "Protein_g", "Carbohydrate_g", "Fat_Total_g", "Fat_Saturated_g",
    "Fat_Trans_g", "Sugar_Total_g", "Sugar_Added_g", "Cholesterol_mg",
    "Sodium_mg", "Energy_kcal",
]

NOVA_LABELS = {
    1: "🟢 1 — Unprocessed",
    2: "🟡 2 — Processed ingredient",
    3: "🟠 3 — Processed food",
    4: "🔴 4 — Ultra-processed",
}

# Claude Vision prompt
SCAN_PROMPT = """You are a nutrition expert assistant for a school science project about food health.

Analyse this food image and return ONLY a valid JSON object. No markdown, no explanation, just JSON.

{
  "food_name": "exact name of the food",
  "brand": "brand name or empty string if unpackaged",
  "category": "one of: Snack, Biscuit/Cookie, Noodles/Pasta, Dairy, Fruit, Vegetable, Beverage, Chocolate, Chips, Bread/Roti, Rice/Grain, Legume, Nut/Seed, Processed Meat, Other",
  "food_type": "packaged or unpackaged",
  "serving_size_g": typical serving size as a number,
  "nova_class": 1 or 2 or 3 or 4,
  "nova_reason": "one sentence explaining NOVA classification",
  "confidence": confidence 0.0 to 1.0,
  "confidence_reason": "brief reason",
  "has_preservatives": true or false,
  "preservative_list": "comma-separated INS codes or empty string",
  "has_artificial_colors": true or false,
  "color_list": "comma-separated or empty string",
  "has_emulsifiers": true or false,
  "emulsifier_list": "comma-separated INS codes or empty string",
  "has_msg": true or false,
  "has_artificial_sweeteners": true or false,
  "sweetener_list": "comma-separated or empty string",
  "refined_grain": true or false,
  "nutrients_per_100g": {
    "Energy_kcal": number,
    "Protein_g": number,
    "Carbohydrate_g": number,
    "Sugar_Total_g": number,
    "Sugar_Added_g": number,
    "Fat_Total_g": number,
    "Fat_Saturated_g": number,
    "Fat_Trans_g": number,
    "Fibre_g": number,
    "Sodium_mg": number,
    "Cholesterol_mg": number
  }
}

Rules:
- All nutrients must be per 100g of the food
- Use 0 not null for nutrients that are absent
- For packaged food where you can read the label, use label values (higher confidence)
- For fresh/unpackaged food, use USDA/IFCT standard values
- NOVA 1=unprocessed natural food, 2=processed culinary ingredient (oil/sugar/salt),
  3=processed food (canned, fermented), 4=ultra-processed (additives, flavours, emulsifiers)
"""


# ── Google Sheets connection ──────────────────────────────────────────────────
@st.cache_resource(ttl=60)
def get_sheets_client():
    """Get authenticated gspread client — uses Streamlit secrets or local credentials.json."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        # Streamlit Cloud: credentials stored in st.secrets
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"], scopes=SCOPES
            )
        # Local dev: credentials.json file
        elif Path("credentials.json").exists():
            creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
        else:
            return None, "credentials.json not found and no Streamlit secrets configured"

        client = gspread.authorize(creds)
        return client, None

    except Exception as e:
        return None, str(e)


SHEET_ID = "1wuFdlPlvmU-1_ZwPOLfsdcLbbOlcyx7BfqbFhHlhvjg"


@st.cache_data(ttl=30)
def load_sheet_data() -> pd.DataFrame:
    """Load all data from Google Sheet (cached for 30 seconds)."""
    client, err = get_sheets_client()
    if err or not client:
        return pd.DataFrame()
    try:
        sheet = client.open_by_key(SHEET_ID)
        ws    = sheet.worksheet("Master_Food_Data")
        data  = ws.get_all_records()
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()


def append_to_sheet(record: dict) -> tuple[bool, str]:
    """Append one food record to the Sheet."""
    client, err = get_sheets_client()
    if err or not client:
        return False, f"Sheet connection failed: {err}"
    try:
        from sheets_connector import COLUMNS, _safe, _add_per_serving, _add_completeness
        record = _add_per_serving(record)
        record = _add_completeness(record)
        row = [_safe(record.get(col)) for col in COLUMNS]
        sheet = client.open_by_key(SHEET_ID)
        ws    = sheet.worksheet("Master_Food_Data")
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True, f"✅ '{record.get('Food_Name')}' saved to database"
    except Exception as e:
        return False, f"❌ Failed to save: {e}"


# ── Claude Vision scan ────────────────────────────────────────────────────────
def scan_food_image(image_bytes: bytes, media_type: str, food_hint: str = "") -> dict:
    """Send image to Claude Vision, return parsed nutrition dict."""
    api_key = (
        st.secrets.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in secrets or environment")

    client   = anthropic.Anthropic(api_key=api_key)
    b64_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    # Build prompt — include food hint if provided
    prompt_text = SCAN_PROMPT
    if food_hint:
        prompt_text = f"The user has identified this food as: '{food_hint}'. Use this as your primary reference for food_name, but still analyse the image for nutrition and processing details.\n\n" + SCAN_PROMPT

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}},
                {"type": "text",  "text": prompt_text},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def build_record(scan: dict, kid: str, image_name: str) -> dict:
    """Convert Claude scan result into a master schema record."""
    n = scan.get("nutrients_per_100g", {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    return {
        "Food_ID":        str(uuid.uuid4())[:8].upper(),
        "Food_Name":      scan.get("food_name", "Unknown"),
        "Brand":          scan.get("brand", ""),
        "Category":       scan.get("category", ""),
        "Source_Kid":     kid,
        "Data_Type":      "Packaged (label read)" if scan.get("food_type") == "packaged" else "Unpackaged (AI estimate)",
        "Date_Added":     datetime.now().strftime("%Y-%m-%d"),
        "Source_Module":  "Module2",
        "Energy_kcal":    n.get("Energy_kcal"),
        "Protein_g":      n.get("Protein_g"),
        "Carbohydrate_g": n.get("Carbohydrate_g"),
        "Sugar_Total_g":  n.get("Sugar_Total_g"),
        "Sugar_Added_g":  n.get("Sugar_Added_g"),
        "Fat_Total_g":    n.get("Fat_Total_g"),
        "Fat_Saturated_g":n.get("Fat_Saturated_g"),
        "Fat_Trans_g":    n.get("Fat_Trans_g"),
        "Fibre_g":        n.get("Fibre_g"),
        "Sodium_mg":      n.get("Sodium_mg"),
        "Cholesterol_mg": n.get("Cholesterol_mg"),
        "Serving_Size_g": scan.get("serving_size_g"),
        "NOVA_Class":     scan.get("nova_class"),
        "NOVA_Verified":  "No",
        "Has_Preservatives":        "Yes" if scan.get("has_preservatives") else "No",
        "Preservative_List":        scan.get("preservative_list", ""),
        "Has_Artificial_Colors":    "Yes" if scan.get("has_artificial_colors") else "No",
        "Color_List":               scan.get("color_list", ""),
        "Has_Emulsifiers":          "Yes" if scan.get("has_emulsifiers") else "No",
        "Emulsifier_List":          scan.get("emulsifier_list", ""),
        "Has_Artificial_Sweeteners":"Yes" if scan.get("has_artificial_sweeteners") else "No",
        "Sweetener_List":           scan.get("sweetener_list", ""),
        "Has_MSG":                  "Yes" if scan.get("has_msg") else "No",
        "Refined_Grain":            "Yes" if scan.get("refined_grain") else "No",
        "AI_Confidence":            scan.get("confidence", 0),
        "Notes": f"Image: {image_name} | Confidence: {scan.get('confidence',0):.0%} ({scan.get('confidence_reason','')}) | Scanned: {ts}",
    }


# ── Styling ───────────────────────────────────────────────────────────────────
def nova_badge(nova_class):
    colors = {1:"#2E7D32", 2:"#F9A825", 3:"#E65100", 4:"#B71C1C"}
    labels = {1:"NOVA 1 · Unprocessed", 2:"NOVA 2 · Processed Ingredient",
              3:"NOVA 3 · Processed", 4:"NOVA 4 · Ultra-processed"}
    c = int(nova_class) if nova_class else 0
    col = colors.get(c, "#607D8B")
    lbl = labels.get(c, "NOVA ?")
    return f'<span style="background:{col};color:white;padding:3px 10px;border-radius:12px;font-size:0.8em;font-weight:bold">{lbl}</span>'


def confidence_bar(conf):
    pct = int(float(conf) * 100)
    col = "#2E7D32" if pct >= 80 else "#F9A825" if pct >= 60 else "#B71C1C"
    return f"""
    <div style="background:#eee;border-radius:8px;height:12px;width:100%">
      <div style="background:{col};width:{pct}%;height:12px;border-radius:8px"></div>
    </div>
    <small>{pct}% confidence</small>"""


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .main-header {
    background: linear-gradient(135deg, #1B6CA8, #2E7D32);
    color: white; padding: 20px 30px; border-radius: 12px;
    margin-bottom: 24px; text-align: center;
  }
  .main-header h1 { margin: 0; font-size: 2em; }
  .main-header p  { margin: 4px 0 0; opacity: 0.85; font-size: 1em; }
  .nutrient-card {
    background: #f8f9fa; border-radius: 10px;
    padding: 12px 16px; margin: 4px 0;
    border-left: 4px solid #1B6CA8;
  }
  .nutrient-card.warning { border-left-color: #E65100; background: #FFF3E0; }
  .stat-box {
    background: white; border-radius: 10px; padding: 16px;
    text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }
  .stat-box h2 { margin: 0; font-size: 2em; color: #1B6CA8; }
  .stat-box p  { margin: 4px 0 0; color: #666; font-size: 0.85em; }
  .stTabs [data-baseweb="tab"] { font-size: 1em; }
</style>
""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
  <h1>🥗 Hi Tech Nutri Warriors</h1>
  <p>Scan any food · Build the database · Score your health</p>
</div>
""", unsafe_allow_html=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📸 Scan Food", "📊 Database", "🏆 Leaderboard", "ℹ️ How to Use"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — SCAN FOOD
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Scan a Food Item")
    st.caption("Take a photo of any food or snack — packaged or fresh. Claude will analyse the nutrition.")

    col1, col2 = st.columns([1, 1], gap="large")

    with col1:
        kid = st.selectbox("👤 Who are you?", KIDS)
        input_method = st.radio(
            "How do you want to add the photo?",
            ["📷 Use Camera", "🖼️ Upload File"],
            horizontal=True
        )

        if input_method == "📷 Use Camera":
            uploaded = st.camera_input(
                "Point your camera at the food or its label"
            )
        else:
            uploaded = st.file_uploader(
                "📸 Upload food photo",
                type=["jpg", "jpeg", "png", "webp"],
                help="Take a photo of the food label or the food itself"
            )

        if uploaded:
            # camera_input has no .name attribute — handle both cases
            img_name = getattr(uploaded, "name", "camera_capture.jpg")
            st.image(uploaded, caption=img_name, use_container_width=True)

            st.markdown("**Step 2 — Confirm what this food is**")
            food_hint = st.text_input(
                "What is this food? (optional — helps Claude be more accurate)",
                placeholder="e.g. Parle-G biscuits, Orange, Maggi Masala Noodles...",
                help="Leave blank and Claude will identify it from the photo. Fill in if you already know what it is."
            )
            if food_hint:
                st.caption(f"✏️ Claude will use your hint: '{food_hint}'")
            else:
                st.caption("🔍 Claude will identify the food from the photo automatically")

    with col2:
        if uploaded:
            if "scan_result" not in st.session_state:
                st.session_state.scan_result = None
                st.session_state.scan_record = None

            if st.button("🔍 Analyse This Food", type="primary", use_container_width=True):
                with st.spinner("Claude is analysing your food..."):
                    try:
                        # Handle both camera_input (no .name) and file_uploader
                        img_name = getattr(uploaded, "name", "camera_capture.jpg")
                        ext = Path(img_name).suffix.lower()
                        mt_map = {".jpg":"image/jpeg",".jpeg":"image/jpeg",
                                  ".png":"image/png",".webp":"image/webp"}
                        media_type = mt_map.get(ext, "image/jpeg") if ext else "image/png"
                        scan = scan_food_image(uploaded.read(), media_type, food_hint=food_hint)
                        record = build_record(scan, kid, img_name)
                        st.session_state.scan_result = scan
                        st.session_state.scan_record = record
                    except Exception as e:
                        st.error(f"Analysis failed: {e}")
                        st.session_state.scan_result = None

            # Show results
            if st.session_state.get("scan_result"):
                scan   = st.session_state.scan_result
                record = st.session_state.scan_record
                n      = scan.get("nutrients_per_100g", {})

                st.markdown("---")
                st.markdown(f"### {scan.get('food_name','Unknown')}")
                if scan.get("brand"):
                    st.caption(f"Brand: {scan['brand']}")

                st.markdown(nova_badge(scan.get("nova_class")), unsafe_allow_html=True)
                st.markdown(f"*{scan.get('nova_reason','')}*")
                st.markdown(confidence_bar(scan.get("confidence",0)), unsafe_allow_html=True)

                st.markdown("**Nutrition per 100g:**")
                nc1, nc2 = st.columns(2)
                nutrients_display = [
                    ("Energy", n.get("Energy_kcal"), "kcal", False),
                    ("Protein", n.get("Protein_g"), "g", False),
                    ("Carbohydrate", n.get("Carbohydrate_g"), "g", False),
                    ("Total Sugar", n.get("Sugar_Total_g"), "g", n.get("Sugar_Total_g",0) > 10),
                    ("Added Sugar", n.get("Sugar_Added_g"), "g", n.get("Sugar_Added_g",0) > 5),
                    ("Total Fat", n.get("Fat_Total_g"), "g", False),
                    ("Saturated Fat", n.get("Fat_Saturated_g"), "g", n.get("Fat_Saturated_g",0) > 5),
                    ("Trans Fat", n.get("Fat_Trans_g"), "g", n.get("Fat_Trans_g",0) > 0),
                    ("Sodium", n.get("Sodium_mg"), "mg", n.get("Sodium_mg",0) > 400),
                    ("Fibre", n.get("Fibre_g"), "g", False),
                ]
                for i, (label, val, unit, warn) in enumerate(nutrients_display):
                    target = nc1 if i % 2 == 0 else nc2
                    cls = "nutrient-card warning" if warn else "nutrient-card"
                    target.markdown(
                        f'<div class="{cls}"><b>{label}</b><br>'
                        f'{val:.1f} {unit}</div>', unsafe_allow_html=True
                    )

                # Additives flags
                st.markdown("**Processing flags:**")
                flags = []
                if scan.get("has_preservatives"): flags.append(f"🔴 Preservatives: {scan.get('preservative_list','')}")
                if scan.get("has_artificial_colors"): flags.append(f"🔴 Artificial colors: {scan.get('color_list','')}")
                if scan.get("has_emulsifiers"): flags.append(f"🟠 Emulsifiers: {scan.get('emulsifier_list','')}")
                if scan.get("has_artificial_sweeteners"): flags.append(f"🟠 Sweeteners: {scan.get('sweetener_list','')}")
                if scan.get("has_msg"): flags.append("🟡 Contains MSG")
                if scan.get("refined_grain"): flags.append("🟡 Refined grain")
                if not flags: flags.append("🟢 No major processing flags detected")
                for f in flags:
                    st.markdown(f"- {f}")

                # ── Health Score ──────────────────────────────────
                st.markdown("---")
                st.markdown("**🏅 Health Score**")
                scoring_record = record.copy()
                scoring_record.update({k: scan.get("nutrients_per_100g", {}).get(k) for k in [
                    "Energy_kcal","Protein_g","Carbohydrate_g","Sugar_Total_g",
                    "Sugar_Added_g","Fat_Total_g","Fat_Saturated_g","Fat_Trans_g",
                    "Fibre_g","Sodium_mg"
                ]})
                health = compute_health_score(scoring_record)

                if "error" not in health:
                    hs = health["health_score"]
                    col = "#2E7D32" if hs>=8 else "#F9A825" if hs>=6 else "#E65100" if hs>=4 else "#B71C1C"
                    st.markdown(
                        f'<div style="background:{col};color:white;padding:12px 20px;'
                        f'border-radius:12px;text-align:center;font-size:1.4em;font-weight:bold">'
                        f'{hs}/10 &nbsp; {health["label"]}</div>',
                        unsafe_allow_html=True
                    )

                    # Dimension breakdown
                    st.markdown("**Score breakdown:**")
                    dcols = st.columns(4)
                    for i, (dim, score) in enumerate(health["dimension_scores"].items()):
                        if score is not None:
                            dcols[i].metric(dim, f"{score}/10")

                    # RDA breakdown
                    if health["rda_breakdown"]:
                        st.markdown(f"**% of daily allowance per serving ({record.get('Serving_Size_g','?')}g):**")
                        for nutrient, pct in health["rda_breakdown"].items():
                            bar_col = "#B71C1C" if pct > 30 else "#E65100" if pct > 15 else "#2E7D32"
                            st.markdown(
                                f'<div style="margin:3px 0">'
                                f'<span style="display:inline-block;width:120px;font-size:0.85em">{nutrient}</span>'
                                f'<div style="display:inline-block;width:60%;background:#eee;border-radius:6px;height:14px;vertical-align:middle">'
                                f'<div style="background:{bar_col};width:{min(pct,100)}%;height:14px;border-radius:6px"></div></div>'
                                f'<span style="font-size:0.85em;margin-left:8px">{pct}%</span></div>',
                                unsafe_allow_html=True
                            )

                    if health["missing_data"]:
                        st.caption(f"⚠️ Partial score — missing: {', '.join(health['missing_data'])}")
                else:
                    st.warning("Not enough data to compute health score yet.")

                st.markdown("---")

                # Verify NOVA + save
                nova_verified = st.checkbox(
                    f"✅ I confirm NOVA {scan.get('nova_class')} classification is correct",
                    help="Check this if you agree with Claude's NOVA rating"
                )
                if nova_verified:
                    st.session_state.scan_record["NOVA_Verified"] = "Yes"

                if st.button("💾 Save to Database", type="primary", use_container_width=True):
                    success, msg = append_to_sheet(st.session_state.scan_record)
                    if success:
                        st.success(msg)
                        st.balloons()
                        st.session_state.scan_result = None
                        st.session_state.scan_record = None
                        load_sheet_data.clear()
                    else:
                        st.error(msg)
        else:
            st.info("👈 Upload a food photo to get started")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — DATABASE
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Food Database")

    df = load_sheet_data()

    if df.empty:
        st.info("No foods in the database yet. Scan your first food in the 📸 tab!")
    else:
        # Filters
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            kid_filter = st.multiselect("Filter by kid", KIDS, default=KIDS)
        with fc2:
            nova_opts = sorted([x for x in df["NOVA_Class"].unique() if x != ""])
            nova_filter = st.multiselect("Filter by NOVA class", nova_opts, default=nova_opts)
        with fc3:
            cat_opts = sorted([x for x in df["Category"].unique() if x != ""])
            cat_filter = st.multiselect("Filter by category", cat_opts, default=cat_opts)

        # Safely filter — handle missing columns gracefully
        mask = pd.Series([True] * len(df))
        if "Source_Kid" in df.columns:
            mask &= df["Source_Kid"].isin(kid_filter)
        if "NOVA_Class" in df.columns and nova_filter:
            mask &= df["NOVA_Class"].isin(nova_filter)
        if "Category" in df.columns and cat_filter:
            mask &= df["Category"].isin(cat_filter)
        filtered = df[mask]

        # Stats row
        s1, s2, s3, s4 = st.columns(4)
        s1.markdown(f'<div class="stat-box"><h2>{len(filtered)}</h2><p>Foods scanned</p></div>', unsafe_allow_html=True)
        nova4 = len(filtered[filtered["NOVA_Class"].astype(str) == "4"])
        s2.markdown(f'<div class="stat-box"><h2>{nova4}</h2><p>Ultra-processed (NOVA 4)</p></div>', unsafe_allow_html=True)
        try:
            avg_energy = filtered["Energy_kcal"].replace("", None).dropna().astype(float).mean()
            s3.markdown(f'<div class="stat-box"><h2>{avg_energy:.0f}</h2><p>Avg kcal per 100g</p></div>', unsafe_allow_html=True)
        except Exception:
            s3.markdown('<div class="stat-box"><h2>—</h2><p>Avg kcal per 100g</p></div>', unsafe_allow_html=True)
        try:
            avg_sodium = filtered["Sodium_mg"].replace("", None).dropna().astype(float).mean()
            s4.markdown(f'<div class="stat-box"><h2>{avg_sodium:.0f}</h2><p>Avg sodium mg per 100g</p></div>', unsafe_allow_html=True)
        except Exception:
            s4.markdown('<div class="stat-box"><h2>—</h2><p>Avg sodium mg</p></div>', unsafe_allow_html=True)

        st.markdown("---")

        # Table — show key columns only
        display_cols = [c for c in [
            "Food_Name","Brand","Category","Source_Kid","NOVA_Class",
            "Energy_kcal","Protein_g","Carbohydrate_g","Fat_Total_g",
            "Sugar_Total_g","Sodium_mg","Has_Preservatives",
            "Has_Artificial_Colors","Data_Completeness_pct","Source_Module"
        ] if c in filtered.columns]

        st.dataframe(
            filtered[display_cols].reset_index(drop=True),
            use_container_width=True,
            height=420,
        )

        st.caption(f"Showing {len(filtered)} of {len(df)} foods · "
                   f"[Open full Google Sheet ↗](https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit)")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — LEADERBOARD
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("🏆 Leaderboard")
    df = load_sheet_data()

    if df.empty:
        st.info("No data yet. Start scanning foods!")
    else:
        lc1, lc2 = st.columns(2)

        with lc1:
            st.markdown("**Foods scanned per kid**")
            counts = df["Source_Kid"].value_counts().reset_index() if "Source_Kid" in df.columns else pd.DataFrame({"Source_Kid":[],"count":[]})
            counts.columns = ["Kid", "Foods Scanned"]
            st.dataframe(counts, use_container_width=True, hide_index=True)

            st.markdown("**NOVA class breakdown**")
            try:
                nova_counts = df["NOVA_Class"].replace("",None).dropna().astype(int).value_counts().sort_index()
                nova_df = pd.DataFrame({
                    "NOVA Class": [NOVA_LABELS.get(k,str(k)) for k in nova_counts.index],
                    "Count": nova_counts.values
                })
                st.dataframe(nova_df, use_container_width=True, hide_index=True)
            except Exception:
                st.write("NOVA data not yet available")

        with lc2:
            st.markdown("**Most sodium (top 5 — worth knowing!)**")
            try:
                cols = [c for c in ["Food_Name","Source_Kid","Sodium_mg"] if c in df.columns]
                sod = df[cols].copy()
                sod["Sodium_mg"] = pd.to_numeric(sod["Sodium_mg"], errors="coerce")
                sod = sod.dropna().sort_values("Sodium_mg", ascending=False).head(5)
                st.dataframe(sod.reset_index(drop=True), use_container_width=True, hide_index=True)
            except Exception:
                st.write("—")

            st.markdown("**Most added sugar (top 5)**")
            try:
                cols = [c for c in ["Food_Name","Source_Kid","Sugar_Added_g"] if c in df.columns]
                sug = df[cols].copy()
                sug["Sugar_Added_g"] = pd.to_numeric(sug["Sugar_Added_g"], errors="coerce")
                sug = sug.dropna().sort_values("Sugar_Added_g", ascending=False).head(5)
                st.dataframe(sug.reset_index(drop=True), use_container_width=True, hide_index=True)
            except Exception:
                st.write("—")

        st.markdown("---")
        st.markdown("**Category distribution**")
        try:
            cat_counts = df["Category"].replace("",None).dropna().value_counts()
            st.bar_chart(cat_counts)
        except Exception:
            st.write("Category data not yet available")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — HOW TO USE
# ════════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("How to Use This App")
    st.markdown("""
    ### 📸 Scanning a Food
    1. Go to the **Scan Food** tab
    2. Select your name from the dropdown
    3. Tap **Upload food photo** — you can take a photo directly from your phone camera
    4. Tap **Analyse This Food** — Claude AI will read the nutrition facts and identify additives
    5. Review the results — check if the NOVA class looks right
    6. Tap **Save to Database** — it appears in the shared Google Sheet instantly

    ---

    ### 📊 The Database
    - Every food scanned by any kid appears here in real time
    - You can filter by kid, NOVA class, and category
    - Click **Open full Google Sheet** to see all columns including CGM data

    ---

    ### 🔬 NOVA Classification
    | Class | Meaning | Examples |
    |---|---|---|
    | 🟢 NOVA 1 | Unprocessed natural food | Apple, milk, egg, rice |
    | 🟡 NOVA 2 | Processed culinary ingredient | Oil, sugar, salt, butter |
    | 🟠 NOVA 3 | Processed food | Cheese, canned fish, bread |
    | 🔴 NOVA 4 | Ultra-processed | Chips, instant noodles, packaged cookies, soft drinks |

    ---

    ### 💡 Tips for Good Scans
    - **Packaged food:** photograph the nutrition label clearly — Claude reads it directly
    - **Fresh food:** photograph the whole item — Claude estimates from standard databases
    - **Good lighting** makes a big difference for label scans
    - If confidence is below 60%, try a clearer photo

    ---

    ### 📋 What to Fill In After Scanning
    Open the Google Sheet and fill in any **empty yellow cells** for your food:
    - Fibre content (from the label)
    - Micronutrients (Calcium, Iron, Vitamin C, D, Potassium)
    - Verify the NOVA class with your own reading of the ingredient list
    """)
