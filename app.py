from __future__ import annotations

import calendar
import hmac
import os
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from comment_store import CommentStore
from rebuy_loader import (
    WorkbookData,
    actual_sales_long,
    clean_text,
    export_workbook_with_comments,
    find_col,
    future_demand_long,
    get_value,
    load_display_labels,
    load_workbook_data,
    safe_filename,
)

BASE_DIR = Path(__file__).resolve().parent
APP_DATA = BASE_DIR / "app_data"
UPLOAD_DIR = APP_DATA / "uploads"
EXPORT_DIR = APP_DATA / "exports"
HISTORY_DIR = APP_DATA / "meeting_history"
DB_PATH = APP_DATA / "comments.db"
LABEL_PATH = BASE_DIR / "config" / "display_labels.json"
for folder in [APP_DATA, UPLOAD_DIR, EXPORT_DIR, HISTORY_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="Inventory Rebuy Review",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _configured_passcode() -> str:
    """Read the access passcode without storing it in public source control."""
    try:
        secret_value = st.secrets.get("APP_PASSCODE", "")
    except Exception:
        secret_value = ""
    return str(secret_value or os.environ.get("REBUY_REVIEW_PASSCODE", "")).strip()


def require_passcode() -> None:
    """Stop page rendering until this browser session supplies the passcode."""
    if st.session_state.get("rebuy_access_granted") is True:
        return

    st.markdown(
        """
        <style>
            header[data-testid="stHeader"],
            section[data-testid="stSidebar"] {
                display: none !important;
            }

            .stApp {
                background:
                    radial-gradient(circle at 18% 14%, rgba(37, 99, 235, 0.12), transparent 28rem),
                    radial-gradient(circle at 86% 84%, rgba(14, 165, 233, 0.10), transparent 24rem),
                    #f8fafc;
            }

            .block-container {
                max-width: 31rem;
                padding-top: 14vh;
            }

            .access-brand {
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 0.8rem;
                margin-bottom: 1.4rem;
            }

            .access-brand-mark {
                position: relative;
                display: grid;
                place-items: center;
                width: 3rem;
                height: 3rem;
                overflow: hidden;
                border-radius: 0.9rem;
                color: #ffffff;
                background: linear-gradient(145deg, #111827 10%, #1d4ed8 100%);
                font-size: 1.5rem;
                font-weight: 850;
                letter-spacing: -0.08em;
                box-shadow: 0 10px 24px rgba(29, 78, 216, 0.28);
            }

            .access-brand-mark::after {
                content: "";
                position: absolute;
                right: -0.18rem;
                bottom: -0.18rem;
                width: 1.05rem;
                height: 1.05rem;
                border: 0.2rem solid rgba(255, 255, 255, 0.72);
                border-radius: 50%;
            }

            .access-brand-copy {
                display: flex;
                flex-direction: column;
                line-height: 1;
            }

            .access-brand-copy strong {
                color: #111827;
                font-size: 1.05rem;
                font-weight: 850;
                letter-spacing: 0.1em;
            }

            .access-brand-copy span {
                margin-top: 0.3rem;
                color: #64748b;
                font-size: 0.65rem;
                font-weight: 750;
                letter-spacing: 0.1em;
            }

            .access-heading {
                margin-bottom: 0.25rem;
                color: #0f172a;
                text-align: center;
                font-size: 1.55rem;
                font-weight: 800;
                letter-spacing: -0.025em;
            }

            .access-subtitle {
                margin-bottom: 1.5rem;
                color: #64748b;
                text-align: center;
                font-size: 0.9rem;
            }

            div[data-testid="stForm"] {
                padding: 1.4rem;
                background: rgba(255, 255, 255, 0.94);
                border: 1px solid #e2e8f0;
                border-radius: 1.1rem;
                box-shadow: 0 18px 45px rgba(15, 23, 42, 0.09);
            }
        </style>
        <div class="access-brand" aria-label="Rebuy Review Platform">
            <div class="access-brand-mark" aria-hidden="true">R</div>
            <div class="access-brand-copy">
                <strong>REBUY</strong>
                <span>REVIEW PLATFORM</span>
            </div>
        </div>
        <div class="access-heading">Welcome back</div>
        <div class="access-subtitle">Enter the access passcode to continue.</div>
        """,
        unsafe_allow_html=True,
    )

    expected_passcode = _configured_passcode()
    if not expected_passcode:
        st.error("App access is not configured. Add APP_PASSCODE to the app's Streamlit secrets.")
        st.stop()

    with st.form("rebuy_passcode_form", clear_on_submit=True):
        entered_passcode = st.text_input(
            "Passcode",
            type="password",
            placeholder="Enter passcode",
        )
        submitted = st.form_submit_button("Unlock dashboard", type="primary", use_container_width=True)

    if submitted:
        if hmac.compare_digest(entered_passcode, expected_passcode):
            st.session_state["rebuy_access_granted"] = True
            st.session_state.pop("rebuy_passcode_error", None)
            st.rerun()
        else:
            st.session_state["rebuy_passcode_error"] = True

    if st.session_state.get("rebuy_passcode_error"):
        st.error("Incorrect passcode. Please try again.")

    st.stop()


@st.cache_data(show_spinner="Reading workbook...")
def cached_load_workbook(path: str, workbook_name: str, mtime: float) -> WorkbookData:
    # mtime is included only to invalidate the cache if the local file changes.
    return load_workbook_data(path, workbook_name=workbook_name)


def fmt_num(value: Any, decimals: int = 0) -> str:
    try:
        if value is None or value == "" or pd.isna(value):
            return "—"
        n = float(value)
        if decimals == 0:
            return f"{n:,.0f}"
        return f"{n:,.{decimals}f}"
    except Exception:
        return clean_text(value) or "—"


def fmt_dollar(value: Any, decimals: int = 0) -> str:
    try:
        if value is None or value == "" or pd.isna(value):
            return "—"
        return f"${float(value):,.{decimals}f}"
    except Exception:
        return clean_text(value) or "—"

def fmt_campaign(value: Any) -> str:
    """Format campaign values like 202603.0 as 202603."""
    try:
        if value is None or value == "" or pd.isna(value):
            return "—"

        number = pd.to_numeric(value, errors="coerce")

        if pd.notna(number):
            return str(int(number))

        text = clean_text(value)

        if text.endswith(".0"):
            text = text[:-2]

        return text or "—"

    except Exception:
        text = clean_text(value)

        if text.endswith(".0"):
            text = text[:-2]

        return text or "—"

def display_value(value: Any) -> str:
    if value is None:
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except Exception:
        pass
    text = str(value).strip()
    return text if text else "—"


def label(original: str, labels: dict[str, str]) -> str:
    return labels.get(original, original.replace("\n", " "))


def row_field(row: pd.Series, logical_name: str, default: Any = "") -> Any:
    return get_value(row, logical_name, default)


def key_value_table(items: list[tuple[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{"Field": k, "Value": display_value(v)} for k, v in items])


def save_uploaded_file(uploaded_file) -> tuple[str, str]:
    raw = uploaded_file.getvalue()
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()[:12]
    safe = safe_filename(uploaded_file.name)
    path = UPLOAD_DIR / f"{safe}_{digest}.xlsx"
    path.write_bytes(raw)
    return str(path), uploaded_file.name


def filter_options(df: pd.DataFrame, logical_name: str) -> list[str]:
    col = find_col(df, logical_name)
    if not col:
        return []
    values = sorted({clean_text(v) for v in df[col].dropna().tolist() if clean_text(v)})
    return values


def responsive_filter_options(
    df: pd.DataFrame,
    logical_name: str,
    current_filters: dict[str, list[str]],
) -> list[str]:
    """Return values compatible with every other active sidebar filter."""
    compatible = df
    for other_logical, selected in current_filters.items():
        if other_logical == logical_name or not selected:
            continue
        other_col = find_col(compatible, other_logical)
        if other_col:
            compatible = compatible[compatible[other_col].map(clean_text).isin(selected)]
    return filter_options(compatible, logical_name)


def persist_filter_selection(widget_key: str, selection_key: str):
    """Keep a filter selection stable when its compatible option list changes."""
    st.session_state[selection_key] = list(st.session_state.get(widget_key, []))


def apply_filters(df: pd.DataFrame, filters: dict[str, list[str]], search: str) -> pd.DataFrame:
    out = df.copy()
    for logical, selected in filters.items():
        col = find_col(out, logical)
        if col and selected:
            out = out[out[col].map(clean_text).isin(selected)]
    search = clean_text(search).lower()
    if search:
        fsc_col = find_col(out, "fsc")
        full_col = find_col(out, "full_description")
        desc_col = find_col(out, "description")
        mask = pd.Series(False, index=out.index)
        for col in [fsc_col, full_col, desc_col]:
            if col:
                mask = mask | out[col].astype(str).str.lower().str.contains(search, na=False)
        out = out[mask]
    return out.reset_index(drop=True)

DECISION_LABELS = {
    "Y": "Rebought / Bought (Y)",
    "N": "Not Bought (N)",
    "TBD": "Escalated (TBD)",
}


def normalize_rebuy_decision(value: Any) -> str:
    """Convert workbook decision values into Y, N, TBD, or blank."""
    text = clean_text(value).upper().replace(" ", "")
    text = text.replace(".", "")

    if text in {"Y", "YES"}:
        return "Y"
    if text in {"N", "NO"}:
        return "N"
    if text in {"TBD", "TBA", "ESCALATED"}:
        return "TBD"

    return ""


def save_history_upload(uploaded_file, provided_meeting_date: date) -> tuple[str, str]:
    """Save a past meeting workbook into local history storage."""
    raw = uploaded_file.getvalue()

    import hashlib

    digest = hashlib.sha256(raw).hexdigest()[:12]
    safe = safe_filename(uploaded_file.name)
    date_text = provided_meeting_date.strftime("%Y-%m-%d")

    path = HISTORY_DIR / f"{date_text}__{safe}__{digest}.xlsx"
    path.write_bytes(raw)

    return str(path), uploaded_file.name


def list_history_files() -> list[dict[str, Any]]:
    """Return saved past meeting files, newest meeting date first."""
    records = []

    for path in HISTORY_DIR.glob("*.xlsx"):
        name_parts = path.name.split("__")
        provided_date = name_parts[0] if name_parts else "Unknown Date"

        original_name = path.name
        if len(name_parts) >= 3:
            original_name = "__".join(name_parts[1:-1]) + ".xlsx"

        records.append(
            {
                "path": path,
                "provided_date": provided_date,
                "original_name": original_name,
                "modified_time": path.stat().st_mtime,
            }
        )

    return sorted(
        records,
        key=lambda x: (x["provided_date"], x["modified_time"]),
        reverse=True,
    )

def summarize_rebuy_meeting(data: WorkbookData, provided_date: str) -> dict[str, Any]:
    """Summarize one historical rebuy workbook by unique FSC and decision."""
    rows = []

    for _, row in data.rebuys.iterrows():
        fsc = clean_text(row_field(row, "fsc"))
        if not fsc:
            continue

        full_description = clean_text(row_field(row, "full_description"))
        description = clean_text(row_field(row, "description"))
        product_name = full_description or description

        rows.append(
            {
                "FSC": fsc,
                "Profile": clean_text(row_field(row, "profile")),
                "Product Name": product_name,
                "Description": description,
                "Rebuy Qty": pd.to_numeric(row_field(row, "rebuy_qty"), errors="coerce"),
                "Price": pd.to_numeric(row_field(row, "rebuy_dollars"), errors="coerce"),
                "Decision": normalize_rebuy_decision(row_field(row, "rebuy_decision")),
                "Raw Decision": clean_text(row_field(row, "rebuy_decision")),
            }
        )

    raw_df = pd.DataFrame(rows)

    empty_detail = pd.DataFrame(
        columns=[
            "Profile",
            "FSC",
            "Product Name",
            "Description",
            "Rebuy Qty",
            "Price",
        ]
    )

    if raw_df.empty:
        return {
            "provided_date": provided_date,
            "meeting_name": f"{provided_date} Rebuy",
            "unique_fsc_count": 0,
            "unique_profile_count": 0,
            "decision_counts": {"Y": 0, "N": 0, "TBD": 0},
            "total_bought_amount": 0,
            "review_required": True,
            "details": {
                "Y": empty_detail,
                "N": empty_detail,
                "TBD": empty_detail,
            },
        }

    sku_rows = []

    for fsc, group in raw_df.groupby("FSC", sort=True):
        decisions = sorted(set(group["Decision"].tolist()))
        valid_decisions = [d for d in decisions if d in DECISION_LABELS]

        if len(decisions) == 1 and len(valid_decisions) == 1:
            final_decision = valid_decisions[0]
        else:
            final_decision = "REVIEW"

        product_names = [x for x in group["Product Name"].tolist() if clean_text(x)]
        descriptions = [x for x in group["Description"].tolist() if clean_text(x)]
        profiles = sorted({x for x in group["Profile"].tolist() if clean_text(x)})

        rebuy_qty_values = group["Rebuy Qty"].dropna().tolist()
        price_values = group["Price"].dropna().tolist()

        sku_rows.append(
            {
                "Profile": ", ".join(profiles),
                "FSC": fsc,
                "Product Name": product_names[0] if product_names else "",
                "Description": descriptions[0] if descriptions else "",
                "Rebuy Qty": rebuy_qty_values[0] if rebuy_qty_values else None,
                "Price": price_values[0] if price_values else None,
                "Decision": final_decision,
            }
        )

    sku_df = pd.DataFrame(sku_rows)

    unique_fsc_count = int(sku_df["FSC"].nunique())
    unique_profile_count = int(raw_df["Profile"].replace("", pd.NA).dropna().nunique())

    decision_counts = {
        "Y": int((sku_df["Decision"] == "Y").sum()),
        "N": int((sku_df["Decision"] == "N").sum()),
        "TBD": int((sku_df["Decision"] == "TBD").sum()),
    }

    decision_total = sum(decision_counts.values())
    review_required = decision_total != unique_fsc_count

    total_bought_amount = float(
        sku_df.loc[sku_df["Decision"] == "Y", "Price"].fillna(0).sum()
    )

    details = {}

    for decision in ["Y", "N", "TBD"]:
        detail_df = sku_df.loc[
            sku_df["Decision"] == decision,
            [
                "Profile",
                "FSC",
                "Product Name",
                "Description",
                "Rebuy Qty",
                "Price",
            ],
        ]

        details[decision] = detail_df.sort_values(["Profile", "FSC"]).reset_index(drop=True)

    return {
        "provided_date": provided_date,
        "meeting_name": f"{provided_date} Rebuy",
        "unique_fsc_count": unique_fsc_count,
        "unique_profile_count": unique_profile_count,
        "decision_counts": decision_counts,
        "total_bought_amount": total_bought_amount,
        "review_required": review_required,
        "details": details,
    }

def render_page_top_nav(back_button_key: str):
    nav_container_key = f"top_home_nav_{back_button_key}"

    with st.container(key=nav_container_key):
        if st.button(
            "← Home",
            key=back_button_key,
        ):
            st.session_state["page"] = "home"
            st.rerun()

    st.markdown(
        f"""
        <style>
            .st-key-{nav_container_key} {{
                position: fixed;
                top: 3rem;
                left: 1.2rem;
                z-index: 1000000;
                width: auto;
            }}

            .st-key-{nav_container_key} button {{
                min-height: 2.25rem;
                height: 2.25rem;
                padding: 0rem 0.75rem;
                font-size: 0.875rem;
                font-weight: 400;
                border-radius: 0.5rem;
                line-height: 1;
                white-space: nowrap;
            }}

            button[kind="header"] {{
                margin-top: 2.8rem !important;
            }}

            .block-container {{
                padding-top: 3.7rem !important;
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )

def render_starting_page():
    st.title("Inventory Rebuy Platform")
    st.write("Choose where you want to go.")

    left, right = st.columns(2)

    with left:
        st.subheader("1) Review Dashboard")
        st.write("Use the current SKU-by-SKU rebuy review dashboard.")

        if st.button("Open Review Dashboard", type="primary", use_container_width=True):
            st.session_state["page"] = "review"
            st.rerun()

    with right:
        st.subheader("2) Past Decisions / Meetings")
        st.write("Upload and review past rebuy meeting files by meeting date.")

        if st.button("Open Past Meetings", use_container_width=True):
            st.session_state["page"] = "history"
            st.rerun()

def render_history_calendar(history_summaries: list[dict[str, Any]]):
    meeting_dates = sorted(
        {
            item["summary"]["provided_date"]
            for item in history_summaries
            if item.get("summary")
        }
    )

    if not meeting_dates:
        st.caption("No meeting dates saved yet.")
        return

    month_options = sorted({d[:7] for d in meeting_dates}, reverse=True)

    selected_month = st.selectbox(
        "Meeting calendar",
        options=month_options,
        key="history_calendar_month",
    )

    year, month = [int(x) for x in selected_month.split("-")]
    meeting_date_set = set(meeting_dates)

    summary_by_date = {
        item["summary"]["provided_date"]: item["summary"]
        for item in history_summaries
        if item.get("summary")
    }

    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdayscalendar(year, month)

    html = """
    <style>
        body {
            margin: 0;
            font-family: sans-serif;
            overflow: visible;
        }

        .calendar-wrap {
            width: 100%;
            overflow: visible;
            padding-bottom: 80px;
        }

        .rebuy-calendar {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: center;
            overflow: visible;
        }

        .rebuy-calendar th {
            color: #666;
            font-weight: 600;
            padding: 4px;
        }

        .rebuy-calendar td {
            position: relative;
            padding: 5px;
            height: 30px;
            overflow: visible;
        }

        .normal-day {
            color: #777;
        }

        .meeting-day {
            display: inline-block;
            min-width: 26px;
            padding: 5px 7px;
            border-radius: 999px;
            background: #d7ebff;
            color: #003d73;
            font-weight: 700;
            cursor: default;
        }

        .sticky-tooltip {
            display: none;
            position: absolute;
            z-index: 9999;
            top: -10px;
            left: 42px;
            width: 155px;
            background: #fff8c6;
            color: #222;
            border: 1px solid #e3d37a;
            border-radius: 8px;
            padding: 9px 10px;
            text-align: left;
            font-size: 12px;
            line-height: 1.45;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
            white-space: normal;
        }

        .sticky-tooltip.left-side {
            left: auto;
            right: 42px;
        }

        .sticky-tooltip-title {
            font-weight: 700;
            margin-bottom: 5px;
            border-bottom: 1px solid #e3d37a;
            padding-bottom: 3px;
        }

        td:hover .sticky-tooltip {
            display: block;
        }
    </style>

    <div class="calendar-wrap">
    <table class="rebuy-calendar">
        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
    """

    for week in weeks:
        html += "<tr>"

        for weekday_index, day in enumerate(week):
            if day == 0:
                html += "<td></td>"
                continue

            date_text = f"{year}-{month:02d}-{day:02d}"

            if date_text in meeting_date_set:
                summary = summary_by_date[date_text]

                y_count = summary["decision_counts"]["Y"]
                n_count = summary["decision_counts"]["N"]
                tbd_count = summary["decision_counts"]["TBD"]
                total_rebuy = fmt_dollar(summary.get("total_bought_amount", 0))

                side_class = "left-side" if weekday_index >= 5 else ""

                html += f"""
                <td>
                    <span class="meeting-day">{day}</span>
                    <div class="sticky-tooltip {side_class}">
                        <div class="sticky-tooltip-title">{date_text} Rebuy</div>
                        <div>Y: {y_count:,}</div>
                        <div>N: {n_count:,}</div>
                        <div>TBD: {tbd_count:,}</div>
                        <div>Total Rebuy: {total_rebuy}</div>
                    </div>
                </td>
                """
            else:
                html += f'<td><span class="normal-day">{day}</span></td>'

        html += "</tr>"

    html += """
    </table>
    </div>
    """

    components.html(html, height=330)

def render_history_calendar(history_summaries: list[dict[str, Any]]):
    meeting_dates = sorted(
        {
            item["summary"]["provided_date"]
            for item in history_summaries
            if item.get("summary")
        }
    )

    if not meeting_dates:
        st.caption("No meeting dates saved yet.")
        return

    month_options = sorted({d[:7] for d in meeting_dates}, reverse=True)

    selected_month = st.selectbox(
        "Meeting calendar",
        options=month_options,
        key="history_calendar_month",
    )

    year, month = [int(x) for x in selected_month.split("-")]
    meeting_date_set = set(meeting_dates)

    summary_by_date = {
        item["summary"]["provided_date"]: item["summary"]
        for item in history_summaries
        if item.get("summary")
    }

    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdayscalendar(year, month)

    html = """
    <style>
        body {
            margin: 0;
            font-family: sans-serif;
            overflow: visible;
        }

        .calendar-box {
            width: 100%;
            overflow: visible;
            padding-bottom: 100px;
        }

        .rebuy-calendar {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: center;
        }

        .rebuy-calendar th {
            color: #666;
            font-weight: 600;
            padding: 4px;
        }

        .rebuy-calendar td {
            position: relative;
            padding: 5px;
            height: 32px;
            overflow: visible;
        }

        .normal-day {
            color: #777;
        }

        .meeting-day {
            display: inline-block;
            min-width: 26px;
            padding: 5px 7px;
            border-radius: 999px;
            background: #d7ebff;
            color: #003d73;
            font-weight: 700;
            cursor: default;
        }

        .sticky-note {
            display: none;
            position: absolute;
            z-index: 999999;
            top: -8px;
            left: 42px;
            width: 150px;
            background: #fff6b8;
            color: #222;
            border: 1px solid #d8c95f;
            border-radius: 8px;
            padding: 9px 10px;
            text-align: left;
            font-size: 12px;
            line-height: 1.5;
            box-shadow: 0 5px 14px rgba(0, 0, 0, 0.25);
            white-space: nowrap;
        }

        .sticky-note.left-side {
            left: auto;
            right: 42px;
        }

        .sticky-title {
            font-weight: 700;
            margin-bottom: 5px;
            padding-bottom: 4px;
            border-bottom: 1px solid #d8c95f;
        }

        td:hover .sticky-note {
            display: block;
        }
    </style>

    <div class="calendar-box">
    <table class="rebuy-calendar">
        <tr>
            <th>Sun</th>
            <th>Mon</th>
            <th>Tue</th>
            <th>Wed</th>
            <th>Thu</th>
            <th>Fri</th>
            <th>Sat</th>
        </tr>
    """

    for week in weeks:
        html += "<tr>"

        for weekday_index, day in enumerate(week):
            if day == 0:
                html += "<td></td>"
                continue

            date_text = f"{year}-{month:02d}-{day:02d}"

            if date_text in meeting_date_set:
                summary = summary_by_date[date_text]

                y_count = summary["decision_counts"]["Y"]
                n_count = summary["decision_counts"]["N"]
                tbd_count = summary["decision_counts"]["TBD"]
                total_rebuy = fmt_dollar(summary.get("total_bought_amount", 0))

                side_class = "left-side" if weekday_index >= 5 else ""

                html += f"""
                <td>
                    <span class="meeting-day">{day}</span>
                    <div class="sticky-note {side_class}">
                        <div class="sticky-title">{date_text}</div>
                        <div>Y: {y_count:,}</div>
                        <div>N: {n_count:,}</div>
                        <div>TBD: {tbd_count:,}</div>
                        <div>Total Rebuy: {total_rebuy}</div>
                    </div>
                </td>
                """
            else:
                html += f'<td><span class="normal-day">{day}</span></td>'

        html += "</tr>"

    html += """
    </table>
    </div>
    """

    components.html(html, height=300)

def render_history_summary_table(history_summaries: list[dict[str, Any]]):
    """Show saved meetings as a compact clickable table."""
    if not history_summaries:
        st.info("No past meeting files have been saved yet.")
        return

    sorted_items = sorted(
        history_summaries,
        key=lambda item: item["summary"]["provided_date"],
        reverse=True,
    )

    st.markdown(
        """
        <style>
            .history-table-header {
                font-size: 0.82rem;
                font-weight: 700;
                color: #555;
                border-bottom: 1px solid #ddd;
                padding-bottom: 0.35rem;
                margin-bottom: 0.25rem;
            }

            .history-table-row {
                border-bottom: 1px solid #eee;
                padding-top: 0.15rem;
                padding-bottom: 0.15rem;
            }

            div[class*="st-key-open_history_review_"] button {
                border: none !important;
                background: transparent !important;
                color: #1f77b4 !important;
                padding: 0 !important;
                min-height: 1.6rem !important;
                height: 1.6rem !important;
                text-align: left !important;
                box-shadow: none !important;
                font-weight: 600 !important;
            }

            div[class*="st-key-open_history_review_"] button:hover {
                text-decoration: underline !important;
                color: #0b4f8a !important;
            }

            div[class*="st-key-open_history_review_"] button p {
                text-align: left !important;
                font-size: 0.9rem !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    header = st.columns([2.1, 0.9, 0.7, 0.85, 1.05, 1.0, 1.05])
    header[0].markdown('<div class="history-table-header">Meeting</div>', unsafe_allow_html=True)
    header[1].markdown('<div class="history-table-header">Profiles</div>', unsafe_allow_html=True)
    header[2].markdown('<div class="history-table-header">FSCs</div>', unsafe_allow_html=True)
    header[3].markdown('<div class="history-table-header">Bought</div>', unsafe_allow_html=True)
    header[4].markdown('<div class="history-table-header">Not Bought</div>', unsafe_allow_html=True)
    header[5].markdown('<div class="history-table-header">Escalated</div>', unsafe_allow_html=True)
    header[6].markdown('<div class="history-table-header">Total Bought $</div>', unsafe_allow_html=True)

    for i, item in enumerate(sorted_items):
        summary = item["summary"]

        row = st.columns([2.1, 0.9, 0.7, 0.85, 1.05, 1.0, 1.05])

        button_key = f"open_history_review_{i}_{safe_filename(summary['meeting_name'])}"

        if row[0].button(
            summary["meeting_name"],
            key=button_key,
            use_container_width=True,
        ):
            st.session_state["history_review_path"] = str(item["path"])
            st.session_state["history_review_workbook_name"] = item["original_name"]
            st.session_state["history_review_meeting_name"] = summary["meeting_name"]
            st.session_state["current_pos"] = 0
            st.session_state["page"] = "history_review"
            st.rerun()

        row[1].write(f"{summary['unique_profile_count']:,}")
        row[2].write(f"{summary['unique_fsc_count']:,}")
        row[3].write(f"{summary['decision_counts']['Y']:,}")
        row[4].write(f"{summary['decision_counts']['N']:,}")
        row[5].write(f"{summary['decision_counts']['TBD']:,}")
        row[6].write(fmt_dollar(summary.get("total_bought_amount", 0)))

def render_meeting_history_page():
    render_page_top_nav("back_to_home_from_history")

    st.title("Past Rebuy Decisions / Meetings")

    history_files = list_history_files()
    history_summaries = []

    for item in history_files:
        path = item["path"]
        provided_date = item["provided_date"]

        try:
            data = cached_load_workbook(
                str(path),
                item["original_name"],
                os.path.getmtime(path),
            )

            summary = summarize_rebuy_meeting(data, provided_date)

            history_summaries.append(
                {
                    "path": path,
                    "provided_date": provided_date,
                    "original_name": item["original_name"],
                    "summary": summary,
                }
            )

        except Exception as exc:
            st.error(f"Could not read saved meeting file {path.name}: {exc}")

    with st.sidebar:
        st.markdown("### Add Meeting")

        uploaded_history = st.file_uploader(
            "Drag and drop past rebuy meeting file",
            type=["xlsx"],
            key="history_file_uploader",
        )

        provided_meeting_date = st.date_input(
            "What day was this rebuy meeting held?",
            value=date.today(),
            key="history_meeting_date",
        )

        if st.button("Save meeting", type="primary", use_container_width=True):
            if uploaded_history is None:
                st.warning("Upload a past rebuy meeting file first.")
            else:
                saved_path, original_name = save_history_upload(
                    uploaded_history,
                    provided_meeting_date,
                )

                st.success(
                    f"Saved {original_name} for {provided_meeting_date:%Y-%m-%d}."
                )

                st.rerun()

        st.divider()

        st.markdown("### Meeting Calendar")
        render_history_calendar(history_summaries)

        st.caption(
            "Blue dates have saved meetings. Hover over a blue date to see Y/N/TBD counts and total rebuy amount."
        )

    st.subheader("Saved Meetings")

    if not history_summaries:
        st.info("No past meeting files have been saved yet.")
        return

    render_history_summary_table(history_summaries)


    with st.expander("FSC / Product details by decision", expanded=False):
        meeting_options = [
            item["summary"]["meeting_name"]
            for item in history_summaries
        ]

        selected_meeting_name = st.selectbox(
            "Select meeting",
            options=meeting_options,
            key="history_detail_meeting",
        )

        selected_item = next(
            item
            for item in history_summaries
            if item["summary"]["meeting_name"] == selected_meeting_name
        )

        decision_choice = st.selectbox(
            "Select decision group",
            options=["Y", "N", "TBD"],
            format_func=lambda x: DECISION_LABELS[x],
            key="history_detail_decision",
        )

        detail_df = selected_item["summary"]["details"][decision_choice]

        if detail_df.empty:
            st.info(f"No FSCs found for {DECISION_LABELS[decision_choice]}.")
        else:
            display_detail_df = detail_df.copy()

            display_detail_df["Rebuy Qty"] = display_detail_df["Rebuy Qty"].apply(
                lambda x: fmt_num(x)
            )

            display_detail_df["Price"] = display_detail_df["Price"].apply(
                lambda x: fmt_dollar(x)
            )

            st.dataframe(
                display_detail_df,
                hide_index=True,
                use_container_width=True,
            )

def make_sku_label(row: pd.Series) -> str:
    row_num = int(row["__row_number"])
    fsc = clean_text(row_field(row, "fsc"))
    profile = clean_text(row_field(row, "profile"))
    name = clean_text(row_field(row, "full_description") or row_field(row, "description"))
    planner = clean_text(row_field(row, "planner"))
    short_name = name[:55] + ("..." if len(name) > 55 else "")
    return f"Row {row_num} | {fsc} | Profile {profile} | {short_name} | {planner}"


def render_metric_cards(row: pd.Series):
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

    c1.metric("MOQ", fmt_num(row_field(row, "moq")))
    c2.metric("Rebuy Qty", fmt_num(row_field(row, "rebuy_qty")))
    c3.metric("Rebuy $", fmt_dollar(row_field(row, "rebuy_dollars")))
    c4.metric("ABC Tier", display_value(row_field(row, "abc")))
    c5.metric("Stockout C with SS", fmt_campaign(row_field(row, "stockout_with_ss")))
    c6.metric("Stockout C without SS", fmt_campaign(row_field(row, "stockout_no_ss")))
    c7.metric("Fill-by Campaign", fmt_campaign(row_field(row, "fill_by_campaign")))


def render_section_tables(row: pd.Series):
    inventory_components = [
        ("On Hand", "NA OH", fmt_num(row_field(row, "na_oh"))),
        ("In Transit", "NA AIT", fmt_num(row_field(row, "na_ait"))),
        ("POs", "Open purchase orders", fmt_num(row_field(row, "pos"))),
        ("BOs", "Backorders", fmt_num(row_field(row, "bos"))),
    ]
    component_html = "".join(
        f"""
        <div class="inventory-tree-row">
            <div class="inventory-tree-name">
                <span class="inventory-tree-branch">↳</span>
                <span>{escape(label_text)}</span>
                <small>{escape(sub_text)}</small>
            </div>
            <div class="inventory-tree-value">{escape(value_text)}</div>
        </div>
        """
        for label_text, sub_text, value_text in inventory_components
    )

    lead_time_components = [
        ("Management LT", fmt_num(row_field(row, "mgt_lt_days"))),
        ("Transit LT", fmt_num(row_field(row, "transit_lt_days"))),
    ]
    lead_time_component_html = "".join(
        f"""
        <div class="inventory-tree-row">
            <div class="inventory-tree-name">
                <span class="inventory-tree-branch">↳</span>
                <span>{escape(label_text)}</span>
            </div>
            <div class="inventory-tree-value">{escape(value_text)} days</div>
        </div>
        """
        for label_text, value_text in lead_time_components
    )

    inventory_status_html = f"""
        <div class="inventory-status-layout">
            <div class="inventory-tree">
                <div class="inventory-tree-total">
                    <div>
                        <div class="inventory-tree-total-label">Total NA Inventory</div>
                        <div class="inventory-tree-total-note">Current inventory position</div>
                    </div>
                    <div class="inventory-tree-total-value">{escape(fmt_num(row_field(row, "total_na_inv")))}</div>
                </div>
                <div class="inventory-tree-children">
                    {component_html}
                </div>
            </div>
            <div class="inventory-tree inventory-secondary-tree">
                <div class="inventory-tree-total">
                    <div>
                        <div class="inventory-tree-total-label">Customer Orders</div>
                        <div class="inventory-tree-total-note">Separate sales channel</div>
                    </div>
                    <div class="inventory-tree-total-value">{escape(fmt_num(row_field(row, "cust_orders")))}</div>
                </div>
                <div class="inventory-tree-context">
                    Separate sales channel · Not included in the demand forecast above
                </div>
            </div>
            <div class="inventory-tree inventory-secondary-tree">
                <div class="inventory-tree-total">
                    <div>
                        <div class="inventory-tree-total-label">Grand Lead Time</div>
                        <div class="inventory-tree-total-note">
                            {escape(fmt_num(row_field(row, "grand_lt_weeks")))} weeks ·
                            {escape(fmt_num(row_field(row, "grand_lt_camps"), 1))} campaigns
                        </div>
                    </div>
                    <div class="inventory-tree-total-value">
                        {escape(fmt_num(row_field(row, "grand_lt_days")))}
                        <span class="inventory-tree-total-unit">days</span>
                    </div>
                </div>
                <div class="inventory-tree-children">
                    {lead_time_component_html}
                </div>
            </div>
        </div>
    """
    st.markdown(
        inventory_status_html.replace("\n", ""),
        unsafe_allow_html=True,
    )


def render_demand(data: WorkbookData, row: pd.Series):
    future_df = future_demand_long(data, row)
    fsc = clean_text(row_field(row, "fsc"))
    actual_df = actual_sales_long(data, fsc, include_related=True)

    related = data.cm_map.get(fsc, [])
    if related:
        st.info(f"CM SKU link detected. Actual sales include related FSC(s): {', '.join(related)}")

    # ------------------------------------------------------------
    # Build a consistent campaign x-axis for every SKU.
    # Past actual sales = most recent 26 actual sales campaigns.
    # Future demand = first 26 future demand campaigns from REBUYS.
    # ------------------------------------------------------------
    actual_campaigns = sorted(
        [str(c) for c in data.actual_campaigns if str(c).strip()],
        key=lambda x: pd.to_numeric(x, errors="coerce"),
    )

    future_campaigns = sorted(
        [str(c) for c in data.future_campaigns if str(c).strip()],
        key=lambda x: pd.to_numeric(x, errors="coerce"),
    )

    past_axis = actual_campaigns[-26:]
    future_axis = future_campaigns[:26]

    full_campaign_axis = []
    for camp in past_axis + future_axis:
        if camp not in full_campaign_axis:
            full_campaign_axis.append(camp)

    # Calendar lookup for chart tooltip only.
    calendar_lookup = getattr(data, "calendar_map", {}) or {}

    axis_df = pd.DataFrame({"Campaign": full_campaign_axis})
    axis_df["Campaign Sort"] = axis_df["Campaign"].apply(
        lambda x: pd.to_numeric(x, errors="coerce")
    )
    axis_df["US Start Date"] = axis_df["Campaign"].map(calendar_lookup).fillna("")

    # ------------------------------------------------------------
    # Prepare past actual sales.
    # ------------------------------------------------------------
    if actual_df.empty:
        actual_summary = pd.DataFrame(columns=["Campaign", "Quantity"])
    else:
        actual_summary = (
            actual_df.groupby("Campaign", as_index=False)["Sales Qty"]
            .sum()
            .rename(columns={"Sales Qty": "Quantity"})
        )

    actual_summary["Campaign"] = actual_summary["Campaign"].astype(str)

    actual_chart = axis_df.merge(
        actual_summary,
        on="Campaign",
        how="left",
    )
    actual_chart["Type"] = "Past Actual Sales"

    # ------------------------------------------------------------
    # Prepare future demand.
    # ------------------------------------------------------------
    if future_df.empty:
        future_summary = pd.DataFrame(columns=["Campaign", "Quantity"])
    else:
        future_summary = (
            future_df[["Campaign", "Future Demand"]]
            .copy()
            .rename(columns={"Future Demand": "Quantity"})
        )

    future_summary["Campaign"] = future_summary["Campaign"].astype(str)

    future_chart = axis_df.merge(
        future_summary,
        on="Campaign",
        how="left",
    )
    future_chart["Type"] = "Future Demand"

    # ------------------------------------------------------------
    # Combine actual + future.
    # Missing values stay blank.
    # Zero values are also converted to blank for the compact sparkline.
    # ------------------------------------------------------------
    combined = pd.concat([actual_chart, future_chart], ignore_index=True)
    combined["Quantity"] = pd.to_numeric(combined["Quantity"], errors="coerce")

    if not full_campaign_axis:
        st.subheader("Demand View")
        st.warning("No campaign columns were detected for actual sales or future demand.")
        return

    # ------------------------------------------------------------
    # Compact Demand View header with sparkline on the right.
    # ------------------------------------------------------------
    title_col, spark_col = st.columns([1.2, 5])

    with title_col:
        st.subheader("Demand View")

    spark_data = combined.copy()

    # Leave blanks where value is zero or missing.
    spark_data.loc[
        (spark_data["Quantity"].isna()) | (spark_data["Quantity"] == 0),
        "Quantity",
    ] = None

    endpoint_labels = (
        [full_campaign_axis[0], full_campaign_axis[-1]]
        if len(full_campaign_axis) > 1
        else full_campaign_axis
    )

    sparkline = (
        alt.Chart(spark_data)
        .mark_bar(size=5)
        .encode(
            x=alt.X(
                "Campaign:N",
                sort=full_campaign_axis,
                title=None,
                axis=alt.Axis(
                    values=endpoint_labels,
                    labelAngle=0,
                    labelFontSize=10,
                    labelPadding=4,
                    ticks=False,
                    domain=True,
                    domainColor="#D0D0D0",
                    domainWidth=1,
                    grid=False,
                ),
                scale=alt.Scale(domain=full_campaign_axis),
            ),
            xOffset=alt.XOffset("Type:N"),
            y=alt.Y(
                "Quantity:Q",
                axis=None,
                title=None,
            ),
            color=alt.Color(
                "Type:N",
                scale=alt.Scale(
                    domain=["Past Actual Sales", "Future Demand"],
                    range=["#1f77b4", "#2ca02c"],
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Campaign:N", title="Campaign"),
                alt.Tooltip("US Start Date:N", title="US Start Date"),
                alt.Tooltip("Type:N", title="Type"),
                alt.Tooltip("Quantity:Q", title="Units", format=",.0f"),
            ],
        )
        .properties(height=55)
    )

    with spark_col:
        st.altair_chart(sparkline, use_container_width=True)

    # ------------------------------------------------------------
    # Full bar chart is now collapsible.
    # When expanded, it looks like the current full chart.
    # ------------------------------------------------------------
    with st.expander("Show / hide full Demand View chart", expanded=False):
        st.markdown("**Past 1-Year Actual Sales + Future 1-Year Demand**")

        chart = (
            alt.Chart(combined)
            .mark_bar()
            .encode(
                x=alt.X(
                    "Campaign:N",
                    sort=full_campaign_axis,
                    title="Campaign",
                    axis=alt.Axis(labelAngle=-45),
                    scale=alt.Scale(domain=full_campaign_axis),
                ),
                xOffset=alt.XOffset("Type:N"),
                y=alt.Y("Quantity:Q", title="Units"),
                color=alt.Color(
                    "Type:N",
                    scale=alt.Scale(
                        domain=["Past Actual Sales", "Future Demand"],
                        range=["#1f77b4", "#2ca02c"],
                    ),
                    legend=alt.Legend(title="Demand Type"),
                ),
                tooltip=[
                    alt.Tooltip("Campaign:N", title="Campaign"),
                    alt.Tooltip("US Start Date:N", title="US Start Date"),
                    alt.Tooltip("Type:N", title="Type"),
                    alt.Tooltip("Quantity:Q", title="Units", format=",.0f"),
                ],
            )
            .properties(height=360)
        )

        st.altair_chart(chart, use_container_width=True)

    # ------------------------------------------------------------
    # Collapsible detail tables side by side.
    # Keep only Campaign + Sales Qty / Future Demand.
    # ------------------------------------------------------------
    actual_col, future_col = st.columns(2)

    with actual_col:
        with st.expander("Show / hide Past Actual Sales Detail", expanded=False):
            if actual_df.empty:
                st.warning("No matching ACTUAL SALES row was found for this FSC or its CM-related FSCs.")
            else:
                actual_table = actual_df.copy()
                actual_table["Sales Qty"] = pd.to_numeric(
                    actual_table["Sales Qty"], errors="coerce"
                ).fillna(0)

                actual_table = (
                    actual_table.groupby("Campaign", as_index=False)["Sales Qty"]
                    .sum()
                )

                actual_table["Campaign"] = actual_table["Campaign"].astype(str)
                actual_table["Campaign Sort"] = actual_table["Campaign"].apply(
                    lambda x: pd.to_numeric(x, errors="coerce")
                )
                actual_table = actual_table.sort_values("Campaign Sort")
                actual_table = actual_table[["Campaign", "Sales Qty"]]

                st.dataframe(
                    actual_table,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Sales Qty": st.column_config.NumberColumn(format="%0.0f")
                    },
                )

    with future_col:
        with st.expander("Show / hide Future Demand Detail", expanded=False):
            if future_df.empty:
                st.info("No future campaign demand columns were detected in REBUYS.")
            else:
                future_table = future_df.copy()
                future_table["Future Demand"] = pd.to_numeric(
                    future_table["Future Demand"], errors="coerce"
                ).fillna(0)

                future_table["Campaign"] = future_table["Campaign"].astype(str)
                future_table["Campaign Sort"] = future_table["Campaign"].apply(
                    lambda x: pd.to_numeric(x, errors="coerce")
                )
                future_table = future_table.sort_values("Campaign Sort")
                future_table = future_table[["Campaign", "Future Demand"]]

                st.dataframe(
                    future_table,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Future Demand": st.column_config.NumberColumn(format="%0.0f")
                    },
                )


def render_comments(data: WorkbookData, row: pd.Series, store: CommentStore):
    st.subheader("Comments + Finance Recommendation")

    row_number = int(row["__row_number"])
    fsc = clean_text(row_field(row, "fsc"))
    profile = clean_text(row_field(row, "profile"))
    product = clean_text(row_field(row, "full_description") or row_field(row, "description"))

    existing_initial = clean_text(row.get(data.finance_comment_header, ""))

    saved_comment = store.get(
        meeting_id=data.meeting_id,
        row_number=row_number,
        fsc=fsc,
        profile=profile,
    )

    default_comment = saved_comment if saved_comment is not None else existing_initial

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**Existing Comments**")
        comments_df = key_value_table(
            [
                ("Rebuy (Y/N/TBD)", row_field(row, "rebuy_decision")),
                ("Planner Comments", row_field(row, "planner_comments")),
                ("Campaign Planning Comments", row_field(row, "campaign_planning_comments")),
                ("Rebuy Meeting Comment", row_field(row, "rebuy_meeting_comment")),
                ("Finance Comment", default_comment),
            ]
        )
        st.dataframe(comments_df, hide_index=True, use_container_width=True)

    with right:
        st.markdown("**Finance Comment Input**")

        comment_key = f"comment_{data.meeting_id}_{row_number}_{fsc}_{profile}"
        clear_flag_key = f"{comment_key}__clear_requested"

        # If Clear local box was clicked in the previous run,
        # clear the text before Streamlit creates the text_area widget.
        if st.session_state.pop(clear_flag_key, False):
            st.session_state[comment_key] = ""

        # Initialize the text box only once.
        # This avoids Streamlit's session_state modification error.
        if comment_key not in st.session_state:
            st.session_state[comment_key] = default_comment

        comment = st.text_area(
            "Enter or edit Finance comment for this SKU",
            height=180,
            key=comment_key,
        )

        c1, c2, c3 = st.columns([1, 1, 1])

        if c1.button("Save Comment", type="primary", use_container_width=True):
            store.save(
                meeting_id=data.meeting_id,
                workbook_name=data.workbook_name,
                workbook_hash=data.workbook_hash,
                row_number=row_number,
                fsc=fsc,
                profile=profile,
                product_name=product,
                finance_comment=comment,
            )
            st.success("Comment saved.")
            st.rerun()

        if c2.button("Clear this comment", use_container_width=True):
            # Delete only the saved comment for the currently selected SKU.
            store.delete_one(
                meeting_id=data.meeting_id,
                row_number=row_number,
                fsc=fsc,
                profile=profile,
            )

            # Clear the visible text box on the next rerun.
            st.session_state[clear_flag_key] = True

            st.success("Cleared the locally saved comment for this SKU.")
            st.rerun()

        if c3.button("Clear all comments", use_container_width=True):
            store.delete_for_meeting(data.meeting_id)

            # Clear all comment boxes for this workbook from Streamlit memory.
            prefix = f"comment_{data.meeting_id}_"
            for key in list(st.session_state.keys()):
                if key.startswith(prefix):
                    del st.session_state[key]

            st.success("All locally saved comments for this workbook were cleared.")
            st.rerun()


def render_exports(data: WorkbookData, store: CommentStore):
    st.subheader("Export / Put Comments Back Into Excel")
    comments_df = store.list_for_meeting(data.meeting_id)
    nonblank_comments = comments_df[comments_df["Finance Comment"].astype(str).str.strip() != ""] if not comments_df.empty else comments_df

    st.write(f"Saved Finance comments for this workbook: **{len(nonblank_comments)}**")
    if not comments_df.empty:
        st.dataframe(
            comments_df,
            hide_index=True,
            use_container_width=True,
            height=340,
        )
        st.download_button(
            "Download comments CSV",
            data=comments_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"finance_comments_{safe_filename(data.workbook_name)}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("No comments saved yet. Save at least one SKU comment to enable export.")

    if st.button("Create Excel copy with comments written to Initial Finance Comments", disabled=comments_df.empty, use_container_width=True):
        output_path = EXPORT_DIR / f"{safe_filename(data.workbook_name)}_with_finance_comments.xlsx"
        export_workbook_with_comments(data.path, comments_df, output_path)
        st.session_state["last_export_path"] = str(output_path)
        st.success("Excel copy created. The original workbook was not changed.")

    export_path = st.session_state.get("last_export_path")
    if export_path and Path(export_path).exists():
        st.download_button(
            "Download reviewed Excel workbook",
            data=Path(export_path).read_bytes(),
            file_name=Path(export_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def render_review_dashboard(
    preloaded_workbook_path: str | None = None,
    preloaded_workbook_name: str | None = None,
    history_meeting_name: str | None = None,
):

    labels = load_display_labels(LABEL_PATH)
    store = CommentStore(DB_PATH)

    nav_key = (
        "back_to_home_from_history_review"
        if preloaded_workbook_path
        else "back_to_home_from_review"
    )

    render_page_top_nav(nav_key)

    st.title("Rebuy Review Dashboard")

    if history_meeting_name:
        st.caption(f"Viewing saved meeting: {history_meeting_name}")

    workbook_path = None
    workbook_name = None
    history_review_mode = preloaded_workbook_path is not None

    if history_review_mode:
        workbook_path = preloaded_workbook_path
        workbook_name = preloaded_workbook_name or Path(preloaded_workbook_path).name
    else:
        with st.sidebar:
            st.header("1) Load workbook")
            uploaded = st.file_uploader("Upload bi-weekly rebuy Excel file", type=["xlsx"])
            local_path = st.text_input(
                "Or enter local Excel path",
                placeholder=r"C:\Users\you\Documents\BEAUTY REBUYS.xlsx",
            )

            if uploaded is not None:
                workbook_path, workbook_name = save_uploaded_file(uploaded)
            elif local_path and Path(local_path).exists():
                workbook_path = local_path
                workbook_name = Path(local_path).name

    if not workbook_path:
        if history_review_mode:
            st.error("Could not find the saved meeting workbook.")
        else:
            st.info("Upload the Excel workbook or enter a local file path to begin.")
            with st.expander("First-time setup reminder"):
                st.markdown(
                    """
                    1. Open Terminal / Command Prompt in this app folder.
                    2. Run `pip install -r requirements.txt`.
                    3. Run `streamlit run app.py`.
                    4. Upload your bi-weekly Excel workbook.
                    """
                )
        return

    try:
        data = cached_load_workbook(workbook_path, workbook_name or Path(workbook_path).name, os.path.getmtime(workbook_path))
    except Exception as exc:
        st.error(f"Could not read workbook: {exc}")
        st.stop()

    with st.sidebar:
        if history_review_mode:
            st.header("Workbook")
            st.success("Workbook loaded")
            st.caption(f"Excel file: {workbook_name}")
            if history_meeting_name:
                st.caption(f"Saved meeting: {history_meeting_name}")
        else:
            st.success("Workbook loaded")

        st.caption(f"Meeting date: {data.meeting_date}")
        st.caption(f"Rows in REBUYS: {len(data.rebuys):,}")

        st.divider()
        st.header("2) Filter SKUs")
        planner_sel = st.multiselect("Planner", filter_options(data.rebuys, "planner"))
        category_sel = st.multiselect("Category", filter_options(data.rebuys, "category"))
        profile_sel = st.multiselect("Profile #", filter_options(data.rebuys, "profile"))
        abc_sel = st.multiselect("ABC Tier", filter_options(data.rebuys, "abc"))
        search = st.text_input("Search FSC or product name")

        st.divider()
        st.header("Privacy")
        st.caption("This app runs locally. Uploaded files are saved only under this app's local app_data/uploads folder.")

    filtered = apply_filters(
        data.rebuys,
        {"planner": planner_sel, "category": category_sel, "profile": profile_sel, "abc": abc_sel},
        search,
    )

    if filtered.empty:
        st.warning("No SKU rows match the current filters/search.")
        return

    if "current_pos" not in st.session_state:
        st.session_state["current_pos"] = 0
    st.session_state["current_pos"] = max(0, min(st.session_state["current_pos"], len(filtered) - 1))

    labels_for_select = [make_sku_label(filtered.iloc[i]) for i in range(len(filtered))]
    nav_left, nav_mid, nav_right = st.columns([1, 4, 1])
    if nav_left.button("⬅ Prev.", use_container_width=True, disabled=st.session_state["current_pos"] <= 0):
        st.session_state["current_pos"] -= 1
        st.rerun()

    selected_label = nav_mid.selectbox(
        "Jump to SKU",
        labels_for_select,
        index=st.session_state["current_pos"],
        label_visibility="collapsed",
    )
    selected_pos = labels_for_select.index(selected_label)
    if selected_pos != st.session_state["current_pos"]:
        st.session_state["current_pos"] = selected_pos
        st.rerun()

    if nav_right.button("Next ➡", use_container_width=True, disabled=st.session_state["current_pos"] >= len(filtered) - 1):
        st.session_state["current_pos"] += 1
        st.rerun()

    row = filtered.iloc[st.session_state["current_pos"]]

    fsc = clean_text(row_field(row, "fsc"))
    full_description = clean_text(row_field(row, "full_description"))
    description = clean_text(row_field(row, "description"))

    product_name = full_description or description

    st.header(f"{fsc} — {product_name}")

    if description:
        st.markdown(
            f"""
            <div style="
                font-size: 1.50rem;
                color: #666666;
                margin-top: -0.65rem;
                margin-bottom: 1rem;
            ">
                {description}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Useful flags for fast review.
    flags = []
    try:
        if float(row_field(row, "total_na_inv", 0) or 0) < 0:
            flags.append("Negative total NA inventory")
    except Exception:
        pass
    if data.cm_map.get(fsc):
        flags.append("CM SKU relationship detected")
    if actual_sales_long(data, fsc).empty:
        flags.append("No ACTUAL SALES match")
    if flags:
        st.warning("Review flags: " + " | ".join(flags))

    render_metric_cards(row)
    st.divider()
    render_section_tables(row)
    st.divider()
    render_demand(data, row)
    st.divider()
    render_comments(data, row, store)
    st.divider()
    render_exports(data, store)

    with st.expander("Workbook structure detected"):
        st.write("Sheets detected:")
        sheets_df = pd.DataFrame(
            [{"Sheet": k, "Max Row": v[0], "Max Column": v[1]} for k, v in data.observed_sheets.items()]
        )
        st.dataframe(sheets_df, hide_index=True, use_container_width=True)
        st.write("Future demand campaign headers detected in REBUYS:")
        st.code(", ".join(data.future_campaigns), language=None)
        st.write("ACTUAL SALES campaign headers detected from row 2:")
        st.code(", ".join(data.actual_campaigns), language=None)
        st.caption("To rename display labels, edit config/display_labels.json. The parser still uses the original workbook headers.")

def render_history_review_dashboard_page():
    workbook_path = st.session_state.get("history_review_path")
    workbook_name = st.session_state.get("history_review_workbook_name")
    meeting_name = st.session_state.get("history_review_meeting_name")

    if not workbook_path:
        st.error("No saved meeting was selected.")
        if st.button("Back to Past Meetings"):
            st.session_state["page"] = "history"
            st.rerun()
        return

    if not Path(workbook_path).exists():
        st.error("The saved meeting workbook could not be found.")
        st.caption(f"Expected file path: {workbook_path}")
        if st.button("Back to Past Meetings"):
            st.session_state["page"] = "history"
            st.rerun()
        return

    render_review_dashboard(
        preloaded_workbook_path=workbook_path,
        preloaded_workbook_name=workbook_name,
        history_meeting_name=meeting_name,
    )

# -----------------------------------------------------------------------------
# UI/UX refresh overrides
# The original functions above are kept for reference. These definitions below
# replace the active page rendering with a cleaner two-page product experience.
# -----------------------------------------------------------------------------

def inject_global_css():
    st.markdown(
        """
        <style>
            :root {
                --rebuy-blue: #2563eb;
                --rebuy-blue-dark: #173b8f;
                --rebuy-button: #2563eb;
                --rebuy-button-hover: #1d4ed8;
                --rebuy-button-active: #173b8f;
                --rebuy-button-soft: #eaf2ff;
                --rebuy-bg: #f7f9fc;
                --rebuy-sidebar: #f2f5fa;
                --rebuy-border: #e5eaf2;
                --rebuy-text: #0f172a;
                --rebuy-muted: #64748b;
                --rebuy-green: #16a34a;
                --rebuy-orange: #f59e0b;
                --rebuy-red: #dc2626;

                /* MAIN CONTROLS FOR SKU HEADER ROW */
                --sku-header-height: 12rem;
                --sku-side-button-width: 5.3rem;
                --sku-nav-gap: 0.85rem;
                --sku-row-bottom-gap: 0.2rem;
            }

            .stApp {
                background: var(--rebuy-bg);
            }

            [data-testid="stHeader"],
            header[data-testid="stHeader"],
            .stAppHeader {
                background: var(--rebuy-bg) !important;
                box-shadow: none !important;
                border-bottom: 1px solid var(--rebuy-border) !important;
            }

            [data-testid="stToolbar"] {
                background: var(--rebuy-bg) !important;
            }

            div[data-testid="stVerticalBlockBorderWrapper"] {
                border-radius: 18px !important;
                border-color: var(--rebuy-border) !important;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.035) !important;
                background: #ffffff !important;
            }

            [data-testid="stSidebar"] {
                background: var(--rebuy-sidebar) !important;
                border-right: 1px solid var(--rebuy-border);
            }

            [data-testid="stSidebar"] > div {
                background: var(--rebuy-sidebar) !important;
            }

            .block-container {
                padding-top: 2.0rem !important;
                padding-left: 2.2rem !important;
                padding-right: 2.2rem !important;
                max-width: 1500px;
            }

            h1, h2, h3 {
                color: var(--rebuy-text);
                letter-spacing: -0.02em;
            }

            div[data-testid="stMetric"] {
                background: #ffffff;
                border: 1px solid var(--rebuy-border);
                border-radius: 16px;
                padding: 1rem 1.1rem;
                box-shadow: 0 8px 20px rgba(15, 23, 42, 0.035);
            }

            div[data-testid="stMetricLabel"] p {
                color: var(--rebuy-muted) !important;
                font-size: 0.82rem !important;
            }

            div[data-testid="stMetricValue"] {
                color: var(--rebuy-text) !important;
                font-size: 1.55rem !important;
                font-weight: 750 !important;
            }

            .st-key-rebuy_brand {
                margin: 0.6rem 0 1.2rem 0;
            }

            .rebuy-brand-lockup {
                display: inline-flex;
                align-items: center;
                gap: 0.7rem;
                padding: 0.55rem 0.8rem 0.55rem 0.55rem;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 0.85rem;
                box-shadow: 0 8px 18px rgba(37, 99, 235, 0.18);
            }

            .rebuy-brand-mark {
                position: relative;
                display: grid;
                place-items: center;
                width: 2.35rem;
                height: 2.35rem;
                flex: 0 0 2.35rem;
                overflow: hidden;
                border-radius: 0.7rem;
                color: #ffffff;
                background: linear-gradient(145deg, #111827 10%, #1d4ed8 100%);
                font-size: 1.22rem;
                font-weight: 850;
                letter-spacing: -0.08em;
                box-shadow: 0 5px 12px rgba(29, 78, 216, 0.28);
            }

            .rebuy-brand-mark::after {
                content: "";
                position: absolute;
                right: -0.15rem;
                bottom: -0.15rem;
                width: 0.9rem;
                height: 0.9rem;
                border: 0.18rem solid rgba(255, 255, 255, 0.72);
                border-radius: 50%;
            }

            .rebuy-brand-copy {
                display: flex;
                flex-direction: column;
                line-height: 1;
            }

            .rebuy-brand-copy strong {
                color: #111827;
                font-size: 0.9rem;
                font-weight: 850;
                letter-spacing: 0.09em;
            }

            .rebuy-brand-copy span {
                margin-top: 0.28rem;
                color: #64748b;
                font-size: 0.56rem;
                font-weight: 750;
                letter-spacing: 0.09em;
            }

            .sidebar-section-label {
                color: var(--rebuy-muted);
                font-size: 0.72rem;
                font-weight: 800;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                margin-top: 1.2rem;
                margin-bottom: 0.45rem;
            }

            /* SKU HEADER CARD */
            .sku-card {
                background: white;
                border: 1px solid var(--rebuy-border);
                border-radius: 18px;
                padding: 1.35rem 1.5rem;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04);
                margin: 0 0 1.1rem 0;
                display: grid;
                grid-template-columns: minmax(0, 1.35fr) minmax(560px, 1.65fr);
                gap: 1rem;
                align-items: center;
                height: var(--sku-header-height);
                min-height: var(--sku-header-height);
                box-sizing: border-box;
                overflow: hidden;
                overflow-wrap: anywhere;
            }

            .sku-left {
                display: flex;
                flex-direction: column;
                align-items: stretch;
                min-width: 0;
            }

            .sku-identity-row {
                display: grid;
                grid-template-columns: 76px 1fr;
                gap: 1rem;
                align-items: center;
                min-width: 0;
            }

            .sku-icon {
                width: 72px;
                height: 72px;
                border-radius: 18px;
                background: #eef5ff;
                border: 1px solid #dbeafe;
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--rebuy-blue);
                font-weight: 900;
                font-size: 1.65rem;
            }

            .sku-icon.tier-a {
                background: #dcfce7;
                border-color: #86efac;
                color: #15803d;
            }

            .sku-icon.tier-b {
                background: #fef9c3;
                border-color: #fde047;
                color: #a16207;
            }

            .sku-icon.tier-c {
                background: #fee2e2;
                border-color: #fca5a5;
                color: #dc2626;
            }

            .sku-icon.tier-k {
                background: #fce7f3;
                border-color: #f9a8d4;
                color: #be185d;
            }

            .sku-icon.tier-na {
                background: #111827;
                border-color: #111827;
                color: #ffffff;
                font-size: 1.15rem;
            }

            .sku-label {
                color: var(--rebuy-muted);
                font-size: 0.74rem;
                text-transform: uppercase;
                letter-spacing: 0.06em;
                font-weight: 800;
            }

            .sku-fsc {
                color: var(--rebuy-text);
                font-weight: 850;
                font-size: 1.45rem;
                margin-top: 0.1rem;
            }

            .sku-title {
                color: var(--rebuy-text);
                font-weight: 700;
                font-size: 1.02rem;
                margin-top: 0.1rem;
                line-height: 1.25;
            }

            .sku-subtitle {
                color: var(--rebuy-muted);
                font-size: 0.9rem;
                margin-top: 0.15rem;
                line-height: 1.25;
            }

            .sku-planner-comment {
                color: var(--rebuy-blue);
                font-size: 0.82rem;
                margin-top: 0.75rem;
                line-height: 1.25;
                font-weight: 400;
                display: -webkit-box;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 2;
                overflow: hidden;
            }

            .sku-planner-comment em {
                color: inherit;
                font-weight: 400;
            }

            .sku-meta-grid {
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                gap: 0.75rem;
                min-width: 0;
            }

            .sku-meta-label {
                color: var(--rebuy-muted);
                font-size: 0.75rem;
                font-weight: 700;
            }

            .sku-meta-value {
                color: var(--rebuy-text);
                font-size: 0.95rem;
                font-weight: 750;
                margin-top: 0.2rem;
                line-height: 1.35;
                overflow-wrap: anywhere;
            }

            /*
            SKU NAVIGATION ROW
            This controls the Prev button + SKU card + Next button row.
            */
            .st-key-sku_nav_row {
                margin-bottom: var(--sku-row-bottom-gap) !important;
            }

            .st-key-sku_nav_row [data-testid="stHorizontalBlock"] {
                align-items: stretch !important;
                gap: var(--sku-nav-gap) !important;
            }

            .st-key-sku_nav_row [data-testid="column"] {
                display: flex !important;
                align-items: stretch !important;
            }

            .st-key-sku_nav_row [data-testid="column"] > div {
                width: 100% !important;
                height: var(--sku-header-height) !important;
            }

            .st-key-sku_nav_row .sku-card {
                margin: 0 !important;
                height: var(--sku-header-height) !important;
                min-height: var(--sku-header-height) !important;
            }

            .st-key-sku_nav_row div[class*="st-key-top_prev_sku_"],
            .st-key-sku_nav_row div[class*="st-key-top_next_sku_"],
            .st-key-sku_nav_row div[class*="st-key-top_prev_sku_"] div[data-testid="stButton"],
            .st-key-sku_nav_row div[class*="st-key-top_next_sku_"] div[data-testid="stButton"] {
                height: var(--sku-header-height) !important;
                width: var(--sku-side-button-width) !important;
            }

            .st-key-sku_nav_row div[class*="st-key-top_prev_sku_"] button,
            .st-key-sku_nav_row div[class*="st-key-top_next_sku_"] button {
                height: var(--sku-header-height) !important;
                min-height: var(--sku-header-height) !important;
                width: var(--sku-side-button-width) !important;
                min-width: var(--sku-side-button-width) !important;
                padding: 0 0.35rem !important;
                border-radius: 16px !important;
                border: 1px solid var(--rebuy-border) !important;
                background: #ffffff !important;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.035) !important;
                white-space: nowrap !important;
                font-weight: 700 !important;
            }

            .st-key-sku_nav_row div[class*="st-key-top_prev_sku_"] button p,
            .st-key-sku_nav_row div[class*="st-key-top_next_sku_"] button p {
                white-space: nowrap !important;
                font-size: 0.95rem !important;
            }

            .st-key-title_nav_row div[class*="st-key-title_prev_sku_"] button,
            .st-key-title_nav_row div[class*="st-key-title_next_sku_"] button {
                width: 2.15rem !important;
                min-width: 2.15rem !important;
                height: 2.15rem !important;
                min-height: 2.15rem !important;
                padding: 0 !important;
                border-radius: 50% !important;
                border: 1px solid var(--rebuy-border) !important;
                background: #ffffff !important;
                color: var(--rebuy-text) !important;
                box-shadow: 0 4px 12px rgba(15, 23, 42, 0.05) !important;
            }

            .st-key-title_nav_row [data-testid="stHorizontalBlock"] {
                align-items: center !important;
            }

            .st-key-title_nav_row div[class*="st-key-title_prev_sku_"] button p,
            .st-key-title_nav_row div[class*="st-key-title_next_sku_"] button p {
                font-size: 1rem !important;
                font-weight: 800 !important;
                line-height: 1 !important;
            }

            .st-key-title_nav_row h1 {
                margin: 0 !important;
            }

            .st-key-sticky_sku_header {
                position: static;
                background: var(--rebuy-bg);
                padding: 0.15rem 0 0 0;
            }

            [data-testid="stLayoutWrapper"]:has(> .st-key-sticky_sku_header) {
                position: sticky;
                top: 3rem;
                z-index: 50;
                background: var(--rebuy-bg);
            }

            .st-key-filtered_fsc_list {
                background: #ffffff;
                border: 1px solid var(--rebuy-border);
                border-radius: 12px;
                padding: 0.25rem !important;
                overflow-x: auto !important;
                overflow-y: auto !important;
                overscroll-behavior: contain;
                counter-reset: matching-fsc-row;
            }

            .st-key-filtered_fsc_list [data-testid="stVerticalBlock"] {
                gap: 0 !important;
            }

            .st-key-filtered_fsc_list,
            .st-key-filtered_fsc_list > div,
            .st-key-filtered_fsc_list div[data-testid="stVerticalBlock"],
            .st-key-filtered_fsc_list div[data-testid="stVerticalBlockBorderWrapper"],
            .st-key-filtered_fsc_list div[class*="stVerticalBlock"] {
                row-gap: 0 !important;
                gap: 0 !important;
            }

            .st-key-filtered_fsc_list div {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"],
            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] div[data-testid="stButton"] {
                height: 1.75rem !important;
                min-height: 1.75rem !important;
                width: max-content !important;
                min-width: 100% !important;
                margin: 0 !important;
                padding: 0 !important;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] {
                counter-increment: matching-fsc-row;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] button {
                min-height: 1.75rem !important;
                height: 1.75rem !important;
                padding: 0 !important;
                border: 0 !important;
                border-bottom: 1px solid var(--rebuy-border) !important;
                border-radius: 0 !important;
                background: transparent !important;
                color: var(--rebuy-text) !important;
                width: max-content !important;
                min-width: 100% !important;
                justify-content: flex-start !important;
                text-align: left !important;
                box-shadow: none !important;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] button::before {
                content: counter(matching-fsc-row);
                display: inline-flex;
                align-items: center;
                justify-content: center;
                align-self: stretch;
                flex: 0 0 1.8rem;
                width: 1.8rem;
                min-width: 1.8rem;
                color: var(--rebuy-muted);
                background: #f8fafc;
                border-right: 1px solid var(--rebuy-border);
                font-size: 0.72rem;
                font-weight: 500;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] button:hover {
                background: #edf3fb !important;
                color: var(--rebuy-text) !important;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] button[kind="primary"] {
                background: #e4edfb !important;
                border-left: 3px solid var(--rebuy-blue) !important;
                color: var(--rebuy-blue-dark) !important;
            }

            .st-key-filtered_fsc_list div[class*="st-key-sidebar_sku_pick_"] button p {
                width: max-content !important;
                white-space: nowrap !important;
                overflow: visible !important;
                text-overflow: clip !important;
                text-align: left !important;
                font-size: 0.72rem !important;
                line-height: 1.1 !important;
                font-weight: 500 !important;
                padding: 0 0.35rem !important;
            }

            .metric-card {
                background: white;
                border: 1px solid var(--rebuy-border);
                border-radius: 16px;
                padding: 0.95rem 1rem;
                min-height: 96px;
                box-shadow: 0 8px 22px rgba(15, 23, 42, 0.035);
            }

            .metric-label {
                color: var(--rebuy-muted);
                font-size: 0.78rem;
                font-weight: 750;
                margin-bottom: 0.25rem;
            }

            .metric-value {
                color: var(--rebuy-text);
                font-size: 1.35rem;
                line-height: 1.15;
                font-weight: 850;
            }

            .metric-sub {
                color: var(--rebuy-muted);
                font-size: 0.75rem;
                margin-top: 0.2rem;
            }

            .inventory-tree {
                overflow: hidden;
                border: 1px solid #dbeafe;
                border-radius: 14px;
                background: #ffffff;
            }

            .inventory-tree-total {
                display: flex;
                min-height: 72px;
                align-items: center;
                justify-content: space-between;
                gap: 1rem;
                padding: 0.85rem 1rem;
                background: #eff6ff;
                border-bottom: 1px solid #bfdbfe;
            }

            .inventory-tree-total-label {
                color: var(--rebuy-blue-dark);
                font-size: 0.9rem;
                font-weight: 850;
            }

            .inventory-tree-total-note {
                margin-top: 0.15rem;
                color: #64748b;
                font-size: 0.7rem;
            }

            .inventory-tree-total-value {
                color: var(--rebuy-blue-dark);
                font-size: 1.6rem;
                font-weight: 850;
                line-height: 1;
                white-space: nowrap;
            }

            .inventory-tree-children {
                position: relative;
                padding-left: 1rem;
                background: #ffffff;
            }

            .inventory-tree-children::before {
                content: "";
                position: absolute;
                top: 0;
                bottom: 0;
                left: 1.35rem;
                width: 1px;
                background: #dbeafe;
            }

            .inventory-tree-row {
                position: relative;
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                min-height: 48px;
                align-items: center;
                gap: 1rem;
                margin-left: 0.8rem;
                padding: 0.55rem 1rem 0.55rem 1.05rem;
                border-bottom: 1px solid #eef2f7;
            }

            .inventory-tree-row:last-child {
                border-bottom: 0;
            }

            .inventory-tree-name {
                display: flex;
                min-width: 0;
                align-items: baseline;
                gap: 0.45rem;
                color: var(--rebuy-text);
                font-size: 0.8rem;
                font-weight: 750;
            }

            .inventory-tree-name small {
                color: var(--rebuy-muted);
                font-size: 0.67rem;
                font-weight: 500;
            }

            .inventory-tree-branch {
                color: #60a5fa;
                font-size: 0.85rem;
                font-weight: 800;
            }

            .inventory-tree-value {
                color: var(--rebuy-text);
                font-size: 0.95rem;
                font-weight: 850;
                text-align: right;
                white-space: nowrap;
            }

            .inventory-status-layout {
                display: grid;
                grid-template-columns: minmax(340px, 1.4fr) minmax(190px, 0.75fr) minmax(330px, 1.2fr);
                align-items: start;
                gap: 0.75rem;
            }

            .inventory-secondary-tree {
                align-self: start;
            }

            .inventory-tree-context {
                min-height: 58px;
                display: flex;
                align-items: center;
                padding: 0.75rem 1rem;
                color: var(--rebuy-muted);
                font-size: 0.7rem;
                line-height: 1.35;
                background: #ffffff;
            }

            .inventory-tree-total-unit {
                color: #64748b;
                font-size: 0.68rem;
                font-weight: 650;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] {
                overflow: hidden;
                background: #ffffff !important;
                border: 1px solid var(--rebuy-border) !important;
                border-radius: 18px !important;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04) !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] details,
            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpanderDetails"] {
                background: #ffffff !important;
                border: 0 !important;
                outline: 0 !important;
                box-shadow: none !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] > details {
                overflow: hidden !important;
                background: #ffffff !important;
                border: 0 !important;
                border-radius: inherit !important;
                outline: 0 !important;
                box-shadow: none !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] summary {
                min-height: 66px;
                background: #ffffff !important;
                color: var(--rebuy-text) !important;
                border: 0 !important;
                outline: 0 !important;
                box-shadow: none !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] details[open] summary {
                border: 0 !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] summary p {
                color: var(--rebuy-text) !important;
                font-size: 1.35rem !important;
                line-height: 1.15 !important;
                font-weight: 850 !important;
            }

            :is(
                .st-key-demand_view_panel,
                .st-key-past_actual_detail_panel,
                .st-key-future_demand_detail_panel,
                .st-key-demand_inventory_summary_panel
            ) [data-testid="stExpander"] summary svg {
                color: var(--rebuy-muted) !important;
                fill: var(--rebuy-muted) !important;
            }

            .st-key-demand_view_panel [data-testid="stVegaLiteChart"],
            .st-key-demand_view_panel [data-testid="stVegaLiteChart"] > div {
                background: transparent !important;
                border: 0 !important;
                box-shadow: none !important;
            }

            .demand-summary-metrics {
                display: grid;
                gap: 0.5rem;
            }

            .demand-summary-metric {
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                align-items: center;
                gap: 0.2rem 0.75rem;
                padding: 0.6rem 0.7rem;
                background: #f8fafc;
                border-radius: 10px;
            }

            .demand-summary-metric span,
            .demand-summary-row span {
                min-width: 0;
                color: var(--rebuy-text);
                font-size: 0.9rem;
                font-weight: 700;
            }

            .demand-summary-metric strong,
            .demand-summary-row strong {
                color: var(--rebuy-text);
                font-size: 1.15rem;
                font-weight: 850;
                text-align: right;
                white-space: nowrap;
            }

            .demand-summary-metric small {
                grid-column: 1 / -1;
                color: var(--rebuy-muted);
                font-size: 0.74rem;
            }

            .demand-summary-section {
                margin-top: 0.65rem;
                overflow: hidden;
                border: 1px solid var(--rebuy-border);
                border-radius: 10px;
            }

            .demand-summary-row {
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                align-items: center;
                gap: 0.75rem;
                min-height: 42px;
                padding: 0.52rem 0.75rem;
                border-bottom: 1px solid #eef2f7;
            }

            .demand-summary-row:last-child {
                border-bottom: 0;
            }

            .demand-summary-emphasis {
                background: #f8fafc;
            }

            .demand-summary-child {
                padding-left: 1rem;
            }

            .demand-summary-child span {
                color: var(--rebuy-muted);
                font-weight: 600;
            }

            .promo-strip {
                display: flex;
                flex-wrap: wrap;
                gap: 0.55rem;
                margin-top: 0.65rem;
                margin-bottom: 0.25rem;
            }

            .promo-pill {
                border-radius: 999px;
                padding: 0.35rem 0.7rem;
                background: #eef6ff;
                color: #1d4ed8;
                border: 1px solid #bfdbfe;
                font-size: 0.8rem;
                font-weight: 700;
            }

            .soft-card {
                background: white;
                border: 1px solid var(--rebuy-border);
                border-radius: 18px;
                padding: 1rem;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.035);
            }

            .history-table-header {
                font-size: 0.78rem;
                font-weight: 800;
                color: var(--rebuy-muted);
                border-bottom: 1px solid var(--rebuy-border);
                padding-bottom: 0.5rem;
                margin-bottom: 0.25rem;
                white-space: nowrap;
            }

            .history-row-cell {
                font-size: 0.88rem;
                padding: 0.28rem 0;
                color: var(--rebuy-text);
                white-space: nowrap;
            }

            div[class*="st-key-open_history_review_"] button {
                border: none !important;
                background: transparent !important;
                color: var(--rebuy-blue) !important;
                padding: 0 !important;
                min-height: 1.55rem !important;
                height: 1.55rem !important;
                text-align: left !important;
                box-shadow: none !important;
                font-weight: 750 !important;
            }

            div[class*="st-key-open_history_review_"] button:hover {
                text-decoration: underline !important;
                color: var(--rebuy-blue-dark) !important;
            }

            div[class*="st-key-open_history_review_"] button p {
                text-align: left !important;
                font-size: 0.88rem !important;
            }

            /* GLOBAL BUTTON COLOR OVERRIDES */
            div[data-testid="stButton"] button[kind="primary"] {
                background-color: var(--rebuy-button) !important;
                border-color: var(--rebuy-button) !important;
                color: white !important;
            }

            div[data-testid="stButton"] button[kind="primary"]:hover {
                background-color: var(--rebuy-button-hover) !important;
                border-color: var(--rebuy-button-hover) !important;
                color: white !important;
            }

            div[data-testid="stButton"] button[kind="primary"]:active,
            div[data-testid="stButton"] button[kind="primary"]:focus {
                background-color: var(--rebuy-button-active) !important;
                border-color: var(--rebuy-button-active) !important;
                color: white !important;
                box-shadow: 0 0 0 0.18rem rgba(37, 99, 235, 0.20) !important;
            }

            /* SECONDARY BUTTON CLICK / FOCUS COLOR OVERRIDE */
            div[data-testid="stButton"] button[kind="secondary"]:active,
            div[data-testid="stButton"] button[kind="secondary"]:focus {
                border-color: var(--rebuy-button) !important;
                color: var(--rebuy-button) !important;
                background-color: var(--rebuy-button-soft) !important;
                box-shadow: 0 0 0 0.18rem rgba(37, 99, 235, 0.14) !important;
            }

            /* SIDEBAR NAV BUTTONS */
            [data-testid="stSidebar"] div[data-testid="stButton"] button:hover {
                border-color: var(--rebuy-button) !important;
                color: var(--rebuy-button) !important;
                background-color: var(--rebuy-button-soft) !important;
            }

            [data-testid="stSidebar"] div[data-testid="stButton"] button:active,
            [data-testid="stSidebar"] div[data-testid="stButton"] button:focus {
                border-color: var(--rebuy-button-active) !important;
                color: var(--rebuy-button-active) !important;
                background-color: var(--rebuy-button-soft) !important;
                box-shadow: 0 0 0 0.16rem rgba(37, 99, 235, 0.14) !important;
            }

            @media (max-width: 1100px) {
                .sku-card {
                    grid-template-columns: 1fr;
                    height: auto;
                    min-height: var(--sku-header-height);
                }

                .sku-meta-grid {
                    grid-template-columns: repeat(2, 1fr);
                }

                .inventory-status-layout {
                    grid-template-columns: 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

def render_sidebar_brand():
    with st.container(key="rebuy_brand"):
        st.markdown(
            """
            <div class="rebuy-brand-lockup" aria-label="Rebuy Review Platform">
                <div class="rebuy-brand-mark" aria-hidden="true">R</div>
                <div class="rebuy-brand-copy">
                    <strong>REBUY</strong>
                    <span>REVIEW PLATFORM</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_sidebar_nav(active_page: str):
    render_sidebar_brand()
    if st.button(
        "📈  Review Dashboard",
        key=f"nav_review_{active_page}",
        use_container_width=True,
        type="primary" if active_page == "review" else "secondary",
    ):
        st.session_state["page"] = "review"
        st.rerun()
    if st.button(
        "🗓️  Past Decisions",
        key=f"nav_history_{active_page}",
        use_container_width=True,
        type="primary" if active_page == "history" else "secondary",
    ):
        st.session_state["page"] = "history"
        st.rerun()
    st.divider()


def workbook_alerts(data: WorkbookData) -> list[str]:
    alerts: list[str] = []
    if data.rebuys.empty:
        return ["No REBUYS rows were detected."]

    decision_col = find_col(data.rebuys, "rebuy_decision")
    if decision_col:
        normalized = data.rebuys[decision_col].apply(normalize_rebuy_decision)
        invalid_count = int((normalized == "").sum())
        if invalid_count:
            alerts.append(f"{invalid_count:,} row(s) have blank or unusual rebuy decisions.")

    fsc_col = find_col(data.rebuys, "fsc")
    if fsc_col and not data.actual_sales.empty:
        missing = 0
        for fsc in data.rebuys[fsc_col].astype(str).tolist():
            if actual_sales_long(data, fsc).empty:
                missing += 1
        if missing:
            alerts.append(f"{missing:,} SKU(s) have no matching ACTUAL SALES row.")

    inv_col = find_col(data.rebuys, "total_na_inv")
    if inv_col:
        inv = pd.to_numeric(data.rebuys[inv_col], errors="coerce")
        neg = int((inv < 0).sum())
        if neg:
            alerts.append(f"{neg:,} SKU(s) have negative total NA inventory.")

    return alerts


def render_alert_popover(alerts: list[str]):
    label = f"🔔 Alerts ({len(alerts)})" if alerts else "🔔 Alerts"
    with st.popover(label, use_container_width=True):
        if not alerts:
            st.success("No workbook-level alerts detected.")
        else:
            for alert in alerts:
                st.warning(alert)


def render_kpi_strip(items: list[tuple[str, str, str]]):
    cols = st.columns(len(items))
    for col, (label_text, value_text, sub_text) in zip(cols, items):
        with col:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{escape(label_text)}</div>
                    <div class="metric-value">{escape(value_text)}</div>
                    <div class="metric-sub">{escape(sub_text)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_metric_cards(row: pd.Series):
    coverage_count = fmt_num(row_field(row, "coverage_camps"))
    coverage_campaign = fmt_campaign(row_field(row, "coverage_campaign"))
    coverage_value = coverage_count
    if coverage_campaign != "—":
        coverage_value = f"{coverage_count} (~{coverage_campaign})"
    safety_stock_units = fmt_num(row_field(row, "safety_stock"))
    if safety_stock_units == "—":
        safety_stock_units = "0"
    safety_stock_text = f"SS = {safety_stock_units} units"

    items = [
        ("MOQ", fmt_num(row_field(row, "moq")), "Units"),
        ("Rebuy Qty", fmt_num(row_field(row, "rebuy_qty")), "Units"),
        (
            "Rebuy $",
            fmt_dollar(row_field(row, "rebuy_dollars")),
            f"Std Cost = {fmt_dollar(row_field(row, 'std_cost'), decimals=2)}",
        ),
        ("Campaign Coverage", coverage_value, "Campaigns"),
        ("Stockout C with SS", fmt_campaign(row_field(row, "stockout_with_ss")), safety_stock_text),
        ("Stockout C without SS", fmt_campaign(row_field(row, "stockout_no_ss")), safety_stock_text),
        ("Fill-by Campaign", fmt_campaign(row_field(row, "fill_by_campaign")), "Campaign"),
    ]
    render_kpi_strip(items)


def render_sku_header_card(row: pd.Series):
    fsc = clean_text(row_field(row, "fsc"))
    abc_tier = clean_text(row_field(row, "abc")).upper()
    if abc_tier not in {"A", "B", "C", "K"}:
        abc_tier = "N/A"
    tier_class = abc_tier.lower() if abc_tier != "N/A" else "na"
    full_description = clean_text(row_field(row, "full_description"))
    description = clean_text(row_field(row, "description"))
    planner_comment = clean_text(row_field(row, "planner_comments")) or "—"
    first_desc = full_description or description or "—"
    second_desc = description if description and description != first_desc else ""

    meta = [
        ("Category", clean_text(row_field(row, "category")) or "—"),
        ("Planner", clean_text(row_field(row, "planner")) or "—"),
        ("Supplier", clean_text(row_field(row, "supplier")) or "—"),
        ("Country of Origin", clean_text(row_field(row, "coo")) or "—"),
        ("Campaign of Introduction", fmt_campaign(row_field(row, "coi"))),
    ]

    meta_html = "".join(
        f"""
        <div>
            <div class="sku-meta-label">{escape(k)}</div>
            <div class="sku-meta-value">{escape(v)}</div>
        </div>
        """
        for k, v in meta
    )
    st.markdown(
        f"""
        <div class="sku-card">
            <div class="sku-left">
                <div class="sku-identity-row">
                    <div class="sku-icon tier-{tier_class}">{escape(abc_tier)}</div>
                    <div>
                        <div class="sku-fsc">{escape(fsc or "—")}</div>
                        <div class="sku-title">{escape(first_desc)}</div>
                        <div class="sku-subtitle">{escape(second_desc)}</div>
                    </div>
                </div>
                <div class="sku-planner-comment">Planner: <em>“{escape(planner_comment)}”</em></div>
            </div>
            <div class="sku-meta-grid">{meta_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_demand(data: WorkbookData, row: pd.Series):
    future_df = future_demand_long(data, row)
    fsc = clean_text(row_field(row, "fsc"))
    actual_df = actual_sales_long(data, fsc, include_related=True)

    related = data.cm_map.get(fsc, [])
    if related:
        st.info(f"CM SKU link detected. Actual sales include related FSC(s): {', '.join(related)}")

    actual_campaigns = sorted(
        [str(c) for c in data.actual_campaigns if str(c).strip()],
        key=lambda x: pd.to_numeric(x, errors="coerce"),
    )
    future_campaigns = sorted(
        [str(c) for c in data.future_campaigns if str(c).strip()],
        key=lambda x: pd.to_numeric(x, errors="coerce"),
    )

    past_axis = actual_campaigns[-26:]
    future_axis = future_campaigns[:26]
    full_campaign_axis: list[str] = []
    for camp in past_axis + future_axis:
        if camp not in full_campaign_axis:
            full_campaign_axis.append(camp)

    if not full_campaign_axis:
        with st.container(key="demand_view_panel"):
            with st.expander("Demand View", expanded=True):
                st.warning("No campaign columns were detected for actual sales or future demand.")
        return

    calendar_lookup = getattr(data, "calendar_map", {}) or {}
    axis_df = pd.DataFrame({"Campaign": full_campaign_axis})
    axis_df["Campaign Sort"] = axis_df["Campaign"].apply(lambda x: pd.to_numeric(x, errors="coerce"))
    axis_df["US Start Date"] = axis_df["Campaign"].map(calendar_lookup).fillna("")

    if actual_df.empty:
        actual_summary = pd.DataFrame(columns=["Campaign", "Quantity"])
    else:
        actual_summary = (
            actual_df.groupby("Campaign", as_index=False)["Sales Qty"]
            .sum()
            .rename(columns={"Sales Qty": "Quantity"})
        )
    actual_summary["Campaign"] = actual_summary["Campaign"].astype(str)

    actual_chart = axis_df.merge(actual_summary, on="Campaign", how="left")
    actual_chart["Type"] = "Past Actual Sales"
    coi_campaign = fmt_campaign(row_field(row, "coi"))
    if coi_campaign in past_axis:
        actual_chart.loc[actual_chart["Campaign"] == coi_campaign, "Type"] = "COI"

    if future_df.empty:
        future_summary = pd.DataFrame(columns=["Campaign", "Quantity"])
    else:
        future_summary = future_df[["Campaign", "Future Demand"]].copy().rename(columns={"Future Demand": "Quantity"})
    future_summary["Campaign"] = future_summary["Campaign"].astype(str)

    future_chart = axis_df.merge(future_summary, on="Campaign", how="left")
    future_chart["Type"] = "Future Demand"

    combined = pd.concat([actual_chart, future_chart], ignore_index=True)
    combined["Quantity"] = pd.to_numeric(combined["Quantity"], errors="coerce")
    combined.loc[(combined["Quantity"].isna()) | (combined["Quantity"] == 0), "Quantity"] = None

    chart = (
        alt.Chart(combined)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            x=alt.X(
                "Campaign:N",
                sort=full_campaign_axis,
                title="Campaign",
                axis=alt.Axis(labelAngle=-45, labelFontSize=10),
                scale=alt.Scale(domain=full_campaign_axis),
            ),
            y=alt.Y("Quantity:Q", title="Units"),
            color=alt.Color(
                "Type:N",
                scale=alt.Scale(
                    domain=["Past Actual Sales", "Future Demand", "COI"],
                    range=["#2563eb", "#16a34a", "#dc2626"],
                ),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[
                alt.Tooltip("Campaign:N", title="Campaign"),
                alt.Tooltip("US Start Date:N", title="US Start Date"),
                alt.Tooltip("Type:N", title="Type"),
                alt.Tooltip("Quantity:Q", title="Units", format=",.0f"),
            ],
        )
        .properties(height=330, background="transparent")
        .configure_view(stroke=None)
        .configure_axis(gridColor="#edf2f7", domainColor="#cbd5e1")
    )

    with st.container(key="demand_view_panel"):
        with st.expander("Demand View", expanded=True):
            st.altair_chart(chart, use_container_width=True)

    displayed_actual_values = pd.to_numeric(
        actual_chart.loc[actual_chart["Campaign"].isin(past_axis), "Quantity"],
        errors="coerce",
    ).fillna(0)
    displayed_future_values = pd.to_numeric(
        future_chart.loc[future_chart["Campaign"].isin(future_axis), "Quantity"],
        errors="coerce",
    ).fillna(0)
    qualifying_actual_values = displayed_actual_values[displayed_actual_values > 1]
    qualifying_future_values = displayed_future_values[displayed_future_values > 1]

    total_past_actual_sales = float(displayed_actual_values.sum())
    average_actual_sales = (
        float(qualifying_actual_values.mean())
        if not qualifying_actual_values.empty
        else 0.0
    )
    average_future_demand = (
        float(qualifying_future_values.mean())
        if not qualifying_future_values.empty
        else 0.0
    )

    actual_col, future_col, summary_col = st.columns(3)
    with actual_col:
        with st.container(key="past_actual_detail_panel"):
            with st.expander("Past Actual Sales Detail", expanded=False):
                if actual_df.empty:
                    st.warning("No matching ACTUAL SALES row was found for this FSC or its CM-related FSCs.")
                else:
                    actual_table = actual_df.copy()
                    actual_table["Sales Qty"] = pd.to_numeric(actual_table["Sales Qty"], errors="coerce").fillna(0)
                    actual_table = actual_table.groupby("Campaign", as_index=False)["Sales Qty"].sum()
                    actual_table["Campaign"] = actual_table["Campaign"].astype(str)
                    actual_table["Campaign Sort"] = actual_table["Campaign"].apply(lambda x: pd.to_numeric(x, errors="coerce"))
                    actual_table = actual_table.sort_values("Campaign Sort")[["Campaign", "Sales Qty"]]
                    st.dataframe(
                        actual_table,
                        hide_index=True,
                        use_container_width=True,
                        column_config={"Sales Qty": st.column_config.NumberColumn("Sales Qty", format="%d")},
                    )

    with future_col:
        with st.container(key="future_demand_detail_panel"):
            with st.expander("Future Demand Detail", expanded=False):
                if future_df.empty:
                    st.info("No future campaign demand columns were detected in REBUYS.")
                else:
                    future_table = future_df.copy()
                    future_table["Future Demand"] = pd.to_numeric(future_table["Future Demand"], errors="coerce").fillna(0)
                    future_table["Campaign"] = future_table["Campaign"].astype(str)
                    future_table["Campaign Sort"] = future_table["Campaign"].apply(lambda x: pd.to_numeric(x, errors="coerce"))
                    future_table = future_table.sort_values("Campaign Sort")[["Campaign", "Future Demand"]]
                    st.dataframe(
                        future_table,
                        hide_index=True,
                        use_container_width=True,
                        column_config={"Future Demand": st.column_config.NumberColumn("Future Demand", format="%d")},
                    )

    with summary_col:
        with st.container(key="demand_inventory_summary_panel"):
            with st.expander("Demand & Inventory Summary", expanded=False):
                summary_html = f"""
                    <div class="demand-summary-metrics">
                        <div class="demand-summary-metric">
                            <span>Total Past Actual Sales</span>
                            <strong>{escape(fmt_num(total_past_actual_sales))}</strong>
                            <small>Displayed past campaigns</small>
                        </div>
                        <div class="demand-summary-metric">
                            <span>Avg. Actual / Campaign</span>
                            <strong>{escape(fmt_num(average_actual_sales, 1))}</strong>
                            <small>{len(qualifying_actual_values)} campaign(s) over 1 unit</small>
                        </div>
                        <div class="demand-summary-metric">
                            <span>Avg. Future Demand / Campaign</span>
                            <strong>{escape(fmt_num(average_future_demand, 1))}</strong>
                            <small>{len(qualifying_future_values)} campaign(s) over 1 unit</small>
                        </div>
                    </div>
                    <div class="demand-summary-section">
                        <div class="demand-summary-row demand-summary-emphasis">
                            <span>Customer Orders</span>
                            <strong>{escape(fmt_num(row_field(row, "cust_orders")))}</strong>
                        </div>
                    </div>
                    <div class="demand-summary-section">
                        <div class="demand-summary-row demand-summary-emphasis">
                            <span>Total NA Inventory</span>
                            <strong>{escape(fmt_num(row_field(row, "total_na_inv")))}</strong>
                        </div>
                        <div class="demand-summary-row demand-summary-child">
                            <span>↳ On Hand / NA OH</span>
                            <strong>{escape(fmt_num(row_field(row, "na_oh")))}</strong>
                        </div>
                        <div class="demand-summary-row demand-summary-child">
                            <span>↳ In Transit / NA AIT</span>
                            <strong>{escape(fmt_num(row_field(row, "na_ait")))}</strong>
                        </div>
                        <div class="demand-summary-row demand-summary-child">
                            <span>↳ POs</span>
                            <strong>{escape(fmt_num(row_field(row, "pos")))}</strong>
                        </div>
                        <div class="demand-summary-row demand-summary-child">
                            <span>↳ BOs</span>
                            <strong>{escape(fmt_num(row_field(row, "bos")))}</strong>
                        </div>
                    </div>
                """
                st.markdown(
                    summary_html.replace("\n", ""),
                    unsafe_allow_html=True,
                )


def render_comments(
    data: WorkbookData,
    row: pd.Series,
    store: CommentStore,
    total_skus: int,
):
    st.subheader("Finance Recommendations")

    save_notice = st.session_state.pop("comment_save_notice", "")
    if save_notice:
        st.success(save_notice)

    row_number = int(row["__row_number"])
    fsc = clean_text(row_field(row, "fsc"))
    profile = clean_text(row_field(row, "profile"))
    product = clean_text(row_field(row, "full_description") or row_field(row, "description"))
    existing_initial = clean_text(row.get(data.finance_comment_header, ""))

    saved_comment = store.get(
        meeting_id=data.meeting_id,
        row_number=row_number,
        fsc=fsc,
        profile=profile,
    )
    default_comment = saved_comment if saved_comment is not None else existing_initial

    comment_key = f"comment_{data.meeting_id}_{row_number}_{fsc}_{profile}"
    clear_flag_key = f"{comment_key}__clear_requested"

    if st.session_state.pop(clear_flag_key, False):
        st.session_state[comment_key] = ""

    if comment_key not in st.session_state:
        st.session_state[comment_key] = default_comment

    comment = st.text_area(
        "Finance comment",
        height=75,
        key=comment_key,
        label_visibility="collapsed",
    )

    c1, c2, c3 = st.columns([1, 1, 1])
    if c1.button("Save Comment", type="primary", use_container_width=True):
        store.save(
            meeting_id=data.meeting_id,
            workbook_name=data.workbook_name,
            workbook_hash=data.workbook_hash,
            row_number=row_number,
            fsc=fsc,
            profile=profile,
            product_name=product,
            finance_comment=comment,
        )
        st.session_state["comment_save_notice"] = f"Comment saved for FSC {fsc or '—'}."
        if st.session_state["current_pos"] < total_skus - 1:
            st.session_state["current_pos"] += 1
            # Let the full-width SKU Jump widget initialize to the new row
            # instead of restoring its previous selection on the rerun.
            st.session_state.pop(f"sku_jump_select_{data.meeting_id}", None)
        st.rerun()

    if c2.button("Clear this comment", use_container_width=True):
        store.delete_one(meeting_id=data.meeting_id, row_number=row_number, fsc=fsc, profile=profile)
        st.session_state[clear_flag_key] = True
        st.rerun()

    if c3.button("Clear all comments", use_container_width=True):
        store.delete_for_meeting(data.meeting_id)
        prefix = f"comment_{data.meeting_id}_"
        for key_name in list(st.session_state.keys()):
            if key_name.startswith(prefix):
                del st.session_state[key_name]
        st.rerun()



def render_review_dashboard(
    preloaded_workbook_path: str | None = None,
    preloaded_workbook_name: str | None = None,
    history_meeting_name: str | None = None,
):
    inject_global_css()
    labels = load_display_labels(LABEL_PATH)
    store = CommentStore(DB_PATH)

    workbook_path = None
    workbook_name = None
    history_review_mode = preloaded_workbook_path is not None

    with st.sidebar:
        render_sidebar_nav("review")
        has_saved_workbook = bool(st.session_state.get("review_active_workbook_path"))
        with st.expander("WORKBOOK", expanded=not has_saved_workbook):
            if history_review_mode:
                workbook_path = preloaded_workbook_path
                workbook_name = preloaded_workbook_name or Path(preloaded_workbook_path).name
                st.success("Workbook loaded")
                st.caption(f"Excel file: {workbook_name}")
                if history_meeting_name:
                    st.caption(f"Saved meeting: {history_meeting_name}")
            else:
                workbook_path = st.session_state.get("review_active_workbook_path")
                workbook_name = st.session_state.get("review_active_workbook_name")
                uploaded = st.file_uploader("Upload rebuy Excel file", type=["xlsx"], key="review_workbook_upload")
                if uploaded is not None:
                    workbook_path, workbook_name = save_uploaded_file(uploaded)
                    st.session_state["review_active_workbook_path"] = workbook_path
                    st.session_state["review_active_workbook_name"] = workbook_name
            workbook_details_placeholder = st.empty()

    if not workbook_path:
        st.title("Rebuy Review Dashboard")
        st.caption("Upload a rebuy workbook in the left panel to start the SKU-by-SKU review workflow.")
        st.info("No workbook is loaded yet.")
        with st.expander("First-time setup reminder"):
            st.markdown(
                """
                1. Open Terminal / Command Prompt in this app folder.
                2. Run `pip install -r requirements.txt`.
                3. Run `streamlit run app.py`.
                4. Upload your bi-weekly Excel workbook.
                """
            )
        return

    try:
        data = cached_load_workbook(
            workbook_path,
            workbook_name or Path(workbook_path).name,
            os.path.getmtime(workbook_path),
        )
    except Exception as exc:
        st.error(f"Could not read workbook: {exc}")
        st.stop()

    alerts = workbook_alerts(data)

    with workbook_details_placeholder.container():
        if not history_review_mode:
            st.success("Workbook loaded")
        st.caption(f"Meeting date: {data.meeting_date}")
        st.caption(f"Rows in REBUYS: {len(data.rebuys):,}")
        render_alert_popover(alerts)

    with st.sidebar:
        st.markdown('<div class="sidebar-section-label">Filters</div>', unsafe_allow_html=True)
        filter_widget_keys = {
            "planner": f"filter_planner_{data.meeting_id}",
            "category": f"filter_category_{data.meeting_id}",
            "profile": f"filter_profile_{data.meeting_id}",
            "abc": f"filter_abc_{data.meeting_id}",
        }
        filter_selection_keys = {
            logical: f"selected_{widget_key}"
            for logical, widget_key in filter_widget_keys.items()
        }

        current_filters = {
            logical: list(
                st.session_state.get(
                    filter_selection_keys[logical],
                    st.session_state.get(widget_key, []),
                )
            )
            for logical, widget_key in filter_widget_keys.items()
        }
        available_options = {
            logical: responsive_filter_options(data.rebuys, logical, current_filters)
            for logical in filter_widget_keys
        }

        for logical, widget_key in filter_widget_keys.items():
            retained = [value for value in current_filters[logical] if value in available_options[logical]]
            current_filters[logical] = retained
            st.session_state[filter_selection_keys[logical]] = retained
            st.session_state[widget_key] = retained

        planner_sel = st.multiselect(
            "Planner",
            available_options["planner"],
            key=filter_widget_keys["planner"],
            on_change=persist_filter_selection,
            args=(filter_widget_keys["planner"], filter_selection_keys["planner"]),
        )
        category_sel = st.multiselect(
            "Category",
            available_options["category"],
            key=filter_widget_keys["category"],
            on_change=persist_filter_selection,
            args=(filter_widget_keys["category"], filter_selection_keys["category"]),
        )
        profile_sel = st.multiselect(
            "Profile #",
            available_options["profile"],
            key=filter_widget_keys["profile"],
            on_change=persist_filter_selection,
            args=(filter_widget_keys["profile"], filter_selection_keys["profile"]),
        )
        abc_sel = st.multiselect(
            "ABC Tier",
            available_options["abc"],
            key=filter_widget_keys["abc"],
            on_change=persist_filter_selection,
            args=(filter_widget_keys["abc"], filter_selection_keys["abc"]),
        )

        active_filters = {
            "planner": planner_sel,
            "category": category_sel,
            "profile": profile_sel,
            "abc": abc_sel,
        }
        filtered = apply_filters(data.rebuys, active_filters, "")

        if "current_pos" not in st.session_state:
            st.session_state["current_pos"] = 0
        if not filtered.empty:
            st.session_state["current_pos"] = max(
                0,
                min(st.session_state["current_pos"], len(filtered) - 1),
            )

        st.markdown('<div class="sidebar-section-label">Matching FSCs</div>', unsafe_allow_html=True)
        if filtered.empty:
            st.caption("No FSCs match the current filters.")
        else:
            with st.container(height=150, border=False, key="filtered_fsc_list"):
                for position in range(len(filtered)):
                    list_row = filtered.iloc[position]
                    product_name = clean_text(row_field(list_row, "description"))
                    if not product_name:
                        product_name = clean_text(row_field(list_row, "full_description"))
                    if not product_name:
                        product_name = clean_text(row_field(list_row, "fsc")) or "Unnamed FSC"
                    row_number = int(list_row["__row_number"])
                    if st.button(
                        product_name,
                        key=f"sidebar_sku_pick_{data.meeting_id}_{row_number}",
                        use_container_width=True,
                        type="primary" if position == st.session_state["current_pos"] else "secondary",
                    ):
                        st.session_state["current_pos"] = position
                        st.rerun()

        st.divider()
        st.markdown('<div class="sidebar-section-label">Privacy</div>', unsafe_allow_html=True)
        st.caption("Files are processed locally in this app unless you deploy it to a shared server.")

    with st.container(key="sticky_sku_header"):
        with st.container(key="title_nav_row"):
            title_text, title_spacer, title_back, title_prev, title_next = st.columns(
                [3.3, 2.8, 1.25, 0.34, 0.34],
                gap="small",
            )
            with title_text:
                st.title("Rebuy Review Dashboard")
            with title_back:
                if history_review_mode:
                    if st.button("← Past Meetings", use_container_width=True):
                        st.session_state["page"] = "history"
                        st.rerun()
            with title_prev:
                if st.button(
                    "<",
                    key=f"title_prev_sku_{data.meeting_id}",
                    help="Previous FSC",
                    use_container_width=True,
                    disabled=filtered.empty or st.session_state["current_pos"] <= 0,
                ):
                    st.session_state["current_pos"] -= 1
                    st.rerun()
            with title_next:
                if st.button(
                    r"\>",
                    key=f"title_next_sku_{data.meeting_id}",
                    help="Next FSC",
                    use_container_width=True,
                    disabled=filtered.empty or st.session_state["current_pos"] >= len(filtered) - 1,
                ):
                    st.session_state["current_pos"] += 1
                    st.rerun()

        if not filtered.empty:
            row = filtered.iloc[st.session_state["current_pos"]]
            render_sku_header_card(row)
            render_metric_cards(row)
            st.divider()

    if filtered.empty:
        st.warning("No SKU rows match the current filters.")
        return

    render_demand(data, row)
    st.divider()
    render_section_tables(row)
    st.divider()
    render_comments(data, row, store, total_skus=len(filtered))
    st.divider()
    render_exports(data, store)

    labels_for_select = [make_sku_label(filtered.iloc[i]) for i in range(len(filtered))]
    selected_label = st.selectbox(
        "SKU Jump",
        labels_for_select,
        index=st.session_state["current_pos"],
        key=f"sku_jump_select_{data.meeting_id}",
    )
    selected_pos = labels_for_select.index(selected_label)
    if selected_pos != st.session_state["current_pos"]:
        st.session_state["current_pos"] = selected_pos
        st.rerun()

    with st.expander("Workbook structure detected"):
        sheets_df = pd.DataFrame(
            [{"Sheet": k, "Max Row": v[0], "Max Column": v[1]} for k, v in data.observed_sheets.items()]
        )
        st.dataframe(sheets_df, hide_index=True, use_container_width=True)
        st.write("Future demand campaign headers detected in REBUYS:")
        st.code(", ".join(data.future_campaigns), language=None)
        st.write("ACTUAL SALES campaign headers detected from row 2:")
        st.code(", ".join(data.actual_campaigns), language=None)


def render_history_summary_table(history_summaries: list[dict[str, Any]]):
    if not history_summaries:
        st.info("No past meeting files have been saved yet.")
        return

    sorted_items = sorted(
        history_summaries,
        key=lambda item: item["summary"]["provided_date"],
        reverse=True,
    )

    header = st.columns([2.2, 0.9, 0.7, 0.85, 1.05, 1.0, 1.15])
    for col, text in zip(
        header,
        ["Meeting", "Profiles", "FSCs", "Bought", "Not Bought", "Escalated", "Total Bought $"],
    ):
        col.markdown(f'<div class="history-table-header">{text}</div>', unsafe_allow_html=True)

    for i, item in enumerate(sorted_items):
        summary = item["summary"]
        row = st.columns([2.2, 0.9, 0.7, 0.85, 1.05, 1.0, 1.15])
        button_key = f"open_history_review_{i}_{safe_filename(summary['meeting_name'])}"

        if row[0].button(summary["meeting_name"], key=button_key, use_container_width=True):
            st.session_state["history_review_path"] = str(item["path"])
            st.session_state["history_review_workbook_name"] = item["original_name"]
            st.session_state["history_review_meeting_name"] = summary["meeting_name"]
            st.session_state["current_pos"] = 0
            st.session_state["page"] = "history_review"
            st.rerun()

        values = [
            f"{summary['unique_profile_count']:,}",
            f"{summary['unique_fsc_count']:,}",
            f"{summary['decision_counts']['Y']:,}",
            f"{summary['decision_counts']['N']:,}",
            f"{summary['decision_counts']['TBD']:,}",
            fmt_dollar(summary.get("total_bought_amount", 0)),
        ]
        for col, val in zip(row[1:], values):
            col.markdown(f'<div class="history-row-cell">{escape(val)}</div>', unsafe_allow_html=True)


def render_meeting_history_page():
    inject_global_css()

    history_files = list_history_files()
    history_summaries = []
    for item in history_files:
        path = item["path"]
        provided_date = item["provided_date"]
        try:
            data = cached_load_workbook(str(path), item["original_name"], os.path.getmtime(path))
            summary = summarize_rebuy_meeting(data, provided_date)
            history_summaries.append(
                {
                    "path": path,
                    "provided_date": provided_date,
                    "original_name": item["original_name"],
                    "summary": summary,
                }
            )
        except Exception as exc:
            st.error(f"Could not read saved meeting file {path.name}: {exc}")

    with st.sidebar:
        render_sidebar_nav("history")
        st.markdown('<div class="sidebar-section-label">Add Meeting</div>', unsafe_allow_html=True)
        uploaded_history = st.file_uploader("Drag and drop workbook here", type=["xlsx"], key="history_file_uploader")
        provided_meeting_date = st.date_input("Meeting Date", value=date.today(), key="history_meeting_date")
        if st.button("Save Meeting", type="primary", use_container_width=True):
            if uploaded_history is None:
                st.warning("Upload a past rebuy meeting file first.")
            else:
                _, original_name = save_history_upload(uploaded_history, provided_meeting_date)
                st.success(f"Saved {original_name} for {provided_meeting_date:%Y-%m-%d}.")
                st.rerun()

        st.divider()
        st.markdown('<div class="sidebar-section-label">Meeting Calendar</div>', unsafe_allow_html=True)
        render_history_calendar(history_summaries)
        st.caption("Blue dates have saved meetings. Hover over a blue date to see Y/N/TBD counts and total rebuy amount.")

    st.title("Past Rebuy Decisions / Meetings")
    st.caption("Browse and review historical rebuy meetings, decisions, and workbook outcomes.")

    total_meetings = len(history_summaries)
    total_bought = sum(item["summary"].get("total_bought_amount", 0) for item in history_summaries)
    total_fsc_reviewed = sum(item["summary"].get("unique_fsc_count", 0) for item in history_summaries)
    recent_uploads = len(history_summaries[:8])
    render_kpi_strip(
        [
            ("Total Meetings", fmt_num(total_meetings), "Saved locally"),
            ("Total Bought $", fmt_dollar(total_bought), "Across saved meetings"),
            ("FSCs Reviewed", fmt_num(total_fsc_reviewed), "Across saved meetings"),
            ("Recent Uploads", fmt_num(recent_uploads), "Most recent files"),
        ]
    )

    st.markdown("### Saved Meetings")
    if not history_summaries:
        st.info("No past meeting files have been saved yet.")
        return

    c1, c2 = st.columns([2, 1])
    meeting_search = c1.text_input("Search meetings", placeholder="Search by meeting date or workbook name", key="history_search")
    month_options = ["All"] + sorted({item["summary"]["provided_date"][:7] for item in history_summaries}, reverse=True)
    selected_month = c2.selectbox("Filter by month", options=month_options, key="history_month_filter")

    filtered_history = history_summaries
    if meeting_search.strip():
        s = meeting_search.strip().lower()
        filtered_history = [
            item for item in filtered_history
            if s in item["summary"]["meeting_name"].lower() or s in item["original_name"].lower()
        ]
    if selected_month != "All":
        filtered_history = [item for item in filtered_history if item["summary"]["provided_date"].startswith(selected_month)]

    render_history_summary_table(filtered_history)

    st.markdown("### FSC / Product details by decision")
    with st.expander("Open details table", expanded=False):
        meeting_options = [item["summary"]["meeting_name"] for item in history_summaries]
        selected_meeting_name = st.selectbox("Select meeting", options=meeting_options, key="history_detail_meeting")
        selected_item = next(item for item in history_summaries if item["summary"]["meeting_name"] == selected_meeting_name)
        decision_choice = st.radio(
            "Decision Filter",
            options=["Y", "N", "TBD"],
            horizontal=True,
            format_func=lambda x: DECISION_LABELS[x],
            key="history_detail_decision",
        )
        detail_df = selected_item["summary"]["details"][decision_choice]
        if detail_df.empty:
            st.info(f"No FSCs found for {DECISION_LABELS[decision_choice]}.")
        else:
            display_detail_df = detail_df.copy()
            display_detail_df["Rebuy Qty"] = display_detail_df["Rebuy Qty"].apply(lambda x: fmt_num(x))
            display_detail_df["Price"] = display_detail_df["Price"].apply(lambda x: fmt_dollar(x))
            st.dataframe(display_detail_df, hide_index=True, use_container_width=True)


def render_history_review_dashboard_page():
    workbook_path = st.session_state.get("history_review_path")
    workbook_name = st.session_state.get("history_review_workbook_name")
    meeting_name = st.session_state.get("history_review_meeting_name")

    if not workbook_path:
        st.error("No saved meeting was selected.")
        if st.button("Back to Past Meetings"):
            st.session_state["page"] = "history"
            st.rerun()
        return

    if not Path(workbook_path).exists():
        st.error("The saved meeting workbook could not be found.")
        st.caption(f"Expected file path: {workbook_path}")
        if st.button("Back to Past Meetings"):
            st.session_state["page"] = "history"
            st.rerun()
        return

    render_review_dashboard(
        preloaded_workbook_path=workbook_path,
        preloaded_workbook_name=workbook_name,
        history_meeting_name=meeting_name,
    )

def main():
    require_passcode()

    if "page" not in st.session_state or st.session_state.get("page") == "home":
        st.session_state["page"] = "review"

    if st.session_state["page"] == "history":
        render_meeting_history_page()
    elif st.session_state["page"] == "history_review":
        render_history_review_dashboard_page()
    else:
        render_review_dashboard()


if __name__ == "__main__":
    main()
