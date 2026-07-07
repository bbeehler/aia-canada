import os
from datetime import datetime
import requests
from supabase import create_client
from google import genai
from google.genai import types

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)
ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Read timeframe parameter passed from GitHub workflow inputs (defaults to 'qdr:w' if scheduled)
chosen_timeframe = os.environ.get("TIMEFRAME_INPUT") or "qdr:w"

def analyze_quality_and_flags(text: str):
    flags = {"naming_error": False, "data_conflict": False, "conflict_details": ""}
    incorrect_variants = ["JPMS", "AIA alone", "AIAC", "Forum canadien de l'industrie de la collision"]
    for variant in incorrect_variants:
        if variant.lower() in text.lower():
            flags["naming_error"] = True
    if "$37.8 billion" in text or "$37.8B" in text.upper():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated sector value ($37.8B vs $43.9B cited)."
    if "membership@aiacanada.ca" in text.lower():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated domain suffix used for membership email."
    return flags

def compute_live_sentiment_with_gemini(title: str, snippet: str):
    """Leverages Gemini to extract sentiment metrics and clear strategic action recommendations[cite: 2]."""
    system_prompt = (
        "You are an expert PR and media monitoring AI tracking brand reputation for AIA Canada[cite: 2]. "
        "Analyze the provided headline and snippet text context and return a clean JSON payload matching this exact schema:\n"
        "{\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"A single clear sentence explaining your dynamic analysis decision.\",\n"
        "  \"ai_action_recommendation\": \"A strategic, 1-2 sentence tactical recommendation explaining exactly what action the team should execute next based on this piece of media (e.g., draft a holding statement, log and add to the weekly report, ignore as industry noise, reach out for light-touch correction). Keep it highly actionable.\"\n"
        "}\n"
        "Output raw JSON data files only. Do not format inside markdown blocks."
    )
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[f"Headline: {title}\nExcerpt Snippet: {snippet}"],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json"
            )
        )
        import json
        data = json.loads(response.text)
        return (
            data.get("category", "Neutral"), 
            data.get("score", 0.0), 
            data.get("rationale", "Standard automated processing baseline."),
            data.get("ai_action_recommendation", "Monitor tracking index; no emergency action required.")
        )
    except Exception as e:
        print(f"Gemini evaluation failure: {e}")
        return "Neutral", 0.0, "Analysis fallback loops applied.", "Monitor only."

def process_and_save_mention(live_item, keyword_meta):
    title = live_item.get("title", "")
    url_link = live_item.get("link", "")
    snippet = live_item.get("snippet", "")
    source_platform = live_item.get("source", "Web Resource")
    
    # 1. Quality Assurance inspection
    flags = analyze_quality_and_flags(title + " " + snippet)
    
    # 2. Extract metrics AND the new AI recommendation column text
    category, score, rationale, ai_rec = compute_live_sentiment_with_gemini(title, snippet)
    
    payload = {
        "title": title,
        "url": url_link,
        "outlet_platform": source_platform,
        "date_published": datetime.now().date().isoformat(),
        "snippet": snippet,
        "brands_affected": keyword_meta['brand'], 
        "theme": keyword_meta['theme'],
        "sentiment_category": category,
        "sentiment_score": score,
        "sentiment_rationale": rationale,
        "ai_action_recommendation": ai_rec,  # <-- CRUCIAL: Make sure this exactly matches your new DB column
        "naming_error_flag": flags["naming_error"],
        "data_conflict_flag": flags["data_conflict"],
        "data_conflict_details": flags["conflict_details"],
        "status": "pending"
    }
    
    try:
        supabase.table("mentions").insert(payload).execute()
        print(f"Successfully logged mention: {title}")
    except Exception as e:
        # Changed this print line to reveal the ACTUAL error if database rejects it
        print(f"Database insertion error for '{title}': {e}")

if __name__ == "__main__":
    print("Automation engine initialized. Fetching live parameters from Supabase...")

    # Query dynamic user-defined keyword targets from database row records
    try:
        kw_response = supabase.table("monitor_keywords").select("*").execute()
        target_keywords = kw_response.data
    except Exception as e:
        print(f"Failed to query database keywords: {e}")
        target_keywords = []

    if not target_keywords:
        print("No active tracking keywords discovered in configuration tables. Terminating sweep.")
        exit(0)

    for kw in target_keywords:
        # term, brand_tags, and theme_layer are loaded straight from the database row entries
        query_string = f"{kw['term']} -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca"
        url = "https://google.serper.dev/search"
        
        payload = {"q": query_string, "num": 5}
        headers = {'X-API-KEY': os.environ.get("SERPER_API_KEY"), 'Content-Type': 'application/json'}
        
        try:
            res = requests.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                for mention in res.json().get("organic", []):
                    # Package metadata variables using database list keys
                    kw_meta = {"brand": kw["brand_tags"], "theme": kw["theme_layer"]}
                    process_and_save_mention(mention, kw_meta)
        except Exception as e:
            print(f"Crawl failure for phrase '{kw['term']}': {e}")
