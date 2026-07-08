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
    """Deep structural inspection for corporate compliance violations."""
    flags = {"naming_error": False, "data_conflict": False, "conflict_details": ""}
    
    incorrect_variants = ["JPMS", "AIA alone", "AIAC"]
    for variant in incorrect_variants:
        if variant.lower() in text.lower():
            flags["naming_error"] = True
            
    if "$37.8 billion" in text or "$37.8B" in text.upper():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated sector value encountered ($37.8B vs current $43.9B standard)."
        
    if "membership@aiacanada.ca" in text.lower():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated domain suffix used for membership email tracking."
        
    return flags


def process_and_filter_mention_with_gemini(mention_id: str, title: str, snippet: str, keyword_meta: dict):
    """
    Trained Bilingual Media Coordinator Node.
    Evaluates corporate relevance. If irrelevant, hard deletes the row from Supabase.
    If valid, updates the row with rich analysis, sentiment metrics, and recommendations.
    """
    
    brand_knowledge_base = (
        "=== MANDATORY AIA CANADA CORPORATE KNOWLEDGE BASE ===\n"
        "1. PARENT ORGANIZATION:\n"
        "   - English: Automotive Industries Association of Canada (AIA Canada)\n"
        "   - French: L'Association des industries de l'automobile du Canada (AIA Canada)\n"
        "   - Context: Represents the $43.9 Billion automotive aftermarket supply/service chain, auto care, and repair shops.\n\n"
        "2. PROTECTED SUB-BRANDS & ACROYNMS:\n"
        "   - CCIF: Canadian Collision Industry Forum / Forum canadien de l'industrie de la collision.\n"
        "   - I-CAR Canada: Professional collision repair training wing operated directly by AIA Canada.\n"
        "   - YPA: Young Professionals in the Auto care sector community / Le réseau des jeunes professionnels.\n"
        "   - High Fives for Kids: AIA High Fives for Kids Foundation / La Fondation High Fives for Kids d'AIA.\n\n"
        "3. CORE STRATEGIC PILLARS & CAMPAIGNS:\n"
        "   - Right to Repair / Droit à la réparation (Legislation allowing independent shops vehicle data access).\n"
        "   - Automotive skilled trades labor shortages, training programs, EV up-skilling, and collision metrics.\n\n"
        "4. ANTI-NOISE RULES (CRITERIA FOR REJECTION):\n"
        "   - REJECT/DELETE any mentions of the American Institute of Architects (AIA), Aerospace Industries Association, or AIA insurance.\n"
        "   - REJECT/DELETE any mentions of global climate forums or finance metrics named CCIF unless explicitly tied to Canadian collision repair shops.\n"
        "====================================================="
    )

    system_prompt = (
        f"{brand_knowledge_base}\n\n"
        "YOU ARE THE ELITE MEDIA AUDITOR AND COMMUNICATIONS COORDINATOR FOR AIA CANADA.\n"
        "Analyze the provided text context (supports English and French) and determine if this record "
        "is a genuine match for our organization, sub-brands, or pillars. Return a clean JSON payload matching this schema:\n\n"
        "{\n"
        "  \"is_genuine_match\": true | false,\n"
        "  \"gatekeeper_rationale\": \"A short explanation of why this was approved as genuine or rejected as noise.\",\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"Explanation of sentiment analysis score choice.\",\n"
        "  \"ai_action_recommendation\": \"A strategic, 1-2 sentence tactical recommendation explaining exactly what action the team should execute next based on this piece of media.\"\n"
        "}\n"
        "Output raw JSON fields only. Do not format inside markdown blocks or fences."
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
        data = json.loads(response.text)
        is_genuine = data.get("is_genuine_match", True)
        
        if not is_genuine:
            # --- ACTION A: HARD PURGE NOISE FROM THE DATABASE ---
            supabase.table("mentions").delete().eq("id", mention_id).execute()
            print(f" Wiped Noise Record: [ID: {mention_id}] {title[:40]}... (Reason: {data.get('gatekeeper_rationale')})")
            return False
            
        # --- ACTION B: UPDATE REAL RECORDS WITH GEMINI METRICS ---
        flags = analyze_quality_and_flags(title + " " + snippet)
        
        update_payload = {
            "sentiment_category": data.get("category", "Neutral"),
            "sentiment_score": data.get("score", 0.0),
            "sentiment_rationale": data.get("rationale", "Analysis processing complete."),
            "ai_action_recommendation": data.get("ai_action_recommendation", "Monitor tracking loop index."),
            "naming_error_flag": flags["naming_error"],
            "data_conflict_flag": flags["data_conflict"],
            "data_conflict_details": flags["conflict_details"]
        }
        
        supabase.table("mentions").update(update_payload).eq("id", mention_id).execute()
        
        # Log the gatekeeper's confirmation note into history logs table
        supabase.table("mention_actions").insert({
            "mention_id": mention_id,
            "action_note": f"⚙️ Coordinator Verification: {data.get('gatekeeper_rationale')}",
            "performed_by": "Gemini System Intelligence"
        }).execute()
        
        print(f" Verified & Analyzed Record: {title[:40]}... | Saved to Triage.")
        return True
        
    except Exception as e:
        print(f"Smart Processing Node error on record {mention_id}: {e}")
        return True


if __name__ == "__main__":
    print("Automation engine initialized. Fetching live parameters from Supabase...")

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
                    title = mention.get("title", "")
                    url_link = mention.get("link", "")
                    snippet = mention.get("snippet", "")
                    source_platform = mention.get("source", "Web Resource")
                    
                    raw_date_string = mention.get("date") 
                    if raw_date_string:
                        parsed_date = dateparser.parse(raw_date_string, settings={'RELATIVE_BASE': datetime.now()})
                        date_published = parsed_date.date().isoformat() if parsed_date else datetime.now().date().isoformat()
                    else:
                        date_published = datetime.now().date().isoformat()
                    
                    # 1. Ingestion Stage: Save baseline search record to generate a unique record ID
                    initial_payload = {
                        "title": title,
                        "url": url_link,
                        "outlet_platform": source_platform,
                        "date_published": date_published,
                        "snippet": snippet,
                        "brands_affected": kw['brand_tags'], 
                        "theme": kw['theme_layer'],
                        "status": "pending",
                        "recommendation": "monitor only"
                    }
                    
                    try:
                        db_res = supabase.table("mentions").insert(initial_payload).execute()
                        if db_res.data:
                            inserted_id = db_res.data[0]["id"]
                            
                            # 2. Automated Purge & Analysis Stage: Hand off record immediately to Gemini
                            kw_meta = {"term": kw["term"], "brand": kw["brand_tags"], "theme": kw["theme_layer"]}
                            process_and_filter_mention_with_gemini(inserted_id, title, snippet, kw_meta)
                    except Exception as db_err:
                        print(f"Initial row lock error: {db_err}")
                        
        except Exception as e:
            print(f"Crawl failure for phrase '{kw['term']}': {e}")
