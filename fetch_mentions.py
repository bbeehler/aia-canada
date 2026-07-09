import os
from datetime import datetime
import requests
from supabase import create_client
from google import genai
from google.genai import types
import dateparser
import json
import time 

# --- 1. SETUP & INITIALIZATION ---
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)
ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

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
    """
    Trained Bilingual Media Coordinator Node.
    Saves your quota by marking noise instead of deleting it, allowing future blocks.
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
        "4. ANTI-NOISE RULES:\n"
        "   - REJECT (is_genuine_match = false) ANY mentions contextually referring to the American Institute of Architects (AIA), Aerospace Industries Association, AIA insurance, or financial funds like Carlyle Credit Income Fund (CCIF).\n"
        "====================================================="
    )

    system_prompt = (
        f"{brand_knowledge_base}\n\n"
        "YOU ARE THE ELITE MEDIA AUDITOR AND COMMUNICATIONS COORDINATOR FOR AIA CANADA.\n"
        "Your job is to determine if this search result is a genuine match for our organization or an unrelated false positive.\n\n"
        f"🎯 SEARCH QUERY CONTEXT MATRIX:\n"
        f"- Specific Keyword Found: '{keyword_meta.get('term')}'\n"
        f"- Expected Impact Brands: {keyword_meta.get('brand')}\n"
        f"- Target Theme Category: '{keyword_meta.get('theme')}'\n\n"
        "TRIAGE DIRECTION:\n"
        "- If the text mentions the target keyword but contextually refers to architecture, aviation, finance funds, or anything outside the Canadian auto care/aftermarket industry, set is_genuine_match to false.\n"
        "- If it is a short, vague snippet containing our keyword and cannot be explicitly verified as noise, give it the benefit of the doubt and set is_genuine_match to true for human review.\n\n"
        "Return a clean JSON payload matching this schema exactly:\n"
        "{\n"
        "  \"is_genuine_match\": true | false,\n"
        "  \"gatekeeper_rationale\": \"A short explanation of why this was approved as genuine or rejected as noise.\",\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"Explanation of sentiment analysis score choice.\",\n"
        "  \"ai_action_recommendation\": \"A strategic tactical recommendation text sentence.\"\n"
        "}\n"
        "Output raw JSON fields only. Do not format inside markdown blocks or fences."
    )
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-3",
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
            # --- QUOTA SAVER CHANGE: UPDATE STATUS TO 'NOISE' INSTEAD OF HARD DELETING ---
            supabase.table("mentions").update({
                "status": "noise",
                "recommendation": "ignore",
                "sentiment_rationale": f"Suppressed Noise: {data.get('gatekeeper_rationale')}"
            }).eq("id", mention_id).execute()
            print(f"🤫 Noise Suppressed (URL locked to shield future requests): {title[:50]}...")
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
                    
                    # --- THE TOTAL QUOTA SHIELD ---
                    # Blocks repeat visits to processed articles AND repeat visits to known noise URLs
                    try:
                        dup_check = supabase.table("mentions").select("id, status").eq("url", url_link).execute()
                        if dup_check.data:
                            print(f"   ⏭️ TOTAL QUOTA SAVED: URL already matches history log index layer (Status: '{dup_check.data[0]['status']}'). Skipping Gemini API calls entirely.")
                            continue
                    except Exception as db_check_err:
                        print(f"   ⚠️ Duplicate check failed: {db_check_err}")
                    
                    raw_date_string = mention.get("date") 
                    if raw_date_string:
                        parsed_date = dateparser.parse(raw_date_string, settings={'RELATIVE_BASE': datetime.now()})
                        date_published = parsed_date.date().isoformat() if parsed_date else datetime.now().date().isoformat()
                    else:
                        date_published = datetime.now().date().isoformat()
                    
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
                            kw_meta = {"term": kw["term"], "brand": kw["brand_tags"], "theme": kw["theme_layer"]}
                            
                            process_and_filter_mention_with_gemini(inserted_id, title, snippet, kw_meta)
                            
                            print("💤 Sleeping 4 seconds to comply with API rate limits...")
                            time.sleep(4)
                            
                    except Exception as db_err:
                        print(f"   ❌ Initial database ingestion failure: {db_err}")
            else:
                print(f"❌ Serper API returned error code {res.status_code}: {res.text}")
                        
        except Exception as e:
            print(f"❌ Network/Crawl connection failure for phrase '{kw['term']}': {e}")
