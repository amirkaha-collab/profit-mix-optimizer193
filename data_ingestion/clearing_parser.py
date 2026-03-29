# -*- coding: utf-8 -*-
"""
data_ingestion/clearing_parser.py
───────────────────────────────────
Pure clearing-report parser and baseline calculator.

Extracted from streamlit_app.py so client_flow.py and other modules
can import these without triggering streamlit_app's module-level code
(_check_password → st.secrets → StreamlitSecretNotFoundError).

Zero Streamlit dependencies.
"""
from __future__ import annotations
import io, math, re
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


PARAM_ALIASES = {
    # קרנות השתלמות
    "stocks":   ["סך חשיפה למניות", "מניות", "חשיפה למניות"],
    "foreign":  ['סך חשיפה לנכסים המושקעים בחו"ל', "סך חשיפה לנכסים המושקעים בחו׳ל",
                 'חו"ל', "חו׳ל", "חשיפה לנכסים המושקעים בחו"],
    "fx":       ['חשיפה למט"ח', 'מט"ח', "מט׳׳ח"],
    "illiquid": ["נכסים לא סחירים", "לא סחירים", "לא-סחיר", "לא סחיר"],
    "sharpe":   ["מדד שארפ", "שארפ"],
}


def _to_float(x) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return np.nan
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = re.sub(r"[^\d.\-]", "", str(x).replace(",", "").replace("−", "-"))
    if s in ("", "-", "."):
        return np.nan
    try:
        return float(s)
    except Exception:
        return np.nan


def _match_param(row_name: str, key: str) -> bool:
    rn = str(row_name).strip()
    return any(a in rn for a in PARAM_ALIASES[key])


_PRODUCT_KEYWORDS = [
    ("קרן פנסיה",           ["פנסיה","pension"]),
    ("קרן השתלמות",         ["השתלמות","hishtalmut"]),
    ("ביטוח מנהלים",        ["ביטוח מנהלים","מנהלים"]),
    ("פוליסת חיסכון",       ["פוליסה","פוליסת","polisa"]),
    ("קופת גמל להשקעה",     ["גמל להשקעה","gemel lehashkaa"]),
    ("קופת גמל",            ["קופת גמל","קופה לגמל","gemel"]),
    ("קופה מרכזית לפיצויים",["פיצויים","merkazi"]),
]

def _infer_product_type(name: str) -> str:
    """Infer product type from fund/manager name text."""
    nl = (name or "").strip().lower()
    for pt, keywords in _PRODUCT_KEYWORDS:
        if any(k.lower() in nl for k in keywords):
            return pt
    return "קרן השתלמות"  # default for most clearing reports


def _extract_manager(fund_name: str) -> str:
    name = str(fund_name).strip()
    for splitter in [" קרן", " השתלמות", " -", "-", "  "]:
        if splitter in name:
            head = name.split(splitter)[0].strip()
            if head:
                return head
    return name.split()[0] if name.split() else name

def _gsheet_to_bytes(sheet_id: str) -> Tuple[bytes, str]:
    import requests as _req
    urls = [
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx",
        f"https://docs.google.com/feeds/download/spreadsheets/Export?key={sheet_id}&exportFormat=xlsx",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    last_err = ""
    for url in urls:
        try:
            resp = _req.get(url, headers=headers, allow_redirects=True, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 500:
                if resp.content[:2] == b"PK":
                    return resp.content, ""
                else:
                    preview = resp.content[:120].decode("utf-8", errors="ignore").replace("\n"," ") if resp.content else ""
                    last_err = (
                        f"קוד 200 אבל התקבל HTML במקום XLSX (גיליון {sheet_id[:20]}). "
                        "בדוק ש-Share מוגדר 'Anyone with the link' כ-Viewer. "
                        f"Preview: {preview[:80]}"
                    )
            else:
                last_err = f"HTTP {resp.status_code} מ-{url[:60]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    return b"", last_err

def _load_service_scores(xlsx_bytes: bytes) -> Tuple[Dict[str, float], str]:
    try:
        df = pd.read_excel(io.BytesIO(xlsx_bytes), header=None)
    except Exception as e:
        return {}, f"שגיאה בטעינת ציוני שירות: {e}"
    if df.empty:
        return {}, "גיליון ציוני שירות ריק"

    try:
        df_hdr = pd.read_excel(io.BytesIO(xlsx_bytes))
        if not df_hdr.empty:
            cols = [str(c).lower().strip() for c in df_hdr.columns]
            df_hdr.columns = cols
            if "provider" in df_hdr.columns and "score" in df_hdr.columns:
                out = {}
                for _, r in df_hdr.iterrows():
                    p = _extract_manager(str(r["provider"]).strip())
                    sc = _to_float(r["score"])
                    if p and not math.isnan(sc):
                        out[p] = float(sc)
                if out:
                    return out, ""
    except Exception:
        pass

    df2 = df.copy().dropna(how="all").dropna(how="all", axis=1)
    if df2.shape[0] >= 2 and df2.shape[1] >= 2:
        first_col = df2.iloc[:, 0].astype(str).str.strip().str.lower()
        prov_rows = df2.index[first_col.eq("provider")].tolist()
        combo_cell = df2.iloc[:, 0].astype(str).str.strip().str.lower()
        combo_rows = df2.index[combo_cell.str.contains("provider") & combo_cell.str.contains("score")].tolist()
        for r0 in combo_rows:
            if r0 not in prov_rows:
                prov_rows.append(r0)
        for r0 in prov_rows[:3]:
            if r0 + 1 in df2.index:
                header = df2.loc[r0].tolist()
                values = df2.loc[r0 + 1].tolist()
                tag = str(values[0]).strip().lower()
                if tag in {"score", "ציון", "שירות ואיכות", "ציון שירות"} or tag in {"nan", "", "none"}:
                    out = {}
                    for name, val in zip(header[1:], values[1:]):
                        p = _extract_manager(str(name).strip())
                        sc = _to_float(val)
                        if p and not math.isnan(sc):
                            out[p] = float(sc)
                    if out:
                        return out, ""

    return {}, "מבנה גיליון שירות לא מזוהה"

def parse_clearing_report(xlsx_bytes: bytes) -> Tuple[Optional[Dict], str]:
    """
    מנתח דו"ח מסלקה (Excel) ומחזיר dict עם:
      {
        "holdings": [{"fund": str, "manager": str, "track": str, "amount": float, "weight_pct": float}],
        "total_amount": float,
        "baseline": {"foreign": float, "stocks": float, "fx": float, "illiquid": float}
      }
    או None + הודעת שגיאה.
    """
    AMOUNT_ALIASES  = ["יתרה", "ערך", "סכום", "balance", "amount", "שווי"]
    FUND_ALIASES    = ["שם הקרן", "קרן", "שם מוצר", "fund", "product", "שם הקופה", "שם הגוף"]
    MANAGER_ALIASES = ["מנהל", "גוף מנהל", "בית השקעות", "manager", "provider", "מנהל ההשקעות"]
    TRACK_ALIASES   = ["מסלול", "track", "שם מסלול"]

    try:
        xls = pd.ExcelFile(io.BytesIO(xlsx_bytes))
    except Exception as e:
        return None, f"לא ניתן לפתוח את הקובץ: {e}"

    all_records = []

    for sheet in xls.sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet, header=None)
        except Exception:
            continue
        if df.empty or df.shape[0] < 2:
            continue

        # Find header row (contains at least one known alias)
        header_idx = None
        for i in range(min(10, len(df))):
            row_vals = [str(v).strip().lower() for v in df.iloc[i].tolist()]
            matches = sum(
                1 for v in row_vals
                if any(a.lower() in v for a in AMOUNT_ALIASES + FUND_ALIASES + MANAGER_ALIASES)
            )
            if matches >= 2:
                header_idx = i
                break

        if header_idx is None:
            continue

        df_clean = df.iloc[header_idx:].copy().reset_index(drop=True)
        df_clean.columns = [str(c).strip() for c in df_clean.iloc[0].tolist()]
        df_clean = df_clean.iloc[1:].reset_index(drop=True)

        def _find_col(aliases):
            for col in df_clean.columns:
                col_l = col.lower()
                for a in aliases:
                    if a.lower() in col_l:
                        return col
            return None

        fund_col    = _find_col(FUND_ALIASES)
        manager_col = _find_col(MANAGER_ALIASES)
        amount_col  = _find_col(AMOUNT_ALIASES)
        track_col   = _find_col(TRACK_ALIASES)

        if not fund_col and not manager_col:
            continue
        if not amount_col:
            continue

        for _, row in df_clean.iterrows():
            fund_name    = str(row.get(fund_col, "") or "").strip() if fund_col else ""
            manager_name = str(row.get(manager_col, "") or "").strip() if manager_col else ""
            track_name   = str(row.get(track_col, "") or "").strip() if track_col else ""
            amount_val   = _to_float(row.get(amount_col, np.nan))

            if not fund_name and not manager_name:
                continue
            if math.isnan(amount_val) or amount_val <= 0:
                continue

            if not manager_name and fund_name:
                manager_name = _extract_manager(fund_name)

            # Infer product_type from fund name
            inferred_type = _infer_product_type(fund_name or manager_name)
            all_records.append({
                "fund":         fund_name or manager_name,
                "manager":      manager_name or _extract_manager(fund_name),
                "track":        track_name,
                "amount":       amount_val,
                # portfolio_analysis fields
                "product_name": fund_name or manager_name,
                "provider":     manager_name or _extract_manager(fund_name),
                "product_type": inferred_type,
                "source_type":  "imported",
                "entry_mode":   "imported",
                "allocation_source": "missing",
                "equity_pct":   float("nan"),
                "foreign_pct":  float("nan"),
                "fx_pct":       float("nan"),
                "illiquid_pct": float("nan"),
                "sharpe":       float("nan"),
                "notes":        "",
                "locked":       False,
                "excluded":     False,
                "weight":       0.0,
            })

    if not all_records:
        return None, (
            "לא נמצאו נתונים בקובץ. וודא שהקובץ הוא דו\"ח מסלקה תקני "
            "עם עמודות שם קרן/מנהל וסכום/יתרה."
        )

    total = sum(r["amount"] for r in all_records)
    import uuid as _uuid
    for r in all_records:
        r["weight_pct"] = round(r["amount"] / total * 100, 2) if total > 0 else 0.0
        r["weight"]     = r["weight_pct"] / 100
        if "uid" not in r:
            r["uid"] = _uuid.uuid4().hex[:12]

    return {
        "holdings":     all_records,
        "total_amount": total,
        "baseline":     None,  # יחושב בהמשך מהנתונים של האפליקציה
    }, ""


def _compute_baseline_from_holdings(holdings: List[Dict], df_long: pd.DataFrame) -> Optional[Dict]:
    """מחשב פרמטרי חשיפה משוקללים לפורטפוליו הנוכחי."""
    if not holdings:
        return None
    total = sum(r["amount"] for r in holdings)
    if total <= 0:
        return None

    result = {"foreign": 0.0, "stocks": 0.0, "fx": 0.0, "illiquid": 0.0, "sharpe": 0.0, "service": 0.0}
    matched = 0

    for h in holdings:
        w = h["amount"] / total
        # חפש התאמה בנתוני האפליקציה (לפי שם מנהל או שם קרן)
        fund_match = df_long[df_long["fund"].str.lower().str.strip() == h["fund"].lower().strip()]
        if fund_match.empty:
            mgr_match = df_long[df_long["manager"].str.lower().str.strip() == h["manager"].lower().strip()]
            if not mgr_match.empty:
                fund_match = mgr_match.head(1)
        if fund_match.empty:
            # fuzzy: check if any word matches
            words = h["manager"].lower().split()
            for word in words:
                if len(word) > 2:
                    m = df_long[df_long["manager"].str.lower().str.contains(word, na=False)]
                    if not m.empty:
                        fund_match = m.head(1)
                        break

        if not fund_match.empty:
            r = fund_match.iloc[0]
            for key in ["foreign", "stocks", "fx", "illiquid", "sharpe", "service"]:
                val = _to_float(r.get(key, np.nan))
                if not math.isnan(val):
                    result[key] += val * w
            matched += 1

    return result if matched > 0 else None
