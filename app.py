import streamlit as st
import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta
from urllib.parse import quote
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
    """Return mentions inserted or explicitly included for a report date."""
    start_iso = datetime.combine(target_date, datetime.min.time()).isoformat()
    end_iso = datetime.combine(target_date, datetime.max.time()).isoformat()

    selected_fields = (
        "id, title, url, outlet_platform, theme, status, recommendation, "
        "brands_affected, alert_level, assigned_to_user, date_published, "
        "inserted_at, sentiment_category, sentiment_score, "
        "ai_action_recommendation, naming_error_flag, data_conflict_flag, "
        "data_conflict_details, include_in_daily_report, daily_report_date"
    )

    inserted_response = (
        supabase.table("mentions")
        .select(selected_fields)
        .gte("inserted_at", start_iso)
        .lte("inserted_at", end_iso)
        .execute()
    )

    included_response = (
        supabase.table("mentions")
        .select(selected_fields)
        .eq("include_in_daily_report", True)
        .eq("daily_report_date", target_date.isoformat())
        .execute()
    )

    unique_mentions = {}
    for mention in inserted_response.data or []:
        unique_mentions[mention["id"]] = mention
    for mention in included_response.data or []:
        unique_mentions[mention["id"]] = mention

    return list(unique_mentions.values())

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
            tmpl_res = supabase.table("monitor_templates").select("*").eq("template_name", "Daily Triage Rollup").execute()
            daily_instruction = tmpl_res.data[0]["system_instruction_prompt"]
        except Exception:
            daily_instruction = "You are a PR assistant for AIA Canada. Summarize the day's media mentions briefly."

        if st.button(
            "Generate Daily Rollup",
            use_container_width=True,
            type="primary",
            key="generate_daily_rollup",
        ):
            with st.spinner("Extracting records for the selected daily report..."):
                try:
                    daily_mentions = load_daily_report_mentions(target_date)

                    if not daily_mentions:
                        st.warning(
                            "No mentions were inserted or explicitly included "
                            "for this daily report date."
                        )
                    else:
                        response = ai_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[
                                (
                                    f"Daily report date: {target_date.isoformat()}\n\n"
                                    f"Daily media records:\n{daily_mentions}"
                                )
                            ],
                            config=types.GenerateContentConfig(
                                system_instruction=daily_instruction
                            ),
                        )
                        st.session_state["latest_daily_report"] = response.text
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
        st.markdown("### Generate Weekly Trend Analysis")
        st.write("Compiles a broad, macro-level summary of industry trends from the last 100 processed tracking items.")
        
        try:
            tmpl_res = supabase.table("monitor_templates").select("*").eq("template_name", "Weekly Trend Summary").execute()
            weekly_instruction = tmpl_res.data[0]["system_instruction_prompt"]
        except Exception:
            weekly_instruction = "You are the senior media monitoring AI analyst for AIA Canada. Format using CP rules."

        if st.button("Generate Weekly Trend Document", use_container_width=True):
            with st.spinner("Compiling historical database records for processing with Gemini..."):
                # Fetches the latest 100 items (Includes pending so recent items can be compiled)
                raw_data = (
                    supabase.table("mentions")
                    .select(
                        "id, title, url, outlet_platform, theme, status, "
                        "recommendation, brands_affected, alert_level, "
                        "assigned_to_user, date_published, sentiment_category, "
                        "sentiment_score, naming_error_flag, data_conflict_flag, "
                        "data_conflict_details"
                    )
                    .order("inserted_at", desc=True)
                    .limit(100)
                    .execute()
                )
                
                if not raw_data.data:
                    st.warning("No validated tracking records discovered.")
                else:
                    try:
                        response = ai_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[f"Historical Logs:\n{str(raw_data.data)}"],
                            config=types.GenerateContentConfig(system_instruction=weekly_instruction)
                        )
                        st.session_state["latest_weekly_report"] = response.text
                        st.success("Weekly Report Generation Complete!")
                    except Exception as e:
                        st.error(f"Generation failed: {e}")
                    
        if "latest_weekly_report" in st.session_state:
            st.markdown("---")
            st.markdown(st.session_state["latest_weekly_report"])

# --- MODULE 5: DATABASE Q&A ASSISTANT ---
elif app_mode == "💬 Ask AIA Media":
    st.subheader("💬 Ask AIA Media")
    user_query = st.text_input("Enter your tracking question:", placeholder="Ask about trends, owners, or entries...")
    
    if user_query:
        with st.spinner("Scanning logs..."):
            try:
                # 1. Fetch the data
                all_mentions = supabase.table("mentions").select("title, outlet_platform, theme, sentiment_category, assigned_to_user, alert_level").limit(150).execute()
                
                import json
                # 2. Convert raw Python dictionaries into safely escaped, clean JSON for the AI to read
                clean_context = json.dumps(all_mentions.data, indent=2)
                
                qa_instruction = "You are an intelligent data specialist tracking brand awareness for AIA Canada. Answer using only the provided context."
                
                # 3. Pass the clean string to Gemini
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=f"Context:\n{clean_context}\n\nQuery: {user_query}",
                    config=types.GenerateContentConfig(system_instruction=qa_instruction)
                )
                
                st.info(response.text)
                
            except Exception as e:
                # 4. If Gemini rejects the payload, this explicitly catches the error and forces Streamlit 
                # to print the real error text to the screen instead of redacting it!
                st.error(f"⚠️ API Error Details: {str(e)}")
                
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
        
        if u_res.data:
            for current_row in u_res.data:
                is_own_profile = current_row['user_id'] == st.session_state["auth_user"].id
                if not IS_ADMIN and not is_own_profile:
                    continue
                    
                with st.expander(f"👤 {current_row['full_name']} | Role: {current_row['user_role']} ({current_row['tracking_email']})"):
                    col_e1, col_e2 = st.columns(2)
                    with col_e1:
                        current_role_idx = ["Administrator", "Editor", "Viewer"].index(current_row['user_role'])
                        update_role_selection = st.selectbox(
                            "Modify Privileges Role", 
                            ["Administrator", "Editor", "Viewer"], 
                            index=current_role_idx, 
                            key=f"edit_role_select_{current_row['id']}",
                            disabled=not IS_ADMIN
                        )
                        if st.button("Overwrite Access Role", key=f"save_role_btn_{current_row['id']}", disabled=not IS_ADMIN):
                            supabase.table("monitor_users").update({"user_role": update_role_selection}).eq("id", current_row['id']).execute()
                            st.success("User privilege profile updated.")
                            st.rerun()
                            
                    with col_e2:
                        overwrite_password_string = st.text_input("Overwrite Password / Force Reset", type="password", key=f"reset_pass_field_{current_row['id']}", placeholder="Type new credentials string...")
                        if st.button("Deploy New Password Overwrite", key=f"save_pass_btn_{current_row['id']}"):
                            if len(overwrite_password_string) >= 6:
                                try:
                                    supabase.auth.admin.update_user_by_id(current_row['user_id'], {"password": overwrite_password_string})
                                    st.success("Security token updated successfully!")
                                except Exception as pass_err:
                                    st.error(f"Password overwrite failed: {pass_err}")
                            else:
                                st.error("Password strings must be at least 6 characters.")
                    
                    if IS_ADMIN:
                        st.markdown("---")
                        if st.button("❌ Terminate Account & Wipe Platform Data Logs", key=f"wipe_user_btn_{current_row['id']}", type="primary", use_container_width=True):
                            try:
                                supabase.auth.admin.delete_user(current_row['user_id'])
                            except Exception:
                                pass
                            supabase.table("monitor_users").delete().eq("id", current_row['id']).execute()
                            st.success("Identity vectors cleared.")
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
