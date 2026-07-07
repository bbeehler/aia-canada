import os
import urllib.parse
from supabase import create_client

# Initialize Supabase clients securely using environment variables
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

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

def process_and_save_mention(mention_data):
    """Saves a processed mention safely into the Supabase database."""
    flags = analyze_quality_and_flags(mention_data['text'])
    
    payload = {
        "title": mention_data['title'],
        "url": mention_data['url'],
        "outlet_platform": mention_data['platform'],
        "date_published": mention_data['date'],
        "snippet": mention_data['text'],
        "brands_affected": mention_data['brands'], 
        "theme": mention_data['theme'],
        "sentiment_category": mention_data['sentiment'],
        "sentiment_score": mention_data['sentiment_score'],
        "sentiment_rationale": mention_data['sentiment_rationale'],
        "naming_error_flag": flags["naming_error"],
        "data_conflict_flag": flags["data_conflict"],
        "data_conflict_details": flags["conflict_details"],
        "status": "pending"
    }
    
    try:
        supabase.table("mentions").insert(payload).execute()
        print(f"Successfully logged mention: {mention_data['title']}")
    except Exception as e:
        print(f"Skipping entry (likely duplicate URL): {e}")

if __name__ == "__main__":
    print("Automation engine initialized. Simulating targeted industry web-scraping queue...")

    # The actual search criteria drawn from your Master Keyword List guidelines
    # Excluding self-owned domains (-site:aiacanada.com) keeps tracking focused on 3rd-party media
    target_keywords = [
        {"term": "AIA Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "Automotive Industries Association of Canada", "brand": ["AIA Canada"], "theme": "Core Brand Tracking"},
        {"term": "CCIF", "brand": ["CCIF"], "theme": "Collision Sector Forums"},
        {"term": "I-CAR Canada", "brand": ["I-CAR Canada"], "theme": "Skilled Trades Training"},
        {"term": "Young Professionals in the Auto care sector", "brand": ["YPA"], "theme": "Youth Engagement"},
        {"term": "righttorepair.ca", "brand": ["AIA Canada"], "theme": "Right to Repair Campaign"}
    ]

    # Generating seed data items directly mapped to your Watch List Sentiment Risks
    # This ensures your dashboard instantly fills with relevant test items to check your flags!
    mock_scraped_results = [
        {
            "title": "AIA Canada releases statement on national standards framework",
            "url": "https://www.autosphere-news-example.ca/news/national-standards-2026",
            "platform": "Autosphere",
            "date": "2026-07-06",
            "brands": ["AIA Canada"],
            "theme": "National Standards Framework",
            "sentiment": "Positive",
            "sentiment_score": 0.85,
            "sentiment_rationale": "Strong data-backed authority positioning regarding shop standards rollout.",
            "text": "The Automotive Industries Association of Canada published a comprehensive baseline showing robust support for shop frameworks across Ontario."
        },
        {
            "title": "Local repair networks debate membership value equations",
            "url": "https://www.collision-quarterly-mock.com/opinions/independent-value-critique",
            "platform": "Collision Quarterly",
            "date": "2026-07-05",
            "brands": ["AIA Canada", "CCIF"],
            "theme": "Membership Value Perception",
            "sentiment": "Negative",
            "sentiment_score": -0.45,
            "sentiment_rationale": "Surfaces typical risk narrative that smaller independent shops receive less practical day-to-day value.",
            "text": "Critics at the AIAC meeting claimed that small independent repair facilities see minimal practical utility compared to the high entry costs."
        },
        {
            "title": "New Right to Repair campaign parameters launch across provincial spaces",
            "url": "https://www.media-matters-testing.ca/righttorepair-updates",
            "platform": "Media Matters",
            "date": "2026-07-04",
            "brands": ["AIA Canada"],
            "theme": "Right to Repair Legislation",
            "sentiment": "Neutral",
            "sentiment_score": 0.0,
            "sentiment_rationale": "Straightforward reporting on digital auto care sector definitions.",
            "text": "The old informational graphics highlight an outdated sector value of $37.8 billion to explain independent vehicle diagnostics reach."
        }
    ]

    # Process and stream the items straight into Supabase
    for mention in mock_scraped_results:
        process_and_save_mention(mention)

    print("Sync process successfully terminated.")
