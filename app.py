import streamlit as st
import requests
import pandas as pd
import os
import re
import time
import json
from datetime import datetime, timedelta, time as datetime_time
from typing import Any
from urllib.parse import quote
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.widgets.markers import makeMarker
from supabase import create_client, Client
from google import genai
from google.genai import types

# --- 1. CONFIGURATION & APP INITIALIZATION ---
st.set_page_config(
    page_title="AIA Canada Media Monitor", 
    layout="wide",
    page_icon="📊"
)

GITHUB_REPO = "bbeehler/aia-canada"  
WORKFLOW_FILE = "monitor.yml"

@st.cache_resource
def init_connections():
    sb_url = st.secrets["SUPABASE_URL"]
    sb_key = st.secrets["SUPABASE_KEY"]
    gemini_key = st.secrets["GEMINI_API_KEY"]
    
    sb_client = create_client(sb_url, sb_key)
    gem_client = genai.Client(api_key=gemini_key)
    return sb_client, gem_client

try:
    supabase, ai_client = init_connections()
except Exception as e:
    st.error("Initialization Error: Check your Streamlit Secrets configuration.")
    st.stop()

# --- 🔐 SUPABASE AUTH & ROLE LOOKUP INTERCEPTOR ---
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "user_role" not in st.session_state:
    st.session_state["user_role"] = "Viewer"  
if "user_full_name" not in st.session_state:
    st.session_state["user_full_name"] = ""

if st.session_state["auth_user"] is None:
    st.markdown("<h2 style='text-align: center;'>📊 AIA Canada Media Monitor Access Portal</h2>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        with st.form("login_form"):
            st.markdown("### Account Authentication")
            login_email = st.text_input("Corporate Email Address")
            login_password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Authenticate Sign-In", use_container_width=True)
            
            if submit_login:
                try:
                    res = supabase.auth.sign_in_with_password({"email": login_email, "password": login_password})
                    st.session_state["auth_user"] = res.user
                    
                    role_query = supabase.table("monitor_users").select("user_role, full_name").eq("user_id", res.user.id).execute()
                    if role_query.data:
                        st.session_state["user_role"] = role_query.data[0]["user_role"]
                        st.session_state["user_full_name"] = role_query.data[0]["full_name"]
                    else:
                        st.session_state["user_role"] = "Viewer"
                        st.session_state["user_full_name"] = res.user.email
                        
                    st.success(f"Access authorized as {st.session_state['user_role']}!")
                    time.sleep(1)
                    st.rerun()
                except Exception as login_err:
                    st.error(f"Authentication Failed: {login_err}")
    st.stop()

USER_ROLE = st.session_state["user_role"]
IS_ADMIN = USER_ROLE == "Administrator"
IS_MANAGER = USER_ROLE == "Editor"  
IS_VIEWER = USER_ROLE == "Viewer"

def load_active_team_users():
    try:
        res = supabase.table("monitor_users").select("full_name").order("full_name").execute()
        return ["Unassigned"] + [row["full_name"] for row in res.data]
    except Exception:
        return ["Unassigned", "Brian Beehler", "Emily Chung"]

TEAM_USERS = load_active_team_users()

# --- NEW GLOBAL CONTACT LOADER ---
def load_media_contacts():
    try:
        res = supabase.table("media_contacts").select("id, full_name, outlet").order("full_name").execute()
        return res.data if res.data else []
    except Exception:
        return []

MEDIA_CONTACTS = load_media_contacts()
CONTACT_NAMES = ["Unassigned", "➕ Add New Contact..."] + [f"{c['full_name']} ({c['outlet']})" for c in MEDIA_CONTACTS]
CONTACT_MAP = {f"{c['full_name']} ({c['outlet']})": c['id'] for c in MEDIA_CONTACTS}

# --- GLOBAL UTILITY OPERATIONS ---
def delete_mention_record(record_id):
    try:
        supabase.table("mentions").delete().eq("id", record_id).execute()
        st.toast("Mention successfully removed from index!")
    except Exception as e:
        st.error(f"Deletion failed: {e}")

def add_action_note(mention_id, note_text, user):
    if note_text.strip():
        try:
            supabase.table("mention_actions").insert({
                "mention_id": mention_id,
                "action_note": note_text,
                "performed_by": user
            }).execute()
            st.toast("Action log note saved successfully!")
        except Exception as e:
            st.error(f"Failed to record note: {e}")

def send_assignment_notification(mention_id, mention_title, recipient, sender, message):
    if recipient and recipient != "Unassigned":
        final_message = message if message.strip() else "Please review this newly assigned mention."
        try:
            supabase.table("notifications").insert({
                "recipient_name": recipient,
                "sender_name": sender,
                "mention_id": mention_id,
                "mention_title": mention_title,
                "message": final_message
            }).execute()
        except Exception as e:
            st.error(f"Failed to send notification: {e}")


def get_app_record_url(mention_id):
    """Return an absolute internal record URL when APP_BASE_URL is configured."""
    base_url = st.secrets.get("APP_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return f"{base_url}/?mention_id={mention_id}"
    return f"/?mention_id={mention_id}"


@st.cache_data(ttl=300)
def load_registered_email_recipients():
    """Load registered application users with valid tracking email addresses."""
    try:
        response = (
            supabase.table("monitor_users")
            .select("full_name, tracking_email, user_role")
            .not_.is_("tracking_email", "null")
            .order("full_name")
            .execute()
        )
    except Exception as exc:
        st.warning(f"Could not load registered email recipients: {exc}")
        return []

    recipients = []
    seen_emails = set()

    for row in response.data or []:
        email = str(row.get("tracking_email") or "").strip()
        if not email or email.lower() in seen_emails:
            continue

        seen_emails.add(email.lower())
        full_name = row.get("full_name") or email
        role = row.get("user_role") or "User"
        recipients.append({
            "label": f"{full_name} ({role})",
            "email": email,
        })

    return recipients


def build_mention_email_body(mention, additional_message=""):
    """Build the plain-text body used by the email share link."""
    brands = mention.get("brands_affected") or []
    brand_text = ", ".join(brands) if isinstance(brands, list) else str(brands)
    introduction = (
        f"{additional_message.strip()}\n\n" if additional_message.strip() else ""
    )

    return f"""{introduction}AIA Canada Media Mention

Title: {mention.get("title") or "Untitled"}
Outlet: {mention.get("outlet_platform") or "Unknown"}
Published: {mention.get("date_published") or "Unknown"}
Assigned to: {mention.get("assigned_to_user") or "Unassigned"}
Brand(s) affected: {brand_text or "Not specified"}
Theme: {mention.get("theme") or "Not specified"}
Sentiment: {mention.get("sentiment_category") or "Unknown"}
Sentiment score: {mention.get("sentiment_score") if mention.get("sentiment_score") is not None else "N/A"}
Alert level: {mention.get("alert_level") or "Not set"}
Recommendation: {mention.get("recommendation") or "Not set"}

AI recommendation:
{mention.get("ai_action_recommendation") or "Not available"}

Summary:
{mention.get("snippet") or "No summary available."}

Read article:
{mention.get("url") or "Not available"}

Open app workspace:
{get_app_record_url(mention.get("id", ""))}
"""


def build_mailto_url(mention, recipients, additional_message=""):
    """Build a mailto URL for Outlook or the user's default email client."""
    subject = f"AIA Canada Media Mention: {mention.get('title') or 'Untitled'}"
    body = build_mention_email_body(mention, additional_message)

    clean_recipients = []
    seen_emails = set()
    for email in recipients:
        normalized_email = str(email or "").strip()
        if not normalized_email or normalized_email.lower() in seen_emails:
            continue
        seen_emails.add(normalized_email.lower())
        clean_recipients.append(normalized_email)

    recipient_string = ",".join(clean_recipients)

    return (
        f"mailto:{quote(recipient_string, safe='@,')}"
        f"?subject={quote(subject)}"
        f"&body={quote(body)}"
    )


def mark_mention_for_daily_report(mention_id, report_date, shared_by):
    """Mark a mention for explicit inclusion in a selected daily report."""
    shared_at = datetime.now().astimezone().isoformat()

    (
        supabase.table("mentions")
        .update({
            "include_in_daily_report": True,
            "daily_report_date": report_date.isoformat(),
            "last_shared_at": shared_at,
            "last_shared_by": shared_by,
        })
        .eq("id", mention_id)
        .execute()
    )

    (
        supabase.table("mention_actions")
        .insert({
            "mention_id": mention_id,
            "action_note": (
                f"Marked for the {report_date.isoformat()} daily media report "
                "and prepared for email sharing."
            ),
            "performed_by": shared_by,
        })
        .execute()
    )


def render_share_mention_controls(mention, key_prefix):
    """Render registered-user email and daily-report inclusion controls."""
    st.markdown("#### 📧 Share Mention")

    registered_recipients = load_registered_email_recipients()
    recipient_map = {
        recipient["label"]: recipient["email"]
        for recipient in registered_recipients
    }

    assigned_owner = mention.get("assigned_to_user")
    default_recipient_labels = [
        label
        for label in recipient_map
        if assigned_owner
        and label.startswith(f"{assigned_owner} (")
    ]

    selected_recipient_labels = st.multiselect(
        "Select registered recipients",
        options=list(recipient_map.keys()),
        default=default_recipient_labels,
        placeholder="Select one or more registered users",
        key=f"{key_prefix}_registered_recipients",
    )

    selected_recipient_emails = [
        recipient_map[label]
        for label in selected_recipient_labels
    ]

    additional_recipient_text = st.text_input(
        "Additional email addresses",
        placeholder="external@example.com, another@example.com",
        key=f"{key_prefix}_additional_recipients",
        help="Optional. Separate multiple addresses with commas or semicolons.",
    )
    additional_recipient_emails = [
        email.strip()
        for email in additional_recipient_text.replace(";", ",").split(",")
        if email.strip()
    ]
    all_recipient_emails = (
        selected_recipient_emails + additional_recipient_emails
    )

    existing_report_date = mention.get("daily_report_date")
    default_report_date = datetime.now().date()
    if existing_report_date:
        try:
            default_report_date = datetime.fromisoformat(
                str(existing_report_date)
            ).date()
        except ValueError:
            pass

    report_date = st.date_input(
        "Include in daily media report for",
        value=default_report_date,
        key=f"{key_prefix}_report_date",
    )
    additional_message = st.text_area(
        "Optional email introduction",
        placeholder="Please review this media mention.",
        height=90,
        key=f"{key_prefix}_message",
    )

    if selected_recipient_emails:
        st.caption(
            "Selected recipients: "
            + ", ".join(selected_recipient_emails)
        )

    if mention.get("include_in_daily_report"):
        st.info(
            "This mention is already marked for the "
            f"{mention.get('daily_report_date') or 'selected'} daily report."
        )

    if st.button(
        "Mark for Daily Report & Prepare Email",
        type="primary",
        use_container_width=True,
        key=f"{key_prefix}_prepare",
        disabled=IS_VIEWER,
    ):
        if not all_recipient_emails:
            st.error("Select at least one registered recipient or enter an additional email address.")
        else:
            try:
                current_user = (
                    st.session_state.get("user_full_name")
                    or "Unknown user"
                )
                mark_mention_for_daily_report(
                    mention_id=mention["id"],
                    report_date=report_date,
                    shared_by=current_user,
                )
                st.session_state[f"{key_prefix}_mailto"] = build_mailto_url(
                    mention=mention,
                    recipients=all_recipient_emails,
                    additional_message=additional_message,
                )
                st.success(
                    f"Mention added to the {report_date.isoformat()} daily report."
                )
            except Exception as exc:
                st.error(f"Unable to prepare the mention for sharing: {exc}")

    mailto_url = st.session_state.get(f"{key_prefix}_mailto")
    if mailto_url:
        st.link_button(
            "Open Email in Outlook",
            mailto_url,
            use_container_width=True,
        )
        st.caption(
            "The selected registered users are added to the recipient field. "
            "This opens the computer's default email application."
        )


def load_daily_report_mentions(target_date):
    """Return eligible mentions inserted or explicitly included for a report date."""
    start_iso = datetime.combine(target_date, datetime.min.time()).isoformat()
    end_iso = datetime.combine(target_date, datetime.max.time()).isoformat()

    selected_fields = (
        "id, title, url, outlet_platform, theme, status, recommendation, "
        "brands_affected, alert_level, assigned_to_user, date_published, "
        "inserted_at, sentiment_category, sentiment_score, sentiment_rationale, "
        "ai_action_recommendation, naming_error_flag, data_conflict_flag, "
        "data_conflict_details, include_in_daily_report, daily_report_date"
    )

    inserted_response = (
        supabase.table("mentions")
        .select(selected_fields)
        .gte("inserted_at", start_iso)
        .lte("inserted_at", end_iso)
        .neq("status", "noise")
        .execute()
    )

    included_response = (
        supabase.table("mentions")
        .select(selected_fields)
        .eq("include_in_daily_report", True)
        .eq("daily_report_date", target_date.isoformat())
        .neq("status", "noise")
        .execute()
    )

    unique_mentions = {}

    for mention in (inserted_response.data or []) + (included_response.data or []):
        status = str(mention.get("status") or "").strip().lower()
        recommendation = str(
            mention.get("recommendation") or ""
        ).strip().lower()

        sentiment_rationale = str(
            mention.get("sentiment_rationale") or ""
        ).strip().lower()
        ai_recommendation = str(
            mention.get("ai_action_recommendation") or ""
        ).strip().lower()

        is_noise = (
            status == "noise"
            or recommendation == "ignore"
            or sentiment_rationale.startswith("suppressed noise")
            or "suppressed noise" in sentiment_rationale
            or ai_recommendation == "ignore"
        )

        if is_noise:
            continue

        unique_mentions[mention["id"]] = mention

    return list(unique_mentions.values())


@st.cache_data(ttl=300)
def load_auth_login_status():
    """Return Supabase Auth login metadata keyed by user ID."""
    try:
        admin_client = create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
        )
        response = admin_client.auth.admin.list_users(page=1, per_page=1000)

        if isinstance(response, list):
            auth_users = response
        else:
            auth_users = getattr(response, "users", [])

        return {
            str(user.id): {
                "email": getattr(user, "email", None),
                "created_at": getattr(user, "created_at", None),
                "last_sign_in_at": getattr(user, "last_sign_in_at", None),
                "confirmed_at": getattr(user, "confirmed_at", None),
            }
            for user in auth_users
        }
    except Exception as exc:
        st.error(f"Unable to load authentication activity: {exc}")
        return {}


def format_auth_timestamp(value):
    """Format a Supabase Auth timestamp for display."""
    if not value:
        return "Not recorded"

    try:
        parsed = pd.to_datetime(value, utc=True)
        return parsed.tz_convert("America/Toronto").strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return str(value)



# --- ASK AIA MEDIA DATABASE HELPERS ---
ASK_AIA_TABLES = {
    "mentions": {"date_field": "inserted_at", "order_field": "inserted_at"},
    "mention_actions": {"date_field": "inserted_at", "order_field": "inserted_at"},
    "media_contacts": {"date_field": None, "order_field": "full_name"},
    "media_inquiries": {"date_field": "inserted_at", "order_field": "inserted_at"},
    "inquiry_actions": {"date_field": "inserted_at", "order_field": "inserted_at"},
    "monitor_users": {"date_field": None, "order_field": "full_name"},
    "monitor_keywords": {"date_field": None, "order_field": "term"},
    "monitor_templates": {"date_field": None, "order_field": "template_name"},
    "notifications": {"date_field": "created_at", "order_field": "created_at"},
}


def fetch_all_table_rows(
    table_name: str,
    order_field: str | None = None,
    date_field: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """Fetch every matching row and field from a Supabase table."""
    rows: list[dict[str, Any]] = []
    start_index = 0

    while True:
        end_index = start_index + page_size - 1
        query = supabase.table(table_name).select("*")

        if date_field and start_iso:
            query = query.gte(date_field, start_iso)
        if date_field and end_iso:
            query = query.lte(date_field, end_iso)
        if order_field:
            query = query.order(order_field, desc=True)

        response = query.range(start_index, end_index).execute()
        page_rows = response.data or []
        rows.extend(page_rows)

        if len(page_rows) < page_size:
            break

        start_index += page_size

    return rows


def load_complete_ask_aia_context(
    use_date_filter: bool,
    start_date,
    end_date,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Load all fields from all application tables, with optional date filtering."""
    if use_date_filter:
        start_iso = datetime.combine(start_date, datetime_time.min).astimezone().isoformat()
        end_iso = datetime.combine(end_date, datetime_time.max).astimezone().isoformat()
    else:
        start_iso = None
        end_iso = None

    database_context: dict[str, list[dict[str, Any]]] = {}
    table_errors: dict[str, str] = {}

    for table_name, configuration in ASK_AIA_TABLES.items():
        try:
            database_context[table_name] = fetch_all_table_rows(
                table_name=table_name,
                order_field=configuration["order_field"],
                date_field=configuration["date_field"],
                start_iso=start_iso,
                end_iso=end_iso,
            )
        except Exception as exc:
            database_context[table_name] = []
            table_errors[table_name] = str(exc)

    return database_context, table_errors


def split_database_context(
    database_context: dict[str, list[dict[str, Any]]],
    maximum_characters: int = 120_000,
) -> list[str]:
    """Split complete database content into model-safe JSON batches."""
    chunks: list[str] = []
    current_records: list[dict[str, Any]] = []
    current_size = 0

    for table_name, rows in database_context.items():
        if not rows:
            wrapped_record = {
                "table": table_name,
                "record": None,
                "message": "No matching records.",
            }
            encoded = json.dumps(wrapped_record, default=str)

            if current_records and current_size + len(encoded) > maximum_characters:
                chunks.append(json.dumps(current_records, default=str))
                current_records = []
                current_size = 0

            current_records.append(wrapped_record)
            current_size += len(encoded)
            continue

        for row in rows:
            wrapped_record = {"table": table_name, "record": row}
            encoded = json.dumps(wrapped_record, default=str)

            if current_records and current_size + len(encoded) > maximum_characters:
                chunks.append(json.dumps(current_records, default=str))
                current_records = []
                current_size = 0

            current_records.append(wrapped_record)
            current_size += len(encoded)

    if current_records:
        chunks.append(json.dumps(current_records, default=str))

    return chunks


def analyse_database_chunk(
    user_question: str,
    chunk_text: str,
    chunk_number: int,
    total_chunks: int,
) -> str:
    """Extract question-relevant evidence from one complete database batch."""
    extraction_instruction = """
You are an evidence extraction system for AIA Canada's media-monitoring database.

Review every supplied record and every supplied field.

Rules:
1. Extract only facts relevant to the user's question.
2. Do not invent facts or infer unsupported intent.
3. Preserve exact IDs, names, dates, statuses, scores, recommendations and relationships.
4. Match related records using id, mention_id, inquiry_id, contact_id,
   author_contact_id and user_id.
5. Distinguish date_published from inserted_at and created_at.
6. Include conflicting, missing or incomplete values when they affect the answer.
7. State plainly when the batch contains no relevant evidence.
8. Never expose passwords, API keys, access tokens or service-role keys.
9. Keep the extraction compact without omitting relevant evidence.
"""

    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            f"User question:\n{user_question}\n\n"
            f"Database batch {chunk_number} of {total_chunks}:\n{chunk_text}"
        ),
        config=types.GenerateContentConfig(system_instruction=extraction_instruction),
    )
    return response.text


def generate_final_ask_aia_answer(
    user_question: str,
    extracted_evidence: list[str],
    table_counts: dict[str, int],
    table_errors: dict[str, str],
    date_scope: str,
) -> str:
    """Create one final answer from evidence extracted from every database batch."""
    synthesis_instruction = """
You are the internal media-monitoring data analyst for AIA Canada.

Answer the user's question using only the extracted database evidence.

Rules:
1. Do not invent facts.
2. State clearly when the records do not contain enough information.
3. Reconcile related records using their IDs.
4. Distinguish publication dates from insertion and creation dates.
5. Include relevant counts, owners, contacts, actions, recommendations,
   statuses, sentiment values and source records.
6. State the date scope used.
7. When useful, identify the source table and record ID.
8. Mention any table that could not be read if that limitation affects the answer.
9. Keep the response clear, operational and concise.
10. Never expose passwords, API keys, access tokens or service-role keys.
"""

    evidence_text = "\n\n".join(
        f"Evidence batch {index + 1}:\n{evidence}"
        for index, evidence in enumerate(extracted_evidence)
    )

    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            f"User question:\n{user_question}\n\n"
            f"Date scope:\n{date_scope}\n\n"
            f"Rows reviewed by table:\n{json.dumps(table_counts, indent=2)}\n\n"
            f"Table read errors:\n{json.dumps(table_errors, indent=2)}\n\n"
            f"Evidence from all database batches:\n{evidence_text}"
        ),
        config=types.GenerateContentConfig(system_instruction=synthesis_instruction),
    )
    return response.text



def remove_existing_executive_summary(report_text):
    """Remove an AI-generated executive summary before adding the required one."""
    if not report_text:
        return ""

    pattern = re.compile(
        r"^\s*#{1,3}\s*Executive Summary\s*\n+.*?"
        r"(?=\n#{1,3}\s+|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned_text = pattern.sub("", report_text, count=1).strip()
    return cleaned_text or report_text.strip()



def calculate_percentage_change(current_value: float, previous_value: float):
    """Return percentage change, using None when no valid baseline exists."""
    if previous_value == 0:
        return None if current_value == 0 else float("inf")
    return ((current_value - previous_value) / previous_value) * 100


def format_metric_delta(current_value: float, previous_value: float, suffix: str = "") -> str:
    """Format a Streamlit metric delta."""
    difference = current_value - previous_value
    if isinstance(current_value, float) or isinstance(previous_value, float):
        return f"{difference:+.2f}{suffix}"
    return f"{difference:+}{suffix}"


def fetch_reportable_mentions(start_date, end_date) -> list[dict]:
    """Fetch non-noise, non-ignored mentions by database insertion date."""
    start_iso = datetime.combine(start_date, datetime.min.time()).isoformat()
    end_iso = datetime.combine(end_date, datetime.max.time()).isoformat()

    response = (
        supabase.table("mentions")
        .select(
            "id, title, snippet, url, outlet_platform, theme, status, "
            "recommendation, brands_affected, alert_level, assigned_to_user, "
            "date_published, inserted_at, sentiment_category, sentiment_score, "
            "naming_error_flag, data_conflict_flag, data_conflict_details, "
            "ai_action_recommendation"
        )
        .gte("inserted_at", start_iso)
        .lte("inserted_at", end_iso)
        .neq("status", "noise")
        .neq("recommendation", "ignore")
        .order("inserted_at")
        .execute()
    )

    return [
        record
        for record in (response.data or [])
        if str(record.get("status") or "").strip().lower() != "noise"
        and str(record.get("recommendation") or "").strip().lower() != "ignore"
        and "suppressed noise"
        not in str(record.get("sentiment_rationale") or "").strip().lower()
    ]


def load_monitoring_keywords() -> list[dict]:
    """Load configured monitoring keywords for quantitative matching."""
    try:
        response = (
            supabase.table("monitor_keywords")
            .select("term, brand_tags, theme_layer")
            .order("term")
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def normalize_sentiment_category(value) -> str:
    """Normalize sentiment labels for consistent reporting."""
    normalized = str(value or "Unknown").strip().title()
    allowed = {"Positive", "Neutral", "Negative", "Mixed"}
    return normalized if normalized in allowed else "Unknown"


def records_to_weekly_dataframe(records: list[dict]) -> pd.DataFrame:
    """Convert mention records into a normalized analytical dataframe."""
    if not records:
        return pd.DataFrame()

    frame = pd.DataFrame(records)
    frame["inserted_at"] = pd.to_datetime(frame["inserted_at"], errors="coerce", utc=True)
    frame["insert_date"] = frame["inserted_at"].dt.date
    frame["sentiment_category"] = frame["sentiment_category"].apply(
        normalize_sentiment_category
    )
    frame["sentiment_score"] = pd.to_numeric(
        frame["sentiment_score"], errors="coerce"
    ).fillna(0.0)
    frame["alert_level"] = frame["alert_level"].fillna("Not set")
    frame["outlet_platform"] = frame["outlet_platform"].fillna("Unknown")
    frame["theme"] = frame["theme"].fillna("Unclassified")
    return frame


def count_keyword_mentions(
    records: list[dict],
    keyword_rows: list[dict],
) -> dict[str, int]:
    """Count records containing each configured keyword."""
    counts: dict[str, int] = {}

    for keyword_row in keyword_rows:
        term = str(keyword_row.get("term") or "").strip()
        if not term:
            continue

        normalized_term = term.casefold()
        count = 0

        for record in records:
            searchable_values = [
                record.get("title"),
                record.get("snippet"),
                record.get("theme"),
                " ".join(record.get("brands_affected") or []),
            ]
            searchable_text = " ".join(
                str(value) for value in searchable_values if value
            ).casefold()

            if normalized_term in searchable_text:
                count += 1

        counts[term] = count

    return counts


def build_weekly_quantitative_package(
    current_records: list[dict],
    previous_records: list[dict],
    keyword_rows: list[dict],
    current_start,
    current_end,
    previous_start,
    previous_end,
) -> dict:
    """Build measured week-over-week reporting data."""
    current_df = records_to_weekly_dataframe(current_records)
    previous_df = records_to_weekly_dataframe(previous_records)

    current_volume = len(current_df)
    previous_volume = len(previous_df)

    current_average_sentiment = (
        float(current_df["sentiment_score"].mean()) if current_volume else 0.0
    )
    previous_average_sentiment = (
        float(previous_df["sentiment_score"].mean()) if previous_volume else 0.0
    )

    current_positive = (
        int((current_df["sentiment_category"] == "Positive").sum())
        if current_volume
        else 0
    )
    previous_positive = (
        int((previous_df["sentiment_category"] == "Positive").sum())
        if previous_volume
        else 0
    )

    current_negative = (
        int((current_df["sentiment_category"] == "Negative").sum())
        if current_volume
        else 0
    )
    previous_negative = (
        int((previous_df["sentiment_category"] == "Negative").sum())
        if previous_volume
        else 0
    )

    current_high_priority = (
        int(current_df["alert_level"].isin(["High", "Critical"]).sum())
        if current_volume
        else 0
    )
    previous_high_priority = (
        int(previous_df["alert_level"].isin(["High", "Critical"]).sum())
        if previous_volume
        else 0
    )

    current_unique_outlets = (
        int(current_df["outlet_platform"].nunique()) if current_volume else 0
    )
    previous_unique_outlets = (
        int(previous_df["outlet_platform"].nunique()) if previous_volume else 0
    )

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    current_dates = [
        current_start + timedelta(days=offset) for offset in range(7)
    ]
    previous_dates = [
        previous_start + timedelta(days=offset) for offset in range(7)
    ]

    current_daily_counts = (
        current_df.groupby("insert_date").size().to_dict()
        if current_volume
        else {}
    )
    previous_daily_counts = (
        previous_df.groupby("insert_date").size().to_dict()
        if previous_volume
        else {}
    )

    volume_chart = pd.DataFrame(
        {
            "Day": weekday_labels,
            "Selected week": [
                int(current_daily_counts.get(day, 0)) for day in current_dates
            ],
            "Previous week": [
                int(previous_daily_counts.get(day, 0)) for day in previous_dates
            ],
        }
    ).set_index("Day")

    sentiment_categories = ["Positive", "Neutral", "Negative", "Mixed", "Unknown"]
    current_sentiment_counts = (
        current_df["sentiment_category"].value_counts().to_dict()
        if current_volume
        else {}
    )
    previous_sentiment_counts = (
        previous_df["sentiment_category"].value_counts().to_dict()
        if previous_volume
        else {}
    )

    sentiment_chart = pd.DataFrame(
        {
            "Sentiment": sentiment_categories,
            "Selected week": [
                int(current_sentiment_counts.get(category, 0))
                for category in sentiment_categories
            ],
            "Previous week": [
                int(previous_sentiment_counts.get(category, 0))
                for category in sentiment_categories
            ],
        }
    ).set_index("Sentiment")

    current_keyword_counts = count_keyword_mentions(current_records, keyword_rows)
    previous_keyword_counts = count_keyword_mentions(previous_records, keyword_rows)
    keyword_rows_output = []

    for term in sorted(
        set(current_keyword_counts) | set(previous_keyword_counts),
        key=lambda item: current_keyword_counts.get(item, 0),
        reverse=True,
    ):
        current_count = current_keyword_counts.get(term, 0)
        previous_count = previous_keyword_counts.get(term, 0)
        percentage_change = calculate_percentage_change(
            current_count,
            previous_count,
        )

        if percentage_change is None:
            change_label = "0.0%"
        elif percentage_change == float("inf"):
            change_label = "New"
        else:
            change_label = f"{percentage_change:+.1f}%"

        keyword_rows_output.append(
            {
                "Keyword": term,
                "Selected week": current_count,
                "Previous week": previous_count,
                "Change": current_count - previous_count,
                "Change %": change_label,
            }
        )

    keyword_table = pd.DataFrame(keyword_rows_output)
    if not keyword_table.empty:
        keyword_table = keyword_table.sort_values(
            ["Selected week", "Change"],
            ascending=[False, False],
        )

    outlet_chart = pd.DataFrame()
    if current_volume or previous_volume:
        current_outlets = (
            current_df["outlet_platform"].value_counts()
            if current_volume
            else pd.Series(dtype=int)
        )
        previous_outlets = (
            previous_df["outlet_platform"].value_counts()
            if previous_volume
            else pd.Series(dtype=int)
        )
        top_outlets = list(
            (current_outlets.add(previous_outlets, fill_value=0))
            .sort_values(ascending=False)
            .head(10)
            .index
        )
        outlet_chart = pd.DataFrame(
            {
                "Outlet": top_outlets,
                "Selected week": [
                    int(current_outlets.get(outlet, 0)) for outlet in top_outlets
                ],
                "Previous week": [
                    int(previous_outlets.get(outlet, 0)) for outlet in top_outlets
                ],
            }
        ).set_index("Outlet")

    theme_chart = pd.DataFrame()
    if current_volume or previous_volume:
        current_themes = (
            current_df["theme"].value_counts()
            if current_volume
            else pd.Series(dtype=int)
        )
        previous_themes = (
            previous_df["theme"].value_counts()
            if previous_volume
            else pd.Series(dtype=int)
        )
        top_themes = list(
            (current_themes.add(previous_themes, fill_value=0))
            .sort_values(ascending=False)
            .head(10)
            .index
        )
        theme_chart = pd.DataFrame(
            {
                "Theme": top_themes,
                "Selected week": [
                    int(current_themes.get(theme, 0)) for theme in top_themes
                ],
                "Previous week": [
                    int(previous_themes.get(theme, 0)) for theme in top_themes
                ],
            }
        ).set_index("Theme")

    detail_columns = [
        "date_published",
        "inserted_at",
        "outlet_platform",
        "title",
        "theme",
        "sentiment_category",
        "sentiment_score",
        "alert_level",
        "recommendation",
        "assigned_to_user",
    ]
    detail_table = (
        current_df[detail_columns]
        .sort_values("inserted_at", ascending=False)
        .copy()
        if current_volume
        else pd.DataFrame(columns=detail_columns)
    )

    if not detail_table.empty:
        detail_table["inserted_at"] = detail_table["inserted_at"].dt.strftime(
            "%Y-%m-%d %H:%M"
        )
        detail_table = detail_table.rename(
            columns={
                "date_published": "Published",
                "inserted_at": "Inserted",
                "outlet_platform": "Outlet",
                "title": "Title",
                "theme": "Theme",
                "sentiment_category": "Sentiment",
                "sentiment_score": "Score",
                "alert_level": "Alert",
                "recommendation": "Recommendation",
                "assigned_to_user": "Assigned to",
            }
        )

    metrics = {
        "current_volume": current_volume,
        "previous_volume": previous_volume,
        "current_average_sentiment": current_average_sentiment,
        "previous_average_sentiment": previous_average_sentiment,
        "current_positive": current_positive,
        "previous_positive": previous_positive,
        "current_negative": current_negative,
        "previous_negative": previous_negative,
        "current_high_priority": current_high_priority,
        "previous_high_priority": previous_high_priority,
        "current_unique_outlets": current_unique_outlets,
        "previous_unique_outlets": previous_unique_outlets,
        "volume_change_percent": calculate_percentage_change(
            current_volume,
            previous_volume,
        ),
    }

    return {
        "current_period": (
            f"{current_start.isoformat()} to {current_end.isoformat()}"
        ),
        "previous_period": (
            f"{previous_start.isoformat()} to {previous_end.isoformat()}"
        ),
        "metrics": metrics,
        "volume_chart": volume_chart.to_dict(),
        "sentiment_chart": sentiment_chart.to_dict(),
        "keyword_table": keyword_table.to_dict("records"),
        "outlet_chart": outlet_chart.to_dict() if not outlet_chart.empty else {},
        "theme_chart": theme_chart.to_dict() if not theme_chart.empty else {},
        "detail_table": detail_table.to_dict("records"),
    }


def generate_weekly_quantitative_interpretation(package: dict) -> str:
    """Use Gemini to interpret calculated metrics without recalculating them."""
    instruction = """
You are the senior media-monitoring analyst for AIA Canada.

Interpret the supplied quantitative weekly report. The calculations have
already been completed by the application.

Rules:
1. Use only the supplied metrics and tables.
2. Do not recalculate, invent or estimate values.
3. Compare the selected week with the previous week.
4. Explicitly identify increases, decreases and unchanged measures.
5. Treat low-volume samples cautiously and say when a trend may be unstable.
6. Highlight sentiment movement, volume movement, keyword movement, outlet
   concentration, themes and high-priority mentions.
7. Use Canadian Press style.
8. Provide exactly these sections:
   ## Quantitative Executive Summary
   ## Material Week-over-Week Changes
   ## Emerging Topics and Keywords
   ## Risks and Recommended Actions
9. Keep the analysis concise and suitable for executives.
"""

    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=json.dumps(package, default=str),
        config=types.GenerateContentConfig(system_instruction=instruction),
    )
    return response.text


def render_weekly_quantitative_report(package: dict) -> None:
    """Render a persistent quantitative weekly dashboard with explicit charts."""
    metrics = package["metrics"]

    st.markdown("### Quantitative Executive Dashboard")
    st.caption(
        f"Selected week: {package['current_period']} | "
        f"Comparison week: {package['previous_period']}"
    )

    metric_col1, metric_col2, metric_col3, metric_col4, metric_col5 = st.columns(5)

    with metric_col1:
        st.metric(
            "Mention volume",
            metrics["current_volume"],
            format_metric_delta(
                metrics["current_volume"],
                metrics["previous_volume"],
            ),
        )

    with metric_col2:
        st.metric(
            "Average sentiment",
            f"{metrics['current_average_sentiment']:.2f}",
            format_metric_delta(
                metrics["current_average_sentiment"],
                metrics["previous_average_sentiment"],
            ),
        )

    with metric_col3:
        st.metric(
            "Positive mentions",
            metrics["current_positive"],
            format_metric_delta(
                metrics["current_positive"],
                metrics["previous_positive"],
            ),
        )

    with metric_col4:
        st.metric(
            "Negative mentions",
            metrics["current_negative"],
            format_metric_delta(
                metrics["current_negative"],
                metrics["previous_negative"],
            ),
            delta_color="inverse",
        )

    with metric_col5:
        st.metric(
            "High/Critical",
            metrics["current_high_priority"],
            format_metric_delta(
                metrics["current_high_priority"],
                metrics["previous_high_priority"],
            ),
            delta_color="inverse",
        )

    volume_df = pd.DataFrame(package.get("volume_chart") or {})
    sentiment_df = pd.DataFrame(package.get("sentiment_chart") or {})
    theme_df = pd.DataFrame(package.get("theme_chart") or {})
    outlet_df = pd.DataFrame(package.get("outlet_chart") or {})
    keyword_df = pd.DataFrame(package.get("keyword_table") or [])

    volume_col, sentiment_col = st.columns(2)

    with volume_col:
        st.markdown("#### Daily Mention Volume")
        if volume_df.empty:
            st.info("No daily volume data is available.")
        else:
            volume_plot = (
                volume_df.rename_axis("Day")
                .reset_index()
                .melt(
                    id_vars="Day",
                    value_vars=["Selected week", "Previous week"],
                    var_name="Period",
                    value_name="Mentions",
                )
            )
            st.vega_lite_chart(
                volume_plot,
                {
                    "mark": {"type": "line", "point": True},
                    "encoding": {
                        "x": {
                            "field": "Day",
                            "type": "ordinal",
                            "sort": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                            "title": "Day",
                        },
                        "y": {
                            "field": "Mentions",
                            "type": "quantitative",
                            "title": "Mention count",
                        },
                        "color": {
                            "field": "Period",
                            "type": "nominal",
                            "title": "Period",
                        },
                        "tooltip": [
                            {"field": "Day", "type": "ordinal"},
                            {"field": "Period", "type": "nominal"},
                            {"field": "Mentions", "type": "quantitative"},
                        ],
                    },
                },
                use_container_width=True,
            )

    with sentiment_col:
        st.markdown("#### Sentiment Distribution")
        if sentiment_df.empty:
            st.info("No sentiment data is available.")
        else:
            sentiment_plot = (
                sentiment_df.rename_axis("Sentiment")
                .reset_index()
                .melt(
                    id_vars="Sentiment",
                    value_vars=["Selected week", "Previous week"],
                    var_name="Period",
                    value_name="Mentions",
                )
            )
            st.vega_lite_chart(
                sentiment_plot,
                {
                    "mark": "bar",
                    "encoding": {
                        "x": {
                            "field": "Sentiment",
                            "type": "nominal",
                            "title": "Sentiment",
                        },
                        "y": {
                            "field": "Mentions",
                            "type": "quantitative",
                            "title": "Mention count",
                        },
                        "xOffset": {"field": "Period"},
                        "color": {
                            "field": "Period",
                            "type": "nominal",
                            "title": "Period",
                        },
                        "tooltip": [
                            {"field": "Sentiment", "type": "nominal"},
                            {"field": "Period", "type": "nominal"},
                            {"field": "Mentions", "type": "quantitative"},
                        ],
                    },
                },
                use_container_width=True,
            )

    keyword_col, theme_col = st.columns(2)

    with keyword_col:
        st.markdown("#### Keyword Mention Trends")
        if keyword_df.empty:
            st.info("No configured monitoring keywords matched either week.")
        else:
            keyword_plot = keyword_df.head(15).melt(
                id_vars="Keyword",
                value_vars=["Selected week", "Previous week"],
                var_name="Period",
                value_name="Mentions",
            )
            st.vega_lite_chart(
                keyword_plot,
                {
                    "mark": "bar",
                    "encoding": {
                        "y": {
                            "field": "Keyword",
                            "type": "nominal",
                            "sort": "-x",
                            "title": "Keyword",
                        },
                        "x": {
                            "field": "Mentions",
                            "type": "quantitative",
                            "title": "Mention count",
                        },
                        "yOffset": {"field": "Period"},
                        "color": {
                            "field": "Period",
                            "type": "nominal",
                            "title": "Period",
                        },
                        "tooltip": [
                            {"field": "Keyword", "type": "nominal"},
                            {"field": "Period", "type": "nominal"},
                            {"field": "Mentions", "type": "quantitative"},
                        ],
                    },
                },
                use_container_width=True,
            )
            with st.expander("View keyword comparison table"):
                st.dataframe(keyword_df, use_container_width=True, hide_index=True)

    with theme_col:
        st.markdown("#### Theme Volume")
        if theme_df.empty:
            st.info("No theme data is available.")
        else:
            theme_plot = (
                theme_df.rename_axis("Theme")
                .reset_index()
                .melt(
                    id_vars="Theme",
                    value_vars=["Selected week", "Previous week"],
                    var_name="Period",
                    value_name="Mentions",
                )
            )
            st.vega_lite_chart(
                theme_plot,
                {
                    "mark": "bar",
                    "encoding": {
                        "y": {
                            "field": "Theme",
                            "type": "nominal",
                            "sort": "-x",
                            "title": "Theme",
                        },
                        "x": {
                            "field": "Mentions",
                            "type": "quantitative",
                            "title": "Mention count",
                        },
                        "yOffset": {"field": "Period"},
                        "color": {
                            "field": "Period",
                            "type": "nominal",
                            "title": "Period",
                        },
                        "tooltip": [
                            {"field": "Theme", "type": "nominal"},
                            {"field": "Period", "type": "nominal"},
                            {"field": "Mentions", "type": "quantitative"},
                        ],
                    },
                },
                use_container_width=True,
            )

    st.markdown("#### Top Outlet Volume")
    if outlet_df.empty:
        st.info("No outlet data is available.")
    else:
        outlet_plot = (
            outlet_df.rename_axis("Outlet")
            .reset_index()
            .melt(
                id_vars="Outlet",
                value_vars=["Selected week", "Previous week"],
                var_name="Period",
                value_name="Mentions",
            )
        )
        st.vega_lite_chart(
            outlet_plot,
            {
                "mark": "bar",
                "encoding": {
                    "y": {
                        "field": "Outlet",
                        "type": "nominal",
                        "sort": "-x",
                        "title": "Outlet",
                    },
                    "x": {
                        "field": "Mentions",
                        "type": "quantitative",
                        "title": "Mention count",
                    },
                    "yOffset": {"field": "Period"},
                    "color": {
                        "field": "Period",
                        "type": "nominal",
                        "title": "Period",
                    },
                    "tooltip": [
                        {"field": "Outlet", "type": "nominal"},
                        {"field": "Period", "type": "nominal"},
                        {"field": "Mentions", "type": "quantitative"},
                    ],
                },
            },
            use_container_width=True,
        )

    st.markdown("#### Gemini Interpretation")
    st.markdown(package.get("ai_interpretation") or "No interpretation available.")

    with st.expander("View mentions used in the selected week"):
        detail_df = pd.DataFrame(package.get("detail_table") or [])
        if detail_df.empty:
            st.info("No reportable mentions were available.")
        else:
            st.dataframe(detail_df, use_container_width=True, hide_index=True)



def _pdf_safe(value) -> str:
    """Convert a value to escaped text suitable for ReportLab paragraphs."""
    if value is None:
        return ""
    return escape(str(value))


def _make_line_chart(
    chart_df: pd.DataFrame,
    title: str,
    width: float = 7.2 * inch,
    height: float = 3.0 * inch,
) -> Drawing:
    """Build a two-series line chart for the PDF report."""
    drawing = Drawing(width, height)
    drawing.add(
        Paragraph(
            _pdf_safe(title),
            ParagraphStyle(
                "ChartTitle",
                parent=getSampleStyleSheet()["Heading3"],
                alignment=TA_CENTER,
                fontSize=11,
                leading=13,
            ),
        )
    )

    if chart_df.empty:
        return drawing

    selected = [float(value) for value in chart_df["Selected week"].tolist()]
    previous = [float(value) for value in chart_df["Previous week"].tolist()]
    points_selected = list(enumerate(selected))
    points_previous = list(enumerate(previous))

    chart = LinePlot()
    chart.x = 55
    chart.y = 35
    chart.width = width - 85
    chart.height = height - 75
    chart.data = [points_selected, points_previous]
    chart.lines[0].strokeColor = colors.HexColor("#1f77b4")
    chart.lines[0].strokeWidth = 2
    chart.lines[0].symbol = makeMarker("Circle")
    chart.lines[1].strokeColor = colors.HexColor("#7f7f7f")
    chart.lines[1].strokeWidth = 2
    chart.lines[1].symbol = makeMarker("Square")
    chart.xValueAxis.valueMin = 0
    chart.xValueAxis.valueMax = max(len(chart_df.index) - 1, 1)
    chart.xValueAxis.valueSteps = list(range(len(chart_df.index)))
    chart.xValueAxis.labelTextFormat = lambda value: str(chart_df.index[int(value)]) if int(value) < len(chart_df.index) else ""
    chart.yValueAxis.valueMin = 0
    max_value = max(selected + previous + [1])
    chart.yValueAxis.valueMax = max_value + max(1, max_value * 0.15)
    chart.yValueAxis.valueStep = max(1, round(chart.yValueAxis.valueMax / 5))
    drawing.add(chart)
    return drawing


def _make_vertical_bar_chart(
    chart_df: pd.DataFrame,
    title: str,
    width: float = 7.2 * inch,
    height: float = 3.2 * inch,
) -> Drawing:
    """Build a grouped vertical bar chart for the PDF report."""
    drawing = Drawing(width, height)
    if chart_df.empty:
        return drawing

    chart = VerticalBarChart()
    chart.x = 55
    chart.y = 45
    chart.width = width - 85
    chart.height = height - 85
    chart.data = [
        [float(value) for value in chart_df["Selected week"].tolist()],
        [float(value) for value in chart_df["Previous week"].tolist()],
    ]
    chart.categoryAxis.categoryNames = [str(value)[:18] for value in chart_df.index]
    chart.categoryAxis.labels.angle = 20
    chart.categoryAxis.labels.dy = -12
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    max_value = max(chart.data[0] + chart.data[1] + [1])
    chart.valueAxis.valueMax = max_value + max(1, max_value * 0.15)
    chart.valueAxis.valueStep = max(1, round(chart.valueAxis.valueMax / 5))
    chart.bars[0].fillColor = colors.HexColor("#1f77b4")
    chart.bars[1].fillColor = colors.HexColor("#7f7f7f")
    drawing.add(chart)

    styles = getSampleStyleSheet()
    drawing.add(
        Paragraph(
            _pdf_safe(title),
            ParagraphStyle(
                "BarTitle",
                parent=styles["Heading3"],
                alignment=TA_CENTER,
                fontSize=11,
                leading=13,
            ),
        )
    )
    return drawing


def _make_horizontal_bar_chart(
    chart_df: pd.DataFrame,
    title: str,
    label_column: str,
    width: float = 7.2 * inch,
    height: float = 4.2 * inch,
) -> Drawing:
    """Build a grouped horizontal bar chart for the PDF report."""
    drawing = Drawing(width, height)
    if chart_df.empty:
        return drawing

    limited_df = chart_df.head(12).copy()
    labels = [str(value)[:36] for value in limited_df[label_column].tolist()]
    chart = HorizontalBarChart()
    chart.x = 140
    chart.y = 35
    chart.width = width - 175
    chart.height = height - 80
    chart.data = [
        [float(value) for value in limited_df["Selected week"].tolist()],
        [float(value) for value in limited_df["Previous week"].tolist()],
    ]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    max_value = max(chart.data[0] + chart.data[1] + [1])
    chart.valueAxis.valueMax = max_value + max(1, max_value * 0.15)
    chart.valueAxis.valueStep = max(1, round(chart.valueAxis.valueMax / 5))
    chart.bars[0].fillColor = colors.HexColor("#1f77b4")
    chart.bars[1].fillColor = colors.HexColor("#7f7f7f")
    drawing.add(chart)

    styles = getSampleStyleSheet()
    drawing.add(
        Paragraph(
            _pdf_safe(title),
            ParagraphStyle(
                "HorizontalBarTitle",
                parent=styles["Heading3"],
                alignment=TA_CENTER,
                fontSize=11,
                leading=13,
            ),
        )
    )
    return drawing


def build_weekly_trend_pdf(package: dict) -> bytes:
    """Create a portable PDF version of the quantitative weekly report."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
        title="AIA Canada Weekly Quantitative Trend Report",
        author="AIA Canada Media Monitor",
    )

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontSize=20,
            leading=24,
            spaceAfter=12,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubtitle",
            parent=styles["Normal"],
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#555555"),
            alignment=TA_CENTER,
            spaceAfter=16,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallBody",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
        )
    )

    story = [
        Paragraph("AIA Canada Weekly Quantitative Trend Report", styles["ReportTitle"]),
        Paragraph(
            f"Selected week: {_pdf_safe(package.get('current_period'))} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Comparison week: {_pdf_safe(package.get('previous_period'))}",
            styles["ReportSubtitle"],
        ),
    ]

    metrics = package.get("metrics") or {}
    metric_rows = [
        ["Metric", "Selected week", "Previous week", "Change"],
        [
            "Mention volume",
            metrics.get("current_volume", 0),
            metrics.get("previous_volume", 0),
            format_metric_delta(metrics.get("current_volume", 0), metrics.get("previous_volume", 0)),
        ],
        [
            "Average sentiment",
            f"{metrics.get('current_average_sentiment', 0):.2f}",
            f"{metrics.get('previous_average_sentiment', 0):.2f}",
            format_metric_delta(
                metrics.get("current_average_sentiment", 0),
                metrics.get("previous_average_sentiment", 0),
            ),
        ],
        [
            "Positive mentions",
            metrics.get("current_positive", 0),
            metrics.get("previous_positive", 0),
            format_metric_delta(metrics.get("current_positive", 0), metrics.get("previous_positive", 0)),
        ],
        [
            "Negative mentions",
            metrics.get("current_negative", 0),
            metrics.get("previous_negative", 0),
            format_metric_delta(metrics.get("current_negative", 0), metrics.get("previous_negative", 0)),
        ],
        [
            "High/Critical",
            metrics.get("current_high_priority", 0),
            metrics.get("previous_high_priority", 0),
            format_metric_delta(
                metrics.get("current_high_priority", 0),
                metrics.get("previous_high_priority", 0),
            ),
        ],
        [
            "Unique outlets",
            metrics.get("current_unique_outlets", 0),
            metrics.get("previous_unique_outlets", 0),
            format_metric_delta(
                metrics.get("current_unique_outlets", 0),
                metrics.get("previous_unique_outlets", 0),
            ),
        ],
    ]
    metric_table = Table(metric_rows, colWidths=[2.2 * inch, 1.4 * inch, 1.4 * inch, 1.6 * inch], repeatRows=1)
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f8")]),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
            ]
        )
    )
    story.extend([Paragraph("Executive Metrics", styles["Heading2"]), metric_table, Spacer(1, 16)])

    interpretation = package.get("ai_interpretation") or "No interpretation available."
    for raw_line in str(interpretation).splitlines():
        line = raw_line.strip()
        if not line:
            story.append(Spacer(1, 5))
        elif line.startswith("## "):
            story.append(Paragraph(_pdf_safe(line[3:]), styles["Heading2"]))
        elif line.startswith("- "):
            story.append(Paragraph(f"• {_pdf_safe(line[2:])}", styles["BodyText"]))
        else:
            story.append(Paragraph(_pdf_safe(line), styles["BodyText"]))

    story.append(PageBreak())

    volume_df = pd.DataFrame(package.get("volume_chart") or {})
    sentiment_df = pd.DataFrame(package.get("sentiment_chart") or {})
    keyword_df = pd.DataFrame(package.get("keyword_table") or [])
    theme_df = pd.DataFrame(package.get("theme_chart") or {})
    outlet_df = pd.DataFrame(package.get("outlet_chart") or {})

    story.extend(
        [
            Paragraph("Charts and Trend Comparisons", styles["Heading1"]),
            _make_line_chart(volume_df, "Daily Mention Volume"),
            Spacer(1, 12),
            _make_vertical_bar_chart(sentiment_df, "Sentiment Distribution"),
            PageBreak(),
        ]
    )

    if not keyword_df.empty:
        story.extend(
            [
                _make_horizontal_bar_chart(
                    keyword_df.sort_values("Selected week", ascending=False),
                    "Keyword Mention Trends",
                    "Keyword",
                ),
                Spacer(1, 12),
            ]
        )

    if not theme_df.empty:
        theme_pdf_df = theme_df.rename_axis("Theme").reset_index()
        story.extend(
            [
                _make_horizontal_bar_chart(
                    theme_pdf_df.sort_values("Selected week", ascending=False),
                    "Theme Volume",
                    "Theme",
                ),
                PageBreak(),
            ]
        )

    if not outlet_df.empty:
        outlet_pdf_df = outlet_df.rename_axis("Outlet").reset_index()
        story.extend(
            [
                _make_horizontal_bar_chart(
                    outlet_pdf_df.sort_values("Selected week", ascending=False),
                    "Top Outlet Volume",
                    "Outlet",
                ),
                Spacer(1, 12),
            ]
        )

    detail_df = pd.DataFrame(package.get("detail_table") or [])
    if not detail_df.empty:
        story.append(Paragraph("Selected-Week Mention Detail", styles["Heading1"]))
        display_columns = [
            column
            for column in [
                "Published",
                "Outlet",
                "Title",
                "Theme",
                "Sentiment",
                "Score",
                "Alert",
                "Recommendation",
                "Assigned to",
            ]
            if column in detail_df.columns
        ]
        detail_df = detail_df[display_columns].fillna("")
        detail_rows = [
            [Paragraph(_pdf_safe(column), styles["SmallBody"]) for column in display_columns]
        ]
        for _, row in detail_df.iterrows():
            detail_rows.append(
                [
                    Paragraph(_pdf_safe(row[column]), styles["SmallBody"])
                    for column in display_columns
                ]
            )

        available_width = 10.0 * inch
        widths = []
        for column in display_columns:
            if column == "Title":
                widths.append(2.6 * inch)
            elif column in {"Outlet", "Theme", "Assigned to"}:
                widths.append(1.25 * inch)
            else:
                widths.append(0.9 * inch)
        scale = available_width / sum(widths)
        widths = [width * scale for width in widths]

        detail_table = Table(detail_rows, colWidths=widths, repeatRows=1)
        detail_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e78")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cccccc")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(detail_table)

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(36, 20, "AIA Canada Media Monitor")
        canvas.drawRightString(landscape(letter)[0] - 36, 20, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    return buffer.getvalue()


def build_weekly_report_mailto(
    package: dict,
    recipients: list[str],
    additional_message: str = "",
) -> str:
    """Build an Outlook-ready email draft for a weekly PDF report."""
    clean_recipients = []
    seen = set()
    for email in recipients:
        email = str(email or "").strip()
        if not email or email.lower() in seen:
            continue
        seen.add(email.lower())
        clean_recipients.append(email)

    metrics = package.get("metrics") or {}
    subject = f"AIA Canada Weekly Media Trend Report — {package.get('current_period', '')}"
    standard_message = (
        additional_message.strip()
        or "Hello,\n\nPlease find attached the AIA Canada Weekly Quantitative Media Trend Report."
    )
    body = (
        f"{standard_message}\n\n"
        f"Reporting period: {package.get('current_period', '')}\n"
        f"Comparison period: {package.get('previous_period', '')}\n"
        f"Mention volume: {metrics.get('current_volume', 0)}\n"
        f"Average sentiment: {metrics.get('current_average_sentiment', 0):.2f}\n"
        f"High/Critical mentions: {metrics.get('current_high_priority', 0)}\n\n"
        "The PDF must be downloaded from the Media Monitor and attached to this draft before sending.\n\n"
        "Regards,\nAIA Canada"
    )
    recipient_string = ",".join(clean_recipients)
    return (
        f"mailto:{quote(recipient_string, safe='@,')}"
        f"?subject={quote(subject)}"
        f"&body={quote(body)}"
    )


def render_weekly_pdf_share_controls(package: dict) -> None:
    """Render PDF download and Outlook draft controls for the weekly report."""
    st.markdown("---")
    st.markdown("### 📄 Export and Share")

    pdf_bytes = st.session_state.get("weekly_quantitative_pdf")
    if not pdf_bytes:
        try:
            pdf_bytes = build_weekly_trend_pdf(package)
            st.session_state["weekly_quantitative_pdf"] = pdf_bytes
        except Exception as exc:
            st.error(f"Unable to generate the weekly PDF: {exc}")
            return

    current_period = str(package.get("current_period") or "weekly-report")
    safe_period = re.sub(r"[^0-9A-Za-z_-]+", "_", current_period)
    filename = f"AIA_Canada_Weekly_Media_Trend_{safe_period}.pdf"

    st.download_button(
        "Download Weekly Trend PDF",
        data=pdf_bytes,
        file_name=filename,
        mime="application/pdf",
        use_container_width=True,
        key="download_weekly_trend_pdf",
    )

    registered_recipients = load_registered_email_recipients()
    recipient_map = {
        recipient["label"]: recipient["email"]
        for recipient in registered_recipients
    }
    selected_labels = st.multiselect(
        "Select registered recipients",
        options=list(recipient_map.keys()),
        key="weekly_report_email_recipients",
        placeholder="Select SLT members or other registered users",
    )
    additional_addresses = st.text_input(
        "Additional email addresses",
        key="weekly_report_additional_recipients",
        placeholder="external@example.com, another@example.com",
        help="Separate multiple addresses with commas or semicolons.",
    )
    standard_message = st.text_area(
        "Email message",
        value=(
            "Hello,\n\n"
            "Please find attached the AIA Canada Weekly Quantitative Media Trend Report "
            "for your review."
        ),
        height=110,
        key="weekly_report_email_message",
    )

    selected_emails = [recipient_map[label] for label in selected_labels]
    extra_emails = [
        email.strip()
        for email in additional_addresses.replace(";", ",").split(",")
        if email.strip()
    ]
    all_emails = selected_emails + extra_emails

    if all_emails:
        outlook_url = build_weekly_report_mailto(
            package=package,
            recipients=all_emails,
            additional_message=standard_message,
        )
        st.link_button(
            "Open Outlook Draft",
            outlook_url,
            use_container_width=True,
        )
    else:
        st.caption("Select at least one recipient to enable the Outlook draft button.")

    st.info(
        "Browser email links cannot attach local files automatically. "
        "Download the PDF first, open the Outlook draft, then attach the downloaded PDF. "
        "Automatic attachment requires Microsoft Graph integration and organizational OAuth approval."
    )


# --- 2. SIDEBAR UTILITIES & WORKFLOW TRIGGER ---
st.sidebar.title("📊 AIA Canada Monitor")
st.sidebar.caption(f"Operator: {st.session_state['user_full_name']} ({USER_ROLE})")

if st.sidebar.button("🔒 Sign Out / Lock Session", use_container_width=True):
    supabase.auth.sign_out()
    st.session_state["auth_user"] = None
    st.session_state["user_role"] = "Viewer"
    st.session_state["user_full_name"] = None
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("🔄 Manual Data Sync")

timeframe_label = st.sidebar.selectbox(
    "Select Search Horizon Window:",
    ["Past 24 Hours", "Past Week", "Past Month", "Past Year"],
    disabled=IS_VIEWER
)

timeframe_map = {
    "Past 24 Hours": "qdr:d",
    "Past Week": "qdr:w",
    "Past Month": "qdr:m",
    "Past Year": "qdr:y"
}
selected_tbs = timeframe_map[timeframe_label]

def get_latest_workflow_run_status():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/runs"
    headers = {
        "Authorization": f"Bearer {st.secrets['GITHUB_PAT']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    params = {"per_page": 1}
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            runs = response.json().get("workflow_runs", [])
            if runs:
                return runs[0].get("status"), runs[0].get("conclusion")
    except Exception:
        pass
    return None, None

def trigger_github_sync(tbs_val):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {st.secrets['GITHUB_PAT']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    data = {"ref": "main", "inputs": {"timeframe": tbs_val}} 
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            st.sidebar.success(f"🚀 Sync initiated for {timeframe_label}!")
            with st.spinner("Waiting for GitHub runner to complete search tasks..."):
                time.sleep(5)
                for _ in range(24): 
                    status, conclusion = get_latest_workflow_run_status()
                    if status == "completed":
                        st.sidebar.success("✅ Extraction complete! Refreshing database tables...")
                        time.sleep(1.5)
                        st.rerun()
                    elif status in ["queued", "in_progress"]:
                        time.sleep(5)
                    else:
                        break
                st.rerun()
        else:
            st.sidebar.error(f"❌ API Error: {response.status_code}")
    except Exception as e:
        st.sidebar.error(f"Connection failed: {e}")

if st.sidebar.button("Force Fetch Mentions Now", use_container_width=True, disabled=IS_VIEWER):
    with st.sidebar.spinner("Pinging GitHub Actions API..."):
        trigger_github_sync(selected_tbs)

st.sidebar.markdown("---")

MEDIA_CONTACTS = load_media_contacts()
CONTACT_NAMES = ["Unassigned", "➕ Add New Contact..."] + [f"{c['full_name']} ({c['outlet']})" for c in MEDIA_CONTACTS]
CONTACT_MAP = {f"{c['full_name']} ({c['outlet']})": c['id'] for c in MEDIA_CONTACTS}

app_mode = st.sidebar.radio(
    "Navigation Menu", 
    [
        "📥 Inbox / Triage", 
        "📋 Reviewed Database Table", 
        "📞 Media CRM & Inquiries", 
        "🚨 Daily Crisis Center", 
        "📝 Report Builder", 
        "💬 Ask AIA Media",
        "⚙️ System Settings Dashboard"
    ]
)

st.sidebar.markdown("---")
# --- 🔔 IN-APP NOTIFICATION CENTER ---
if st.session_state.get("user_full_name"):
    notif_res = supabase.table("notifications").select("*").eq("recipient_name", st.session_state["user_full_name"]).eq("is_read", False).order("created_at", desc=True).execute()
    unread_count = len(notif_res.data) if notif_res.data else 0
    
    if unread_count > 0:
        with st.sidebar.expander(f"🔔 Notifications ({unread_count} Unread)", expanded=True):
            for n in notif_res.data:
                st.markdown(f"**From:** {n['sender_name']}")
                st.caption(f"*{n['mention_title'][:40]}...*")
                st.info(f"💬 {n['message']}")
                
                # Use a button to safely update the URL without refreshing the page
                if n.get('mention_id'):
                    if st.button("🔗 Open Direct Record Viewer", key=f"view_{n['id']}", use_container_width=True):
                        st.query_params["mention_id"] = n['mention_id']
                        st.rerun()
                
                if st.button("✅ Mark as Read", key=f"read_{n['id']}", use_container_width=True):
                    supabase.table("notifications").update({"is_read": True}).eq("id", n['id']).execute()
                    st.rerun()
                st.markdown("---")
    else:
        st.sidebar.info("🔔 All caught up! No new notifications.")
st.sidebar.markdown("---")

# --- 🚀 URL DEEP LINK INTERCEPTOR ---
if "mention_id" in st.query_params:
    dl_id = st.query_params["mention_id"]
    st.subheader("🔍 Direct Record Viewer")
    
    if st.button("⬅️ Close Viewer & Return to Dashboard", type="primary"):
        st.query_params.clear()
        st.rerun()
        
    st.markdown("---")
    dl_res = supabase.table("mentions").select("*").eq("id", dl_id).execute()
    
    if not dl_res.data:
        st.error("This record could not be found. It may have been permanently deleted.")
    else:
        target_record = dl_res.data[0]
        
        st.markdown(f"### 📄 Full Metadata Profile: `{target_record['title']}`")
        st.info(f"🤖 **Gemini Strategic Action Recommendation:** {target_record.get('ai_action_recommendation', 'N/A')}")
        
        meta_col1, meta_col2, meta_col3 = st.columns(3)
        with meta_col1:
            st.markdown("**📌 Core Tracking Identifiers**")
            st.write(f"- **Database ID:** `{target_record['id']}`")
            st.write(f"- **Published Date:** `{target_record['date_published']}`")
            st.write(f"- **Direct URL:** [Open Live Web Link]({target_record['url']})")
        with meta_col2:
            st.markdown("**🏷️ Context & Scope Tags**")
            st.write(f"- **Brands Affected:** {', '.join(target_record['brands_affected']) if target_record['brands_affected'] else 'None mapped'}")
            st.write(f"- **Workflow State:** `{target_record['status']}`")
            st.write(f"- **Assigned Owner:** `{target_record['assigned_to_user'] or 'Unassigned'}`")
        with meta_col3:
            st.markdown("**🧠 Sentiment Metrics**")
            st.write(f"- **Tone Category:** `{target_record['sentiment_category']}`")
            st.write(f"- **Intensity Score:** `{target_record['sentiment_score']}`")
            st.write(f"- **Action Strategy:** `{target_record['recommendation']}`")
        
        st.markdown("**📝 Text Snippet & Analytical Explanations**")
        st.write(f"**Raw Snippet:** *\"{target_record['snippet']}\"*")
        st.write(f"**AI Rationale:** *{target_record['sentiment_rationale']}*")
        
        st.markdown("---")
        st.markdown("#### 📜 Actions Taken & Notes History Trail")
        actions_res = supabase.table("mention_actions").select("*").eq("mention_id", target_record['id']).order("inserted_at", desc=True).execute()
        
        if not actions_res.data:
            st.caption("No custom action notes logged for this profile yet.")
        else:
            history_df = pd.DataFrame(actions_res.data)
            history_df = history_df.rename(columns={"inserted_at": "Timestamp", "performed_by": "User", "action_note": "Action Details"})
            st.table(history_df[["Timestamp", "User", "Action Details"]])
        
        st.markdown("#### ✏️ Update Classification & Append New Action Log")
        e1, e2, e3, e4 = st.columns(4)
        with e1:
            current_rec_idx = ["monitor only", "engage", "share", "ignore"].index(target_record['recommendation']) if target_record['recommendation'] in ["monitor only", "engage", "share", "ignore"] else 0
            edit_rec = st.selectbox("Action Recommendation", ["monitor only", "engage", "share", "ignore"], index=current_rec_idx, key="dl_edit_rec", disabled=IS_VIEWER)
        with e2:
            current_lvl_idx = ["Low", "Medium", "High", "Critical"].index(target_record['alert_level']) if target_record['alert_level'] in ["Low", "Medium", "High", "Critical"] else 0
            edit_lvl = st.selectbox("Severity Framework", ["Low", "Medium", "High", "Critical"], index=current_lvl_idx, key="dl_edit_lvl", disabled=IS_VIEWER)
        with e3:
            current_stat_idx = ["pending", "logged", "escalated", "resolved"].index(target_record['status']) if target_record['status'] in ["pending", "logged", "escalated", "resolved"] else 0
            edit_stat = st.selectbox("Workflow State", ["pending", "logged", "escalated", "resolved"], index=current_stat_idx, key="dl_edit_stat", disabled=IS_VIEWER)
        with e4:
            current_user = target_record['assigned_to_user'] if target_record['assigned_to_user'] in TEAM_USERS else "Unassigned"
            edit_user = st.selectbox("Reassign Owner", TEAM_USERS, index=TEAM_USERS.index(current_user), key="dl_edit_user", disabled=IS_VIEWER)
            
        edit_note = st.text_input("Type new action note to append to history trail:", key="dl_edit_note_input", disabled=IS_VIEWER)

        # --- CRM AUTHOR ATTRIBUTION IN DEEP LINK ---
        st.markdown("#### ✍️ Author Attribution")
        current_auth_label = "Unassigned"
        if target_record.get('author_contact_id'):
            match = next((c for c in MEDIA_CONTACTS if c['id'] == target_record['author_contact_id']), None)
            if match:
                current_auth_label = f"{match['full_name']} ({match['outlet']})"
        
        author_sel = st.selectbox("Assign to Media Contact", CONTACT_NAMES, index=CONTACT_NAMES.index(current_auth_label) if current_auth_label in CONTACT_NAMES else 0, key=f"dl_auth_{target_record['id']}", disabled=IS_VIEWER)
        
        new_auth_name, new_auth_outlet = "", ""
        if author_sel == "➕ Add New Contact...":
            a1, a2 = st.columns(2)
            with a1:
                new_auth_name = st.text_input("New Contact Name*", key=f"dl_new_name_{target_record['id']}")
            with a2:
                new_auth_outlet = st.text_input("New Contact Outlet*", key=f"dl_new_out_{target_record['id']}")
        
        st.markdown("---")
        render_share_mention_controls(
            mention=target_record,
            key_prefix=f"direct_share_{target_record['id']}",
        )

        st.markdown("---")
        m1, m2 = st.columns([1, 4])
        with m1:
            if st.button("Save Changes", type="primary", use_container_width=True, key="dl_save_changes_btn", disabled=IS_VIEWER):
                current_user_name = st.session_state["user_full_name"]
                
                # Process Inline Author Creation
                final_contact_id = target_record.get('author_contact_id')
                if author_sel == "Unassigned":
                    final_contact_id = None
                elif author_sel == "➕ Add New Contact...":
                    if new_auth_name.strip() and new_auth_outlet.strip():
                        new_c = supabase.table("media_contacts").insert({"full_name": new_auth_name.strip(), "outlet": new_auth_outlet.strip()}).execute()
                        final_contact_id = new_c.data[0]['id']
                else:
                    final_contact_id = CONTACT_MAP[author_sel]

                if edit_note.strip():
                    add_action_note(target_record['id'], edit_note, current_user_name)
                    
                # Fire Notification if re-assigned
                if edit_user != "Unassigned":
                    send_assignment_notification(target_record['id'], target_record['title'], edit_user, current_user_name, edit_note)

                supabase.table("mentions").update({
                    "recommendation": edit_rec,
                    "alert_level": edit_lvl,
                    "status": edit_stat,
                    "assigned_to_user": edit_user if edit_user != "Unassigned" else None,
                    "author_contact_id": final_contact_id
                }).eq("id", target_record['id']).execute()
                st.rerun()
        with m2:
            if st.button("🗑 Permanent Deletion", type="secondary", key="dl_perm_delete_btn", disabled=IS_VIEWER):
                delete_mention_record(target_record['id'])
                st.query_params.clear()
                st.rerun()
    st.stop() # This entirely pauses the rest of the app from rendering while viewing a deep link!

# --- 3. MODULE 1: INBOX / TRIAGE ---
if app_mode == "📥 Inbox / Triage":
    st.subheader("📥 Unprocessed Mention Queue")
    st.write("Review, assign, and triage incoming raw tracking records. Use the checkboxes to delete items in bulk.")
    
    response = supabase.table("mentions").select("*").eq("status", "pending").order("date_published", desc=True).execute()
    mentions = response.data
    
    if not mentions:
        st.success("All caught up! No pending un-triaged mentions found in the queue.")
    else:
        pending_df = pd.DataFrame(mentions)
        triage_display_cols = ["outlet_platform", "title", "date_published", "theme", "sentiment_category"]
        
        triage_selection = st.dataframe(
            pending_df[triage_display_cols],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row"
        )
        
        selected_rows = triage_selection.get("selection", {}).get("rows", [])
        
        if len(selected_rows) > 0:
            st.markdown(f"### 🛠️ Bulk Actions ({len(selected_rows)} items selected)")
            bulk_c1, bulk_c2 = st.columns([1, 4])
            with bulk_c1:
                if st.button("🗑️ Bulk Delete Selected", type="primary", use_container_width=True):
                    with st.spinner("Wiping items from queue..."):
                        for row_idx in selected_rows:
                            target_id = pending_df.iloc[row_idx]["id"]
                            supabase.table("mentions").delete().eq("id", target_id).execute()
                    st.toast(f"Successfully deleted {len(selected_rows)} mentions.")
                    time.sleep(1)
                    st.rerun()
            st.markdown("---")
            
        st.markdown("### 📄 Detailed Classification Workspaces")
        for m in mentions:
            # INJECTED HTML ANCHOR TARGET FOR REPORT DEEP LINKS
            st.markdown(f'<div id="{m["id"]}"></div>', unsafe_allow_html=True)
            
            with st.expander(f"🔍 {m['outlet_platform']} | {m['title']} (Published: {m['date_published']})"):
                st.info(f"🤖 **Gemini Strategic Action Recommendation:** {m.get('ai_action_recommendation', 'N/A')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**URL:** [Open Source]({m['url']})")
                    st.write(f"**Brands Affected:** {', '.join(m['brands_affected']) if m['brands_affected'] else 'None'}")
                    st.write(f"**Theme:** {m['theme']}")
                    st.write(f"**Snippet:** *\"{m['snippet']}\"*")
                with col2:
                    st.write(f"**Inferred Sentiment:** `{m['sentiment_category']}` (Score: {m['sentiment_score']})")
                    st.write(f"**Rationale:** {m['sentiment_rationale']}")
                    if m['naming_error_flag'] or m['data_conflict_flag']:
                        st.warning(f"⚠️ **Quality Flag Raised:** {m['data_conflict_details'] or 'Branding variation error.'}")
                
                st.markdown("---")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    new_rec = st.selectbox("Assign Action Item", ["monitor only", "engage", "share", "ignore"], index=0, key=f"rec_{m['id']}", disabled=IS_VIEWER)
                with c2:
                    new_level = st.selectbox("Assign Severity Level", ["Low", "Medium", "High", "Critical"], key=f"lvl_{m['id']}", disabled=IS_VIEWER)
                with c3:
                    assignee = st.selectbox("Assign to Team User", TEAM_USERS, key=f"user_{m['id']}", disabled=IS_VIEWER)
                with c4:
                    escalation_target = st.selectbox("If Escalated, Route to", TEAM_USERS, key=f"esc_{m['id']}", disabled=IS_VIEWER)
                
                note_text = st.text_input("Log Action Taken / Progress Note:", key=f"note_input_{m['id']}", placeholder="Type out workflow changes or notes here...", disabled=IS_VIEWER)
                
                # --- NEW AUTHOR ATTRIBUTION BLOCK ---
                st.markdown("#### ✍️ Author Attribution")
                current_auth_label = "Unassigned"
                if m.get('author_contact_id'):
                    match = next((c for c in MEDIA_CONTACTS if c['id'] == m['author_contact_id']), None)
                    if match:
                        current_auth_label = f"{match['full_name']} ({match['outlet']})"
                
                author_sel = st.selectbox("Assign to Media Contact", CONTACT_NAMES, index=CONTACT_NAMES.index(current_auth_label) if current_auth_label in CONTACT_NAMES else 0, key=f"auth_{m['id']}", disabled=IS_VIEWER)
                
                new_auth_name, new_auth_outlet = "", ""
                if author_sel == "➕ Add New Contact...":
                    a1, a2 = st.columns(2)
                    with a1:
                        new_auth_name = st.text_input("New Contact Name*", key=f"new_name_{m['id']}")
                    with a2:
                        new_auth_outlet = st.text_input("New Contact Outlet*", key=f"new_out_{m['id']}")
                
                st.markdown("---")
                b1, b2, b3 = st.columns([2, 2, 1])
                with b1:
                    if st.button("Commit Classification & Update Status", key=f"btn_{m['id']}", use_container_width=True, disabled=IS_VIEWER):
                        determined_status = "escalated" if new_level in ["High", "Critical"] else "logged"
                        current_user_name = st.session_state["user_full_name"]
                        
                        # Process Inline Author Creation
                        final_contact_id = m.get('author_contact_id')
                        if author_sel == "Unassigned":
                            final_contact_id = None
                        elif author_sel == "➕ Add New Contact...":
                            if new_auth_name.strip() and new_auth_outlet.strip():
                                new_c = supabase.table("media_contacts").insert({"full_name": new_auth_name.strip(), "outlet": new_auth_outlet.strip()}).execute()
                                final_contact_id = new_c.data[0]['id']
                        else:
                            final_contact_id = CONTACT_MAP[author_sel]
                        
                        if note_text.strip():
                            add_action_note(m['id'], f"Initial Triage Note: {note_text}", current_user_name)
                        
                        # Trigger the new notification
                        if assignee != "Unassigned":
                            send_assignment_notification(m['id'], m['title'], assignee, current_user_name, note_text)
                        
                        supabase.table("mentions").update({
                            "recommendation": new_rec,
                            "alert_level": new_level,
                            "status": determined_status,
                            "assigned_to_user": assignee if assignee != "Unassigned" else None,
                            "escalated_to_user": escalation_target if escalation_target != "Unassigned" else None,
                            "author_contact_id": final_contact_id
                        }).eq("id", m['id']).execute()
                        st.rerun()
                with b2:
                    if st.button("Add Progress Note Only", key=f"note_btn_{m['id']}", use_container_width=True, disabled=IS_VIEWER):
                        if note_text.strip():
                            add_action_note(m['id'], note_text, current_user_name)
                            st.rerun()
                        else:
                            st.error("Note text field cannot be blank.")
                with b3:
                    if st.button("🗑️ Delete Mention", key=f"del_{m['id']}", use_container_width=True, disabled=IS_VIEWER):
                        delete_mention_record(m['id'])
                        st.rerun()

# --- MODULE 2: REVIEWED DATABASE TABLE ---
elif app_mode == "📋 Reviewed Database Table":
    st.subheader("📋 Reviewed Mentions Archive")
    st.write("Use search filters to populate records by actual publication date. Click on any row to view details.")

    f1, f2, f3, f4 = st.columns([2, 2, 3, 2])

    with f1:
        start_date = st.date_input(
            "Start Date (Published)",
            datetime.now().date() - timedelta(days=7),
            key="reviewed_start_date",
        )

    with f2:
        end_date = st.date_input(
            "End Date (Published)",
            datetime.now().date(),
            key="reviewed_end_date",
        )

    with f3:
        search_kw = st.text_input(
            "Search Title or Snippet Keyword",
            placeholder="Type a term...",
            key="reviewed_search_kw",
        )

    with f4:
        reviewed_owner_options = ["All", "Unassigned"] + [user for user in TEAM_USERS if user != "Unassigned"]
        selected_assigned_user = st.selectbox(
            "Assigned To",
            reviewed_owner_options,
            key="reviewed_assigned_user_filter",
        )

    query = (
        supabase.table("mentions")
        .select("*")
        .in_("status", ["logged", "escalated", "resolved"])
        .gte("date_published", start_date.isoformat())
        .lte("date_published", end_date.isoformat())
    )

    if selected_assigned_user == "Unassigned":
        query = query.is_("assigned_to_user", "null")
    elif selected_assigned_user != "All":
        query = query.eq("assigned_to_user", selected_assigned_user)

    response = query.order("date_published", desc=True).execute()
    reviewed_data = response.data

    if not reviewed_data:
        st.info("No reviewed tracking logs match the specified publication parameters.")
    else:
        df = pd.DataFrame(reviewed_data)

        if search_kw:
            df = df[
                df["title"].str.contains(search_kw, case=False, na=False)
                | df["snippet"].str.contains(search_kw, case=False, na=False)
            ]

        if df.empty:
            st.warning("No records found matching that keyword combination.")
        else:
            display_columns = [
                "date_published", "outlet_platform", "title", "theme",
                "sentiment_category", "sentiment_score", "alert_level",
                "status", "assigned_to_user", "escalated_to_user", "recommendation"
            ]

            selection = st.dataframe(
                df[display_columns],
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row"
            )
            
            st.markdown("---")
            st.subheader("🛠️ Active Record Management & Notes Editor")
            
            if selection and len(selection.get("selection", {}).get("rows", [])) > 0:
                selected_row_idx = selection["selection"]["rows"][0]
                target_record = df.iloc[selected_row_idx].to_dict()
                
                # INJECTED HTML ANCHOR TARGET FOR REPORT LINKS
                st.markdown(f'<div id="{target_record["id"]}"></div>', unsafe_allow_html=True)
                
                st.markdown(f"### 📄 Metadata Profile: `{target_record['title']}`")
                st.info(f"🤖 **AI Strategic Action Recommendation for this Mention:** {target_record.get('ai_action_recommendation', 'N/A')}")
                
                meta_col1, meta_col2, meta_col3 = st.columns(3)
                with meta_col1:
                    st.markdown("**📌 Core Tracking Identifiers**")
                    st.write(f"- **Discovered Date:** `{target_record['inserted_at']}`")
                    st.write(f"- **Official Publication Date:** `{target_record['date_published']}`")
                    st.write(f"- **Direct Source URL:** [Open Live Web Link]({target_record['url']})")
                
                with meta_col2:
                    st.markdown("**🏷️ Corporate Context & Scope Tags**")
                    st.write(f"- **Brands Explicitly Affected:** {', '.join(target_record['brands_affected']) if target_record['brands_affected'] else 'None mapped'}")
                    st.write(f"- **Theme Classification:** `{target_record['theme']}`")
                    st.write(f"- **Workflow State:** `{target_record['status']}`")
                    st.write(f"- **Assigned Active Owner:** `{target_record['assigned_to_user'] or 'Unassigned'}`")
                    st.write(f"- **Escalation Target Recipient:** `{target_record['escalated_to_user'] or 'None assigned'}`")
                
                with meta_col3:
                    st.markdown("**🧠 Sentiment Metrics & Quality Flags**")
                    st.write(f"- **Inferred Tone Category:** `{target_record['sentiment_category']}`")
                    st.write(f"- **Sentiment Intensity Score (-1.0 to 1.0):** `{target_record['sentiment_score']}`")
                    st.write(f"- **Action Recommendation Strategy:** `{target_record['recommendation']}`")
                
                st.markdown("**📝 Text Snippet & Analytical Explanations**")
                st.write(f"**Raw Text Excerpt Snippet:** *\"{target_record['snippet']}\"*")
                st.write(f"**AI Sentiment Rationale:** *{target_record['sentiment_rationale']}*")
                
                st.markdown("---")
                st.markdown("#### 📜 Actions Taken & Notes History Trail")
                actions_res = supabase.table("mention_actions").select("*").eq("mention_id", target_record['id']).order("inserted_at", desc=True).execute()
                
                if not actions_res.data:
                    st.caption("No custom action notes logged for this profile yet.")
                else:
                    history_df = pd.DataFrame(actions_res.data)
                    history_df = history_df.rename(columns={"inserted_at": "Timestamp", "performed_by": "User", "action_note": "Action Details"})
                    st.table(history_df[["Timestamp", "User", "Action Details"]])
                
                st.markdown("#### ✏️ Update Classification & Append New Action Log")
                e1, e2, e3, e4 = st.columns(4)
                with e1:
                    current_rec_idx = ["monitor only", "engage", "share", "ignore"].index(target_record['recommendation']) if target_record['recommendation'] in ["monitor only", "engage", "share", "ignore"] else 0
                    edit_rec = st.selectbox("Action Recommendation", ["monitor only", "engage", "share", "ignore"], index=current_rec_idx, key="edit_rec", disabled=IS_VIEWER)
                with e2:
                    current_lvl_idx = ["Low", "Medium", "High", "Critical"].index(target_record['alert_level']) if target_record['alert_level'] in ["Low", "Medium", "High", "Critical"] else 0
                    edit_lvl = st.selectbox("Severity Framework", ["Low", "Medium", "High", "Critical"], index=current_lvl_idx, key="edit_lvl", disabled=IS_VIEWER)
                with e3:
                    current_stat_idx = ["logged", "escalated", "resolved"].index(target_record['status']) if target_record['status'] in ["logged", "escalated", "resolved"] else 0
                    edit_stat = st.selectbox("Workflow State", ["logged", "escalated", "resolved"], index=current_stat_idx, key="edit_stat", disabled=IS_VIEWER)
                with e4:
                    current_user = target_record['assigned_to_user'] if target_record['assigned_to_user'] in TEAM_USERS else "Unassigned"
                    edit_user = st.selectbox("Reassign Owner", TEAM_USERS, index=TEAM_USERS.index(current_user), key="edit_user", disabled=IS_VIEWER)
                    
                edit_note = st.text_input("Type new action note to append to history trail:", key="edit_note_input", disabled=IS_VIEWER)
                
                # --- NEW AUTHOR ATTRIBUTION BLOCK ---
                st.markdown("#### ✍️ Author Attribution")
                current_auth_label = "Unassigned"
                if target_record.get('author_contact_id'):
                    match = next((c for c in MEDIA_CONTACTS if c['id'] == target_record['author_contact_id']), None)
                    if match:
                        current_auth_label = f"{match['full_name']} ({match['outlet']})"
                
                author_sel = st.selectbox("Assign to Media Contact", CONTACT_NAMES, index=CONTACT_NAMES.index(current_auth_label) if current_auth_label in CONTACT_NAMES else 0, key=f"edit_auth_{target_record['id']}", disabled=IS_VIEWER)
                
                new_auth_name, new_auth_outlet = "", ""
                if author_sel == "➕ Add New Contact...":
                    a1, a2 = st.columns(2)
                    with a1:
                        new_auth_name = st.text_input("New Contact Name*", key=f"edit_new_name_{target_record['id']}")
                    with a2:
                        new_auth_outlet = st.text_input("New Contact Outlet*", key=f"edit_new_out_{target_record['id']}")
                
                st.markdown("---")
                render_share_mention_controls(
                    mention=target_record,
                    key_prefix=f"reviewed_share_{target_record['id']}",
                )

                st.markdown("---")
                m1, m2 = st.columns([1, 4])
                with m1:
                    if st.button("Save Changes", type="primary", use_container_width=True, key="save_changes_btn", disabled=IS_VIEWER):
                        current_user_name = st.session_state["user_full_name"]
                        
                        # Process Inline Author Creation
                        final_contact_id = target_record.get('author_contact_id')
                        if author_sel == "Unassigned":
                            final_contact_id = None
                        elif author_sel == "➕ Add New Contact...":
                            if new_auth_name.strip() and new_auth_outlet.strip():
                                new_c = supabase.table("media_contacts").insert({"full_name": new_auth_name.strip(), "outlet": new_auth_outlet.strip()}).execute()
                                final_contact_id = new_c.data[0]['id']
                        else:
                            final_contact_id = CONTACT_MAP[author_sel]
                            
                        if edit_note.strip():
                            add_action_note(target_record['id'], edit_note, current_user_name)
                            
                        # Trigger notification if reassigning or updating someone else's active ticket
                        if edit_user != "Unassigned":
                            send_assignment_notification(target_record['id'], target_record['title'], edit_user, current_user_name, edit_note)
                            
                        supabase.table("mentions").update({
                            "recommendation": edit_rec,
                            "alert_level": edit_lvl,
                            "status": edit_stat,
                            "assigned_to_user": edit_user if edit_user != "Unassigned" else None,
                            "author_contact_id": final_contact_id
                        }).eq("id", target_record['id']).execute()
                        st.rerun()
                with m2:
                    if st.button("🗑 Permanent Deletion", type="secondary", key="perm_delete_btn", disabled=IS_VIEWER):
                        delete_mention_record(target_record['id'])
                        st.rerun()
            else:
                st.caption("💡 Click on any processed item row inside the tracking matrix above to reveal its parameters.")

# --- MODULE 3: DAILY CRISIS CENTER ---
elif app_mode == "🚨 Daily Crisis Center":
    st.subheader("🚨 Daily Crisis Alert Generator")
    response = supabase.table("mentions").select("*").in_("alert_level", ["High", "Critical"]).neq("status", "resolved").execute()
    crisis_items = response.data
    
    if not crisis_items:
        st.success("No high-severity or critical items require attention.")
    else:
        selected_title = st.selectbox("Select high-priority mention to format:", [c['title'] for c in crisis_items])
        item = next(c for c in crisis_items if c['title'] == selected_title)
        
        subject_line = f"SUBJECT: [{item['alert_level'].upper()} RISK ALERT] {item['title']} - {item['date_published']}"
        st.text_input("Recommended Subject Line", subject_line)
        
        markdown_body = f"""**Alert Level:** {item['alert_level']}
**Brand(s) affected:** {', '.join(item['brands_affected'])}
**Source:** {item['outlet_platform']} - [Source Link]({item['url']})
**Assigned Owner:** {item['assigned_to_user'] or 'Unassigned'}
**Escalated Recipient:** {item['escalated_to_user'] or 'None Specified'}

### WHAT HAPPENED
{item['snippet']}

### WHY IT MATTERS
* **Sentiment:** {item['sentiment_category']}
* **Context Rationale:** {item['sentiment_rationale']}
"""
        st.text_area("Markdown Summary Text Content", markdown_body, height=300)

# --- MODULE 4: AI REPORT BUILDER ---
elif app_mode == "📝 Report Builder":
    st.subheader("📝 Automated Executive Reporting")
    st.write("Generate AI-driven summaries based on the raw tracking data in your database.")
    
    tab_daily, tab_weekly = st.tabs(["📅 Daily Triage Rollup", "🗓️ Weekly Trend Summary"])
    
    # --- DAILY REPORT TAB ---
    with tab_daily:
        st.markdown("### Generate Daily Media Report")
        st.write("Compiles a summary of all items that were successfully processed and cleared from the inbox for a specific date.")
        
        target_date = st.date_input("Select Processing Date", datetime.now().date(), key="daily_date_picker")
        
        # Pull the custom daily prompt from Supabase
        try:
            tmpl_res = (
                supabase.table("monitor_templates")
                .select("*")
                .eq("template_name", "Daily Triage Rollup")
                .execute()
            )
            stored_daily_instruction = (
                tmpl_res.data[0]["system_instruction_prompt"]
                if tmpl_res.data
                else ""
            )
        except Exception:
            stored_daily_instruction = ""

        daily_instruction = f"""
{stored_daily_instruction}

MANDATORY DAILY REPORT OUTPUT RULES:
- Begin the report with a section titled exactly "## Executive Summary".
- The Executive Summary must be 3 to 5 concise sentences written for senior
  leadership.
- Summarize the day's overall media volume, dominant themes, overall sentiment,
  highest-priority issue, affected AIA Canada sub-brands, and any immediate
  action required.
- Do not list every mention in the Executive Summary.
- Do not invent trends, risks, actions, or conclusions not supported by the
  supplied records.
- After the Executive Summary, continue with the detailed daily roundup using
  the structure requested by the saved report template.
- The supplied records have already been filtered for report eligibility.
- Do not include, summarize, count, reference, or create a section for noise.
- Do not create a section named "Filtered Noise".
- Do not include records whose status is noise.
- Do not include records whose recommendation is ignore.
- Build the report only from the eligible records supplied in the user message.
- If an earlier instruction conflicts with these output or exclusion rules,
  these rules take precedence.
"""

        if st.button(
            "Generate Daily Rollup",
            use_container_width=True,
            type="primary",
            key="generate_daily_rollup",
        ):
            st.session_state.pop("latest_daily_report", None)

            with st.spinner("Extracting records for the selected daily report..."):
                try:
                    daily_mentions = load_daily_report_mentions(target_date)

                    if not daily_mentions:
                        st.warning(
                            "No mentions were inserted or explicitly included "
                            "for this daily report date."
                        )
                    else:
                        executive_summary_instruction = """
You are the senior media monitoring analyst for AIA Canada.

Write only the Executive Summary for a daily media report.

Mandatory requirements:
- Write 3 to 5 concise sentences for senior leadership.
- State the total number of eligible mentions.
- Identify the dominant themes and overall sentiment.
- Identify the highest-priority issue, if one exists.
- Name the affected AIA Canada sub-brands supported by the records.
- State any immediate action required.
- Do not include a heading.
- Do not use bullet points or tables.
- Do not mention noise, ignored records or excluded records.
- Do not invent facts.
"""

                        summary_response = ai_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[
                                (
                                    f"Daily report date: {target_date.isoformat()}\n"
                                    f"Eligible mention count: {len(daily_mentions)}\n\n"
                                    f"Eligible daily media records:\n{daily_mentions}"
                                )
                            ],
                            config=types.GenerateContentConfig(
                                system_instruction=executive_summary_instruction
                            ),
                        )

                        detail_response = ai_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[
                                (
                                    f"Daily report date: {target_date.isoformat()}\n\n"
                                    "Prepare the detailed daily roundup from the eligible "
                                    "records below. Do not mention noise or ignored records. "
                                    "Do not create a Filtered Noise section.\n\n"
                                    f"Eligible daily media records:\n{daily_mentions}"
                                )
                            ],
                            config=types.GenerateContentConfig(
                                system_instruction=daily_instruction
                            ),
                        )

                        executive_summary = (
                            summary_response.text.strip()
                            if summary_response.text
                            else (
                                f"{len(daily_mentions)} eligible media mention(s) "
                                "were included in this daily report. "
                                "The available records did not produce an AI-generated "
                                "executive summary."
                            )
                        )
                        detailed_report = remove_existing_executive_summary(
                            detail_response.text
                        )

                        final_report = (
                            "## Executive Summary\n\n"
                            f"{executive_summary}\n\n"
                            f"{detailed_report}"
                        ).strip()

                        st.session_state["latest_daily_report"] = final_report
                        st.success(
                            f"Daily report generated from "
                            f"{len(daily_mentions)} mention(s)."
                        )
                except Exception as exc:
                    st.error(f"Daily report generation failed: {exc}")

        if "latest_daily_report" in st.session_state:
            st.markdown("---")
            st.markdown(st.session_state["latest_daily_report"])

    # --- WEEKLY REPORT TAB ---
    with tab_weekly:
        st.markdown("### Weekly Quantitative Trend Report")
        st.write(
            "Compare a selected seven-day period with the immediately preceding "
            "seven days. Metrics are calculated directly from the mentions database."
        )

        weekly_end_date = st.date_input(
            "Select Week Ending Date",
            value=datetime.now().date(),
            key="weekly_quantitative_end_date",
        )
        weekly_start_date = weekly_end_date - timedelta(days=6)
        previous_end_date = weekly_start_date - timedelta(days=1)
        previous_start_date = previous_end_date - timedelta(days=6)

        st.caption(
            f"Selected week: {weekly_start_date.isoformat()} through "
            f"{weekly_end_date.isoformat()} | Previous week: "
            f"{previous_start_date.isoformat()} through "
            f"{previous_end_date.isoformat()}."
        )

        if st.button(
            "Generate Quantitative Weekly Report",
            use_container_width=True,
            type="primary",
            key="generate_quantitative_weekly_report",
        ):
            st.session_state.pop("weekly_quantitative_package", None)
            st.session_state.pop("weekly_quantitative_pdf", None)

            try:
                with st.spinner("Calculating week-over-week media trends..."):
                    current_records = fetch_reportable_mentions(
                        weekly_start_date,
                        weekly_end_date,
                    )
                    previous_records = fetch_reportable_mentions(
                        previous_start_date,
                        previous_end_date,
                    )
                    keyword_rows = load_monitoring_keywords()

                    if not current_records and not previous_records:
                        st.warning(
                            "No reportable mentions were inserted during either "
                            "comparison period."
                        )
                    else:
                        package = build_weekly_quantitative_package(
                            current_records=current_records,
                            previous_records=previous_records,
                            keyword_rows=keyword_rows,
                            current_start=weekly_start_date,
                            current_end=weekly_end_date,
                            previous_start=previous_start_date,
                            previous_end=previous_end_date,
                        )

                        package["ai_interpretation"] = (
                            generate_weekly_quantitative_interpretation(package)
                        )
                        st.session_state["weekly_quantitative_package"] = package
                        st.success(
                            "Quantitative weekly report generated from "
                            f"{len(current_records)} selected-week mention(s) and "
                            f"{len(previous_records)} previous-week mention(s)."
                        )
            except Exception as exc:
                st.error(f"Weekly quantitative report failed: {exc}")

        if "weekly_quantitative_package" in st.session_state:
            st.markdown("---")
            render_weekly_quantitative_report(
                st.session_state["weekly_quantitative_package"]
            )
            render_weekly_pdf_share_controls(
                st.session_state["weekly_quantitative_package"]
            )

# --- MODULE 5: DATABASE Q&A ASSISTANT ---
elif app_mode == "💬 Ask AIA Media":
    st.subheader("💬 Ask AIA Media")
    st.write(
        "Ask questions across all fields in the application database. "
        "Optionally restrict timestamped records by date, or search the complete history."
    )

    use_date_filter = st.checkbox(
        "Restrict timestamped records to a date range",
        value=False,
        key="ask_aia_use_date_filter",
    )

    ask_start_date = None
    ask_end_date = None

    if use_date_filter:
        date_col1, date_col2 = st.columns(2)

        with date_col1:
            ask_start_date = st.date_input(
                "Start insertion date",
                value=datetime.now().date() - timedelta(days=7),
                key="ask_aia_start_date",
            )

        with date_col2:
            ask_end_date = st.date_input(
                "End insertion date",
                value=datetime.now().date(),
                key="ask_aia_end_date",
            )

        st.caption(
            "The range applies to inserted_at or created_at where those fields exist. "
            "Reference tables without timestamps are included in full."
        )

    user_query = st.text_area(
        "Enter your database question",
        placeholder=(
            "Examples: Which mentions required action last month? "
            "Which reporters have open inquiries? "
            "What actions were recorded for CCIF coverage?"
        ),
        key="ask_aia_query",
        height=120,
    )

    if st.button(
        "Search Complete Database",
        type="primary",
        use_container_width=True,
        key="ask_aia_submit",
    ):
        if not user_query.strip():
            st.error("Enter a question before searching.")
        elif use_date_filter and ask_start_date > ask_end_date:
            st.error("The start date cannot be after the end date.")
        else:
            try:
                with st.spinner("Loading all matching rows and fields..."):
                    database_context, table_errors = load_complete_ask_aia_context(
                        use_date_filter=use_date_filter,
                        start_date=ask_start_date,
                        end_date=ask_end_date,
                    )

                table_counts = {
                    table_name: len(rows)
                    for table_name, rows in database_context.items()
                }
                total_records = sum(table_counts.values())

                if total_records == 0:
                    st.warning("No database records matched the selected scope.")
                else:
                    context_chunks = split_database_context(database_context)
                    progress = st.progress(0)
                    status_text = st.empty()
                    extracted_evidence: list[str] = []

                    for index, chunk_text in enumerate(context_chunks):
                        status_text.write(
                            f"Analysing database batch {index + 1} "
                            f"of {len(context_chunks)}..."
                        )
                        extracted_evidence.append(
                            analyse_database_chunk(
                                user_question=user_query.strip(),
                                chunk_text=chunk_text,
                                chunk_number=index + 1,
                                total_chunks=len(context_chunks),
                            )
                        )
                        progress.progress((index + 1) / len(context_chunks))

                    if use_date_filter:
                        date_scope = (
                            f"Timestamped records from {ask_start_date.isoformat()} "
                            f"through {ask_end_date.isoformat()}; untimestamped "
                            "reference tables included in full."
                        )
                    else:
                        date_scope = (
                            "Complete available application history; "
                            "no date filter was applied."
                        )

                    status_text.write("Preparing final answer...")
                    final_answer = generate_final_ask_aia_answer(
                        user_question=user_query.strip(),
                        extracted_evidence=extracted_evidence,
                        table_counts=table_counts,
                        table_errors=table_errors,
                        date_scope=date_scope,
                    )

                    st.session_state["ask_aia_latest_answer"] = final_answer
                    st.session_state["ask_aia_table_counts"] = table_counts
                    st.session_state["ask_aia_table_errors"] = table_errors
                    st.session_state["ask_aia_date_scope"] = date_scope

                    status_text.empty()
                    progress.empty()

            except Exception as exc:
                st.error(f"Ask AIA Media failed: {exc}")

    if "ask_aia_latest_answer" in st.session_state:
        st.markdown("---")
        st.markdown("### Answer")
        st.markdown(st.session_state["ask_aia_latest_answer"])

        with st.expander("Database coverage used for this answer"):
            st.write(st.session_state["ask_aia_date_scope"])

            coverage_df = pd.DataFrame(
                [
                    {"Table": table_name, "Rows reviewed": row_count}
                    for table_name, row_count
                    in st.session_state["ask_aia_table_counts"].items()
                ]
            )
            st.dataframe(coverage_df, use_container_width=True, hide_index=True)

            table_errors = st.session_state.get("ask_aia_table_errors", {})
            if table_errors:
                st.warning("Some tables could not be read.")
                for table_name, error_text in table_errors.items():
                    st.write(f"- **{table_name}:** {error_text}")

# --- MODULE 6: SYSTEM SETTINGS DASHBOARD ---
elif app_mode == "⚙️ System Settings Dashboard":
    st.subheader("⚙️ System Settings & Parameter Tuning Dashboard")
    
    tab_users, tab_keywords, tab_templates = st.tabs(["👥 User Accounts & Role Permissions", "🔑 Search Keywords & Phrases", "📝 AI Report Templates"])
    
    # 1. USER ACCOUNTS
    with tab_users:
        st.markdown("### 👥 Manage Active Platform Operators & Auth Credentials")
        
        if IS_ADMIN:
            with st.form("create_user_security_form", clear_on_submit=True):
                st.markdown("**Provision New Secured Account Profile**")
                new_full_name = st.text_input("Full Display Name / Team Label")
                new_email = st.text_input("Corporate Email Address")
                new_password = st.text_input("Initial System Password", type="password")
                new_role = st.selectbox("Access Privilege Scope", ["Administrator", "Editor", "Viewer"])
                
                if st.form_submit_button("Provision User Account"):
                    if new_full_name.strip() and new_email.strip() and len(new_password) >= 6:
                        try:
                            # --- ADMIN OVERRIDE FOR USER CREATION ---
                            # This bypasses the security blocker by using your Service Role Key
                            admin_client = create_client(
                                st.secrets["SUPABASE_URL"], 
                                st.secrets["SUPABASE_SERVICE_ROLE_KEY"] 
                            )
                            
                            auth_res = admin_client.auth.admin.create_user({
                                "email": new_email,
                                "password": new_password,
                                "email_confirm": True
                            })
                            
                            # Log them into your app's user dropdown roster
                            supabase.table("monitor_users").insert({
                                "user_id": auth_res.user.id,
                                "full_name": new_full_name,
                                "tracking_email": new_email,
                                "user_role": new_role
                            }).execute()
                            
                            st.success(f"Successfully provisioned login access for {new_full_name}!")
                            st.rerun()
                        except Exception as err:
                            st.error(f"Provisioning Rejected: {err}")
                    else:
                        st.warning("All values must be filled. Passwords must be at least 6 characters.")
        else:
            st.info("ℹ️ Account provisioning frameworks are restricted to system Administrators.")

        st.markdown("---")
        st.markdown("### 🛠️ Profile Management & Security Resets")
        u_res = supabase.table("monitor_users").select("*").order("full_name").execute()
        auth_login_status = load_auth_login_status() if IS_ADMIN else {}

        if u_res.data:
            for current_row in u_res.data:
                user_id = str(current_row["user_id"])
                is_own_profile = (
                    st.session_state.get("auth_user")
                    and str(st.session_state["auth_user"].id) == user_id
                )

                if not IS_ADMIN and not is_own_profile:
                    continue

                auth_status = auth_login_status.get(user_id, {})
                last_sign_in_at = auth_status.get("last_sign_in_at")
                account_created_at = auth_status.get("created_at")
                confirmed_at = auth_status.get("confirmed_at")
                has_logged_in = bool(last_sign_in_at)

                if is_own_profile:
                    login_badge = "🟢 Current session"
                elif has_logged_in:
                    login_badge = "✅ Has logged in"
                else:
                    login_badge = "⚪ Never logged in"

                with st.expander(
                    f"👤 {current_row['full_name']} | "
                    f"Role: {current_row['user_role']} | {login_badge}"
                ):
                    if IS_ADMIN:
                        status_col1, status_col2, status_col3 = st.columns(3)

                        with status_col1:
                            st.metric(
                                "Login status",
                                "Logged in before" if has_logged_in else "Never logged in",
                            )

                        with status_col2:
                            st.markdown("**Last successful login**")
                            st.write(format_auth_timestamp(last_sign_in_at))

                        with status_col3:
                            st.markdown("**Auth account created**")
                            st.write(format_auth_timestamp(account_created_at))

                        if is_own_profile:
                            st.success("This is the account currently signed into this app session.")

                        if confirmed_at:
                            st.caption(
                                f"Account confirmed: {format_auth_timestamp(confirmed_at)}"
                            )
                        else:
                            st.warning("No account confirmation timestamp is available.")

                        st.markdown("---")

                    col_e1, col_e2 = st.columns(2)

                    with col_e1:
                        role_options = ["Administrator", "Editor", "Viewer"]
                        current_role = (
                            current_row["user_role"]
                            if current_row["user_role"] in role_options
                            else "Viewer"
                        )
                        update_role_selection = st.selectbox(
                            "Modify Privileges Role",
                            role_options,
                            index=role_options.index(current_role),
                            key=f"edit_role_select_{current_row['id']}",
                            disabled=not IS_ADMIN,
                        )

                        if st.button(
                            "Overwrite Access Role",
                            key=f"save_role_btn_{current_row['id']}",
                            disabled=not IS_ADMIN,
                        ):
                            (
                                supabase.table("monitor_users")
                                .update({"user_role": update_role_selection})
                                .eq("id", current_row["id"])
                                .execute()
                            )
                            st.success("User privilege profile updated.")
                            st.rerun()

                    with col_e2:
                        overwrite_password_string = st.text_input(
                            "Overwrite Password / Force Reset",
                            type="password",
                            key=f"reset_pass_field_{current_row['id']}",
                            placeholder="Type new credentials string...",
                            disabled=not IS_ADMIN,
                        )

                        if st.button(
                            "Deploy New Password Overwrite",
                            key=f"save_pass_btn_{current_row['id']}",
                            disabled=not IS_ADMIN,
                        ):
                            if len(overwrite_password_string) < 6:
                                st.error("Password strings must be at least 6 characters.")
                            else:
                                try:
                                    admin_client = create_client(
                                        st.secrets["SUPABASE_URL"],
                                        st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
                                    )
                                    admin_client.auth.admin.update_user_by_id(
                                        current_row["user_id"],
                                        {"password": overwrite_password_string},
                                    )
                                    st.success("Security token updated successfully!")
                                except Exception as pass_err:
                                    st.error(f"Password overwrite failed: {pass_err}")

                    if IS_ADMIN:
                        st.markdown("---")
                        if st.button(
                            "❌ Terminate Account & Wipe Platform Data Logs",
                            key=f"wipe_user_btn_{current_row['id']}",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                admin_client = create_client(
                                    st.secrets["SUPABASE_URL"],
                                    st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
                                )
                                admin_client.auth.admin.delete_user(
                                    current_row["user_id"]
                                )
                            except Exception as exc:
                                st.warning(f"Auth account deletion warning: {exc}")

                            (
                                supabase.table("monitor_users")
                                .delete()
                                .eq("id", current_row["id"])
                                .execute()
                            )
                            st.success("Account removed.")
                            st.rerun()

    # 2. KEYWORD MANAGEMENT LAYOUT
    with tab_keywords:
        st.markdown("### 🔑 Target Keyword Monitoring Framework")
        
        if IS_ADMIN:
            col_single, col_bulk = st.columns(2)
            
            with col_single:
                with st.form("add_kw_form", clear_on_submit=True):
                    st.markdown("**Add Single Search Target Phrasing**")
                    k_term = st.text_input("Exact Search Query Word/Phrase")
                    k_brands = st.text_input("Associated Impact Brands (Comma Separated)")
                    k_theme = st.text_input("Theme Layer Category")
                    
                    if st.form_submit_button("Commit Query to Search Engine Index"):
                        if k_term.strip():
                            brand_list = [b.strip() for b in k_brands.split(",") if b.strip()]
                            try:
                                supabase.table("monitor_keywords").insert({"term": k_term, "brand_tags": brand_list, "theme_layer": k_theme}).execute()
                                st.success(f"Logged search query phrase: '{k_term}'")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to index tracking term: {e}")
            
            with col_bulk:
                st.markdown("**Bulk Upload Keywords from Excel**")
                st.caption("Upload an .xlsx file with column headers: term, brand_tags, theme_layer.")
                
                uploaded_excel = st.file_uploader("Choose Excel File", type=["xlsx"])
                if uploaded_excel is not None:
                    try:
                        excel_df = pd.read_excel(uploaded_excel)
                        required_cols = {"term", "brand_tags", "theme_layer"}
                        
                        if not required_cols.issubset(excel_df.columns):
                            st.error(f"Invalid columns. Required: term, brand_tags, theme_layer")
                        else:
                            st.dataframe(excel_df, use_container_width=True)
                            
                            if st.button("Confirm Bulk Ingestion Matrix into Database", type="primary", use_container_width=True):
                                success_count = 0
                                error_count = 0
                                
                                with st.spinner("Streaming rows safely into database engine ledger..."):
                                    for idx, row in excel_df.iterrows():
                                        term_val = str(row['term']).strip()
                                        if not term_val or term_val == "nan":
                                            continue
                                        
                                        raw_brands = str(row['brand_tags'])
                                        brand_tags_list = [b.strip() for b in raw_brands.split(",") if b.strip() and b.lower() != "nan"]
                                        theme_val = str(row['theme_layer']).strip() if str(row['theme_layer']).lower() != "nan" else "General"
                                        
                                        try:
                                            supabase.table("monitor_keywords").upsert({
                                                "term": term_val,
                                                "brand_tags": brand_tags_list,
                                                "theme_layer": theme_val
                                            }, on_conflict="term").execute()
                                            success_count += 1
                                        except Exception:
                                            error_count += 1
                                            
                                st.success(f"Bulk run finished! Registered {success_count} keywords. Errors: {error_count}")
                                time.sleep(1.5)
                                st.rerun()
                    except Exception as parse_err:
                        st.error(f"Failed to unpack Excel binary: {parse_err}")
        else:
            st.info("ℹ️ Search framework scope changes are restricted to system Administrators.")

        st.markdown("---")
        st.markdown("**Active Scraped Keywords Index Matrix**")
        k_res = supabase.table("monitor_keywords").select("*").order("term").execute()
        if k_res.data:
            k_df = pd.DataFrame(k_res.data)
            st.dataframe(k_df[["term", "brand_tags", "theme_layer"]], use_container_width=True, hide_index=True)
            
            if IS_ADMIN:
                kw_to_del = st.selectbox("Select tracking query to delete:", [kw["term"] for kw in k_res.data])
                if st.button("Permanently Remove Term From Scraper", type="primary"):
                    supabase.table("monitor_keywords").delete().eq("term", kw_to_del).execute()
                    st.success(f"Removed target query sequence: '{kw_to_del}'")
                    st.rerun()

    # 3. AI REPORT PROMPT TEMPLATE EDITOR
    with tab_templates:
        st.markdown("### 📝 AI Generation System Report Prompt Templates")
        t_res = supabase.table("monitor_templates").select("*").order("template_name").execute()
        
        if t_res.data:
            selected_tmpl_name = st.selectbox("Select a report template config to edit:", [t["template_name"] for t in t_res.data])
            current_tmpl = next(t for t in t_res.data if t["template_name"] == selected_tmpl_name)
            
            if IS_ADMIN:
                with st.form("edit_tmpl_form"):
                    st.write(f"Editing Prompt Architecture for: **{selected_tmpl_name}**")
                    updated_prompt_text = st.text_area("System Instruction Matrix Guidance Prompt", value=current_tmpl["system_instruction_prompt"], height=300)
                    if st.form_submit_button("Overwrite System Prompt Template Details"):
                        supabase.table("monitor_templates").update({
                            "system_instruction_prompt": updated_prompt_text
                        }).eq("template_name", selected_tmpl_name).execute()
                        st.success("AI prompt configuration successfully updated!")
                        st.rerun()
            else:
                st.info("ℹ️ AI generation structural system prompts are locked and read-only. Modifications are restricted to system Administrators.")
                st.text_area("Current Active Blueprint Framework", value=current_tmpl["system_instruction_prompt"], height=250, disabled=True)

# app.py
elif app_mode == "📞 Media CRM & Inquiries":
    st.subheader("📞 Media Relations CRM & Inquiry Tracker")
    st.write("Manage reporter relationships, log incoming requests, and attribute published mentions to specific contacts.")

    def normalize_joined_row(value):
        if isinstance(value, list):
            return value[0] if value else {}
        if isinstance(value, dict):
            return value
        return {}

    def get_contact_dependency_counts(contact_id):
        mention_count = 0
        inquiry_count = 0

        try:
            mention_res = (
                supabase.table("mentions")
                .select("id", count="exact")
                .eq("author_contact_id", contact_id)
                .execute()
            )
            mention_count = mention_res.count or 0
        except Exception:
            pass

        try:
            inquiry_res = (
                supabase.table("media_inquiries")
                .select("id", count="exact")
                .eq("contact_id", contact_id)
                .execute()
            )
            inquiry_count = inquiry_res.count or 0
        except Exception:
            pass

        return mention_count, inquiry_count

    def delete_contact(contact_id, force_unlink=False):
        mention_count, inquiry_count = get_contact_dependency_counts(contact_id)

        if not force_unlink and (mention_count > 0 or inquiry_count > 0):
            raise ValueError("This contact is still linked to mentions or inquiries.")

        if force_unlink:
            if mention_count > 0:
                (
                    supabase.table("mentions")
                    .update({"author_contact_id": None})
                    .eq("author_contact_id", contact_id)
                    .execute()
                )

            if inquiry_count > 0:
                (
                    supabase.table("media_inquiries")
                    .update({"contact_id": None})
                    .eq("contact_id", contact_id)
                    .execute()
                )

        supabase.table("media_contacts").delete().eq("id", contact_id).execute()

    crm_tab_inquiries, crm_tab_contacts, crm_tab_link = st.tabs(
        ["📨 Active Inquiries", "📇 Media Rolodex", "🔗 Link Articles to Contacts"]
    )

    contacts_res = supabase.table("media_contacts").select("*").order("full_name").execute()
    all_contacts = contacts_res.data if contacts_res.data else []
    contact_options = {f"{c['full_name']} ({c['outlet']})": c["id"] for c in all_contacts}

    with crm_tab_inquiries:
        col_new_inq, col_active_inq = st.columns([1, 2])

        with col_new_inq:
            st.markdown("### 📝 Log New Inquiry")

            if not all_contacts:
                st.warning("Please add a Media Contact in the Rolodex tab before logging an inquiry.")
            else:
                with st.form("crm_new_inquiry_form", clear_on_submit=True):
                    selected_contact_label = st.selectbox(
                        "Assign to Contact",
                        list(contact_options.keys()),
                        key="crm_new_inquiry_contact",
                    )
                    inq_subject = st.text_input("Request Subject / Topic", key="crm_new_inquiry_subject")
                    inq_details = st.text_area("Request Details / Questions", key="crm_new_inquiry_details")
                    inq_deadline = st.date_input("Deadline Date", key="crm_new_inquiry_deadline")
                    inq_owner = st.selectbox("Assign to Team Member", TEAM_USERS, key="crm_new_inquiry_owner")

                    submitted_new_inquiry = st.form_submit_button("Log Inquiry & Notify Owner", type="primary")

                    if submitted_new_inquiry:
                        if not inq_subject.strip():
                            st.error("Subject is required.")
                        else:
                            contact_id = contact_options[selected_contact_label]

                            (
                                supabase.table("media_inquiries")
                                .insert(
                                    {
                                        "contact_id": contact_id,
                                        "inquiry_subject": inq_subject.strip(),
                                        "inquiry_details": inq_details.strip() or None,
                                        "deadline": inq_deadline.isoformat(),
                                        "status": "pending",
                                        "assigned_to_user": inq_owner if inq_owner != "Unassigned" else None,
                                    }
                                )
                                .execute()
                            )

                            if inq_owner != "Unassigned":
                                send_assignment_notification(
                                    None,
                                    f"MEDIA INQUIRY: {inq_subject.strip()}",
                                    inq_owner,
                                    st.session_state["user_full_name"],
                                    f"New request from {selected_contact_label}. Deadline: {inq_deadline.isoformat()}",
                                )

                            st.success("Inquiry logged successfully!")
                            st.rerun()

        with col_active_inq:
            st.markdown("### 📨 Active Ticket Queue")

            inq_res = (
                supabase.table("media_inquiries")
                .select("*, media_contacts(full_name, outlet)")
                .neq("status", "resolved")
                .order("deadline")
                .execute()
            )

            active_inquiries = inq_res.data if inq_res.data else []

            if not active_inquiries:
                st.info("No active media inquiries at this time.")
            else:
                for inq in active_inquiries:
                    contact_info = normalize_joined_row(inq.get("media_contacts"))
                    contact_name = contact_info.get("full_name") or "Unknown"
                    outlet = contact_info.get("outlet") or "Unknown"

                    with st.expander(
                        f"⏳ {str(inq.get('deadline', ''))[:10]} | {contact_name} ({outlet}) - {inq.get('inquiry_subject', 'Untitled')}"
                    ):
                        st.markdown("#### 📜 Activity & Notes History")
                        hist_res = (
                            supabase.table("inquiry_actions")
                            .select("*")
                            .eq("inquiry_id", inq["id"])
                            .order("inserted_at", desc=True)
                            .execute()
                        )

                        history_rows = hist_res.data if hist_res.data else []
                        if not history_rows:
                            st.caption("No notes logged for this inquiry yet.")
                        else:
                            hist_df = pd.DataFrame(history_rows).rename(
                                columns={
                                    "inserted_at": "Timestamp",
                                    "performed_by": "User",
                                    "action_note": "Note details",
                                }
                            )
                            st.table(hist_df[["Timestamp", "User", "Note details"]])

                        st.markdown("#### ✏️ Edit Ticket Details & Add Notes")
                        edit_subj = st.text_input(
                            "Subject",
                            value=inq.get("inquiry_subject", ""),
                            key=f"crm_inq_subject_{inq['id']}",
                        )
                        edit_det = st.text_area(
                            "Details",
                            value=inq.get("inquiry_details") or "",
                            key=f"crm_inq_details_{inq['id']}",
                        )

                        col_a, col_b, col_c = st.columns(3)

                        with col_a:
                            try:
                                current_deadline = datetime.fromisoformat(
                                    str(inq["deadline"]).replace("Z", "+00:00")
                                ).date()
                            except Exception:
                                current_deadline = datetime.now().date()

                            edit_dl = st.date_input(
                                "Deadline",
                                value=current_deadline,
                                key=f"crm_inq_deadline_{inq['id']}",
                            )

                        with col_b:
                            status_options = ["pending", "in-progress", "resolved"]
                            current_status = inq["status"] if inq.get("status") in status_options else "pending"
                            edit_stat = st.selectbox(
                                "Status",
                                status_options,
                                index=status_options.index(current_status),
                                key=f"crm_inq_status_{inq['id']}",
                            )

                        with col_c:
                            outcomes_list = [
                                "Pending",
                                "Interview Scheduled",
                                "Mention Published",
                                "Declined/Passed",
                                "Other",
                            ]
                            current_outcome = inq.get("outcome") or "Pending"
                            if current_outcome not in outcomes_list:
                                outcomes_list.append(current_outcome)

                            edit_out = st.selectbox(
                                "Outcome",
                                outcomes_list,
                                index=outcomes_list.index(current_outcome),
                                key=f"crm_inq_outcome_{inq['id']}",
                            )

                        col_d, col_e = st.columns(2)

                        with col_d:
                            current_owner = (
                                inq.get("assigned_to_user")
                                if inq.get("assigned_to_user") in TEAM_USERS
                                else "Unassigned"
                            )
                            edit_owner = st.selectbox(
                                "Assignee",
                                TEAM_USERS,
                                index=TEAM_USERS.index(current_owner),
                                key=f"crm_inq_owner_{inq['id']}",
                            )

                        with col_e:
                            new_note = st.text_input(
                                "Log New Action/Note to History",
                                key=f"crm_inq_note_{inq['id']}",
                                placeholder="Type an update here...",
                            )

                        st.markdown("---")
                        save_col, delete_col = st.columns([3, 1])

                        with save_col:
                            if st.button(
                                "Save Ticket Changes & Post Note",
                                key=f"crm_inq_save_{inq['id']}",
                                type="primary",
                                use_container_width=True,
                            ):
                                current_user_name = st.session_state["user_full_name"]

                                if new_note.strip():
                                    (
                                        supabase.table("inquiry_actions")
                                        .insert(
                                            {
                                                "inquiry_id": inq["id"],
                                                "action_note": new_note.strip(),
                                                "performed_by": current_user_name,
                                            }
                                        )
                                        .execute()
                                    )

                                if edit_owner != "Unassigned" and edit_owner != current_owner:
                                    send_assignment_notification(
                                        None,
                                        f"MEDIA INQUIRY: {edit_subj.strip()}",
                                        edit_owner,
                                        current_user_name,
                                        f"Ticket assigned to you. Note: {new_note.strip() or 'No note added.'}",
                                    )

                                (
                                    supabase.table("media_inquiries")
                                    .update(
                                        {
                                            "inquiry_subject": edit_subj.strip(),
                                            "inquiry_details": edit_det.strip() or None,
                                            "deadline": edit_dl.isoformat(),
                                            "status": edit_stat,
                                            "outcome": edit_out,
                                            "assigned_to_user": edit_owner if edit_owner != "Unassigned" else None,
                                        }
                                    )
                                    .eq("id", inq["id"])
                                    .execute()
                                )

                                st.toast("Ticket updated successfully!")
                                st.rerun()

                        with delete_col:
                            if st.button(
                                "🗑️ Delete",
                                key=f"crm_inq_delete_{inq['id']}",
                                type="secondary",
                                use_container_width=True,
                            ):
                                supabase.table("media_inquiries").delete().eq("id", inq["id"]).execute()
                                st.rerun()

    with crm_tab_contacts:
        add_col, profile_col = st.columns([1, 2])

        with add_col:
            st.markdown("### 📇 Add New Contact")

            with st.form("crm_new_contact_form", clear_on_submit=True):
                c_name = st.text_input("Full Name*", key="crm_contact_name")
                c_outlet = st.text_input("Primary Outlet / Publication*", key="crm_contact_outlet")
                c_email = st.text_input("Email Address", key="crm_contact_email")
                c_phone = st.text_input("Phone Number", key="crm_contact_phone")
                c_notes = st.text_area(
                    "Background Notes (Bias, past interactions, beats)",
                    key="crm_contact_notes",
                )

                submitted_new_contact = st.form_submit_button("Save Contact", type="primary")

                if submitted_new_contact:
                    if not c_name.strip() or not c_outlet.strip():
                        st.error("Name and Outlet are required.")
                    else:
                        (
                            supabase.table("media_contacts")
                            .insert(
                                {
                                    "full_name": c_name.strip(),
                                    "outlet": c_outlet.strip(),
                                    "email": c_email.strip() or None,
                                    "phone": c_phone.strip() or None,
                                    "background_notes": c_notes.strip() or None,
                                }
                            )
                            .execute()
                        )
                        st.success(f"{c_name.strip()} added to Rolodex!")
                        st.rerun()

        with profile_col:
            st.markdown("### 📂 Contact Profiles & History")

            if not all_contacts:
                st.info("Rolodex is currently empty. Add a contact on the left to begin building profiles.")
            else:
                selected_profile_label = st.selectbox(
                    "Search / Select a Contact Profile",
                    list(contact_options.keys()),
                    key="crm_contact_profile_select",
                )
                profile_id = contact_options[selected_profile_label]
                profile_data = next((c for c in all_contacts if c["id"] == profile_id), None)

                if profile_data:
                    st.markdown(f"#### 👤 {profile_data['full_name']} ({profile_data['outlet']})")
                    st.write(
                        f"📧 **Email:** {profile_data.get('email') or 'N/A'} | "
                        f"📞 **Phone:** {profile_data.get('phone') or 'N/A'}"
                    )
                    st.caption(f"**Internal Notes:** {profile_data.get('background_notes') or 'None provided.'}")

                    st.markdown("---")
                    st.markdown("### ✏️ Edit Contact")

                    with st.form(f"crm_edit_contact_form_{profile_id}"):
                        edit_name = st.text_input(
                            "Full Name*",
                            value=profile_data.get("full_name", ""),
                            key=f"crm_edit_contact_name_{profile_id}",
                        )
                        edit_outlet = st.text_input(
                            "Primary Outlet / Publication*",
                            value=profile_data.get("outlet", ""),
                            key=f"crm_edit_contact_outlet_{profile_id}",
                        )
                        edit_email = st.text_input(
                            "Email Address",
                            value=profile_data.get("email") or "",
                            key=f"crm_edit_contact_email_{profile_id}",
                        )
                        edit_phone = st.text_input(
                            "Phone Number",
                            value=profile_data.get("phone") or "",
                            key=f"crm_edit_contact_phone_{profile_id}",
                        )
                        edit_notes = st.text_area(
                            "Background Notes (Bias, past interactions, beats)",
                            value=profile_data.get("background_notes") or "",
                            key=f"crm_edit_contact_notes_{profile_id}",
                        )

                        submitted_edit_contact = st.form_submit_button("Save Contact Changes", type="primary")

                        if submitted_edit_contact:
                            if not edit_name.strip() or not edit_outlet.strip():
                                st.error("Name and Outlet are required.")
                            else:
                                (
                                    supabase.table("media_contacts")
                                    .update(
                                        {
                                            "full_name": edit_name.strip(),
                                            "outlet": edit_outlet.strip(),
                                            "email": edit_email.strip() or None,
                                            "phone": edit_phone.strip() or None,
                                            "background_notes": edit_notes.strip() or None,
                                        }
                                    )
                                    .eq("id", profile_id)
                                    .execute()
                                )
                                st.success("Contact updated successfully!")
                                st.rerun()

                    st.markdown("---")
                    st.markdown("### 🗑️ Remove Contact")

                    mentions_count, inquiries_count = get_contact_dependency_counts(profile_id)
                    st.caption(
                        f"This contact is linked to {mentions_count} mention(s) and {inquiries_count} inquiry record(s)."
                    )

                    delete_mode = st.radio(
                        "Delete mode",
                        [
                            "Block delete if linked records exist",
                            "Force delete and unlink related records",
                        ],
                        key=f"crm_delete_mode_{profile_id}",
                    )
                    confirm_delete = st.checkbox(
                        "I understand this action cannot be undone.",
                        key=f"crm_confirm_delete_{profile_id}",
                    )

                    if st.button(
                        "Delete Contact",
                        key=f"crm_delete_contact_{profile_id}",
                        type="secondary",
                        use_container_width=True,
                    ):
                        if not confirm_delete:
                            st.error("Please confirm deletion first.")
                        else:
                            try:
                                force_unlink = delete_mode == "Force delete and unlink related records"
                                delete_contact(profile_id, force_unlink=force_unlink)
                                message = (
                                    "Contact deleted and related records unlinked successfully!"
                                    if force_unlink
                                    else "Contact deleted successfully!"
                                )
                                st.success(message)
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                            except Exception as e:
                                st.error(f"Failed to delete contact: {e}")

                    st.markdown("---")
                    st.markdown("#### 📨 Inquiry History")

                    inq_hist_res = (
                        supabase.table("media_inquiries")
                        .select("*")
                        .eq("contact_id", profile_id)
                        .order("deadline", desc=True)
                        .execute()
                    )
                    inquiry_history = inq_hist_res.data if inq_hist_res.data else []

                    if not inquiry_history:
                        st.info("No inquiries logged for this contact.")
                    else:
                        for inq in inquiry_history:
                            status_icon = (
                                "🟢"
                                if inq.get("status") == "resolved"
                                else "🟡"
                                if inq.get("status") == "in-progress"
                                else "🔴"
                            )

                            with st.expander(
                                f"{status_icon} [{str(inq.get('status', 'pending')).upper()}] "
                                f"{str(inq.get('deadline', ''))[:10]} - {inq.get('inquiry_subject', 'Untitled')}"
                            ):
                                st.write(f"**Details:** {inq.get('inquiry_details') or 'No details provided.'}")

                                h_col1, h_col2, h_col3 = st.columns(3)

                                with h_col1:
                                    hist_status_options = ["pending", "in-progress", "resolved"]
                                    hist_status = inq["status"] if inq.get("status") in hist_status_options else "pending"
                                    edit_inq_stat = st.selectbox(
                                        "Update Status",
                                        hist_status_options,
                                        index=hist_status_options.index(hist_status),
                                        key=f"crm_hist_status_{inq['id']}",
                                    )

                                with h_col2:
                                    current_hist_owner = (
                                        inq.get("assigned_to_user")
                                        if inq.get("assigned_to_user") in TEAM_USERS
                                        else "Unassigned"
                                    )
                                    edit_inq_owner = st.selectbox(
                                        "Assignee",
                                        TEAM_USERS,
                                        index=TEAM_USERS.index(current_hist_owner),
                                        key=f"crm_hist_owner_{inq['id']}",
                                    )

                                with h_col3:
                                    inq_note = st.text_input(
                                        "Ping Note to Assignee (Optional)",
                                        key=f"crm_hist_note_{inq['id']}",
                                    )

                                if st.button(
                                    "Save & Notify Team",
                                    key=f"crm_hist_save_{inq['id']}",
                                    use_container_width=True,
                                ):
                                    current_user_name = st.session_state["user_full_name"]

                                    (
                                        supabase.table("media_inquiries")
                                        .update(
                                            {
                                                "status": edit_inq_stat,
                                                "assigned_to_user": edit_inq_owner if edit_inq_owner != "Unassigned" else None,
                                            }
                                        )
                                        .eq("id", inq["id"])
                                        .execute()
                                    )

                                    if edit_inq_owner != "Unassigned":
                                        ping_msg = inq_note.strip() or f"Inquiry status updated to {edit_inq_stat}."
                                        send_assignment_notification(
                                            None,
                                            f"MEDIA INQUIRY: {inq.get('inquiry_subject', 'Untitled')}",
                                            edit_inq_owner,
                                            current_user_name,
                                            ping_msg,
                                        )

                                    st.toast("Inquiry updated successfully!")
                                    st.rerun()

                    st.markdown("#### 📰 Published Articles Linked")

                    mentions_hist_res = (
                        supabase.table("mentions")
                        .select("title, outlet_platform, date_published, url")
                        .eq("author_contact_id", profile_id)
                        .order("date_published", desc=True)
                        .execute()
                    )
                    linked_mentions = mentions_hist_res.data if mentions_hist_res.data else []

                    if not linked_mentions:
                        st.caption("No articles have been linked to this author yet.")
                    else:
                        for mh in linked_mentions:
                            st.markdown(
                                f"- **{mh.get('date_published', '')}** | "
                                f"[{mh.get('title', 'Untitled')}]({mh.get('url', '#')}) - "
                                f"*{mh.get('outlet_platform', 'Unknown')}*"
                            )

    with crm_tab_link:
        st.markdown("### 🔗 Attribute Mentions to Reporters")
        st.write("Link a published article in your database to a specific media contact, or manually log coverage that the automated scraper missed.")

        col_link, col_manual = st.columns(2)

        with col_link:
            st.markdown("#### 📡 Link Existing Scraped Article")
            search_unlinked = st.text_input(
                "🔍 Search Unlinked Articles",
                placeholder="Type a keyword from the title...",
                key="crm_search_unlinked_articles",
            )

            query = (
                supabase.table("mentions")
                .select("id, title, outlet_platform, date_published")
                .is_("author_contact_id", "null")
            )
            if search_unlinked:
                query = query.ilike("title", f"%{search_unlinked.strip()}%")

            unlinked_res = query.order("date_published", desc=True).limit(50).execute()
            unlinked_mentions = unlinked_res.data if unlinked_res.data else []

            if not unlinked_mentions:
                st.success("No unlinked mentions found matching that search!")
            else:
                with st.form("crm_link_existing_article_form"):
                    mention_options = {
                        f"{m['date_published']} | {m['outlet_platform']} - {m['title'][:35]}...": m["id"]
                        for m in unlinked_mentions
                    }
                    selected_mention_label = st.selectbox(
                        "Select Unlinked Article",
                        list(mention_options.keys()),
                        key="crm_link_existing_article_select",
                    )
                    selected_author_label = st.selectbox(
                        "Select Author from Rolodex",
                        list(contact_options.keys()),
                        key="crm_link_existing_author_select",
                    )

                    submitted_link_existing = st.form_submit_button(
                        "🔗 Link Article to Author",
                        type="primary",
                        use_container_width=True,
                    )

                    if submitted_link_existing:
                        mention_id = mention_options[selected_mention_label]
                        contact_id = contact_options[selected_author_label]
                        (
                            supabase.table("mentions")
                            .update({"author_contact_id": contact_id})
                            .eq("id", mention_id)
                            .execute()
                        )
                        st.toast("Article successfully linked to contact!")
                        st.rerun()

        with col_manual:
            st.markdown("#### ➕ Manually Add Missing Article")

            if not all_contacts:
                st.info("Rolodex contacts are required to assign authorship.")
            else:
                with st.form("crm_manual_mention_form", clear_on_submit=True):
                    m_title = st.text_input("Article Title*", key="crm_manual_title")
                    m_url = st.text_input("Article URL Link*", key="crm_manual_url")
                    m_outlet = st.text_input("Outlet / Platform Name*", key="crm_manual_outlet")
                    m_date = st.date_input("Date Published", key="crm_manual_date")
                    m_author_label = st.selectbox("Author / Contact", list(contact_options.keys()), key="crm_manual_author")
                    m_snippet = st.text_area("Snippet / Key Quotes (Optional)", key="crm_manual_snippet")

                    submitted_manual_mention = st.form_submit_button(
                        "Save & Link Manual Article",
                        type="primary",
                        use_container_width=True,
                    )

                    if submitted_manual_mention:
                        if not m_title.strip() or not m_url.strip() or not m_outlet.strip():
                            st.error("Title, URL, and Outlet are required fields.")
                        else:
                            contact_id = contact_options[m_author_label]
                            (
                                supabase.table("mentions")
                                .insert(
                                    {
                                        "title": m_title.strip(),
                                        "url": m_url.strip(),
                                        "outlet_platform": m_outlet.strip(),
                                        "date_published": m_date.isoformat(),
                                        "snippet": m_snippet.strip() or None,
                                        "author_contact_id": contact_id,
                                        "status": "logged",
                                        "recommendation": "monitor only",
                                        "sentiment_category": "Neutral",
                                        "sentiment_score": 0.0,
                                        "sentiment_rationale": "Manually logged by team member.",
                                        "ai_action_recommendation": "Manual entry — tracking for relationship management.",
                                    }
                                )
                                .execute()
                            )
                            st.success("Manual article saved and linked successfully!")
                            st.rerun()
