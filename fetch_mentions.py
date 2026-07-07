import os
from supabase import create_client

# Initialize Supabase clients using environment variables
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase = create_client(url, key)

def analyze_quality_and_flags(text: str):
    flags = {
        "naming_error": False,
        "data_conflict": False,
        "conflict_details": ""
    }
    
    # 1. Naming Risks Checks
    # Flags incorrect brand variants explicitly outlined in guidelines
    incorrect_variants = ["JPMS", "AIA alone", "AIAC", "Forum canadien de l'industrie de la collision"]
    for variant in incorrect_variants:
        if variant.lower() in text.lower():
            flags["naming_error"] = True
            
    # 2. Data Conflict Checks
    # Flags specific core value and contact discrepancies
    if "$37.8 billion" in text or "$37.8B" in text.upper():
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated sector value ($37.8B vs $43.9B cited)."
        
    if "membership@aiacanada.ca" in text.lower():[cite: 3]
        flags["data_conflict"] = True
        flags["conflict_details"] = "Outdated domain suffix used for membership email."

    return flags

def process_and_save_mention(mention_data):
    # Run the validation check against the content body/snippet
    flags = analyze_quality_and_flags(mention_data['text'])
    
    # Combine your mention data with the validation flags
    payload = {
        "title": mention_data['title'],
        "url": mention_data['url'],
        "outlet_platform": mention_data['platform'],
        "date_published": mention_data['date'],
        "snippet": mention_data['text'],
        "brands_affected": mention_data['brands'], 
        "theme": mention_data['theme'],
        "sentiment_category": mention_data['sentiment'],
        "naming_error_flag": flags["naming_error"],
        "data_conflict_flag": flags["data_conflict"],
        "data_conflict_details": flags["conflict_details"],
        "status": "pending"
    }
    
    # Insert safely into Supabase
    supabase.table("mentions").insert(payload).execute()

# Example execution wrapper
if __name__ == "__main__":
    # Your code here would call your Google/Social search API to collect mentions,
    # loop through them, and pass them to process_and_save_mention()
    print("Automation engine initialized.")
