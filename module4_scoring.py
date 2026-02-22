"""
module4_scoring.py
-------------------
Health scoring engine for Hi Tech Nutri Warriors.
Scores each food 0-10 across 4 dimensions using ICMR RDA standards.

Used by app.py to compute scores on the fly from Google Sheets data.
"""

# ── ICMR RDA for children 10-12 years ────────────────────────────────────────
# Source: ICMR-NIN 2020 Nutrient Requirements for Indians
ICMR_RDA = {
    "Energy_kcal":       1970,   # kcal/day (average boy/girl 10-12yr)
    "Protein_g":          39.9,  # g/day
    "Fat_Total_g":        65.0,  # g/day (30% of energy)
    "Fat_Saturated_g":    22.0,  # g/day (<10% of energy)
    "Fat_Trans_g":         2.0,  # g/day upper limit (WHO <1% energy)
    "Sugar_Added_g":      25.0,  # g/day (WHO <5% of energy)
    "Sugar_Total_g":      50.0,  # g/day
    "Fibre_g":            25.0,  # g/day
    "Sodium_mg":        2000.0,  # mg/day
    "Calcium_mg":        800.0,  # mg/day
    "Iron_mg":            13.0,  # mg/day (boys); 27mg girls — using lower
}

# ── Scoring weights ───────────────────────────────────────────────────────────
WEIGHTS = {
    "processing":   0.35,
    "sugar":        0.25,
    "fat_sodium":   0.20,
    "nutrition":    0.20,
}

# ── NOVA base scores ──────────────────────────────────────────────────────────
NOVA_SCORES = {1: 10, 2: 7, 3: 4, 4: 1}


def _safe_float(val, default=None):
    """Convert value to float safely."""
    try:
        if val in (None, "", "None", "nan"):
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pct_of_rda(nutrient: str, amount_per_100g, serving_g) -> float:
    """Return % of daily RDA this serving provides. Returns None if data missing."""
    amount = _safe_float(amount_per_100g)
    serving = _safe_float(serving_g)
    rda = ICMR_RDA.get(nutrient)
    if amount is None or serving is None or rda is None or rda == 0:
        return None
    return (amount * serving / 100) / rda * 100


# ── Dimension 1: Processing Score (0-10) ─────────────────────────────────────
def score_processing(record: dict) -> tuple[float, dict]:
    """
    Score based on NOVA class + additive flags.
    Returns (score 0-10, detail dict)
    """
    nova = _safe_float(record.get("NOVA_Class"))
    if nova is None:
        return None, {"error": "NOVA class missing"}

    base = NOVA_SCORES.get(int(nova), 5)
    penalties = {}

    if str(record.get("Has_Artificial_Colors","")).upper() == "YES":
        penalties["Artificial colours"] = -1.0
    if str(record.get("Has_Preservatives","")).upper() == "YES":
        penalties["Preservatives"] = -1.0
    if str(record.get("Has_Artificial_Sweeteners","")).upper() == "YES":
        penalties["Artificial sweeteners"] = -1.0
    if str(record.get("Has_Emulsifiers","")).upper() == "YES":
        penalties["Emulsifiers"] = -0.5
    if str(record.get("Has_MSG","")).upper() == "YES":
        penalties["MSG"] = -0.5
    if str(record.get("Refined_Grain","")).upper() == "YES":
        penalties["Refined grain"] = -0.5

    total_penalty = sum(penalties.values())
    score = max(0.0, min(10.0, base + total_penalty))

    return score, {
        "nova_class": int(nova),
        "nova_base_score": base,
        "penalties": penalties,
        "total_penalty": total_penalty,
    }


# ── Dimension 2: Sugar Score (0-10) ──────────────────────────────────────────
def score_sugar(record: dict) -> tuple[float, dict]:
    """
    Score based on added sugar % of ICMR RDA per serving.
    Lower sugar = higher score.
    """
    serving = _safe_float(record.get("Serving_Size_g"))
    added_sugar_pct = _pct_of_rda("Sugar_Added_g", record.get("Sugar_Added_g"), serving)
    total_sugar_pct = _pct_of_rda("Sugar_Total_g", record.get("Sugar_Total_g"), serving)

    if added_sugar_pct is None and total_sugar_pct is None:
        return None, {"error": "Sugar data missing"}

    score = 10.0
    detail = {}

    if added_sugar_pct is not None:
        detail["added_sugar_pct_rda"] = round(added_sugar_pct, 1)
        if added_sugar_pct > 40:
            score -= 5.0
        elif added_sugar_pct > 25:
            score -= 3.5
        elif added_sugar_pct > 15:
            score -= 2.0
        elif added_sugar_pct > 8:
            score -= 1.0

    if total_sugar_pct is not None:
        detail["total_sugar_pct_rda"] = round(total_sugar_pct, 1)
        if total_sugar_pct > 30:
            score -= 1.5
        elif total_sugar_pct > 15:
            score -= 0.5

    if str(record.get("Refined_Grain","")).upper() == "YES":
        score -= 0.5
        detail["refined_grain_penalty"] = -0.5

    score = max(0.0, min(10.0, score))
    return score, detail


# ── Dimension 3: Fat & Sodium Score (0-10) ───────────────────────────────────
def score_fat_sodium(record: dict) -> tuple[float, dict]:
    """
    Score based on saturated fat, trans fat and sodium % of ICMR RDA per serving.
    """
    serving = _safe_float(record.get("Serving_Size_g"))
    sat_fat_pct   = _pct_of_rda("Fat_Saturated_g", record.get("Fat_Saturated_g"), serving)
    trans_fat_pct = _pct_of_rda("Fat_Trans_g",     record.get("Fat_Trans_g"),     serving)
    sodium_pct    = _pct_of_rda("Sodium_mg",        record.get("Sodium_mg"),       serving)

    if all(x is None for x in [sat_fat_pct, trans_fat_pct, sodium_pct]):
        return None, {"error": "Fat/sodium data missing"}

    score = 10.0
    detail = {}

    # Saturated fat
    if sat_fat_pct is not None:
        detail["sat_fat_pct_rda"] = round(sat_fat_pct, 1)
        if sat_fat_pct > 40:
            score -= 4.0
        elif sat_fat_pct > 25:
            score -= 2.5
        elif sat_fat_pct > 15:
            score -= 1.5
        elif sat_fat_pct > 8:
            score -= 0.5

    # Trans fat — penalise heavily even tiny amounts
    if trans_fat_pct is not None:
        trans_abs = _safe_float(record.get("Fat_Trans_g"), 0)
        serving_trans = (trans_abs or 0) * (serving or 0) / 100
        detail["trans_fat_per_serving_g"] = round(serving_trans, 2)
        if serving_trans > 0.5:
            score -= 4.0
        elif serving_trans > 0.1:
            score -= 2.0
        elif serving_trans > 0:
            score -= 0.5

    # Sodium
    if sodium_pct is not None:
        detail["sodium_pct_rda"] = round(sodium_pct, 1)
        if sodium_pct > 40:
            score -= 3.0
        elif sodium_pct > 25:
            score -= 2.0
        elif sodium_pct > 15:
            score -= 1.0
        elif sodium_pct > 8:
            score -= 0.5

    score = max(0.0, min(10.0, score))
    return score, detail


# ── Dimension 4: Nutrition Value Score (0-10) ─────────────────────────────────
def score_nutrition(record: dict) -> tuple[float, dict]:
    """
    Score based on protein and fibre % of ICMR RDA per serving.
    Higher = better.
    """
    serving = _safe_float(record.get("Serving_Size_g"))
    protein_pct = _pct_of_rda("Protein_g", record.get("Protein_g"), serving)
    fibre_pct   = _pct_of_rda("Fibre_g",   record.get("Fibre_g"),   serving)

    if protein_pct is None and fibre_pct is None:
        return None, {"error": "Protein/fibre data missing"}

    score = 2.0  # baseline — food exists
    detail = {}

    # Protein bonus
    if protein_pct is not None:
        detail["protein_pct_rda"] = round(protein_pct, 1)
        if protein_pct >= 20:
            score += 4.0
        elif protein_pct >= 12:
            score += 3.0
        elif protein_pct >= 6:
            score += 2.0
        elif protein_pct >= 3:
            score += 1.0

    # Fibre bonus
    if fibre_pct is not None:
        detail["fibre_pct_rda"] = round(fibre_pct, 1)
        if fibre_pct >= 20:
            score += 4.0
        elif fibre_pct >= 12:
            score += 3.0
        elif fibre_pct >= 6:
            score += 2.0
        elif fibre_pct >= 3:
            score += 1.0

    score = max(0.0, min(10.0, score))
    return score, detail


# ── RDA % breakdown for display ───────────────────────────────────────────────
def get_rda_breakdown(record: dict) -> dict:
    """
    Returns % of ICMR daily RDA per serving for all key nutrients.
    Used to display the RDA bar chart in the app.
    """
    serving = _safe_float(record.get("Serving_Size_g"))
    if not serving:
        return {}

    nutrients = [
        ("Energy_kcal",    "Energy"),
        ("Protein_g",      "Protein"),
        ("Fat_Total_g",    "Total Fat"),
        ("Fat_Saturated_g","Saturated Fat"),
        ("Fat_Trans_g",    "Trans Fat"),
        ("Sugar_Added_g",  "Added Sugar"),
        ("Fibre_g",        "Fibre"),
        ("Sodium_mg",      "Sodium"),
    ]

    breakdown = {}
    for key, label in nutrients:
        pct = _pct_of_rda(key, record.get(key), serving)
        if pct is not None:
            breakdown[label] = round(pct, 1)

    return breakdown


# ── Master scorer ─────────────────────────────────────────────────────────────
def compute_health_score(record: dict) -> dict:
    """
    Compute full health score for a food record.

    Returns dict with:
      - health_score: 0-10 overall
      - label: Green/Yellow/Orange/Red
      - dimension_scores: each dimension score
      - dimension_detail: breakdown of each dimension
      - rda_breakdown: % of daily RDA per nutrient per serving
      - data_quality: what was missing
    """
    p_score,  p_detail  = score_processing(record)
    s_score,  s_detail  = score_sugar(record)
    fs_score, fs_detail = score_fat_sodium(record)
    n_score,  n_detail  = score_nutrition(record)

    # Collect available dimensions
    available = {}
    if p_score  is not None: available["processing"]  = (p_score,  WEIGHTS["processing"])
    if s_score  is not None: available["sugar"]        = (s_score,  WEIGHTS["sugar"])
    if fs_score is not None: available["fat_sodium"]   = (fs_score, WEIGHTS["fat_sodium"])
    if n_score  is not None: available["nutrition"]    = (n_score,  WEIGHTS["nutrition"])

    # Renormalise weights if some dimensions missing
    total_weight = sum(w for _, w in available.values())
    if total_weight == 0:
        return {"error": "Insufficient data to score"}

    weighted_sum = sum(score * (weight / total_weight)
                       for score, weight in available.values())
    health_score = round(weighted_sum, 2)

    # Label
    if health_score >= 8:
        label = "🟢 Eat freely"
    elif health_score >= 6:
        label = "🟡 In moderation"
    elif health_score >= 4:
        label = "🟠 Occasional treat"
    else:
        label = "🔴 Avoid regularly"

    # Missing data flags
    missing = []
    if p_score  is None: missing.append("NOVA class")
    if s_score  is None: missing.append("Sugar data")
    if fs_score is None: missing.append("Fat/sodium data")
    if n_score  is None: missing.append("Protein/fibre data")

    return {
        "health_score": health_score,
        "label":        label,
        "dimension_scores": {
            "Processing":  round(p_score,  2) if p_score  is not None else None,
            "Sugar":       round(s_score,  2) if s_score  is not None else None,
            "Fat & Sodium":round(fs_score, 2) if fs_score is not None else None,
            "Nutrition":   round(n_score,  2) if n_score  is not None else None,
        },
        "dimension_detail": {
            "Processing":   p_detail,
            "Sugar":        s_detail,
            "Fat & Sodium": fs_detail,
            "Nutrition":    n_detail,
        },
        "rda_breakdown":   get_rda_breakdown(record),
        "missing_data":    missing,
        "data_complete":   len(missing) == 0,
    }


# ── Batch score a DataFrame ───────────────────────────────────────────────────
def score_dataframe(df) -> "pd.DataFrame":
    """Add Health_Score and Health_Label columns to a DataFrame."""
    import pandas as pd

    scores, labels = [], []
    for _, row in df.iterrows():
        result = compute_health_score(row.to_dict())
        scores.append(result.get("health_score"))
        labels.append(result.get("label", "—"))

    df = df.copy()
    df["Health_Score"] = scores
    df["Health_Label"] = labels
    return df


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test with a Parle-G style biscuit
    test_food = {
        "Food_Name":        "Parle-G Biscuits",
        "NOVA_Class":       4,
        "Serving_Size_g":   30,
        "Sugar_Added_g":    17,
        "Sugar_Total_g":    20,
        "Fat_Saturated_g":  4.5,
        "Fat_Trans_g":      0,
        "Sodium_mg":        220,
        "Protein_g":        6.7,
        "Fibre_g":          0.5,
        "Has_Preservatives":       "No",
        "Has_Artificial_Colors":   "No",
        "Has_Artificial_Sweeteners":"No",
        "Has_Emulsifiers":         "No",
        "Has_MSG":                 "No",
        "Refined_Grain":           "Yes",
    }

    result = compute_health_score(test_food)
    print(f"\n{'='*50}")
    print(f"Food: {test_food['Food_Name']}")
    print(f"Health Score: {result['health_score']}/10  {result['label']}")
    print(f"\nDimension Scores:")
    for dim, score in result["dimension_scores"].items():
        print(f"  {dim:15s}: {score}/10")
    print(f"\nRDA % per serving ({test_food['Serving_Size_g']}g):")
    for nutrient, pct in result["rda_breakdown"].items():
        bar = "█" * int(pct / 5) + "░" * max(0, 20 - int(pct / 5))
        flag = " ⚠️" if pct > 25 else ""
        print(f"  {nutrient:15s}: {bar} {pct}%{flag}")
    print(f"\nMissing data: {result['missing_data'] or 'None — fully scored'}")
    print(f"{'='*50}\n")

    # Test with an apple
    apple = {
        "Food_Name":        "Apple",
        "NOVA_Class":       1,
        "Serving_Size_g":   150,
        "Sugar_Added_g":    0,
        "Sugar_Total_g":    10,
        "Fat_Saturated_g":  0.1,
        "Fat_Trans_g":      0,
        "Sodium_mg":        1,
        "Protein_g":        0.3,
        "Fibre_g":          2.4,
        "Has_Preservatives":       "No",
        "Has_Artificial_Colors":   "No",
        "Has_Artificial_Sweeteners":"No",
        "Has_Emulsifiers":         "No",
        "Has_MSG":                 "No",
        "Refined_Grain":           "No",
    }

    result2 = compute_health_score(apple)
    print(f"Food: {apple['Food_Name']}")
    print(f"Health Score: {result2['health_score']}/10  {result2['label']}")
    print(f"\nDimension Scores:")
    for dim, score in result2["dimension_scores"].items():
        print(f"  {dim:15s}: {score}/10")
    print(f"\nRDA % per serving ({apple['Serving_Size_g']}g):")
    for nutrient, pct in result2["rda_breakdown"].items():
        bar = "█" * int(pct / 5) + "░" * max(0, 20 - int(pct / 5))
        print(f"  {nutrient:15s}: {bar} {pct}%")
