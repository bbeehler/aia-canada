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
    system_prompt = (
        "You are an AI data pipeline engine checking media tracking snippets for AIA Canada. "
        "Analyze the text and return JSON exactly: {\"category\": \"Positive\"|\"Neutral\"|\"Negative\"|\"Mixed\", \"score\": float, \"rationale\": \"string\"}"
    )
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[f"Headline: {title}\nSnippet: {snippet}"],
            config=types.GenerateContentConfig(system_instruction=system_prompt, response_mime_type="application/json")
        )
        import json
        data = json.loads(response.text)
        return data.get("category", "Neutral"), data.get("score", 0.0), data.get("rationale", "")
    except Exception:
        return "Neutral", 0.0, "Fallback configuration execution."

def process_and_save_mention(live_item, keyword_meta):
    title = live_item.get("title", "")
    url_link = live_item.get("link", "")
    snippet = live_item.get("snippet", "")
    source_platform = live_item.get("source", "Web Resource")
    
    flags = analyze_quality_and_flags(title + " " + snippet)
    category, score, rationale = compute_live_sentiment_with_gemini(title, snippet)
    
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
        "naming_error_flag": flags["naming_error"],
        "data_conflict_flag": flags["data_conflict"],
        "data_conflict_details": flags["conflict_details"],
        "status": "pending"
    }
    try:
        supabase.table("mentions").insert(payload).execute()
        print(f"Logged: {title}")
    except Exception:
        print("Duplicate or restricted row skipped.")

if __name__ == "__main__":
    print(f"Beginning crawl utilizing timeframe configuration: {chosen_timeframe}")

    target_keywords = [
        {"term": "AIA Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "Automotive Industries Association of Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "CCIF", "brand": ["CCIF"], "theme": "Collision Sector Forums"},
        {"term": "I-CAR Canada", "brand": ["I-CAR Canada"], "theme": "Skilled Trades Training"},
        {"term": "Young Professionals Auto Care", "brand": ["YPA"], "theme": "Youth Engagement"},
        {"term": "righttorepair.ca", "brand": ["AIA Canada"], "theme": "Right to Repair Campaign"}
    ]

    for kw in target_keywords:
        query_string = f"{kw['term']} -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca"
        url = "https://google.serper.dev/search"
        
        # 'tbs' maps time parameters (e.g., qdr:d = past 24 hours, qdr:w = past week, qdr:m = past month)
        payload = {"q": query_string, "num": 5, "tbs": chosen_timeframe}
        headers = {'X-API-KEY': os.environ.get("SERPER_API_KEY"), 'Content-Type': 'application/json'}
        
        try:
            res = requests.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                for mention in res.json().get("organic", []):
                    # Cache bypass for testing flexibility
                    mention["link"] = mention.get("link", "") + f"?t={datetime.now().timestamp()}"
                    process_and_save_mention(mention, kw)
        except Exception as e:
            print(f"Search failure: {e}")
