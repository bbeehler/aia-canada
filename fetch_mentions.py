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

# Read timeframe parameter passed from GitHub workflow inputs
chosen_timeframe = os.environ.get("TIMEFRAME_INPUT") or "qdr:w"
print(f"⏰ Active Search Horizon Timeframe Window: {chosen_timeframe}")


def analyze_quality_and_flags(text: str):
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
        "4. TRIAGE RULES & BENEFIT OF THE DOUBT:\n"
        "   - REJECT (is_genuine_match = false) ONLY IF the text explicitly proves it is about something else (e.g., American Institute of Architects, aviation, aerospace, or unrelated climate/finance funds like Carlyle Credit Income Fund).\n"
        "   - ACCEPT (is_genuine_match = true) if the text mentions automotive, cars, aftermarket, mechanics, or Right to Repair.\n"
        "   - STRICT BENEFIT OF THE DOUBT: Search engine snippets are very short. If the text contains the target keyword but is vague or lacks full context, you MUST assume it is genuine and set is_genuine_match to true so a human can review it. Do not over-filter ambiguous snippets.\n"
        "====================================================="
    )

    system_prompt = (
        f"{brand_knowledge_base}\n\n"
        "YOU ARE THE ELITE MEDIA AUDITOR AND COMMUNICATIONS COORDINATOR FOR AIA CANADA.\n"
        "Analyze the provided text context (supports English and French) and determine if this record "
        "is a genuine match for our organization. Return a clean JSON payload matching this schema:\n\n"
        "{\n"
        "  \"is_genuine_match\": true | false,\n"
        "  \"gatekeeper_rationale\": \"A short explanation of why this was approved as genuine or rejected as explicit noise.\",\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"Explanation of sentiment analysis score choice.\",\n"
        "  \"ai_action_recommendation\": \"A strategic tactical recommendation text sentence.\"\n"
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
        
        clean_text = response.text.strip().lstrip("```json").rstrip("```").strip()
        data = json.loads(clean_text)
        
        is_genuine = data.get("is_genuine_match", True)
        print(f"🤖 Gemini Evaluation -> Genuine: {is_genuine} | Rationale: {data.get('gatekeeper_rationale')}")
        
        if not is_genuine:
            supabase.table("mentions").delete().eq("id", mention_id).execute()
            print(f"❌ HARD DELETED noise entry from database: {title[:50]}...")
            return False
            
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
        
        supabase.table("mention_actions").insert({
            "mention_id": mention_id,
            "action_note": f"⚙️ Coordinator Verification: {data.get('gatekeeper_rationale')}",
            "performed_by": "Gemini System Intelligence"
        }).execute()
        
        print(f"✅ SAVED AND ANALYZED target entry successfully: {title[:50]}...")
        return True
        
    except Exception as e:
        print(f"⚠️ Error running processing analytics loop on record {mention_id}: {e}")
        return True


if __name__ == "__main__":
    print("🚀 Ingestion and Triage Engine Initializing...")

    try:
        kw_response = supabase.table("monitor_keywords").select("*").execute()
        target_keywords = kw_response.data
        print(f"📋 Found {len(target_keywords)} tracking keywords active in your database roster.")
    except Exception as e:
        print(f"❌ Failed to query database keywords: {e}")
        target_keywords = []

    if not target_keywords:
        print("🛑 No active tracking keywords discovered. Terminating sweep loop.")
        exit(0)

    for kw in target_keywords:
        query_string = f"{kw['term']} -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca"
        print(f"\n🔍 Pinging Google Serper for: {query_string}")
        
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
                results = res.json().get("organic", [])
                print(f"📡 Serper returned {len(results)} search hit candidates.")
                
                for mention in results:
                    title = mention.get("title", "")
                    url_link = mention.get("link", "")
                    snippet = mention.get("snippet", "")
                    source_platform = mention.get("source", "Web Resource")
                    
                    print(f"   👉 Processing Raw Candidate: \"{title[:50]}\"")
                    
                    raw_date_string = mention.get("date") 
                    if raw_date_string:
                        parsed_date = dateparser.parse(raw_date_string, settings={'RELATIVE_BASE': datetime.now()})
                        date_published = parsed_date.date().isoformat() if parsed_date else datetime.now().date().isoformat()
                    else:
                        date_published = datetime.now().date().isoformat()
                    
                    # FIXED: Added required placeholder fields to satisfy the database constraints!
                    initial_payload = {
                        "title": title,
                        "url": url_link,
                        "outlet_platform": source_platform,
                        "date_published": date_published,
                        "snippet": snippet,
                        "brands_affected": kw['brand_tags'], 
                        "theme": kw['theme_layer'],
                        "status": "pending",
                        "recommendation": "monitor only",
                        "sentiment_category": "Neutral",
                        "sentiment_score": 0.0,
                        "sentiment_rationale": "Pending AI analysis...",
                        "ai_action_recommendation": "Pending AI analysis...",
                        "naming_error_flag": False,
                        "data_conflict_flag": False
                    }
                    
                    try:
                        db_res = supabase.table("mentions").insert(initial_payload).execute()
                        if db_res.data:
                            inserted_id = db_res.data[0]["id"]
                            # Hand off to Gemini to overwrite those placeholders OR delete the row
                            kw_meta = {"term": kw["term"], "brand": kw["brand_tags"], "theme": kw["theme_layer"]}
                            process_and_filter_mention_with_gemini(inserted_id, title, snippet, kw_meta)
                    except Exception as db_err:
                        print(f"   ❌ Initial database ingestion failure: {db_err}")
            else:
                print(f"❌ Serper API returned error code {res.status_code}: {res.text}")
                        
        except Exception as e:
            print(f"❌ Network/Crawl connection failure for phrase '{kw['term']}': {e}")
