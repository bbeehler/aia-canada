import streamlit as st
import os
from supabase import create_client, Client
from google import genai
from google.genai import types

# 1. Initialize Clients Safely
@st.cache_resource
def init_connections():
    sb_url = st.secrets["SUPABASE_URL"]
    sb_key = st.secrets["SUPABASE_KEY"]
    gemini_key = st.secrets["GEMINI_API_KEY"]
    
    sb_client = create_client(sb_url, sb_key)
    gem_client = genai.Client(api_key=gemini_key)
    return sb_client, gem_client

supabase, ai_client = init_connections()

st.sidebar.header("Navigation")
app_mode = st.sidebar.radio("Go to", ["Weekly Summarizer", "Database Q&A Assistant"])

# --- MODULE A: WEEKLY SUMMARIZER ENGINE ---
if app_mode == "Weekly Summarizer":
    st.subheader("📝 Template 2: Weekly Trend Summary Generator")
    st.write("This engine reads recent processed mentions from Supabase and applies the AIA Canada formal analysis templates.")
    
    if st.button("Generate Weekly Analysis with Gemini"):
        with st.spinner("Analyzing data and generating report structural layers..."):
            # Fetch last 7 days of non-pending logs
            raw_data = supabase.table("mentions").select("*").neq("status", "pending").limit(50).execute()
            
            if not raw_data.data:
                st.warning("No recent processed tracking data was found in Supabase to analyze.")
            else:
                # Compile structural data context payload for Gemini
                data_payload = str(raw_data.data)
                
                # Instruction prompt feeding standard formatting rules to Gemini
                system_instruction = (
                    "You are the senior media analyst for AIA Canada. Your job is to draft a Weekly Trend Summary. "
                    "Follow these rules strictly:\n"
                    "1. State facts plainly; never editorialize or assign motives.\n"
                    "2. Always name explicitly which sub-brands are affected (AIA Canada, YPA, CCIF, I-CAR Canada).\n"
                    "3. Cite or highlight any naming errors or open data conflicts encountered in the logs.\n"
                    "4. Use Canadian Press (CP) spelling styles (e.g., colour, behaviour, per cent).\n"
                    "5. Format your output exactly matching Template 2 requirements: Executive Summary, Volume & Sentiment Breakdown table, Top Mentions table, and Competitor Coverage summary."
                )
                
                # Call Gemini
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[f"Here is the database log snippet from the last 7 days: {data_payload}"],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction
                    )
                )
                
                # Render complete markdown artifact safely
                st.success("Draft Generated Successfully!")
                st.markdown(response.text)
                
                # Archive the generated text context for audit tracking
                if st.button("Save and Archive Generated Report"):
                    supabase.table("reports").insert({
                        "report_type": "Weekly Trend Summary",
                        "markdown_content": response.text
                    }).execute()
                    st.toast("Report securely archived in reports historical table!")

# --- MODULE B: DATABASE Q&A ASSISTANT ---
elif app_mode == "Database Q&A Assistant":
    st.subheader("💬 Ask Your Media Database")
    st.write("Ask questions about brand reputation, trends, or specific watch-list hits.")
    
    user_query = st.text_input("Example: Are there any recent data conflicts regarding our $43.9 billion industry valuation?")
    
    if user_query:
        with st.spinner("Analyzing database items..."):
            # Fetch raw elements for contextual grounding
            all_mentions = supabase.table("mentions").select("title, outlet_platform, theme, sentiment_category, data_conflict_details, data_conflict_flag").limit(100).execute()
            db_context = str(all_mentions.data)
            
            qa_instruction = (
                "You are an AI assistant helping AIA Canada team members look through their media monitoring records. "
                "Answer the user query completely using only the database context provided. If the information is not present, "
                "state that clearly without fabricating insights. Maintain a supportive, smart tone."
            )
            
            response = ai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[f"Database context entries:\n{db_context}\n\nUser Question: {user_query}"],
                config=types.GenerateContentConfig(
                    system_instruction=qa_instruction
                )
            )
            
            st.markdown("### 🤖 Assistant Response")
            st.write(response.text)
