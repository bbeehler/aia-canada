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

# --- DYNAMIC CONFIGURATION FACTORIES ---
def load_active_team_users():
    try:
        res = supabase.table("monitor_users").select("full_name").order("full_name").execute()
        return ["Unassigned"] + [row["full_name"] for row in res.data]
    except Exception:
        return ["Unassigned", "Brian Beehler", "Emily Chung"]

TEAM_USERS = load_active_team_users()

# --- 2. SIDEBAR UTILITIES & WORKFLOW TRIGGER ---
st.sidebar.title("📊 AIA Canada Monitor")
st.sidebar.caption("Media Tracking & Analytics Platform")

st.sidebar.markdown("---")
st.sidebar.subheader("🔄 Manual Data Sync")

timeframe_label = st.sidebar.selectbox(
    "Select Search Horizon Window:",
    ["Past 24 Hours", "Past Week", "Past Month", "Past Year"]
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

st.sidebar.write("Force an immediate web crawl update:")
if st.sidebar.button("Force Fetch Mentions Now", use_container_width=True):
    with st.sidebar.spinner("Pinging GitHub Actions API..."):
        trigger_github_sync(selected_tbs)

st.sidebar.markdown("---")
app_mode = st.sidebar.radio(
    "Navigation Menu", 
    [
        "📥 Inbox / Triage", 
        "📋 Reviewed Database Table", 
        "🚨 Daily Crisis Center", 
        "📝 Weekly Summarizer", 
        "💬 Database Q&A Assistant",
        "⚙️ System Settings Dashboard"
    ]
)

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

# --- 3. MODULE 1: INBOX / TRIAGE ---
if app_mode == "📥 Inbox / Triage":
    st.subheader("📥 Unprocessed Mention Queue")
    st.write("Review, assign, and triage incoming raw tracking records.")
    
    response = supabase.table("mentions").select("*").eq("status", "pending").order("inserted_at", desc=True).execute()
    mentions = response.data
    
    if not mentions:
        st.success("All caught up! No pending un-triaged mentions found in the queue.")
    else:
        st.info(f"Found {len(mentions)} unprocessed mention records requiring validation.")
        for m in mentions:
            with st.expander(f"🔍 {m['outlet_platform']} | {m['title']} (Published: {m['date_published']})"):
                st.info(f"🤖 **Gemini Strategic Action Recommendation:** {m.get('ai_action_recommendation', 'Monitor tracking loop index context; no critical remediation required.')}")
                
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
                    new_rec = st.selectbox("Assign Action Item", ["monitor only", "engage", "share", "ignore"], index=0, key=f"rec_{m['id']}")
                with c2:
                    new_level = st.selectbox("Assign Severity Level", ["Low", "Medium", "High", "Critical"], key=f"lvl_{m['id']}")
                with c3:
                    assignee = st.selectbox("Assign to Team User", TEAM_USERS, key=f"user_{m['id']}")
                with c4:
                    escalation_target = st.selectbox("If Escalated, Route to", TEAM_USERS, key=f"esc_{m['id']}")
                
                note_text = st.text_input("Log Action Taken / Progress Note:", key=f"note_input_{m['id']}", placeholder="Type out workflow changes or notes here...")
                
                st.markdown("---")
                b1, b2, b3 = st.columns([2, 2, 1])
                with b1:
                    if st.button("Commit Classification & Update Status", key=f"btn_{m['id']}", use_container_width=True):
                        determined_status = "escalated" if new_level in ["High", "Critical"] else "logged"
                        if note_text.strip():
                            add_action_note(m['id'], f"Initial Triage Note: {note_text}", assignee)
                        
                        supabase.table("mentions").update({
                            "recommendation": new_rec,
                            "alert_level": new_level,
                            "status": determined_status,
                            "assigned_to_user": assignee if assignee != "Unassigned" else None,
                            "escalated_to_user": escalation_target if escalation_target != "Unassigned" else None
                        }).eq("id", m['id']).execute()
                        st.rerun()
                with b2:
                    if st.button("Add Progress Note Only", key=f"note_btn_{m['id']}", use_container_width=True):
                        if note_text.strip():
                            add_action_note(m['id'], note_text, assignee)
                            st.rerun()
                        else:
                            st.error("Note text field cannot be blank.")
                with b3:
                    if st.button("🗑️ Delete Mention", key=f"del_{m['id']}", use_container_width=True):
                        delete_mention_record(m['id'])
                        st.rerun()

# --- MODULE 2: REVIEWED DATABASE TABLE ---
elif app_mode == "📋 Reviewed Database Table":
    st.subheader("📋 Reviewed Mentions Archive")
    st.write("Use search filters to populate records. Click on any row to load its entire field metadata profile and action logs history below.")
    
    f1, f2, f3 = st.columns([2, 2, 3])
    with f1:
        start_date = st.date_input("Start Date", datetime.now().date() - timedelta(days=7))
    with f2:
        end_date = st.date_input("End Date", datetime.now().date())
    with f3:
        search_kw = st.text_input("Search Title or Snippet Keyword", placeholder="Type a term...")
    
    response = supabase.table("mentions")\
        .select("*")\
        .neq("status", "pending")\
        .gte("date_published", start_date.isoformat())\
        .lte("date_published", end_date.isoformat())\
        .order("inserted_at", desc=True).execute()
    reviewed_data = response.data
    
    if not reviewed_data:
        st.info("No reviewed tracking logs match the specified parameters.")
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
                    edit_rec = st.selectbox("Action Recommendation", ["monitor only", "engage", "share", "ignore"], index=current_rec_idx, key="edit_rec")
                with e2:
                    current_lvl_idx = ["Low", "Medium", "High", "Critical"].index(target_record['alert_level']) if target_record['alert_level'] in ["Low", "Medium", "High", "Critical"] else 0
                    edit_lvl = st.selectbox("Severity Framework", ["Low", "Medium", "High", "Critical"], index=current_lvl_idx, key="edit_lvl")
                with e3:
                    current_stat_idx = ["logged", "escalated", "resolved"].index(target_record['status']) if target_record['status'] in ["logged", "escalated", "resolved"] else 0
                    edit_stat = st.selectbox("Workflow State", ["logged", "escalated", "resolved"], index=current_stat_idx, key="edit_stat")
                with e4:
                    current_user = target_record['assigned_to_user'] if target_record['assigned_to_user'] in TEAM_USERS else "Unassigned"
                    edit_user = st.selectbox("Reassign Owner", TEAM_USERS, index=TEAM_USERS.index(current_user), key="edit_user")
                    
                edit_note = st.text_input("Type new action note to append to history trail:", key="edit_note_input")
                
                m1, m2 = st.columns([1, 4])
                with m1:
                    if st.button("Save Changes", type="primary", use_container_width=True, key="save_changes_btn"):
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
                    if st.button("🗑 Permanent Deletion", type="secondary", key="perm_delete_btn"):
                        delete_mention_record(target_record['id'])
                        st.rerun()
            else:
                st.caption("💡 Click on any processed item row inside the tracking matrix above to reveal its parameters.")

# --- MODULE 3: DAILY CRISIS CENTER ---
elif app_mode == "🚨 Daily Crisis Center":
    st.subheader("🚨 Template 1: Daily Crisis Alert Generator")
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

# --- MODULE 4: WEEKLY SUMMARIZER ---
elif app_mode == "📝 Weekly Summarizer":
    st.subheader("📝 Template 2: Weekly Trend Summary Engine")
    
    # Load custom Gemini system instructions dynamically from database configurations
    try:
        tmpl_res = supabase.table("monitor_templates").select("*").eq("template_name", "Weekly Trend Summary").execute()
        system_instruction = tmpl_res.data[0]["system_instruction_prompt"]
    except Exception:
        system_instruction = "You are the senior media monitoring AI analyst for AIA Canada. Format into Template 2 using CP rules."

    if st.button("Generate Weekly Trend Analysis Document", use_container_width=True):
        with st.spinner("Compiling database records for processing with Gemini..."):
            raw_data = supabase.table("mentions").select("*").neq("status", "pending").limit(100).execute()
            if not raw_data.data:
                st.warning("No validated tracking records discovered.")
            else:
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[f"Logs: {str(raw_data.data)}"],
                    config=types.GenerateContentConfig(system_instruction=system_instruction)
                )
                st.session_state["latest_weekly_report"] = response.text
                st.success("Generation Complete!")
                
    if "latest_weekly_report" in st.session_state:
        st.markdown(st.session_state["latest_weekly_report"])

# --- MODULE 5: DATABASE Q&A ASSISTANT ---
elif app_mode == "💬 Database Q&A Assistant":
    st.subheader("💬 Ask Your Media Monitoring Database")
    user_query = st.text_input("Enter your tracking question:", placeholder="Ask about trends, owners, or entries...")
    
    if user_query:
        with st.spinner("Scanning logs..."):
            all_mentions = supabase.table("mentions").select("title, outlet_platform, theme, sentiment_category, assigned_to_user, alert_level").limit(150).execute()
            qa_instruction = "You are an intelligent data specialist tracking brand awareness for AIA Canada. Answer using only the literal context provided."
            response = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[f"Context:\n{str(all_mentions.data)}\n\nQuery: {user_query}"],
                config=types.GenerateContentConfig(system_instruction=qa_instruction)
            )
            st.info(response.text)

# --- NEW MODULE 6: SYSTEM SETTINGS DASHBOARD ---
elif app_mode == "⚙️ System Settings Dashboard":
    st.subheader("⚙️ System Settings & Parameter Tuning Dashboard")
    st.write("Manage platform operations, adjust crawler scopes, assign custom roles, and fine-tune Gemini templates.")
    
    tab_users, tab_keywords, tab_templates = st.tabs(["👥 User Roster Settings", "🔑 Search Keywords & Phrases", "📝 AI Report Prompt Templates"])
    
    # 1. USER ROSTER SETTINGS
    with tab_users:
        st.markdown("### 👥 Manage Active Platform Operators")
        
        # Add User Form
        with st.form("add_user_form", clear_on_submit=True):
            st.markdown("**Add New Team Member**")
            u_name = st.text_input("Full Name / Team Label", placeholder="e.g., Jean-François Champagne")
            u_role = st.selectbox("Authorization Access Role", ["Administrator", "Editor", "Viewer"])
            if st.form_submit_button("Register Team Member"):
                if u_name.strip():
                    try:
                        supabase.table("monitor_users").insert({"full_name": u_name, "user_role": u_role}).execute()
                        st.success(f"Successfully added {u_name} to access roster!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to add user (might be a duplicate name): {e}")
                else:
                    st.warning("User name cannot be empty.")

        # Display and Delete User Frame
        st.markdown("**Current Roster Members**")
        u_res = supabase.table("monitor_users").select("*").order("full_name").execute()
        if u_res.data:
            u_df = pd.DataFrame(u_res.data)
            st.dataframe(u_df[["full_name", "user_role", "inserted_at"]], use_container_width=True, hide_index=True)
            
            st.markdown("**Delete a Team Member**")
            user_to_del = st.selectbox("Select user account to terminate:", [user["full_name"] for user in u_res.data])
            if st.button("Delete Selected User Account", type="primary"):
                supabase.table("monitor_users").delete().eq("full_name", user_to_del).execute()
                st.success(f"Removed account authorization for {user_to_del}.")
                st.rerun()

    # 2. KEYWORD MANAGEMENT LAYOUT
    with tab_keywords:
        st.markdown("### 🔑 Target Keyword Monitoring Framework")
        st.write("Keywords added here will automatically populate the background web crawling monitoring schedules.")
        
        # Add Keyword Form
        with st.form("add_kw_form", clear_on_submit=True):
            st.markdown("**Add New Search Target Phrasing**")
            k_term = st.text_input("Exact Search Query Word/Phrase", placeholder="e.g., AIA Canada Right to Repair")
            k_brands = st.text_input("Associated Impact Brands (Comma Separated)", placeholder="e.g., AIA Canada, CCIF")
            k_theme = st.text_input("Theme Layer Category", placeholder="e.g., Government Relations Legislation")
            
            if st.form_submit_button("Commit Query to Search Engine Index"):
                if k_term.strip():
                    brand_list = [b.strip() for b in k_brands.split(",") if b.strip()]
                    try:
                        supabase.table("monitor_keywords").insert({
                            "term": k_term,
                            "brand_tags": brand_list,
                            "theme_layer": k_theme
                        }).execute()
                        st.success(f"Logged search query target phrase: '{k_term}'")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to index tracking term: {e}")
                else:
                    st.warning("Tracking term cannot be empty.")

        # Display and Delete Keywords
        st.markdown("**Active Scraped Keywords Index Matrix**")
        k_res = supabase.table("monitor_keywords").select("*").order("term").execute()
        if k_res.data:
            k_df = pd.DataFrame(k_res.data)
            st.dataframe(k_df[["term", "brand_tags", "theme_layer"]], use_container_width=True, hide_index=True)
            
            st.markdown("**Remove Search Query Phrase**")
            kw_to_del = st.selectbox("Select tracking query to delete:", [kw["term"] for kw in k_res.data])
            if st.button("Permanently Remove Term From Scraper", type="primary"):
                supabase.table("monitor_keywords").delete().eq("term", kw_to_del).execute()
                st.success(f"Removed target query sequence: '{kw_to_del}'")
                st.rerun()

    # 3. AI REPORT PROMPT TEMPLATE EDITOR
    with tab_templates:
        st.markdown("### 📝 AI Generation System Report Prompt Templates")
        st.write("Tune and adjust the specialized markdown layout rules and constraints that Gemini uses to generate automated corporate intelligence reports.")
        
        t_res = supabase.table("monitor_templates").select("*").order("template_name").execute()
        
        if t_res.data:
            selected_tmpl_name = st.selectbox("Select a report template config to edit:", [t["template_name"] for t in t_res.data])
            current_tmpl = next(t for t in t_res.data if t["template_name"] == selected_tmpl_name)
            
            # Edit Workspace Area
            with st.form("edit_tmpl_form"):
                st.write(f"Editing Prompt Architecture for: **{selected_tmpl_name}**")
                updated_prompt_text = st.text_area(
                    "Gemini System Instruction Matrix Guidance Prompt", 
                    value=current_tmpl["system_instruction_prompt"], 
                    height=300
                )
                
                if st.form_submit_button("Overwrite System Prompt Template Details"):
                    supabase.table("monitor_templates").update({
                        "system_instruction_prompt": updated_prompt_text
                    }).eq("template_name", selected_tmpl_name).execute()
                    st.success("Successfully synchronized and deployed updated AI system prompt configurations!")
                    st.rerun()
