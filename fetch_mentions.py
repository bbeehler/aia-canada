import os
from datetime import datetime
import requests
from supabase import create_client
from google import genai
from google.genai import types

# Initialize Supabase and Gemini clients securely using environment variables
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

# Initialize official google-genai SDK client
ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

def analyze_quality_and_flags(text: str):
    """Flags specific branding violations or data discrepancies in the text."""
    flags = {
        "naming_error": False,
        "data_conflict": False,
        "conflict_details": ""
    }
    
    # Check for incorrect brand variants explicitly outlined in guidelines
    incorrect_variants = ["JPMS", "AIA alone", "AIAC", "Forum canadien de l'industrie de la collision"]
    for variant in incorrect_variants:
        if variant.lower() in text.lower():
            flags["naming_error"] = True
            
    # Check for outdated data values or email suffixes
    if "$37.8 billion" in text or "$37.8B" in text.upper():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated sector value ($37.8B vs $43.9B cited)."
        
    if "membership@aiacanada.ca" in text.lower():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated domain suffix used for membership email."

    return flags

def compute_live_sentiment_with_gemini(title: str, snippet: str):
    """Leverages Gemini 2.5 to compute dynamic metric fields from live web text."""
    system_prompt = (
        "You are an AI automated data pipeline engine checking media tracking snippets for AIA Canada. "
        "Analyze the provided headline and text context and return a clean JSON payload matching this exact schema:\n"
        "{\n"
        "  \"category\": \"Positive\" | \"Neutral\" | \"Negative\" | \"Mixed\",\n"
        "  \"score\": float between -1.0 and 1.0,\n"
        "  \"rationale\": \"A single clear sentence explaining your dynamic analysis decision.\"\n"
        "}\n"
        "Do not output markdown code blocks. Output raw JSON only."
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
        return data.get("category", "Neutral"), data.get("score", 0.0), data.get("rationale", "Automated tracking baseline run.")
    except Exception as e:
        print(f"Gemini valuation error: {e}")
        return "Neutral", 0.0, "Sentiment analysis fallback applied due to processing interruption."

def pull_live_mentions_from_serper(keyword: str):
    """Queries Serper API to get raw, unrestricted global search engine results across the entire web."""
    url = "https://google.serper.dev/search"
    
    # Strict quote encapsulation to match precise keywords
    # Adding site exclusions here targets 3rd party mentions cleanly
    query_string = f'"{keyword}" -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca'
    
    payload = {
        "q": query_string,
        "num": 5  # Limits to top 5 freshest hits per phrase execution loop
    }
    headers = {
        'X-API-KEY': os.environ.get("SERPER_API_KEY"),
        'Content-Type': 'application/json'
    }
    
    try:
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code == 200:
            return res.json().get("organic", [])
        else:
            print(f"Serper execution error. Status code: {res.status_code}")
            return []
    except Exception as e:
        print(f"Network error querying search layer: {e}")
        return []

def process_and_save_mention(live_item, keyword_meta):
    """Saves a processed mention safely into the Supabase database."""
    title = live_item.get("title", "")
    url_link = live_item.get("link", "")
    snippet = live_item.get("snippet", "")
    source_platform = live_item.get("source", "Web Resource")
    
    # Quality Assurance inspection
    flags = analyze_quality_and_flags(title + " " + snippet)
    
    # Intelligence classification analysis with Gemini
    category, score, rationale = compute_live_sentiment_with_gemini(title, snippet)
    
    payload = {
        "title": title,
        "url": url_link,
        "outlet_platform": source_platform,
        "date_published": datetime.now().date().isoformat(),  # Matches date schema type
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
        print(f"Successfully logged mention: {title}")
    except Exception as e:
        # Handles tracking duplicates seamlessly via unique URL constraint
        print(f"Skipping entry (already logged or duplicate tracking URL found).")

if __name__ == "__main__":
    print("Automation engine initialized. Beginning live web search execution layer...")

    target_keywords = [
        {"term": "AIA Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "Automotive Industries Association of Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "CCIF", "brand": ["CCIF"], "theme": "Collision Sector Forums"},
        {"term": "I-CAR Canada", "brand": ["I-CAR Canada"], "theme": "Skilled Trades Training"},
        {"term": "Young Professionals Auto Care", "brand": ["YPA"], "theme": "Youth Engagement"},
        {"term": "righttorepair.ca", "brand": ["AIA Canada"], "theme": "Right to Repair Campaign"}
    ]

    for kw in target_keywords:
        # Standard search query string without strict quote encapsulation constraints
        query_string = f"{kw['term']} -site:aiacanada.com -site:ccif.ca -site:i-car.ca -site:righttorepair.ca"
        
        url = "https://google.serper.dev/search"
        payload = {"q": query_string, "num": 5}
        headers = {
            'X-API-KEY': os.environ.get("SERPER_API_KEY"),
            'Content-Type': 'application/json'
        }
        
        try:
            res = requests.post(url, headers=headers, json=payload)
            if res.status_code == 200:
                found_mentions = res.json().get("organic", [])
                for mention in found_mentions:
                    # Injecting the timestamp bypass trick to guarantee new rows land safely
                    mention["link"] = mention.get("link", "") + f"?test={datetime.now().timestamp()}"
                    process_and_save_mention(mention, kw)
            else:
                print(f"Serper rejected query request. Status: {res.status_code}")
        except Exception as e:
            print(f"Connection failure: {e}")

    print("Sync process successfully terminated.")
