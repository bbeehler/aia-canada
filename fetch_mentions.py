Here is your completely updated `fetch_mentions.py` code.

I have seamlessly integrated the **Smart Triage Gatekeeper** logic directly into the script. The script now leverages Gemini to execute a strict, context-aware first-level audit using your specific tracking keyword data.

If Gemini flags a search result as irrelevant noise, it automatically marks the row status as `ignored` and updates the recommendation to `ignore`, keeping your primary triage queue clean while preserving the entry with a detailed audit log note.

```python
import os
from datetime import datetime
import requests
from supabase import create_client
from google import genai
from google.genai import types
import dateparser
import json

# --- 1. SETUP & INITIALIZATION ---
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


def compute_live_sentiment_and_gatekeep_with_gemini(title: str, snippet: str, keyword_meta: dict):
    """
    Executes a high-precision first-level validation audit to eliminate noise.
    Determines if the mention genuinely concerns AIA Canada or its sub-brands.
    """
    system_prompt = (
        "You are a senior media intelligence gatekeeper tracking brand reputation for AIA Canada.\n"
        "Your first job is to run a strict Context Validation Audit to determine if this mention genuinely "
        "concerns the Automotive Industries Association of Canada, its sub-brands (CCIF, I-CAR Canada, YPA/Young Professionals Auto Care), "
        "or relevant Canadian automotive aftermarket sector pillars (e.g., Right to Repair legislation, skilled trades training).\n\n"
        
        f"Target context clues to evaluate against:\n"
        f"- Target Phrase Used: '{keyword_meta.get('term')}'\n"
        f"- Expected Sub-Brands: {keyword_meta.get('brand')}\n"
        f"- Expected Theme Area: '{keyword_meta.get('theme')}'\n\n"
        
        "Analyze the headline and snippet text context carefully. Output a raw JSON payload matching this schema exactly:\n"
        "{\n"
        "  \"is_genuine_match\": true | false,\n"
        "  \"gatekeeper_rationale\": \"A concise single sentence explaining why this is a real brand match or why it is irrelevant noise.\",\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"A single clear sentence explaining your dynamic analysis decision.\",\n"
        "  \"ai_action_recommendation\": \"A strategic, 1-2 sentence tactical recommendation explaining exactly what action the team should execute next based on this piece of media.\"\n"
        "}\n"
        "Output raw JSON data files only. Do not wrap inside markdown blocks or code fences."
    )
    
    # Baseline fallback parameters if the API execution encounters an error
    fallback = (True, "Standard baseline processing bypass applied.", "Neutral", 0.0, "Baseline log verification.", "Monitor tracking loop index.")
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[f"Headline: {title}\nExcerpt Snippet: {snippet}"],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json"
            )
        )
        data = json.loads(response.text)
        return (
            data.get("is_genuine_match", True),
            data.get("gatekeeper_rationale", "Context confirmed via automated routing verification rules."),
            data.get("category", "Neutral"),
            data.get("score", 0.0),
            data.get("rationale", "Analysis processing complete."),
            data.get("ai_action_recommendation", "Monitor tracking loop index; no emergency action required.")
        )
    except Exception as e:
        print(f"Smart Triage Gatekeeper execution failure: {e}")
        return fallback


def process_and_save_mention(live_item, keyword_meta):
    title = live_item.get("title", "")
    url_link = live_item.get("link", "")
    snippet = live_item.get("snippet", "")
    source_platform = live_item.get("source", "Web Resource")
    
    # Extract Google Serper's raw publication date attribute text string
    raw_date_string = live_item.get("date") 
    
    # Parse relative phrasing like "3 hours ago" or "Yesterday" into an explicit date object
    if raw_date_string:
        parsed_date = dateparser.parse(
            raw_date_string, 
            settings={'RELATIVE_BASE': datetime.now()}
        )
        date_published = parsed_date.date().isoformat() if parsed_date else datetime.now().date().isoformat()
    else:
        date_published = datetime.now().date().isoformat()
        
    # Run the Smart Triage Gatekeeper evaluation
    is_genuine, gate_reason, category, score, rationale, ai_rec = compute_live_sentiment_and_gatekeep_with_gemini(
        title, snippet, keyword_meta
    )
    
    # Route status and recommendation dynamically based on gatekeeper determination
    initial_status = "pending" if is_genuine else "ignored"
    initial_recommendation = "monitor only" if is_genuine else "ignore"
    
    flags = analyze_quality_and_flags(title + " " + snippet)
    
    payload = {
        "title": title,
        "url": url_link,
        "outlet_platform": source_platform,
        "date_published": date_published,
        "snippet": snippet,
        "brands_affected": keyword_meta['brand'], 
        "theme": keyword_meta['theme'],
        "sentiment_category": category,
        "sentiment_score": score,
        "sentiment_rationale": rationale,
        "ai_action_recommendation": ai_rec,
        "naming_error_flag": flags["naming_error"],
        "data_conflict_flag": flags["data_conflict"],
        "data_conflict_details": flags["conflict_details"],
        "status": initial_status,
        "recommendation": initial_recommendation
    }
    
    try:
        res = supabase.table("mentions").insert(payload).execute()
        inserted_row_id = res.data[0]["id"] if res.data else None
        
        # Log Gemini's filter justification note into the action log audit table permanently
        if inserted_row_id:
            supabase.table("mention_actions").insert({
                "mention_id": inserted_row_id,
                "action_note": f"🤖 Smart Triage Audit Notice: {gate_reason}",
                "performed_by": "Gemini Gatekeeper"
            }).execute()
            
        print(f"Successfully logged mention: {title} | Routing Status: {initial_status.upper()}")
    except Exception as e:
        print(f"Database insertion error: {e}")


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
        query_string = f"{kw['term']} -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca"
        url = "https://google.serper.dev/search"
        
        payload = {
            "q": query_string, 
            "num": 5,
            "tbs": chosen_timeframe  
        }
        headers = {'X-API-KEY': os.environ.get("SERPER_API_KEY"), 'Content-Type': 'application/json'}
        
        try:
            res = requests.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                for mention in res.json().get("organic", []):
                    # Package metadata variables using database list keys
                    kw_meta = {
                        "term": kw["term"],
                        "brand": kw["brand_tags"], 
                        "theme": kw["theme_layer"]
                    }
                    process_and_save_mention(mention, kw_meta)
        except Exception as e:
            print(f"Crawl failure for phrase '{kw['term']}': {e}")

```
