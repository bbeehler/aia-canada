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
    """Deep structural inspection for known compliance violations."""
    flags = {"naming_error": False, "data_conflict": False, "conflict_details": ""}
    
    # Flag known improper phrasing variants
    incorrect_variants = ["JPMS", "AIA alone", "AIAC"]
    for variant in incorrect_variants:
        if variant.lower() in text.lower():
            flags["naming_error"] = True
            
    # Economic valuation guardrails
    if "$37.8 billion" in text or "$37.8B" in text.upper():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated sector value encountered ($37.8B vs current $43.9B standard)."
        
    if "membership@aiacanada.ca" in text.lower():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated domain suffix used for membership email tracking."
        
    return flags


def compute_live_sentiment_and_gatekeep_with_gemini(title: str, snippet: str, keyword_meta: dict):
    """
    Trained Bilingual Media Coordinator Node.
    Uses an explicit brand dictionary to evaluate true corporate relevance.
    """
    
    # Injection of the Master Corporate Knowledge Base
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
        "4. FALSE POSITIVE WARNINGS (ANTI-NOISE RULES):\n"
        "   - IGNORE mentions of the American Institute of Architects (AIA), Aerospace Industries Association, or AIA insurance.\n"
        "   - IGNORE mentions of global climate forums or finance metrics named CCIF unless explicitly tied to Canadian collision repair shops.\n"
        "====================================================="
    )

    system_prompt = (
        f"{brand_knowledge_base}\n\n"
        "YOU ARE THE ELITE MEDIA AUDITOR AND COMMUNICATIONS COORDINATOR FOR AIA CANADA.\n"
        "Your first mission is to perform a context-aware triage audit. Evaluate if the provided media text "
        "is a genuine match for our organization or sub-brands based strictly on the Corporate Knowledge Base above.\n\n"
        
        "CRITERIA FOR REJECTION (is_genuine_match = false):\n"
        "- The text uses an acronym (AIA, CCIF, YPA) but contextually refers to architects, aviation, insurance, or general finance.\n"
        "- The text has zero relation to the Canadian automotive aftermarket, collision sector, or repair landscape.\n\n"
        
        "CRITERIA FOR APPROVAL (is_genuine_match = true):\n"
        "- The text refers directly to our organization, sub-brands, division events, or core advocacy pillars (Right to Repair).\n\n"
        
        "Analyze the text context (supports English and French) and return a clean JSON payload matching this schema exactly:\n"
        "{\n"
        "  \"is_genuine_match\": true | false,\n"
        "  \"gatekeeper_rationale\": \"A clear bilingual explanation of why this was approved or rejected as noise.\",\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"Explanation of sentiment analysis score choice.\",\n"
        "  \"ai_action_recommendation\": \"A strategic, 1-2 sentence tactical recommendation explaining exactly what action the team should execute next (e.g., Engage via PR, Draft holding statement, Log for Weekly Trend Summary, Ignore as noise).\"\n"
        "}\n"
        "Output raw JSON fields only. Do not format inside markdown blocks or fences."
    )
    
    fallback = (True, "Bypass applied via baseline processing rules.", "Neutral", 0.0, "Baseline check.", "Log tracking entry.")
    
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
        print(f"Smart Triage Coordinator execution failure: {e}")
        return fallback


def process_and_save_mention(live_item, keyword_meta):
    title = live_item.get("title", "")
    url_link = live_item.get("link", "")
    snippet = live_item.get("snippet", "")
    source_platform = live_item.get("source", "Web Resource")
    
    raw_date_string = live_item.get("date") 
    if raw_date_string:
        parsed_date = dateparser.parse(raw_date_string, settings={'RELATIVE_BASE': datetime.now()})
        date_published = parsed_date.date().isoformat() if parsed_date else datetime.now().date().isoformat()
    else:
        date_published = datetime.now().date().isoformat()
        
    # Execute the trained gatekeeper evaluation 
    is_genuine, gate_reason, category, score, rationale, ai_rec = compute_live_sentiment_and_gatekeep_with_gemini(
        title, snippet, keyword_meta
    )
    
    # Route tracking parameters contextually
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
        
        if inserted_row_id:
            supabase.table("mention_actions").insert({
                "mention_id": inserted_row_id,
                "action_note": f"⚙️ Coordinator Triage Audit: {gate_reason}",
                "performed_by": "Gemini System Intelligence"
            }).execute()
            
        print(f"Logged: {title[:40]}... | Status: {initial_status.upper()}")
    except Exception as e:
        print(f"Database insertion error: {e}")


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
                    kw_meta = {
                        "term": kw["term"],
                        "brand": kw["brand_tags"], 
                        "theme": kw["theme_layer"]
                    }
                    process_and_save_mention(mention, kw_meta)
        except Exception as e:
            print(f"Crawl failure for phrase '{kw['term']}': {e}")
