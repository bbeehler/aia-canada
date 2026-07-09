import streamlit as st
import requests
import pandas as pd
import os
import time
from datetime import datetime, timedelta
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
    if recipient and recipient != "Unassigned" and recipient != sender:
        # Fallback message if they didn't write a custom note
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

# --- 2. SIDEBAR UTILITIES & WORKFLOW TRIGGER ---
st.sidebar.title("📊 AIA Canada Monitor")
st.sidebar.caption(f"Operator: {st.session_state['user_full_name']} ({USER_ROLE})")

if st.sidebar.button("🔒 Sign Out / Lock Session", use_container_width=True):
    supabase.auth.sign_out()
    st.session_state["auth_user"] = None
    st.session_state["user_role"] = "Viewer"
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

app_mode = st.sidebar.radio(
    "Navigation Menu", 
    [
        "📥 Inbox / Triage", 
        "📋 Reviewed Database Table", 
        "🚨 Daily Crisis Center", 
        "📝 AI Report Builder", 
        "💬 Database Q&A Assistant",
        "⚙️ System Settings Dashboard"
    ]
)

st.sidebar.markdown("---")
# --- 🔔 IN-APP NOTIFICATION CENTER ---
if st.session_state["user_full_name"]:
    notif_res = supabase.table("notifications").select("*").eq("recipient_name", st.session_state["user_full_name"]).eq("is_read", False).order("created_at", desc=True).execute()
    unread_count = len(notif_res.data) if notif_res.data else 0
    
    if unread_count > 0:
        with st.sidebar.expander(f"🔔 Notifications ({unread_count} Unread)", expanded=True):
            for n in notif_res.data:
                st.markdown(f"**From:** {n['sender_name']}")
                st.caption(f"*{n['mention_title'][:40]}...*")
                st.info(f"💬 {n['message']}")
                
                # Utilizes the Deep Link logic we built earlier!
                st.markdown(f"[🔗 Open Direct Record Viewer](/?mention_id={n['mention_id']})")
                
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
        st.write(f"**Gemini Rationale:** *{target_record['sentiment_rationale']}*")
        
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
        
        m1, m2 = st.columns([1, 4])
        with m1:
            if st.button("Save Changes", type="primary", use_container_width=True, key="dl_save_changes_btn", disabled=IS_VIEWER):
                if edit_note.strip():
                    add_action_note(target_record['id'], edit_note, edit_user)
                supabase.table("mentions").update({
                    "recommendation": edit_rec,
                    "alert_level": edit_lvl,
                    "status": edit_stat,
                    "assigned_to_user": edit_user if edit_user != "Unassigned" else None
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
                
                st.markdown("---")
                b1, b2, b3 = st.columns([2, 2, 1])
                with b1:
                    if st.button("Commit Classification & Update Status", key=f"btn_{m['id']}", use_container_width=True, disabled=IS_VIEWER):
    determined_status = "escalated" if new_level in ["High", "Critical"] else "logged"
    current_user_name = st.session_state["user_full_name"]
    
    if note_text.strip():
        add_action_note(m['id'], f"Initial Triage Note: {note_text}", current_user_name)
    
    # Trigger the new notification if assigned to someone else
    if assignee != "Unassigned" and assignee != current_user_name:
        send_assignment_notification(m['id'], m['title'], assignee, current_user_name, note_text)
    
    supabase.table("mentions").update({
        "recommendation": new_rec,
        "alert_level": new_level,
        "status": determined_status,
        "assigned_to_user": assignee if assignee != "Unassigned" else None,
        "escalated_to_user": escalation_target if escalation_target != "Unassigned" else None
    }).eq("id", m['id']).execute()
    
    st.rerun()
                with b2:
                    if st.button("Add Progress Note Only", key=f"note_btn_{m['id']}", use_container_width=True, disabled=IS_VIEWER):
                        if note_text.strip():
                            add_action_note(m['id'], note_text, assignee)
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
    
    f1, f2, f3 = st.columns([2, 2, 3])
    with f1:
        start_date = st.date_input("Start Date (Published)", datetime.now().date() - timedelta(days=7))
    with f2:
        end_date = st.date_input("End Date (Published)", datetime.now().date())
    with f3:
        search_kw = st.text_input("Search Title or Snippet Keyword", placeholder="Type a term...")
    
    # Query constrained directly to the parsed publication date index layer
    response = supabase.table("mentions")\
        .select("*")\
        .in_("status", ["logged", "escalated", "resolved"])\
        .gte("date_published", start_date.isoformat())\
        .lte("date_published", end_date.isoformat())\
        .order("date_published", desc=True).execute()
    reviewed_data = response.data
    
    if not reviewed_data:
        st.info("No reviewed tracking logs match the specified publication parameters.")
    else:
        df = pd.DataFrame(reviewed_data)
        if search_kw:
            df = df[df['title'].str.contains(search_kw, case=False, na=False) | df['snippet'].str.contains(search_kw, case=False, na=False)]
            
        if df.empty:
            st.warning("No records found matching that keyword combination.")
        else:
            display_columns = [
                "date_published", "outlet_platform", "title", "theme", 
                "sentiment_category", "sentiment_score", "alert_level", "status", "assigned_to_user", "escalated_to_user", "recommendation"
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
                
                st.markdown(f"### 📄 Full Metadata Profile: `{target_record['title']}`")
                st.info(f"🤖 **Gemini Strategic Action Recommendation for this Mention:** {target_record.get('ai_action_recommendation', 'N/A')}")
                
                meta_col1, meta_col2, meta_col3 = st.columns(3)
                with meta_col1:
                    st.markdown("**📌 Core Tracking Identifiers**")
                    st.write(f"- **Database ID (UUID):** `{target_record['id']}`")
                    st.write(f"- **System Insertion Timestamp:** `{target_record['inserted_at']}`")
                    st.write(f"- **Official Publication Date:** `{target_record['date_published']}`")
                    st.write(f"- **Direct Source URL:** [Open Live Web Link]({target_record['url']})")
                
                with meta_col2:
                    st.markdown("**🏷️ Corporate Context & Scope Tags**")
                    st.write(f"- **Brands Explicitly Affected:** {', '.join(target_record['brands_affected']) if target_record['brands_affected'] else 'None mapped'}")
                    st.write(f"- **Structural Theme Classification:** `{target_record['theme']}`")
                    st.write(f"- **Workflow Pipeline State:** `{target_record['status']}`")
                    st.write(f"- **Assigned Active Owner:** `{target_record['assigned_to_user'] or 'Unassigned'}`")
                    st.write(f"- **Escalation Target Recipient:** `{target_record['escalated_to_user'] or 'None assigned'}`")
                
                with meta_col3:
                    st.markdown("**🧠 Gemini Sentiment Metrics & Quality Flags**")
                    st.write(f"- **Inferred Tone Category:** `{target_record['sentiment_category']}`")
                    st.write(f"- **Sentiment Intensity Score (-1.0 to 1.0):** `{target_record['sentiment_score']}`")
                    st.write(f"- **Action Recommendation Strategy:** `{target_record['recommendation']}`")
                
                st.markdown("**📝 Text Snippet & Analytical Explanations**")
                st.write(f"**Raw Text Excerpt Snippet:** *\"{target_record['snippet']}\"*")
                st.write(f"**Gemini Sentiment Rationale:** *{target_record['sentiment_rationale']}*")
                
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
                
                m1, m2 = st.columns([1, 4])
                with m1:
                    if st.button("Save Changes", type="primary", use_container_width=True, key="save_changes_btn", disabled=IS_VIEWER):
                        if edit_note.strip():
                            add_action_note(target_record['id'], edit_note, edit_user)
                        supabase.table("mentions").update({
                            "recommendation": edit_rec,
                            "alert_level": edit_lvl,
                            "status": edit_stat,
                            "assigned_to_user": edit_user if edit_user != "Unassigned" else None
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
elif app_mode == "📝 AI Report Builder":
    st.subheader("📝 Automated Executive Reporting")
    st.write("Generate AI-driven summaries based on the raw tracking data in your database.")
    
    tab_daily, tab_weekly = st.tabs(["📅 Daily Triage Rollup", "🗓️ Weekly Trend Summary"])
    
    # --- DAILY REPORT TAB ---
    with tab_daily:
        st.markdown("### Generate Daily Operations Report")
        st.write("Compiles a summary of all items that were successfully processed and cleared from the inbox for a specific date.")
        
        target_date = st.date_input("Select Processing Date", datetime.now().date(), key="daily_date_picker")
        
        # Pull the custom daily prompt from Supabase
        try:
            tmpl_res = supabase.table("monitor_templates").select("*").eq("template_name", "Daily Triage Rollup").execute()
            daily_instruction = tmpl_res.data[0]["system_instruction_prompt"]
        except Exception:
            daily_instruction = "You are a PR assistant for AIA Canada. Summarize the day's media mentions briefly."

        if st.button("Generate Daily Rollup", use_container_width=True, type="primary"):
            with st.spinner("Extracting today's processed logs..."):
                # Define the start and end of the selected day to filter the database
                start_iso = datetime.combine(target_date, datetime.min.time()).isoformat()
                end_iso = datetime.combine(target_date, datetime.max.time()).isoformat()
                
                # Fetch records that entered the system on this date (Includes pending so recent items can be compiled)
                raw_data = supabase.table("mentions").select("id, title, url, outlet_platform, theme, status, recommendation, brands_affected, alert_level").gte("inserted_at", start_iso).lte("inserted_at", end_iso).execute()
                
                if not raw_data.data:
                    st.warning("No media tracking records were processed or logged on this specific date.")
                else:
                    try:
                        response = ai_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[f"Daily Processed Logs:\n{str(raw_data.data)}"],
                            config=types.GenerateContentConfig(system_instruction=daily_instruction)
                        )
                        st.session_state["latest_daily_report"] = response.text
                        st.success("Daily Report Generation Complete!")
                    except Exception as e:
                        st.error(f"Generation failed: {e}")
                        
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
                raw_data = supabase.table("mentions").select("id, title, url, outlet_platform, theme, status, recommendation, brands_affected, alert_level").order("inserted_at", desc=True).limit(100).execute()
                
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
elif app_mode == "💬 Database Q&A Assistant":
    st.subheader("💬 Ask Your Media Monitoring Database")
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
                            auth_res = supabase.auth.admin.create_user({
                                "email": new_email,
                                "password": new_password,
                                "email_confirm": True
                            })
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
                    updated_prompt_text = st.text_area("Gemini System Instruction Matrix Guidance Prompt", value=current_tmpl["system_instruction_prompt"], height=300)
                    if st.form_submit_button("Overwrite System Prompt Template Details"):
                        supabase.table("monitor_templates").update({
                            "system_instruction_prompt": updated_prompt_text
                        }).eq("template_name", selected_tmpl_name).execute()
                        st.success("AI prompt configuration successfully updated!")
                        st.rerun()
            else:
                st.info("ℹ️ AI generation structural system prompts are locked and read-only. Modifications are restricted to system Administrators.")
                st.text_area("Current Active Blueprint Framework", value=current_tmpl["system_instruction_prompt"], height=250, disabled=True)
