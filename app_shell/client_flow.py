# -*- coding: utf-8 -*-
"""
app_shell/client_flow.py  ·  Phase 3
──────────────────────────────────────
Client Mode wizard.

Architecture:
- WorkflowEngine validates transitions
- CaseStore is the single data source
- Each step renders its UI then writes back to the case
- Steps 1-4,6-7 are self-contained (call st.stop())
- Step 5 (optimization) falls through to the main render block

The wizard renders:
1. Mode header + case status bar
2. Step navigation (with validation-aware blocking)
3. Step content (delegates to existing engines)
4. Next-step CTA (only when step is completable)
"""
from __future__ import annotations
import streamlit as st
from case_management import (
    STEP_CASE_SETUP, STEP_DATA_INTAKE, STEP_SNAPSHOT,
    STEP_CURRENT_REPORT, STEP_CHANGE_PLAN,
    STEP_OPTIMIZATION, STEP_AI_REVIEW, STEP_BEFORE_AFTER, STEP_EXPORT,
    STEP_LABELS, STEP_ICONS,
)
from case_management.case_store import CaseStore
from case_management.workflow_engine import WorkflowEngine, StepStatus

_WIZ_CSS = """
<style>
.wiz-bar{background:#fff;border:1px solid #e2e8f0;border-radius:12px;
  padding:12px 20px 10px;margin-bottom:16px;direction:rtl}
.wiz-bar-mode{font-size:8.5px;font-weight:800;letter-spacing:2.5px;
  text-transform:uppercase;color:#2563eb;margin-bottom:8px}
.wiz-nodes{display:flex;align-items:flex-start;gap:0}
.wiz-node{flex:1;display:flex;flex-direction:column;align-items:center;position:relative}
.wiz-node:not(:last-child)::after{content:'';position:absolute;top:12px;
  right:calc(-50% + 12px);width:calc(100% - 24px);height:2px;
  background:var(--lc,#e2e8f0);z-index:0}
.wiz-node.done::after{--lc:#2563eb}
.wiz-dot{width:32px;height:32px;border-radius:50%;z-index:1;
  display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:800;position:relative;
  background:var(--nb,#f1f5f9);border:2px solid var(--nbd,#e2e8f0);color:var(--nc,#94a3b8)}
.wiz-node.done   .wiz-dot{--nb:#eff6ff;--nbd:#2563eb;--nc:#2563eb}
.wiz-node.active .wiz-dot{--nb:#0f2d5e;--nbd:#0f2d5e;--nc:#fff;
  box-shadow:0 0 0 4px rgba(15,45,94,.12)}
.wiz-node.blocked .wiz-dot{--nb:#fef3c7;--nbd:#fde68a;--nc:#b45309;opacity:.7}
.wiz-lbl{font-size:11.5px;font-weight:700;margin-top:7px;
  color:var(--lbl,#94a3b8);text-align:center;line-height:1.35;max-width:80px}
.wiz-node.done   .wiz-lbl{--lbl:#1d4ed8;font-weight:800}
.wiz-node.active .wiz-lbl{--lbl:#0b1929;font-weight:900}
.wiz-node.blocked .wiz-lbl{--lbl:#b45309;font-size:10.5px}
.wiz-step-header{background:linear-gradient(135deg,#0b1f42,#0f2d5e);
  border-radius:12px;padding:12px 18px;direction:rtl;text-align:right;
  margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
.wiz-sh-left{display:flex;flex-direction:column;gap:3px}
.wiz-sh-lbl{font-size:8.5px;font-weight:800;letter-spacing:2px;
  text-transform:uppercase;color:rgba(96,165,250,.7)}
.wiz-sh-title{font-size:17px;font-weight:900;color:#fff}
.wiz-sh-desc{font-size:11.5px;color:rgba(186,214,254,.65)}
.wiz-sh-pct{font-size:26px;font-weight:900;color:rgba(255,255,255,.2)}
.wiz-next{background:linear-gradient(135deg,#f0fdf4,#dcfce7);
  border:1.5px solid #86efac;border-radius:12px;
  padding:14px 18px;direction:rtl;text-align:right;margin-top:16px}
.wiz-next-title{font-size:13px;font-weight:800;color:#065f46;margin-bottom:3px}
.wiz-next-desc{font-size:12px;color:#047857}
.wiz-blocker{background:#fef3c7;border:1px solid #fde68a;border-right:3px solid #f59e0b;
  border-radius:8px;padding:8px 14px;direction:rtl;text-align:right;margin-top:10px}
</style>
"""

_STEP_DESCS = {
    STEP_CASE_SETUP:      "בחר לקוח, הגדר סוג תהליך",
    STEP_DATA_INTAKE:     "העלה מסלקה, הוסף מוצרים ידנית, השלם נתוני חשיפה",
    STEP_SNAPSHOT:        "תמונת מצב משוקללת של כלל התיק הנוכחי",
    STEP_CURRENT_REPORT:  "טיוטת דוח AI על מצב קיים — ניתן לערוך ולאשר",
    STEP_CHANGE_PLAN:     "סמן מוצרים לשינוי / החלפה / הסרה (What-If)",
    STEP_OPTIMIZATION:    "הגדר יעדים, הפעל אופטימיזציה, בחר חלופה",
    STEP_AI_REVIEW:       "דוח AI סופי — ניתן לערוך ולאשר",
    STEP_BEFORE_AFTER:    "חבילת השוואה לפני / אחרי — מוכנה לאישור",
    STEP_EXPORT:          "ייצוא חבילה מלאה לנוטבוק ולהצגה ללקוח",
}


def render_client_wizard(df_long, nav_to_fn) -> None:
    """
    Main wizard entry point.
    Called from streamlit_app.py routing block.
    Self-contained steps call st.stop().
    Step 5 (OPTIMIZATION) returns without stopping — falls through.
    """
    st.markdown(_WIZ_CSS, unsafe_allow_html=True)

    case   = CaseStore.get()
    engine = WorkflowEngine.for_case(case)
    step   = st.session_state.get("client_wizard_step", case.current_step or STEP_DATA_INTAKE)
    flags  = engine.get_flags()
    status = engine.get_status()
    pct    = case.completion_pct()

    # ── Step progress bar ──────────────────────────────────────────────────────
    _render_progress_bar(status, step)

    # ── Step header ────────────────────────────────────────────────────────────
    lbl  = STEP_LABELS.get(step, "")
    icon = STEP_ICONS.get(step, "")
    desc = _STEP_DESCS.get(step, "")
    st.markdown(f"""
<div class="wiz-step-header">
  <div class="wiz-sh-left">
    <div class="wiz-sh-lbl">שלב {step} מתוך 9 · עבודה עם לקוח</div>
    <div class="wiz-sh-title">{icon} {lbl}</div>
    <div class="wiz-sh-desc">{desc}</div>
  </div>
  <div class="wiz-sh-pct">{pct}%</div>
</div>""", unsafe_allow_html=True)

    # ── Validation warnings ─────────────────────────────────────────────────────
    if flags.warnings and step > STEP_DATA_INTAKE:
        for w in flags.warnings[:2]:  # show max 2
            st.caption(f"⚠️ {w}")

    # ── Route to step content ──────────────────────────────────────────────────
    SELF_CONTAINED = (STEP_CASE_SETUP, STEP_DATA_INTAKE, STEP_SNAPSHOT,
                      STEP_CURRENT_REPORT, STEP_CHANGE_PLAN,
                      STEP_AI_REVIEW, STEP_BEFORE_AFTER, STEP_EXPORT)

    if step == STEP_CASE_SETUP:
        _step_case_setup(case, engine)
        st.stop()

    elif step == STEP_DATA_INTAKE:
        _step_data_intake(case, df_long, engine)
        st.stop()

    elif step == STEP_SNAPSHOT:
        _step_snapshot(case, df_long, engine)
        st.stop()

    elif step == STEP_CURRENT_REPORT:
        _step_current_report(case, engine)
        st.stop()

    elif step == STEP_CHANGE_PLAN:
        _step_change_plan(case, df_long, engine)
        st.stop()

    elif step == STEP_OPTIMIZATION:
        _step_optimize_header(case, engine)
        # DO NOT stop — fall through to main render block

    elif step == STEP_AI_REVIEW:
        _step_ai_review(case, engine)
        st.stop()

    elif step == STEP_BEFORE_AFTER:
        _step_before_after(case, df_long, engine)
        st.stop()

    elif step == STEP_EXPORT:
        _step_export(case, engine)
        st.stop()


# ── Step renderers ────────────────────────────────────────────────────────────

def _step_case_setup(case, engine: WorkflowEngine) -> None:
    """Step 1: Case setup — client name + flow intent."""
    st.markdown("#### 📁 פתיחת תיק עבודה")

    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("שם הלקוח", value=case.client_name or "לקוח", key="setup_name")
    with col2:
        intent = st.radio("סוג תהליך", ["full", "analysis_only"],
                          format_func=lambda x: "תהליך אופטימיזציה מלא" if x=="full" else "ניתוח מצב קיים בלבד",
                          index=0 if case.flow_intent=="full" else 1, key="setup_intent",
                          horizontal=True)

    # Existing case indicator
    has_data = bool(case.all_holdings)
    if has_data:
        st.info(f"✅ תיק קיים: {len(case.all_holdings)} מוצרים · לחץ 'המשך' לשלב קליטת נתונים", icon="💼")

    col_save, col_reset, _ = st.columns([1, 1, 4])
    with col_save:
        if st.button("💾 שמור והמשך", key="setup_save", type="primary", use_container_width=True):
            case.client_name  = name
            case.flow_intent  = intent
            case.mark_step_done(STEP_CASE_SETUP)
            CaseStore.save(case)
            st.session_state["client_wizard_step"] = STEP_DATA_INTAKE
            st.rerun()
    with col_reset:
        if st.button("🔄 תיק חדש", key="setup_reset", use_container_width=True):
            CaseStore.reset()
            st.session_state["client_wizard_step"] = STEP_CASE_SETUP
            st.rerun()


def _step_data_intake(case, df_long, engine: WorkflowEngine) -> None:
    """
    Step 2 — קליטת נתונים.
    Redesigned: compact, RTL, Hebrew, no auto-advance.
    Upload stays on this step; only explicit button advances to step 3.
    """
    # ── Section header ─────────────────────────────────────────────────────
    st.markdown("""
<div dir="rtl" style="text-align:right;padding:4px 0 12px">
  <div style="font-size:11px;font-weight:800;letter-spacing:2px;color:#2563eb;
       text-transform:uppercase;margin-bottom:4px">שלב קליטת נתונים</div>
  <div style="font-size:18px;font-weight:900;color:#0b1929;margin-bottom:3px">
    העלאת נתוני תיק
  </div>
  <div style="font-size:12.5px;color:#475569">
    ייבא דוח מסלקה פנסיונית או הוסף מוצרים ידנית — ניתן לשלב בין שניהם
  </div>
</div>""", unsafe_allow_html=True)

    # ── Holdings summary — always read fresh from CaseStore ─────────────────
    # Re-read here so summary reflects all previous reruns' changes
    case = CaseStore.get()
    holdings = case.all_holdings
    n = len(holdings)
    if n > 0:
        n_miss = sum(1 for h in holdings
                     if not h.get("equity_pct") and h.get("equity_pct") != 0.0)
        total_ils = sum(h.get("amount", 0) for h in holdings)
        pct_ok = int((n - n_miss) / max(n, 1) * 100)
        clr = "#065f46" if pct_ok == 100 else ("#92400e" if pct_ok < 70 else "#1e40af")
        bg  = "#d1fae5" if pct_ok == 100 else ("#fef3c7" if pct_ok < 70 else "#eff6ff")
        st.markdown(
            f'<div dir="rtl" style="background:{bg};border:1px solid;border-radius:10px;'
            f'padding:10px 16px;margin-bottom:14px;display:flex;gap:20px;'
            f'align-items:center;border-color:{clr}40">'
            f'<div><strong style="color:{clr};font-size:13px">{n}</strong>'
            f'<div style="font-size:10px;color:{clr}">מוצרים</div></div>'
            f'<div><strong style="color:{clr};font-size:13px">'
            f'{"₪{:,.0f}".format(total_ils)}</strong>'
            f'<div style="font-size:10px;color:{clr}">סה"כ</div></div>'
            f'<div><strong style="color:{clr};font-size:13px">{pct_ok}%</strong>'
            f'<div style="font-size:10px;color:{clr}">שלמות חשיפות</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Simple holdings table
        import pandas as _pd
        _rows = []
        for h in holdings[:25]:  # cap display at 25
            _rows.append({
                "סוג": h.get("product_type",""),
                "מנהל": h.get("provider",""),
                "מוצר": h.get("product_name",""),
                "מסלול": h.get("track","")[:20] if h.get("track") else "",
                "סכום": "₪{:,.0f}".format(h.get("amount",0)),
                "מניות": "{:.0f}%".format(h["equity_pct"]) if h.get("equity_pct") else "—",
            })
        if _rows:
            st.dataframe(_pd.DataFrame(_rows), use_container_width=True, hide_index=True)
        if n > 25:
            st.caption(f"מוצגים 25 מתוך {n} מוצרים")

    # ══════ Section A: ייבוא מסלקה ═══════════════════════════════════════════
    st.markdown("""
<div dir="rtl" style="background:#f8fafc;border:1px solid #e2e8f0;border-right:3px solid #2563eb;
     border-radius:8px;padding:10px 16px 6px;margin:14px 0 8px">
  <div style="font-size:12px;font-weight:800;color:#1e40af;margin-bottom:2px">
    📤 א. ייבוא דוח מסלקה פנסיונית
  </div>
  <div style="font-size:11px;color:#475569">
    קובץ Excel מגמל-נט או ממשרד האוצר (XLSX)
  </div>
</div>""", unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "העלה קובץ מסלקה",
        type=["xlsx", "xls"],
        key="wizard_clearing_upload",
        label_visibility="collapsed",
    )
    if uploaded is not None:
        with st.spinner("מנתח דוח מסלקה..."):
            try:
                from data_ingestion.clearing_parser import (
                    parse_clearing_report, _compute_baseline_from_holdings,
                )
                result, err_msg = parse_clearing_report(uploaded.read())
                if err_msg and not result:
                    st.error(f"שגיאת פרסינג: {err_msg}")
                else:
                    if err_msg:
                        st.warning(err_msg)
                    holdings_raw = (result or {}).get("holdings", [])
                    if holdings_raw:
                        # Save to case — NO step change, NO page change
                        st.session_state["portfolio_holdings"] = holdings_raw
                        st.session_state["portfolio_total"]    = sum(h.get("amount", 0) for h in holdings_raw)
                        st.session_state["portfolio_managers"] = list({h.get("provider", "") for h in holdings_raw})
                        _upd = CaseStore.get()
                        _upd.holdings_imported   = holdings_raw
                        _upd.holdings_normalized = holdings_raw
                        _upd.current_total       = st.session_state["portfolio_total"]
                        _upd.step_done[STEP_DATA_INTAKE] = True
                        bl = _compute_baseline_from_holdings(holdings_raw, df_long)
                        if bl:
                            from case_management import PortfolioSnapshot
                            _upd.current_snapshot = PortfolioSnapshot.from_baseline_dict(
                                bl, _upd.current_total)
                            st.session_state["portfolio_baseline"] = bl
                            # NOTE: STEP_SNAPSHOT is NOT marked done here.
                            # Snapshot data is PREPARED but the step is considered done
                            # only when the user actually navigates to step 3 and views it.
                        CaseStore.save(_upd)
                        # Stay on this step — show success then re-render intake
                        st.success(
                            f"✅ יובאו {len(holdings_raw)} מוצרים מהמסלקה. "
                            "ניתן להוסיף מוצרים ידנית או לעבור לשלב הבא."
                        )
                        # st.rerun() causes the page to re-render step 2 with new data
                        st.rerun()
                    else:
                        st.warning("לא נמצאו אחזקות בקובץ — בדוק שהקובץ תקין.")
            except Exception as _e:
                st.error(f"שגיאה בייבוא: {_e}")

    # ══════ Section B: הוספה ידנית ════════════════════════════════════════════
    st.markdown("""
<div dir="rtl" style="background:#f8fafc;border:1px solid #e2e8f0;border-right:3px solid #7c3aed;
     border-radius:8px;padding:10px 16px 6px;margin:14px 0 8px">
  <div style="font-size:12px;font-weight:800;color:#5b21b6;margin-bottom:2px">
    ➕ ב. הוספת מוצר ידנית
  </div>
  <div style="font-size:11px;color:#475569">
    נדל"ן, עו"ש, קריפטו, מוצרים פנסיוניים שאינם בדוח המסלקה
  </div>
</div>""", unsafe_allow_html=True)

    try:
        from portfolio_analysis.ui import _render_add_form
        from portfolio_analysis.models import STATE_KEY as _SK
        _render_add_form(list(case.all_holdings), df_long)
        # Sync pf_holdings → CaseStore if form added a product
        import streamlit as _st_inner
        _pf_h = _st_inner.session_state.get(_SK) or []
        # Use uid-based merge: any uid in pf_h not already in case → new product added
        _existing_uids = {h.get("uid","") for h in case.all_holdings}
        _new_in_pf = [h for h in _pf_h if h.get("uid","") not in _existing_uids]
        if _new_in_pf:
            _c2 = CaseStore.get()
            # Preserve imported holdings + add new manual products
            _merged = list(_c2.holdings_imported) + list(_c2.holdings_manual) + _new_in_pf
            _c2.holdings_manual     = [h for h in _pf_h if h.get("entry_mode","manual") == "manual"]
            _c2.holdings_normalized = _merged
            _c2.step_done[STEP_DATA_INTAKE] = True
            CaseStore.save(_c2)
            case = _c2
    except Exception as _add_err:
        st.warning(f"שגיאת טופס הוספה: {_add_err}")

    # ── Edit existing holdings ───────────────────────────────────────────────
    case = CaseStore.get()
    if case.all_holdings:
        with st.expander(
            f"✏️ ניהול מוצרים קיימים ({len(case.all_holdings)})", expanded=False
        ):
            try:
                from portfolio_analysis.ui import _render_edit_controls
                _render_edit_controls(list(case.all_holdings), df_long)
                case = CaseStore.get()
            except Exception as _ed_err:
                st.caption(f"שגיאת עריכה: {_ed_err}")

    # ══════ Continue CTA — explicit only, NO auto-advance ════════════════════
    case = CaseStore.get()
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    if case.all_holdings:
        case.mark_step_done(STEP_DATA_INTAKE)
        CaseStore.save(case)
        st.markdown("""
<div dir="rtl" style="background:linear-gradient(135deg,#f0fdf4,#dcfce7);
     border:1.5px solid #86efac;border-radius:12px;padding:14px 18px;margin-top:10px">
  <div style="font-size:12px;font-weight:800;color:#065f46;margin-bottom:3px">
    ✅ נתוני התיק נקלטו — מוכן להמשך
  </div>
  <div style="font-size:11.5px;color:#047857">
    לחץ "המשך לתמונת מצב" לצפייה בניתוח המשוקלל של התיק כולו
  </div>
</div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        if st.button(
            "📊 המשך לתמונת מצב — שלב 3",
            key="intake_continue_btn",
            type="primary",
        ):
            # Explicit only — user must click
            st.session_state["client_wizard_step"] = STEP_SNAPSHOT
            st.rerun()
    else:
        st.info(
            "הוסף לפחות מוצר אחד (מסלקה או ידנית) כדי להמשיך.",
            icon="ℹ️",
        )
def _step_snapshot(case, df_long, engine: WorkflowEngine) -> None:
    """Step 3: Compute and display snapshot. Fully self-contained."""
    ok, reasons = engine.can_advance(STEP_SNAPSHOT)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_DATA_INTAKE)
        return

    from case_management.before_after_pipeline import compute_baseline
    compute_baseline(case, df_long)
    case = CaseStore.get()

    if not case.current_snapshot:
        st.warning('לא ניתן לחשב תמונת מצב. ודא שיש נתוני חשיפה למוצרים.')
        _back_btn(STEP_DATA_INTAKE)
        return

    snap     = case.current_snapshot
    holdings = case.all_holdings
    total    = max(case.current_total or sum(h.get('amount',0) for h in holdings), 1)
    import math

    def _pf(v):
        try:
            fv = float(v or 'nan')
            return str(round(fv,1)) + '%' if not math.isnan(fv) else '---'
        except Exception: return '---'

    # Header
    st.markdown(
        '<div dir="rtl" style="background:linear-gradient(135deg,#0b1f42,#0f2d5e);'
        'border-radius:12px;padding:14px 20px;color:#fff;margin-bottom:16px">'
        '<div style="font-size:11px;font-weight:800;letter-spacing:2px;color:rgba(96,165,250,.8);'
        'text-transform:uppercase;margin-bottom:4px">תמונת מצב</div>'
        '<div style="font-size:17px;font-weight:900">' + case.client_name + '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # Exposure metrics
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric('מניות',    _pf(snap.stocks_pct))
    c2.metric('חוץ לארץ', _pf(snap.foreign_pct))
    c3.metric('מטח',      _pf(snap.fx_pct))
    c4.metric('לא-סחיר',  _pf(snap.illiquid_pct))
    c5.metric('שארפ',     _pf(snap.sharpe))

    # Pension / Capital
    p_total = sum(h.get('amount',0) for h in holdings if _classify_pc(h.get('product_type','')) == 'pension')
    c_total = sum(h.get('amount',0) for h in holdings if _classify_pc(h.get('product_type','')) == 'capital')
    p_n = sum(1 for h in holdings if _classify_pc(h.get('product_type','')) == 'pension')
    c_n = sum(1 for h in holdings if _classify_pc(h.get('product_type','')) == 'capital')
    pc1,pc2,pc3 = st.columns(3)
    pc1.metric('קצבתי',  str(round(p_total/total*100)) + '% (' + str(p_n) + ' מוצרים)')
    pc2.metric('הוני',   str(round(c_total/total*100)) + '% (' + str(c_n) + ' מוצרים)')
    pc3.metric('סהכ',    'ILS' + f'{total:,.0f}')

    # Holdings table
    with st.expander('פירוט מוצרים (' + str(len(holdings)) + ')', expanded=False):
        import pandas as _pd
        rows = [{'סיווג': {'pension':'קצבתי','capital':'הוני'}.get(_classify_pc(h.get('product_type','')), 'לא ידוע'),
                 'סוג': h.get('product_type',''), 'מנהל': h.get('provider',''),
                 'מוצר': h.get('product_name',''), 'סכום': f'{h.get("amount",0):,.0f}',
                 'מניות': _pf(h.get('equity_pct'))} for h in holdings]
        if rows: st.dataframe(_pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown('---')

    # AI Draft
    if not case.current_report_draft.strip():
        st.info('לחץ להפקת טיוטת דוח AI על בסיס תמונת המצב.')
    else:
        edited = st.text_area('טיוטת דוח מצב קיים (ניתן לערוך):',
                              value=case.current_report_draft, height=220, key='snap_ai_ta')
        if st.button('שמור טיוטה', key='snap_save_draft'):
            _c = CaseStore.get()
            _c.current_report_draft = edited
            _c.current_report_saved = True
            CaseStore.save(_c)
            st.success('נשמר')
            st.rerun()

    # Mark done
    case = CaseStore.get()
    case.step_done[STEP_SNAPSHOT] = True
    CaseStore.save(case)

    # CTAs at bottom only
    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
    ba, bb, bc = st.columns([1, 1, 2])
    with ba: _back_btn(STEP_DATA_INTAKE)
    with bb:
        if st.button('הפק דוח מצב', key='snap_gen_report'):
            with st.spinner('מפיק...'):
                _parts = [
                    'סיכום מצב תיק קיים',
                    '',
                    'סהכ: ' + f'{total:,.0f}' + ' | מוצרים: ' + str(len(holdings)),
                    'קצבתי: ' + str(round(p_total/total*100)) + '% | הוני: ' + str(round(c_total/total*100)) + '%',
                    '',
                    'חשיפות: מניות ' + _pf(snap.stocks_pct) + ' | חוץ ' + _pf(snap.foreign_pct) +
                    ' | מטח ' + _pf(snap.fx_pct) + ' | לא-סחיר ' + _pf(snap.illiquid_pct),
                    '', '(ניתן לערוך)',
                ]
                _c = CaseStore.get()
                _c.current_report_draft = chr(10).join(_parts)
                CaseStore.save(_c)
            st.rerun()
    with bc:
        if st.button('שמור והמשך לתכנון שינויים', key='snap_next', type='primary'):
            _c = CaseStore.get()
            _c.step_done[STEP_SNAPSHOT] = True
            if case.current_report_draft:
                _c.step_done[STEP_CURRENT_REPORT] = True
            CaseStore.save(_c)
            st.session_state['client_wizard_step'] = STEP_CHANGE_PLAN
            st.rerun()


# -- Pension / Capital helpers --

_PENSION_SET = frozenset({'קרן פנסיה', 'ביטוח מנהלים', 'קרן השתלמות'})
_CAPITAL_SET = frozenset({
    'קופת גמל', 'קופת גמל להשקעה', 'קופה מרכזית לפיצויים', 'פוליסת חיסכון',
    'תיק מנוהל', 'קרן גידור', 'קרן השקעות', 'קרן נאמנות', 'פיקדון', 'קריפטו',
})

def _classify_pc(product_type: str) -> str:
    try:
        from portfolio_analysis.catalog import normalize_product_type
        pt = normalize_product_type(product_type or '')
    except Exception:
        pt = (product_type or '').strip()
    if pt in _PENSION_SET: return 'pension'
    if pt in _CAPITAL_SET: return 'capital'
    return 'unknown'

def _pc_label(pc: str) -> str:
    return {'pension': 'קצבתי', 'capital': 'הוני'}.get(pc, 'לא ידוע')


# -- Step 4: Current-State AI Report --

def _step_current_report(case, engine: WorkflowEngine) -> None:
    import math
    ok, reasons = engine.can_advance(STEP_CURRENT_REPORT)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_SNAPSHOT)
        return

    st.markdown('### דוח מצב קיים')
    st.caption('המערכת מייצרת טיוטת AI. ניתן לערוך ולאשר.')

    snap = case.current_snapshot
    if snap:
        def _pf(v):
            try:
                fv = float(v or 'nan')
                return str(round(fv, 1)) + '%' if not math.isnan(fv) else '---'
            except Exception: return '---'
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric('מניות',   _pf(snap.stocks_pct))
        c2.metric('חוץ',     _pf(snap.foreign_pct))
        c3.metric('מטח',     _pf(snap.fx_pct))
        c4.metric('לא-סחיר', _pf(snap.illiquid_pct))
        c5.metric('שארפ',    _pf(snap.sharpe))

    holdings = case.all_holdings
    total    = max(case.current_total or sum(h.get('amount',0) for h in holdings), 1)
    p_total  = sum(h.get('amount',0) for h in holdings if _classify_pc(h.get('product_type','')) == 'pension')
    c_total  = sum(h.get('amount',0) for h in holdings if _classify_pc(h.get('product_type','')) == 'capital')
    col1,col2,col3 = st.columns(3)
    col1.metric('קצבתי',  str(round(p_total/total*100)) + '%')
    col2.metric('הוני',   str(round(c_total/total*100)) + '%')
    col3.metric('לא ידוע', str(round((total-p_total-c_total)/total*100)) + '%')
    st.markdown('---')

    if not case.current_report_draft.strip():
        st.info('לחץ להפקת טיוטה.')
        if st.button('הפק טיוטה', key='gen_cur_rep', type='primary'):
            lines = [
                'סיכום מצב תיק קיים', '',
                'סהכ: ' + f'{total:,.0f}' + ' | מוצרים: ' + str(len(holdings)),
                'קצבתי: ' + str(round(p_total/total*100)) + '% | הוני: ' + str(round(c_total/total*100)) + '%',
                '', '(ניתן לערוך)',
            ]
            _c = CaseStore.get()
            _c.current_report_draft = chr(10).join(lines)
            CaseStore.save(_c)
            st.rerun()
    else:
        edited = st.text_area('טיוטת דוח:', value=case.current_report_draft, height=240, key='cur_rep_ta')
        s1, s2, _ = st.columns([1, 1, 5])
        with s1:
            if st.button('שמור ואשר', key='save_cur_rep', type='primary'):
                _c = CaseStore.get()
                _c.current_report_draft = edited
                _c.current_report_saved = True
                _c.step_done[STEP_CURRENT_REPORT] = True
                CaseStore.save(_c)
                st.success('נשמר')
                st.rerun()
        with s2:
            if st.button('הפק מחדש', key='regen_cur_rep'):
                _c = CaseStore.get()
                _c.current_report_draft = ''
                _c.current_report_saved = False
                CaseStore.save(_c)
                st.rerun()
        case = CaseStore.get()
        if case.current_report_saved:
            _next_cta('דוח אושר', 'עבור לתכנון שינויים',
                      'המשך לשלב 5 - תכנון שינויים', 'cr_next', STEP_CHANGE_PLAN)


# -- Step 5: Change Planning / What-If --

def _step_change_plan(case, df_long, engine: WorkflowEngine) -> None:
    """Step 5: mark each holding + define optimization targets."""
    ok, reasons = engine.can_advance(STEP_CHANGE_PLAN)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_SNAPSHOT)
        return

    holdings = case.all_holdings
    if not holdings:
        st.warning("אין מוצרים — חזור לשלב קליטת נתונים.")
        _back_btn(STEP_DATA_INTAKE)
        return

    st.markdown("### תכנון שינויים")

    # ── A: Product-level change planning ─────────────────────────────
    st.markdown("#### א. סימון מוצרים")
    st.caption("סמן לכל מוצר: נשאר / פתוח לשינוי / יוצא. לפחות מוצר אחד פתוח כדי להמשיך.")

    plan    = dict(case.change_plan)
    notes   = dict(case.change_notes)
    changed = False

    for idx_h, h in enumerate(holdings):
        uid    = h.get("uid") or str(idx_h)
        name   = h.get("product_name") or h.get("fund") or h.get("provider") or "---"
        track  = h.get("track", "")
        amount = h.get("amount", 0)
        pc     = _classify_pc(h.get("product_type", ""))
        cur    = plan.get(uid, "keep")
        lbl    = _pc_label(pc) + " | " + name + ("  - " + track if track else "") + "  (₪" + f"{amount:,.0f}" + ")"
        with st.expander(lbl, expanded=(cur != "keep")):
            rc1, rc2 = st.columns([3, 2])
            with rc1:
                new_plan = st.radio(
                    "פעולה:",
                    ["keep", "open", "remove"],
                    format_func=lambda x: {
                        "keep": "נשאר", "open": "פתוח לשינוי", "remove": "יוצא",
                    }[x],
                    index=["keep","open","remove"].index(cur),
                    key="cp_" + uid,
                    horizontal=True,
                )
            with rc2:
                new_note = st.text_input("הערה:", value=notes.get(uid,""), key="cpn_" + uid)
            if new_plan != cur or new_note != notes.get(uid,""):
                plan[uid] = new_plan; notes[uid] = new_note; changed = True

    if changed:
        _c = CaseStore.get()
        _c.change_plan = plan; _c.change_notes = notes
        for s in range(STEP_OPTIMIZATION, 10): _c.step_done[s] = False
        _c.selected_scenario = None
        _c.change_plan = plan; _c.change_notes = notes
        CaseStore.save(_c)

    open_n   = sum(1 for v in plan.values() if v == "open")
    remove_n = sum(1 for v in plan.values() if v == "remove")
    keep_n   = len(holdings) - open_n - remove_n

    st.markdown(
        "<div dir='rtl' style='background:#f0f4ff;border-radius:8px;padding:10px 16px;"
        "margin:10px 0;display:flex;gap:24px'>"
        "<div><strong style='color:#065f46'>" + str(keep_n) + "</strong><br><small>נשארים</small></div>"
        "<div><strong style='color:#1d4ed8'>" + str(open_n) + "</strong><br><small>פתוחים לשינוי</small></div>"
        "<div><strong style='color:#b91c1c'>" + str(remove_n) + "</strong><br><small>יוצאים</small></div>"
        "</div>", unsafe_allow_html=True,
    )

    st.markdown("---")

    # ── B: Portfolio targets ──────────────────────────────────────────
    st.markdown("#### ב. יעדי תמהיל לאופטימיזציה")
    st.caption("הגדר יעדי חשיפה — המנוע יחפש חלופות שמגיעות ליעדים אלו.")

    tgt = dict(case.optimizer_targets) if case.optimizer_targets else {}

    snap = case.current_snapshot
    import math

    def _snap_default(attr, fallback=0.0):
        if snap:
            v = getattr(snap, attr, None)
            if v is not None:
                try:
                    fv = float(v)
                    if not math.isnan(fv): return fv
                except Exception: pass
        return tgt.get(attr, fallback)

    t1,t2,t3,t4 = st.columns(4)
    new_eq  = t1.number_input("מניות %",    0.0, 100.0, _snap_default("stocks_pct", 30.0),  step=1.0, key="tgt_eq")
    new_fo  = t2.number_input("חוץ לארץ %", 0.0, 100.0, _snap_default("foreign_pct", 20.0), step=1.0, key="tgt_fo")
    new_fx  = t3.number_input("מטח %",      0.0, 100.0, _snap_default("fx_pct", 15.0),       step=1.0, key="tgt_fx")
    new_ill = t4.number_input("לא-סחיר %",  0.0, 100.0, _snap_default("illiquid_pct", 10.0), step=1.0, key="tgt_ill")

    if st.button("💾 שמור יעדים", key="save_targets"):
        _c = CaseStore.get()
        _c.optimizer_targets = {
            "stocks_pct": new_eq, "foreign_pct": new_fo,
            "fx_pct": new_fx, "illiquid_pct": new_ill,
        }
        CaseStore.save(_c)
        st.success("יעדים נשמרו")
        st.rerun()

    # ── CTA ───────────────────────────────────────────────────────────
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    can_continue = open_n > 0 or remove_n > 0
    b1, b2 = st.columns([1, 2])
    with b1:
        _back_btn(STEP_CURRENT_REPORT)
    with b2:
        if can_continue:
            _c = CaseStore.get()
            _c.step_done[STEP_CHANGE_PLAN] = True
            CaseStore.save(_c)
            if st.button("🎯 המשך לאופטימיזציה — שלב 6", key="cp_next", type="primary"):
                st.session_state["client_wizard_step"] = STEP_OPTIMIZATION
                st.rerun()
        else:
            st.info("סמן לפחות מוצר אחד כ-'פתוח לשינוי'.", icon="ℹ️")


def _step_optimize_header(case, engine: WorkflowEngine) -> None:
    """
    Step 4: Optimization — renders compact header + product selector.
    DOES NOT call st.stop(). Falls through to main render block.
    """
    ok, reasons = engine.can_advance(STEP_OPTIMIZATION)
    if not ok:
        _show_blockers(reasons)

    # Compact product type selector
    _WORLDS = ["קרנות השתלמות","פוליסות חיסכון","קרנות פנסיה","קופות גמל","גמל להשקעה"]
    cur_pt = st.session_state.get("product_type", case.active_product_type)
    st.markdown("**עולם מוצר:**")
    pt_cols = st.columns(len(_WORLDS), gap="small")
    for col, w in zip(pt_cols, _WORLDS):
        with col:
            if st.button(w.replace("קרנות ","").replace("קופות ",""),
                         key=f"opt_pt_{w}", use_container_width=True,
                         type="primary" if cur_pt==w else "secondary"):
                st.session_state["product_type"] = w
                case.active_product_type = w
                CaseStore.save(case)
                st.rerun()

    # Show selected alt status
    sc = case.selected_scenario
    if sc:
        st.success(f"✅ חלופה נבחרה: **{sc.label}** — {sc.advantage or 'ניתן להמשיך לשלב AI'}")
        _next_cta("אופטימיזציה הושלמה", "עבור להסברי AI",
                  "🤖 המשך לשלב 5 — הסברי AI", "opt_next", STEP_AI_REVIEW)


def _step_ai_review(case, engine: WorkflowEngine) -> None:
    """Step 5: AI Review — structured, grounded, editable."""
    ok, reasons = engine.can_advance(STEP_AI_REVIEW)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_OPTIMIZATION)
        return

    # Ensure before/after is computed before AI runs
    from case_management.before_after_pipeline import compute_proposed, compute_deltas
    if not case.proposed_snapshot and case.selected_scenario:
        compute_proposed(case)
        compute_deltas(case)
        CaseStore.save(case)

    case = CaseStore.get()
    _render_before_after_summary(case)

    st.markdown("---")
    st.markdown("### 🤖 הסברי AI ואישור")
    st.caption(
        "AI מסביר את ההיגיון של השינויים על בסיס הנתונים בלבד. "
        "אין ייעוץ פיננסי. אין המצאת עובדות מספריות."
    )

    ai = case.ai_review
    _secs = st.session_state.get("final_report_sections", {})
    _has_ai = bool(_secs.get("executive_summary","").strip()) or (ai and ai.is_complete())

    if not _has_ai:
        # Trigger AI generation
        sc  = case.selected_scenario
        bl  = case.current_snapshot
        tgts = case.optimizer_targets

        if not sc:
            st.warning("חסר: חלופה נבחרת. חזור לשלב האופטימיזציה.")
            return

        bl_text = ""
        if bl:
            bl_text = (
                f"מניות={bl.stocks_pct:.1f}% חו\"ל={bl.foreign_pct:.1f}% "
                f"מט\"ח={bl.fx_pct:.1f}% לא-סחיר={bl.illiquid_pct:.1f}% "
                f"שארפ={bl.sharpe:.2f} עלות={bl.cost_pct:.2f}%"
            ) if bl.stocks_pct is not None and bl.stocks_pct == bl.stocks_pct else ""

        if st.button("🤖 הפק הסברי AI", key="wiz_gen_ai_v3", type="primary"):
            try:
                from institutional_strategy_analysis.ai_analyst import (
                    _call_claude, _external_guidance_block
                )
                import re
                guidance = _external_guidance_block()
                deltas_text = ""
                if case.exposure_deltas:
                    deltas_text = " · ".join(
                        f"{d.label_he}: {d.before:.1f}%→{d.after:.1f}% ({d.delta_pp:+.1f}pp)"
                        for d in case.exposure_deltas
                        if d.before is not None and d.after is not None
                    )

                prompt = (
                    f"mode: planning\\n"
                    f"עולם מוצר: {case.active_product_type}\\n"
                    f"חלופה נבחרת: {sc.label}\\n"
                    f"מנהלים: {sc.managers}\\n"
                    f"יתרון מוצהר: {sc.advantage}\\n"
                    f"מצב נוכחי: {bl_text or 'חסר'}\\n"
                    f"שינויי חשיפה: {deltas_text or 'חסר'}\\n"
                    f"יעדים: {tgts}\\n\\n"
                    f"הנחיות ממסמך חיצוני:\\n{guidance}\\n\\n"
                    "כתוב 4 סעיפים בסדר הזה בדיוק, בעברית, כל אחד עם כותרת בסוגריים מרובעים:\\n"
                    "[1. ניתוח וטיעון מקצועי — ליועץ]\\n"
                    "[2. הסבר ללקוח — ברור ונגיש]\\n"
                    "[3. שיקולים ומקבילות]\\n"
                    "[4. הנחות ופערי נתונים]\\n\\n"
                    "כללים: אל תמציא נתונים. אל תשתמש בנתונים שאינם בקלט. "
                    "אל תיתן ייעוץ מחייב. טון: מקצועי ומאוזן."
                )
                with st.spinner("מפיק הסברי AI..."):
                    raw, err = _call_claude(prompt, max_tokens=2500)
                if err:
                    st.error(f"שגיאת AI: {err}")
                else:
                    def _extract(text, n):
                        m = re.search(rf"\\[{n}\\..*?\\](.*?)(?=\\[\\d\\.|$)", text, re.DOTALL)
                        return m.group(1).strip() if m else ""

                    case = CaseStore.get()
                    case.ai_review = type(case.ai_review or __import__('case_management').AIReview)()
                    from case_management import AIReview as AIR
                    case.ai_review = AIR(
                        advisor_rationale  = _extract(raw, "1"),
                        client_explanation = _extract(raw, "2"),
                        trade_offs         = _extract(raw, "3"),
                        assumptions_text   = _extract(raw, "4"),
                        executive_summary  = _extract(raw, "1"),
                        change_advantages  = _extract(raw, "3"),
                        final_summary      = _extract(raw, "2"),
                    )
                    case.step_done[STEP_AI_REVIEW] = True
                    CaseStore.save(case)
                    st.success("✅ הסברי AI נוצרו")
                    st.rerun()
            except Exception as ai_err:
                st.error(f"שגיאת AI: {ai_err}")
    else:
        # Show editable sections
        st.success("✅ הסברי AI קיימים — ניתן לערוך ולאשר")
        _tone = st.radio("טון", ["professional","simple","persuasive"],
                         format_func=lambda x: {"professional":"מקצועי","simple":"נגיש","persuasive":"שכנועי"}[x],
                         horizontal=True, key="ai_tone")

        _editable = {}
        for key, label in [
            ("executive_summary",   "1. תקציר מנהלים"),
            ("current_weaknesses",  "2. חולשות התיק הנוכחי"),
            ("change_advantages",   "3. יתרונות השינויים"),
            ("risks_considerations","4. שיקולים ואיזונים"),
            ("final_summary",       "5. סיכום סופי"),
        ]:
            default = _secs.get(key,"") or (getattr(case.ai_review, key.replace("risks_considerations","risks"), "") if case.ai_review else "")
            _editable[key] = st.text_area(label, value=default, height=110, key=f"ai_edit_{key}")

        col_save, col_regen, _ = st.columns([1, 1, 4])
        with col_save:
            if st.button("💾 שמור", key="ai_save_v3", type="primary"):
                st.session_state["final_report_sections"] = _editable
                case = CaseStore.get()
                if not case.ai_review:
                    from case_management import AIReview as AIR2
                    case.ai_review = AIR2()
                case.ai_review.executive_summary  = _editable.get("executive_summary","")
                case.ai_review.change_advantages  = _editable.get("change_advantages","")
                case.ai_review.risks              = _editable.get("risks_considerations","")
                case.ai_review.final_summary      = _editable.get("final_summary","")
                case.step_done[STEP_AI_REVIEW]    = True
                CaseStore.save(case)
                st.success("נשמר ✅")

        with col_regen:
            if st.button("🔄 הפק מחדש", key="ai_regen"):
                st.session_state.pop("final_report_sections", None)
                case = CaseStore.get(); case.ai_review = None
                CaseStore.save(case); st.rerun()

        if case.ai_review and case.ai_review.is_complete():
            _next_cta("הסברי AI אושרו", "בנה את חבילת לפני/אחרי",
                      "⚖️ המשך לשלב לפני/אחרי", "ai_next", STEP_BEFORE_AFTER)


def _step_before_after(case, df_long, engine: WorkflowEngine) -> None:
    """Step 6: Before/After package build + review."""
    ok, reasons = engine.can_advance(STEP_BEFORE_AFTER)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_AI_REVIEW)
        return

    # Run full pipeline
    from case_management.before_after_pipeline import run_full_pipeline
    run_full_pipeline(case, df_long)
    case = CaseStore.get()

    if not case.has_before_after():
        st.warning("⚠️ לא ניתן לבנות חבילת לפני/אחרי — חסרים נתוני baseline או חלופה נבחרת.")
        _back_btn(STEP_OPTIMIZATION)
        return

    st.markdown("### ⚖️ חבילת לפני / אחרי")
    _render_before_after_table(case)

    st.markdown("---")
    # Summary text
    sc = case.selected_scenario
    if sc:
        st.markdown(f"**חלופה נבחרת:** {sc.label} · {sc.managers}")
        if sc.advantage:
            st.info(f"🎯 {sc.advantage}")

    # Assumptions & missing data
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**הנחות:**")
        assumptions_new = st.text_area("", value="\n".join(case.assumptions), height=80, key="ba_assumptions", label_visibility="collapsed")
    with col2:
        st.markdown("**נתונים חסרים / הערות:**")
        notes_new = st.text_area("", value="\n".join(case.missing_data_notes), height=80, key="ba_notes", label_visibility="collapsed")

    if st.button("💾 שמור ואשר חבילה", key="ba_save", type="primary"):
        case = CaseStore.get()
        case.assumptions        = [l.strip() for l in assumptions_new.split("\n") if l.strip()]
        case.missing_data_notes = [l.strip() for l in notes_new.split("\n") if l.strip()]
        case.mark_step_done(STEP_BEFORE_AFTER)
        CaseStore.save(case)
        st.success("✅ חבילת לפני/אחרי נשמרה")
        _next_cta("חבילה אושרה", "עבור לייצוא",
                  "📋 המשך לשלב ייצוא", "ba_next", STEP_EXPORT)


def _step_export(case, engine: WorkflowEngine) -> None:
    """Step 9: Export .md structured prompt package."""
    ok, reasons = engine.can_advance(STEP_EXPORT)
    if not ok:
        _show_blockers(reasons)
        _back_btn(STEP_BEFORE_AFTER)
        return

    from case_management.before_after_pipeline import build_export_bundle
    import json as _json, math as _math

    bundle = build_export_bundle(case)
    case.export_payload = bundle
    case.mark_step_done(STEP_EXPORT)
    CaseStore.save(case)

    st.markdown("### ייצוא חבילת לקוח")
    flags = engine.get_flags()
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("מוצרים", str(len(case.all_holdings)))
    c2.metric("חשיפות",    "OK" if flags.has_snapshot else "חסר")
    c3.metric("AI",        "OK" if flags.has_ai_review else "חסר")
    c4.metric("לפני/אחרי","OK" if flags.has_before_after else "חסר")

    _render_before_after_table(case)
    st.markdown("---")

    ai  = case.ai_review
    snap = case.current_snapshot
    holdings = case.all_holdings
    total = max(case.current_total or sum(h.get("amount",0) for h in holdings), 1)
    p_tot = sum(h.get("amount",0) for h in holdings if _classify_pc(h.get("product_type","")) == "pension")
    c_tot = sum(h.get("amount",0) for h in holdings if _classify_pc(h.get("product_type","")) == "capital")

    def _pf(v):
        try:
            fv = float(v or "nan")
            return str(round(fv,1)) + "%" if not _math.isnan(fv) else "---"
        except Exception: return "---"

    # Build structured .md prompt package
    L = []
    L.append("# חבילת ייעוץ פיננסי - " + case.client_name)
    L.append("case_id: " + case.case_id)
    L.append("")
    L.append("## מצב קיים")
    L.append("")
    L.append("סהכ תיק: " + f"{total:,.0f}" + " | קצבתי: " + str(round(p_tot/total*100)) + "% | הוני: " + str(round(c_tot/total*100)) + "%")
    if snap:
        L.append("מניות: " + _pf(snap.stocks_pct) + " | חוץ: " + _pf(snap.foreign_pct) + " | מטח: " + _pf(snap.fx_pct) + " | לא-סחיר: " + _pf(snap.illiquid_pct) + " | שארפ: " + _pf(snap.sharpe))
    L.append("")
    L.append("### מוצרים")
    for h in holdings:
        pc = _classify_pc(h.get("product_type",""))
        L.append("- [" + {"pension":"קצבתי","capital":"הוני"}.get(pc,"?") + "] " + (h.get("product_name","") or h.get("provider","")) + " " + f"{h.get('amount',0):,.0f}")
    L.append("")
    if case.change_plan:
        L.append("## תכנון שינויים")
        for h in holdings:
            uid = h.get("uid","")
            act = {"keep":"נשאר","open":"פתוח","remove":"יוצא"}.get(case.change_plan.get(uid,"keep"),"נשאר")
            L.append("- " + (h.get("product_name","") or "") + ": " + act)
        L.append("")
    if case.proposed_snapshot and case.current_snapshot:
        ps = case.proposed_snapshot; cs = case.current_snapshot
        L.append("## לפני / אחרי")
        L.append("מניות: " + _pf(cs.stocks_pct) + " -> " + _pf(ps.stocks_pct))
        L.append("חוץ: " + _pf(cs.foreign_pct) + " -> " + _pf(ps.foreign_pct))
        L.append("שארפ: " + _pf(cs.sharpe) + " -> " + _pf(ps.sharpe))
        L.append("")
    if case.current_report_draft:
        L.append("## דוח מצב קיים")
        L.append(case.current_report_draft)
        L.append("")
    if ai:
        L.append("## ניתוח AI")
        L.append("### ניתוח מקצועי")
        L.append(ai.advisor_rationale or ai.executive_summary or "")
        L.append("")
        L.append("### הסבר ללקוח")
        L.append(ai.client_explanation or ai.final_summary or "")
        L.append("")
        L.append("### שיקולים")
        L.append(ai.trade_offs or ai.risks or "")
        L.append("")
    L.append("---")
    L.append("הנחיות לנוטבוק: קובץ זה מכיל נתוני ייעוץ פיננסי. הדבק כ-context ב-NotebookLM.")
    L.append("schema_version: 3.1")

    md_content = chr(10).join(L)

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "הורד חבילת ייעוץ (.md)",
            data=md_content.encode("utf-8"),
            file_name="advisory_" + case.case_id + ".md",
            mime="text/markdown",
            key="export_dl_md",
            type="primary",
        )
    with d2:
        st.download_button(
            "הורד כ-JSON (גיבוי)",
            data=_json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name="case_" + case.case_id + ".json",
            mime="application/json",
            key="export_dl_json",
        )

    st.markdown("---")
    _back_btn(STEP_BEFORE_AFTER)
    st.balloons()

# ── UI helpers ────────────────────────────────────────────────────────────────

def _render_progress_bar(status: dict, active_step: int) -> None:
    nodes = []
    for s in range(1, 10):
        st_obj: StepStatus = status[s]
        cls   = "done" if st_obj.done else ("active" if st_obj.active else ("blocked" if st_obj.blocked else ""))
        icon  = "✓" if st_obj.done else st_obj.icon
        nodes.append(
            f'<div class="wiz-node {cls}">'
            f'<div class="wiz-dot">{icon}</div>'
            f'<div class="wiz-lbl">{st_obj.label}</div>'
            f'</div>'
        )
    st.markdown(
        '<div class="wiz-bar"><div class="wiz-bar-mode">מסלול לקוח</div>'
        '<div class="wiz-nodes">' + "".join(nodes) + '</div></div>',
        unsafe_allow_html=True
    )
    # Compact navigation buttons
    nav_cols = st.columns(10, gap="small")
    for i, s in enumerate(range(1, 10)):
        with nav_cols[i]:
            t = "primary" if s == active_step else "secondary"
            if st.button(str(s), key=f"wiz_nav_p3_{s}", type=t, use_container_width=True,
                         help=STEP_LABELS.get(s,"")):
                ok, reasons = status[s].done or True, []
                if not status[s].blocked:
                    st.session_state["client_wizard_step"] = s
                    st.rerun()
    with nav_cols[7]:
        if st.button("🏠", key="wiz_home_p3", use_container_width=True):
            st.session_state["app_page"] = "home"
            st.session_state["app_mode"] = "home"
            st.rerun()


def _render_snapshot_summary(snap) -> None:
    if not snap: return
    import math
    def _fmt(v): return f"{v:.1f}%" if v and not math.isnan(v) else "—"
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("מניות",    _fmt(snap.stocks_pct))
    c2.metric('חו"ל',     _fmt(snap.foreign_pct))
    c3.metric('מט"ח',     _fmt(snap.fx_pct))
    c4.metric("לא-סחיר", _fmt(snap.illiquid_pct))
    c5.metric("שארפ",    f"{snap.sharpe:.2f}" if snap.sharpe and not math.isnan(snap.sharpe) else "—")


def _render_before_after_summary(case) -> None:
    cur = case.current_snapshot
    prp = case.proposed_snapshot
    if not cur and not prp: return
    import math

    st.markdown("#### 📊 לפני / אחרי")
    if case.exposure_deltas:
        cols = st.columns(len(case.exposure_deltas))
        for col, d in zip(cols, case.exposure_deltas):
            a_s = f"{d.after:.1f}" if d.after is not None else "—"
            d_s = f"{d.delta_pp:+.1f}pp" if d.delta_pp is not None else None
            col.metric(d.label_he, a_s, delta=d_s)
    else:
        _render_snapshot_summary(cur)


def _render_before_after_table(case) -> None:
    import math
    if not case.exposure_deltas:
        _render_before_after_summary(case)
        return

    rows = []
    for d in case.exposure_deltas:
        b = f"{d.before:.1f}%" if d.before is not None else "—"
        a = f"{d.after:.1f}%"  if d.after  is not None else "—"
        dp= f"{d.delta_pp:+.1f}pp" if d.delta_pp is not None else "—"
        dir_icon = {"up":"↑","down":"↓","neutral":"↔","unknown":"?"}.get(d.direction,"?")
        rows.append({"מדד": d.label_he, "לפני": b, "אחרי": a, "שינוי": dp, "כיוון": dir_icon})

    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _next_cta(title, desc, btn_label, btn_key, target_step) -> None:
    st.markdown(f"""
<div class="wiz-next">
  <div class="wiz-next-title">✅ {title}</div>
  <div class="wiz-next-desc">{desc}</div>
</div>""", unsafe_allow_html=True)
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    if st.button(btn_label, key=btn_key, type="primary"):
        st.session_state["client_wizard_step"] = target_step
        st.rerun()


def _show_blockers(reasons: list) -> None:
    if reasons:
        st.markdown(
            '<div class="wiz-blocker">' +
            "<br>".join(f"⚠️ {r}" for r in reasons) +
            '</div>', unsafe_allow_html=True
        )


def _back_btn(target_step: int) -> None:
    if st.button(f"← חזור לשלב {target_step}", key=f"back_{target_step}"):
        st.session_state["client_wizard_step"] = target_step
        st.rerun()
