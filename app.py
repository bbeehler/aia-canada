import streamlit as st
import requests
import pandas as pd
import os
from supabase import create_client, Client
from google import genai
from google.genai import types

# --- 1. CONFIGURATION & APP INITIALIZATION ---
st.set_page_config(
    page_title="AIA Canada Media Monitor", 
    layout="wide",
    page_icon="📊"
)

# Repository coordinates for manual dispatch tracking
GITHUB_REPO = "bbeehler/aia-canada"  
WORKFLOW_FILE = "monitor.yml"

@st.cache_resource
def init_connections():
    """Safely initializes standard connections to Supabase and Google GenAI."""
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

# --- 2. SIDEBAR UTILITIES & WORKFLOW TRIGGER ---
st.sidebar.title("📊 AIA Canada Monitor")
st.sidebar.caption("Media Tracking & Analytics Platform")

st.sidebar.markdown("---")
st.sidebar.subheader("🔄 Manual Data Sync")

# Dropdown menu to select the timeframe scope
timeframe_label = st.sidebar.selectbox(
    "Select Search Horizon Window:",
    ["Past 24 Hours", "Past Week", "Past Month", "Past Year"]
)

# Map human labels directly to standard Google parameters used by Serper (tbs configuration)
timeframe_map = {
    "Past 24 Hours": "qdr:d",
    "Past Week": "qdr:w",
    "Past Month": "qdr:m",
    "Past Year": "qdr:y"
}
selected_tbs = timeframe_map[timeframe_label]

def trigger_github_sync(tbs_val):
    """Triggers the automated Python monitoring script via GitHub Actions API, passing the selected timeframe."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {st.secrets['GITHUB_PAT']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    # Passing inputs payload containing the time constraint filter values
    data = {
        "ref": "main",
        "inputs": {
            "timeframe": tbs_val
        }
    } 
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 204:
            st.sidebar.success(f"🚀 Sync ({timeframe_label}) triggered on GitHub! Fresh data will appear in ~1 minute.")
        else:
            st.sidebar.error(f"❌ API Error: {response.status_code}")
            st.sidebar.caption(response.text)
    except Exception as e:
        st.sidebar.error(f"Connection failed: {e}")

st.sidebar.write("Force an immediate web crawl and database collection layer update:")
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
        "💬 Database Q&A Assistant"
    ]
)

# --- GLOBAL UTILITY OPERATIONS ---
def delete_mention_record(record_id):
    """Permanently deletes an explicit row entry from the Supabase tracking table."""
    try:
        supabase.table("mentions").delete().eq("id", record_id).execute()
        st.toast("Mention successfully removed from the tracking index!")
    except Exception as e:
        st.error(f"Failed to execute row deletion layer: {e}")

# --- 3. MODULE 1: INBOX / TRIAGE ---
if app_mode == "📥 Inbox / Triage":
    st.subheader("📥 Unprocessed Mention Queue")
    st.write("Review, classify, and audit incoming raw brand tracking records.")
    
    response = supabase.table("mentions").select("*").eq("status", "pending").order("inserted_at", desc=True).execute()
    mentions = response.data
    
    if not mentions:
        st.success("All caught up! No pending un-triaged mentions found in the queue.")
    else:
        st.info(f"Found {len(mentions)} unprocessed mention records requiring validation.")
        for m in mentions:
            with st.expander(f"🔍 {m['outlet_platform']} | {m['title']} (Published: {m['date_published']})"):
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**URL:** [Open Original Snippet Source]({m['url']})")
                    st.write(f"**Brands Explicitly Affected:** {', '.join(m['brands_affected']) if m['brands_affected'] else 'None Specified'}")
                    st.write(f"**Identified Theme Layer:** {m['theme']}")
                    st.write(f"**Raw Metric Snippet:** *\"{m['snippet']}\"*")
                with col2:
                    st.write(f"**Inferred Sentiment:** `{m['sentiment_category']}` (Score: {m['sentiment_score']})")
                    st.write(f"**Sentiment Rationale:** {m['sentiment_rationale']}")
                    
                    if m['naming_error_flag'] or m['data_conflict_flag']:
                        st.warning(f"⚠️ **Quality Flag Raised:** {m['data_conflict_details'] or 'Incorrect branding variation used.'}")
                
                st.markdown("---")
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    new_rec = st.selectbox("Assign Action Item", ["monitor only", "engage", "share", "ignore"], index=0, key=f"rec_{m['id']}")
                with c2:
                    new_level = st.selectbox("Assign Severity Level", ["Low", "Medium", "High", "Critical"], key=f"lvl_{m['id']}")
                with c3:
                    new_status = st.selectbox("Update Tracking Status", ["logged", "escalated", "resolved"], key=f"stat_{m['id']}")
                with c4:
                    st.write("")  # Vertical layout spacing alignment
                    st.write("")
                    if st.button("🗑️ Delete Mention", key=f"del_{m['id']}", use_container_width=True):
                        delete_mention_record(m['id'])
                        st.rerun()
                
                if st.button("Commit Classification Details", key=f"btn_{m['id']}", use_container_width=True):
                    supabase.table("mentions").update({
                        "recommendation": new_rec,
                        "alert_level": new_level,
                        "status": new_status
                    }).eq("id", m['id']).execute()
                    st.toast("Mention details successfully updated!")
                    st.rerun()

# --- MODULE 2: REVIEWED DATABASE TABLE ---
elif app_mode == "📋 Reviewed Database Table":
    st.subheader("📋 Reviewed Mentions Archive")
    st.write("Below is the consolidated matrix containing all processed, evaluated, and classified mentions.")
    
    # Retrieve everything that has advanced beyond a 'pending' state
    response = supabase.table("mentions").select("*").neq("status", "pending").order("inserted_at", desc=True).execute()
    reviewed_data = response.data
    
    if not reviewed_data:
        st.info("No reviewed tracking logs discovered inside archived index tables.")
    else:
        # Convert JSON objects array directly into structured Pandas display frames
        df = pd.DataFrame(reviewed_data)
        
        # Isolate key display layers for clean scannability
        display_columns = [
            "date_published", "outlet_platform", "title", "theme", 
            "sentiment_category", "sentiment_score", "alert_level", "status", "recommendation"
        ]
        
        st.dataframe(df[display_columns], use_container_width=True, hide_index=True)
        
        # Management removal controls deck
        st.markdown("---")
        st.subheader("🛠️ Record Management Panel")
        
        select_to_delete = st.selectbox(
            "Select a specific reviewed mention to permanently delete:", 
            [r['title'] for r in reviewed_data]
        )
        target_record = next(r for r in reviewed_data if r['title'] == select_to_delete)
        
        if st.button("Delete Selected Archive Record", type="primary"):
            delete_mention_record(target_record['id'])
            st.rerun()

# --- 4. MODULE 3: DAILY CRISIS CENTER ---
elif app_mode == "🚨 Daily Crisis Center":
    st.subheader("🚨 Template 1: Daily Crisis Alert Generator")
    st.write("Escalate high-priority brand risks, boilerplate alignment problems, or factual misattributions.")
    
    response = supabase.table("mentions").select("*").in_("alert_level", ["High", "Critical"]).neq("status", "resolved").execute()
    crisis_items = response.data
    
    if not crisis_items:
        st.success("Excellent. No high-severity or critical items require manual escalation alerts right now.")
    else:
        selected_title = st.selectbox("Select high-priority mention to format:", [c['title'] for c in crisis_items])
        item = next(c for c in crisis_items if c['title'] == selected_title)
        
        st.markdown("### 📋 Formatted Crisis Escalation Draft")
        
        subject_line = f"SUBJECT: [{item['alert_level'].upper()} RISK ALERT] {item['title']} - {item['date_published']}"
        st.text_input("Recommended Subject Line", subject_line)
        
        # Format layout using standard structural requirements
        markdown_body = f"""**Alert Level:** {item['alert_level']}
**Brand(s) affected:** {', '.join(item['brands_affected'])}
**Time detected:** {item['inserted_at']}
**Source:** {item['outlet_platform']} - [Source Link]({item['url']})

### WHAT HAPPENED
{item['snippet']}

### WHY IT MATTERS
* **Audience(s) exposed:** Members | Government/Policymakers | General public
* **Sentiment status:** {item['sentiment_category']}
* **Context Rationale:** {item['sentiment_rationale']}

### RELATED WATCH-LIST HITS
* **Keyword/Theme Triggered:** {item['theme']}
* **Naming or Data-Conflict Issue Involved?** {"Yes - " + item['data_conflict_details'] if item['data_conflict_flag'] else "No"}

### RECOMMENDED IMMEDIATE ACTION
[ ] No action needed, monitor only
[ ] Draft corporate holding statement
[ ] Loop in Communications for response
[X] Escalate to Director, Digital Marketing, Communications and Engagement
{"[X] Escalate to President (Emily Chung) - Critical Priority Input Weight Required" if item['alert_level'] == "Critical" else "[ ] Escalate to President (Emily Chung)"}
"""
        st.text_area("Markdown Summary Text Content", markdown_body, height=400)
        
        if st.button("Log and Register Escalation Report", use_container_width=True):
            supabase.table("reports").insert({
                "report_type": f"Daily Crisis Alert: {item['alert_level']}",
                "markdown_content": markdown_body
            }).execute()
            st.success("Crisis operational report safely archived into management table!")

# --- 5. MODULE 4: WEEKLY SUMMARIZER ---
elif app_mode == "📝 Weekly Summarizer":
    st.subheader("📝 Template 2: Weekly Trend Summary Engine")
    st.write("Leverages Gemini to synthesize the weekly trend report from recent database data tracking records.")
    
    if st.button("Generate Weekly Trend Analysis Document", use_container_width=True):
        with st.spinner("Compiling transaction histories and processing with Gemini..."):
            raw_data = supabase.table("mentions").select("*").neq("status", "pending").limit(100).execute()
            
            if not raw_data.data:
                st.warning("No validated tracking records were discovered within the data tables to analyze.")
            else:
                data_string_context = str(raw_data.data)
                
                system_instruction = (
                    "You are the senior media monitoring AI analyst for AIA Canada. Draft a Weekly Trend Summary "
                    "grounded strictly in the historical database records provided. Follow these structural constraints:\n"
                    "1. State facts and analytical findings plainly; do not editorialize or infer intent.\n"
                    "2. Explicitly name exactly which sub-brands are impacted (AIA Canada, YPA, CCIF, I-CAR Canada).\n"
                    "3. Flag and highlight any ongoing data valuation conflicts (e.g., $37.8B vs current $43.9B standard) or naming errors.\n"
                    "4. Use Canadian Press (CP) stylistic configurations (British/Canadian spelling: colour, behaviour, per cent).\n"
                    "5. Build exact structural outputs containing: Executive Summary, Volume & Sentiment Overview markdown matrix, Top Mentions table, Watch-List Performance metrics, and Action Recommendations for next week."
                )
                
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[f"Database transactional tracking logs for processing: {data_string_context}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction
                    )
                )
                
                st.session_state["latest_weekly_report"] = response.text
                st.success("Weekly Analysis Generation Complete!")
                
    if "latest_weekly_report" in st.session_state:
        st.markdown("### Generated Report Preview")
        st.markdown(st.session_state["latest_weekly_report"])
        
        if st.button("Archive Report Output File", use_container_width=True):
            supabase.table("reports").insert({
                "report_type": "Weekly Trend Summary",
                "markdown_content": st.session_state["latest_weekly_report"]
            }).execute()
            st.toast("Report successfully logged to reports data table!")

# --- 6. MODULE 5: DATABASE Q&A ASSISTANT ---
elif app_mode == "💬 Database Q&A Assistant":
    st.subheader("💬 Ask Your Media Monitoring Database")
    st.write("Query your database tracking index using generative intelligence grounding.")
    
    user_query = st.text_input(
        "Enter your tracking question:", 
        placeholder="Example: Have there been any recent complaints or comments regarding membership costs?"
    )
    
    if user_query:
        with st.spinner("Scanning indexing contexts and formulating answer..."):
            all_mentions = supabase.table("mentions").select("title, outlet_platform, theme, sentiment_category, data_conflict_details, recommendation, alert_level").limit(150).execute()
            db_payload_context = str(all_mentions.data)
            
            qa_instruction = (
                "You are an intelligent data specialist tracking brand awareness for AIA Canada. Your role is to answer user analytical "
                "queries completely and truthfully based only on the literal database lists and context blocks provided. If an insight or record "
                "is absent or completely outside the data scope, explain that clearly without fabricating assumptions or conclusions."
            )
            
            response = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[f"Grounding Data Context Scope:\n{db_payload_context}\n\nUser Search Query: {user_query}"],
                config=types.GenerateContentConfig(
                    system_instruction=qa_instruction
                )
            )
            
            st.markdown("### 🤖 Assistant Answer")
            st.info(response.text)
