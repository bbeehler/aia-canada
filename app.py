import datetime
import hashlib
import io
import smtplib
import urllib.parse
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from fpdf import FPDF
import pandas as pd
import streamlit as st
from supabase import Client, create_client

# ----------------------------------------------------
# 1. Configuration & Supabase Connection
# ----------------------------------------------------
st.set_page_config(page_title="Equus Performance Therapeutics", page_icon="🐎", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]


@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


supabase = init_supabase()


# ----------------------------------------------------
# 2. Secure Hashing & Auth Database Helpers
# ----------------------------------------------------
def hash_password(password: str) -> str:
    salt = "equus_perf_2026_salt"
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def authenticate_db_user(email: str, password: str):
    try:
        clean_email = email.strip().lower()
        res = (
            supabase.table("app_users")
            .select("*")
            .eq("email", clean_email)
            .execute()
        )
        users = res.data if res.data else []
        if not users:
            return None, "No account found with this email address."

        user = users[0]
        if user.get("status") == "suspended":
            return None, "This account is currently suspended. Please contact Paige directly."

        pwd_hash = hash_password(password)
        if user.get("password_hash") != pwd_hash:
            return None, "Incorrect password. Please try again."

        return user, "Success"
    except Exception as e:
        return None, f"Auth Error: {e}"


def register_db_user(email: str, password: str, full_name: str, role: str = "Client", phone: str = ""):
    try:
        clean_email = email.strip().lower()
        check = supabase.table("app_users").select("id").eq("email", clean_email).execute()
        if check.data and len(check.data) > 0:
            return False, "An account with this email already exists. Please log in."

        pwd_hash = hash_password(password)
        payload = {
            "email": clean_email,
            "password_hash": pwd_hash,
            "full_name": full_name.strip(),
            "role": role,
            "phone": phone.strip(),
            "status": "active",
        }
        supabase.table("app_users").insert(payload).execute()
        return True, "Account registered successfully!"
    except Exception as e:
        return False, f"Registration Error: {e}"


def reset_user_password_db(email: str, new_password: str):
    try:
        clean_email = email.strip().lower()
        pwd_hash = hash_password(new_password)
        res = supabase.table("app_users").update({"password_hash": pwd_hash}).eq("email", clean_email).execute()
        if res.data:
            return True, "Password updated successfully!"
        return False, "User email not found."
    except Exception as e:
        return False, f"Password Reset Error: {e}"


# ----------------------------------------------------
# 3. Dynamic CMS Content Helper
# ----------------------------------------------------
def get_site_content():
    default_content = {
        "hero_title": "🐎 EQUUS PERFORMANCE THERAPEUTICS",
        "hero_subtitle": "Advanced Cellular Bio-Stimulation & Clinical Airway Halotherapy",
        "hero_badge_1": "📍 Russell & Ottawa Valley",
        "hero_badge_2": "⚡ High-Energy Cell Treatment (HECT)",
        "hero_badge_3": "💨 Micro-Particle Halotherapy",
        "hect_title": "⚡ Equitron-Pro (HECT)",
        "hect_price": "$60 CAD / 20-min session ($2.00/min)",
        "hect_desc": "High-Energy Cell Treatment pulses bio-electromagnetic energy deep into cellular tissue, restoring cellular resting potential up to 20 cm deep without medication or sedation.",
        "hect_bullet_1": "Relieves lumbar, SI & top-line muscular tension",
        "hect_bullet_2": "Accelerates tendon, ligament & suspensory repair",
        "hect_bullet_3": "Sore-free, non-invasive biofeedback scan",
        "halo_title": "💨 HaloEQ2 (Halotherapy)",
        "halo_price": "$45 CAD / session (Custom Stabling Blocks)",
        "halo_desc": "Medical-grade dry aerosol salt halotherapy clears the deepest pulmonary bronchioles, flushing mucus, environmental allergens, and arena dust.",
        "halo_bullet_1": "Anti-bacterial & anti-inflammatory airway flush",
        "halo_bullet_2": "Enhances oxygenation & stamina in competition",
        "halo_bullet_3": "Rapid post-trailering respiratory recovery",
        "combo_title": "⭐ Peak Performance Combo (HECT + Halo)",
        "combo_price": "$95 CAD / combined package",
        "corridor_routes": """* **Monday:** Ottawa Metro & Russell Home Corridor
* **Tuesday:** Kingston Corridor *(South via Hwy 416/401)*
* **Wednesday:** Pembroke & Upper Ottawa Valley *(North via Hwy 17)*
* **Thursday:** Montreal Corridor *(East via Hwy 417)*
* **Friday:** Flagship Partner Facilities Dedicated Intensive""",
        "announcement_banner": "🔔 Now scheduling Fall/Winter Regional Corridor dates across Eastern Ontario.",
    }
    try:
        res = supabase.table("site_content").select("content_key, content_value").execute()
        if res.data:
            for item in res.data:
                default_content[item["content_key"]] = item["content_value"]
    except Exception:
        pass
    return default_content


def update_site_content(content_dict):
    try:
        for k, v in content_dict.items():
            supabase.table("site_content").upsert({"content_key": k, "content_value": v}).execute()
        return True, "Landing page content and pricing updated successfully!"
    except Exception as e:
        return False, f"CMS Update Error: {e}"


# ----------------------------------------------------
# 4. Business Logic Helpers
# ----------------------------------------------------
def calculate_session_fee(
    duration_minutes: int,
    is_flagship: bool,
    is_marketing_tier: bool,
    previous_minutes: int,
):
    new_total = previous_minutes + duration_minutes

    if not is_flagship:
        fee = 60.0 if duration_minutes == 20 else duration_minutes * 2.0
        return fee, new_total, "Standard Mobile Rate ($2.00/min)"

    if is_marketing_tier:
        if new_total <= 200:
            return 0.0, new_total, "Promo Allowance (100% Free)"
        billable = duration_minutes if previous_minutes >= 200 else (new_total - 200)
        return float(billable * 1.0), new_total, "Marketing Tier Overage ($1.00/min)"

    if previous_minutes >= 200:
        return (
            float(duration_minutes * 2.0),
            new_total,
            "Standard Tier Overage ($2.00/min)",
        )
    if new_total <= 200:
        return (
            float(duration_minutes * 1.0),
            new_total,
            "Standard Tier Baseline ($1.00/min)",
        )

    base_mins = 200 - previous_minutes
    overage_mins = new_total - 200
    fee = (base_mins * 1.0) + (overage_mins * 2.0)
    return float(fee), new_total, "Standard Tier (Mixed Baseline & Overage Rate)"


def calculate_travel_fee(distance_km: float, same_day_horses: int):
    if same_day_horses >= 3:
        return 0.0, True, "Group Booking Incentive (3+ Horses: Fee Waived)"
    if distance_km <= 30:
        return 0.0, True, "Within Free 30km Base Radius"
    billable_km = distance_km - 30
    fee = round(billable_km * 0.73, 2)
    return fee, False, f"Standard Mileage ({billable_km:.1f} km @ $0.73/km)"


def send_email_with_pdf(recipient_email, subject, body_text, pdf_bytes, pdf_filename):
    try:
        smtp_server = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(st.secrets.get("SMTP_PORT", 587))
        smtp_user = st.secrets.get("SMTP_USERNAME", "")
        smtp_pass = st.secrets.get("SMTP_PASSWORD", "")
        sender_email = st.secrets.get("SENDER_EMAIL", smtp_user)

        if not smtp_user or not smtp_pass:
            return False, "⚠️ Missing SMTP credentials in Streamlit Secrets. Please check your secrets configuration."

        msg = MIMEMultipart()
        msg["From"] = f"Equus Performance Therapeutics <{sender_email}>"
        msg["To"] = recipient_email
        msg["Subject"] = subject

        msg.attach(MIMEText(body_text, "plain"))

        part = MIMEApplication(pdf_bytes, Name=pdf_filename)
        part["Content-Disposition"] = f'attachment; filename="{pdf_filename}"'
        msg.attach(part)

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        return True, f"✅ Statement successfully emailed to {recipient_email}!"
    except Exception as e:
        return False, f"❌ Failed to send email: {e}"


def get_data_maps():
    try:
        barns_res = supabase.table("barns").select("*").execute()
        barns = barns_res.data if barns_res.data else []
    except Exception:
        barns = []
    barn_map = {b["id"]: b for b in barns}

    try:
        horses_res = supabase.table("horses").select("*").execute()
        horses = horses_res.data if horses_res.data else []
    except Exception:
        horses = []

    for h in horses:
        h["barn_details"] = barn_map.get(
            h.get("barn_id"),
            {
                "name": "No Barn",
                "is_flagship": False,
                "rate_per_minute": 2.0,
                "address": "Local Mobile",
            },
        )

    return barns, horses, barn_map


# ----------------------------------------------------
# 5. PDF Generator Classes
# ----------------------------------------------------
class PDFInvoice(FPDF):

    def header(self):
        self.set_font("helvetica", "B", 16)
        self.cell(0, 8, "EQUUS PERFORMANCE THERAPEUTICS", 0, 1, "L")
        self.set_font("helvetica", "", 10)
        self.cell(
            0,
            5,
            "Equine Cellular Regeneration & Pulmonary Recovery | Russell, ON",
            0,
            1,
            "L",
        )
        self.ln(5)
        self.set_draw_color(100, 100, 100)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.cell(
            0,
            10,
            f"Page {self.page_no()} | Equus Performance Therapeutics - Official Record",
            0,
            0,
            "C",
        )


def create_pdf_invoice(barn_name, invoice_rows, total_billed):
    pdf = PDFInvoice()
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 8, f"STATEMENT OF ACCOUNT: {barn_name.upper()}", 0, 1, "L")

    pdf.set_font("helvetica", "", 10)
    pdf.cell(
        0, 6, f"Statement Date: {datetime.date.today().strftime('%B %d, %Y')}", 0, 1
    )
    pdf.cell(0, 6, "Payment Terms: Due upon receipt via e-Transfer to paige@equusperformance.ca", 0, 1)
    pdf.ln(5)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(22, 7, "Date", 1, 0, "C", True)
    pdf.cell(38, 7, "Horse Name", 1, 0, "L", True)
    pdf.cell(32, 7, "Owner", 1, 0, "L", True)
    pdf.cell(38, 7, "Modality", 1, 0, "L", True)
    pdf.cell(15, 7, "Mins", 1, 0, "C", True)
    pdf.cell(22, 7, "Fee (CAD)", 1, 0, "R", True)
    pdf.cell(23, 7, "Notes", 1, 1, "L", True)

    pdf.set_font("helvetica", "", 8)
    for r in invoice_rows:
        pdf.cell(22, 6, str(r["Date"]), 1, 0, "C")
        pdf.cell(38, 6, str(r["Horse Name"][:20]), 1, 0, "L")
        pdf.cell(32, 6, str(r["Owner"][:18]), 1, 0, "L")
        pdf.cell(38, 6, str(r["Modality"][:20]), 1, 0, "L")
        pdf.cell(15, 6, str(r["Duration (Mins)"]), 1, 0, "C")
        pdf.cell(22, 6, str(r["Fee (CAD)"]), 1, 0, "R")
        pdf.cell(23, 6, str(r["Notes"][:15]), 1, 1, "L")

    pdf.ln(5)
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 8, f"Total Balance Due: ${total_billed:.2f} CAD", 0, 1, "R")

    return pdf.output()


def create_facility_reconciliation_pdf(
    barn_obj, horse_summary_rows, total_billed, total_waived
):
    pdf = PDFInvoice()
    pdf.add_page()
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(
        0,
        8,
        f"FACILITY RETAINER & RECONCILIATION: {barn_obj.get('name', 'Facility').upper()}",
        0,
        1,
        "L",
    )

    pdf.set_font("helvetica", "", 10)
    pdf.cell(
        0,
        5,
        f"Billing Period: {datetime.date.today().strftime('%B %Y')} | Status: "
        f"{'Flagship Reference Facility' if barn_obj.get('is_flagship') else 'Standard Partner Facility'}",
        0,
        1,
    )
    pdf.cell(0, 5, "Payment Terms: Due upon receipt via e-Transfer to paige@equusperformance.ca", 0, 1)
    pdf.ln(5)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(40, 7, "Horse Name", 1, 0, "L", True)
    pdf.cell(35, 7, "Owner", 1, 0, "L", True)
    pdf.cell(35, 7, "Tier Status", 1, 0, "C", True)
    pdf.cell(25, 7, "Mins Used", 1, 0, "C", True)
    pdf.cell(25, 7, "Waived Value", 1, 0, "R", True)
    pdf.cell(30, 7, "Billable Fee", 1, 1, "R", True)

    pdf.set_font("helvetica", "", 8)
    for r in horse_summary_rows:
        pdf.cell(40, 6, str(r["Horse Name"][:20]), 1, 0, "L")
        pdf.cell(35, 6, str(r["Owner"][:18]), 1, 0, "L")
        pdf.cell(35, 6, str(r["Tier"]), 1, 0, "C")
        pdf.cell(25, 6, str(r["Minutes Used"]), 1, 0, "C")
        pdf.cell(25, 6, str(r["Waived Promo"]), 1, 0, "R")
        pdf.cell(30, 6, str(r["Total Billable"]), 1, 1, "R")

    pdf.ln(6)
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(
        0,
        6,
        f"Total Promotional Allowance Provided: ${total_waived:.2f} CAD",
        0,
        1,
        "R",
    )
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 7, f"Net Facility Total Due: ${total_billed:.2f} CAD", 0, 1, "R")

    return pdf.output()


def create_vet_report_pdf(horse_obj, vet_name, clinical_logs):
    pdf = PDFInvoice()
    pdf.add_page()

    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 8, "CLINICAL THERAPY & REHABILITATION SUMMARY", 0, 1, "L")
    pdf.set_font("helvetica", "", 10)
    pdf.cell(
        0, 5, f"Report Date: {datetime.date.today().strftime('%B %d, %Y')}", 0, 1
    )
    pdf.cell(0, 5, "Attending Specialist: Paige Cummings (EquusOS Hub)", 0, 1)
    pdf.ln(3)

    pdf.set_fill_color(245, 245, 245)
    pdf.rect(10, pdf.get_y(), 190, 22, "F")
    pdf.set_xy(12, pdf.get_y() + 2)

    pdf.set_font("helvetica", "B", 10)
    pdf.cell(
        90,
        5,
        f"Patient: {horse_obj.get('name', 'N/A')} (Owner: {horse_obj.get('owner_name', 'N/A')})",
        0,
        0,
    )
    pdf.cell(
        90,
        5,
        f"Facility: {horse_obj.get('barn_details', {}).get('name', 'N/A')}",
        0,
        1,
    )
    pdf.set_xy(12, pdf.get_y())
    pdf.set_font("helvetica", "", 9)
    pdf.cell(
        90, 5, f"Primary Veterinarian: {vet_name if vet_name else 'On File'}", 0, 0
    )
    total_mins = sum(int(l.get("duration_minutes", 0)) for l in clinical_logs)
    pdf.cell(90, 5, f"Cumulative Therapy Logged: {total_mins} Minutes", 0, 1)
    pdf.ln(8)

    pdf.set_font("helvetica", "B", 11)
    pdf.cell(0, 7, "Chronological Treatment History & Clinical Notes", 0, 1)

    for log in clinical_logs:
        pdf.set_draw_color(200, 200, 200)
        pdf.set_font("helvetica", "B", 9)
        date_str = str(log.get("created_at", ""))[:10]
        modality_str = str(log.get("modality", "Therapy"))
        mins_str = str(log.get("duration_minutes", "20"))
        pdf.cell(0, 6, f"[{date_str}] - {modality_str} ({mins_str} mins)", "B", 1)

        pdf.set_font("helvetica", "", 8)
        notes_clean = str(
            log.get("session_notes", "Routine complementary therapy administered.")
        )
        pdf.multi_cell(0, 5, f"Observations: {notes_clean}")
        pdf.ln(2)

    pdf.ln(4)
    pdf.set_font("helvetica", "I", 8)
    pdf.multi_cell(
        0,
        4,
        "Disclaimer: Equus Performance Therapeutics provides complementary "
        "non-invasive wellness, high-energy cellular bio-stimulation, and dry "
        "salt halotherapy. This summary is intended to support collaborative "
        "veterinary diagnosis and management.",
    )

    return pdf.output()


# ----------------------------------------------------
# 6. Global Auth State & Public Marketing Landing
# ----------------------------------------------------
barns, horses, barn_map = get_data_maps()
site_content = get_site_content()

if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "auth_role" not in st.session_state:
    st.session_state["auth_role"] = None
if "auth_name" not in st.session_state:
    st.session_state["auth_name"] = ""

# ====================================================
# MARKETING SHOWCASE LANDING PAGE & UNIVERSAL LOGIN
# ====================================================
if st.session_state["auth_user"] is None:
    if site_content.get("announcement_banner"):
        st.info(site_content["announcement_banner"])

    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 36px 24px; border-radius: 16px; color: white; text-align: center; margin-bottom: 24px;">
        <h1 style="font-size: 2.8rem; margin: 0; color: #f8fafc; letter-spacing: -0.5px;">{site_content.get('hero_title', '🐎 EQUUS PERFORMANCE THERAPEUTICS')}</h1>
        <p style="font-size: 1.25rem; color: #94a3b8; margin: 8px 0 16px 0; font-weight: 300;">{site_content.get('hero_subtitle', 'Advanced Cellular Bio-Stimulation & Clinical Airway Halotherapy')}</p>
        <div style="display: flex; justify-content: center; gap: 12px; flex-wrap: wrap;">
            <span style="background: rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 20px; font-size: 0.9rem;">{site_content.get('hero_badge_1', '📍 Russell & Ottawa Valley')}</span>
            <span style="background: rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 20px; font-size: 0.9rem;">{site_content.get('hero_badge_2', '⚡ High-Energy Cell Treatment (HECT)')}</span>
            <span style="background: rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 20px; font-size: 0.9rem;">{site_content.get('hero_badge_3', '💨 Micro-Particle Halotherapy')}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    landing_col_left, landing_col_right = st.columns([3, 2])

    with landing_col_left:
        st.markdown("### 🌟 Clinical Services & Therapy Rates")

        m_col1, m_col2 = st.columns(2)
        with m_col1:
            st.markdown(f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; height: 100%;">
                <h4 style="margin-top: 0; color: #0284c7;">{site_content.get('hect_title')}</h4>
                <p style="font-size: 0.95rem; font-weight: bold; color: #0f172a; margin-bottom: 6px;">💵 Rate: {site_content.get('hect_price')}</p>
                <p style="font-size: 0.88rem; color: #475569; line-height: 1.5;">
                    {site_content.get('hect_desc')}
                </p>
                <ul style="font-size: 0.82rem; color: #334155; padding-left: 18px;">
                    <li>{site_content.get('hect_bullet_1')}</li>
                    <li>{site_content.get('hect_bullet_2')}</li>
                    <li>{site_content.get('hect_bullet_3')}</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

        with m_col2:
            st.markdown(f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; height: 100%;">
                <h4 style="margin-top: 0; color: #0d9488;">{site_content.get('halo_title')}</h4>
                <p style="font-size: 0.95rem; font-weight: bold; color: #0f172a; margin-bottom: 6px;">💵 Rate: {site_content.get('halo_price')}</p>
                <p style="font-size: 0.88rem; color: #475569; line-height: 1.5;">
                    {site_content.get('halo_desc')}
                </p>
                <ul style="font-size: 0.82rem; color: #334155; padding-left: 18px;">
                    <li>{site_content.get('halo_bullet_1')}</li>
                    <li>{site_content.get('halo_bullet_2')}</li>
                    <li>{site_content.get('halo_bullet_3')}</li>
                </ul>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='margin-top: 18px;'></div>", unsafe_allow_html=True)
        st.markdown("### 🗺️ Designated Corridor Travel Schedule")
        st.markdown(site_content.get("corridor_routes", ""))

    with landing_col_right:
        st.markdown("### 🔐 Member & Specialist Access")
        portal_tabs = st.tabs(["Universal Sign-In", "Owner Registration & Horses"])

        # TAB 1: UNIVERSAL SIGN-IN
        with portal_tabs[0]:
            st.caption("Sign in with your email to access your client dashboard, horses, or specialist admin suite.")
            with st.form("universal_login_form"):
                u_email = st.text_input("Email Address", placeholder="name@domain.ca")
                u_pwd = st.text_input("Password", type="password", placeholder="••••••••")

                if st.form_submit_button("Sign In to EquusOS", use_container_width=True):
                    if u_email and u_pwd:
                        user_obj, msg = authenticate_db_user(u_email, u_pwd)
                        if user_obj:
                            st.session_state["auth_user"] = user_obj.get("email")
                            st.session_state["auth_role"] = user_obj.get("role", "Client")
                            st.session_state["auth_name"] = user_obj.get("full_name")
                            st.success(f"Welcome, {user_obj.get('full_name')}!")
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.warning("Please enter your email and password.")

            with st.expander("Reset Password"):
                with st.form("universal_pwd_reset"):
                    r_email = st.text_input("Registered Email")
                    r_pwd = st.text_input("New Password", type="password")
                    if st.form_submit_button("Update Password"):
                        if r_email and r_pwd:
                            ok, r_msg = reset_user_password_db(r_email, r_pwd)
                            if ok:
                                st.success(r_msg)
                            else:
                                st.error(r_msg)
                        else:
                            st.warning("Please enter your email and new password.")

        # TAB 2: NEW OWNER REGISTRATION
        with portal_tabs[1]:
            st.caption("Register as an owner (multiple horses supported) and execute intake waiver.")
            with st.form("landing_signup_form"):
                reg_name = st.text_input("Owner Full Name*")
                reg_email = st.text_input("Email Address (Login)*")
                reg_pwd = st.text_input("Create Password*", type="password")
                reg_phone = st.text_input("Mobile Phone (for SMS)*")
                
                st.markdown("---")
                st.markdown("#### Primary Horse / Animal Profile")
                reg_horse = st.text_input("Horse Name (Show / Barn Name)*")
                barn_names = [b["name"] for b in barns] if barns else ["Private Facility"]
                reg_barn = st.selectbox("Stabling Facility / Barn*", barn_names)
                reg_vet = st.text_input("Primary Veterinarian", placeholder="e.g. Dr. Smith")
                
                reg_agree = st.checkbox("I agree to the liability waiver & complementary care terms.*")
                reg_sig = st.text_input("Digital Signature (Full Legal Name)*")

                if st.form_submit_button("Complete Owner Registration", use_container_width=True):
                    if reg_name and reg_email and reg_pwd and reg_horse and reg_sig:
                        if not reg_agree:
                            st.error("Please accept the waiver terms.")
                        else:
                            ok, msg = register_db_user(reg_email, reg_pwd, reg_name, role="Client", phone=reg_phone)
                            if ok:
                                # 1. Insert Owner Record
                                o_res = supabase.table("owners").insert({
                                    "full_name": reg_name,
                                    "email": reg_email.strip().lower(),
                                    "phone_number": reg_phone.strip(),
                                }).execute()
                                owner_id_val = o_res.data[0]["id"] if o_res.data else None

                                # 2. Insert Waiver Record
                                supabase.table("client_waivers").insert({
                                    "owner_name": reg_name,
                                    "client_email": reg_email.strip().lower(),
                                    "horse_name": reg_horse,
                                    "primary_veterinarian": reg_vet,
                                    "modality_consent": ["Equitron-Pro (HECT)", "HaloEQ2 (Halotherapy)"],
                                    "waiver_agreed": True,
                                    "signature_name": reg_sig,
                                }).execute()

                                # 3. Insert Horse Record
                                barn_id_match = next((b["id"] for b in barns if b["name"] == reg_barn), barns[0]["id"] if barns else None)
                                supabase.table("horses").insert({
                                    "name": reg_horse,
                                    "owner_name": reg_name,
                                    "owner_id": owner_id_val,
                                    "barn_id": barn_id_match,
                                    "is_marketing_tier": False,
                                    "minutes_used_this_month": 0,
                                }).execute()

                                st.session_state["auth_user"] = reg_email.strip().lower()
                                st.session_state["auth_role"] = "Client"
                                st.session_state["auth_name"] = reg_name
                                st.success(f"Account created! Welcome, {reg_name}!")
                                st.rerun()
                            else:
                                st.error(msg)
                    else:
                        st.warning("Please fill in all required fields.")

    st.stop()


# ----------------------------------------------------
# 7. AUTHENTICATED SESSIONS (Client vs Paige Specialist)
# ----------------------------------------------------
st.sidebar.title("🐎 EquusOS")
st.sidebar.markdown(f"**Logged in:** `{st.session_state['auth_name']}` ({st.session_state['auth_role']})")

if st.sidebar.button("🚪 Sign Out"):
    st.session_state["auth_user"] = None
    st.session_state["auth_role"] = None
    st.session_state["auth_name"] = ""
    st.rerun()

st.sidebar.divider()

# ====================================================
# A. CLIENT MEMBER PORTAL EXPERIENCE
# ====================================================
if st.session_state["auth_role"] == "Client":
    matched_owner_name = st.session_state["auth_name"]
    active_client_email = st.session_state["auth_user"]

    st.title(f"🐎 Welcome, {matched_owner_name}")
    st.caption(f"Equus Member Dashboard | Linked Account: {active_client_email}")

    client_horses = [h for h in horses if h.get("owner_name", "").lower() == matched_owner_name.lower()]
    client_h_ids = [h["id"] for h in client_horses]

    try:
        appts_res = supabase.table("appointments").select("*").in_("horse_id", client_h_ids).order("appointment_date").execute()
        my_appts = appts_res.data if appts_res.data else []
    except Exception:
        my_appts = []

    with st.container():
        st.subheader("🗓️ Next Visit & Arrival Window")
        if my_appts:
            for a in my_appts:
                h_obj = next((h for h in client_horses if h["id"] == a["horse_id"]), {})
                st_badge = "🟠 Pending Approval (Tentative)" if a.get("status") == "Tentative" else "🟢 Confirmed Booking"
                st.info(f"""
                📍 **Appointment Date:** **{a.get('appointment_date')}**  
                🐎 **Patient:** **{h_obj.get('name', 'Your Horse')}** @ {h_obj.get('barn_details', {}).get('name', 'Barn')}  
                ⏱️ **Booking Status:** `{st_badge}` | **Estimated Travel / Mileage:** ${float(a.get('travel_fee', 0)):.2f} CAD
                """)
        else:
            st.info("No upcoming visits currently booked. Request your next corridor session below!")

    st.divider()

    c_tab1, c_tab2, c_tab3, c_tab4 = st.tabs(["🐎 My Animals & Clinical History", "📅 Request Corridor Visit (Tentative)", "📸 Photo & Video Gallery", "💳 Invoices & Make Payment"])

    with c_tab1:
        st.subheader("Register Additional Animal / Horse")
        with st.form("client_add_animal_form"):
            new_h_name = st.text_input("New Horse Name*")
            new_h_barn = st.selectbox("Stabling Facility / Barn*", [b["name"] for b in barns] if barns else ["Private Facility"])
            if st.form_submit_button("Add Animal to My Account"):
                if new_h_name and barns:
                    barn_id_match = next((b["id"] for b in barns if b["name"] == new_h_barn), barns[0]["id"])
                    
                    try:
                        ow_r = supabase.table("owners").select("id").eq("email", active_client_email).execute()
                        ow_id = ow_r.data[0]["id"] if ow_r.data else None
                    except Exception:
                        ow_id = None

                    supabase.table("horses").insert({
                        "name": new_h_name,
                        "owner_name": matched_owner_name,
                        "owner_id": ow_id,
                        "barn_id": barn_id_match,
                        "is_marketing_tier": False,
                        "minutes_used_this_month": 0,
                    }).execute()
                    st.success(f"Added {new_h_name} to your profile!")
                    st.rerun()

        st.subheader("My Horses & Clinical Progress Notes")
        if client_horses:
            chosen_h = st.selectbox("Select Your Horse", [h["name"] for h in client_horses], key="client_tab1_h")
            h_obj = next(h for h in client_horses if h["name"] == chosen_h)

            st.metric("Total Therapy Logged This Month", f"{h_obj.get('minutes_used_this_month', 0)} Minutes")

            try:
                l_res = supabase.table("treatment_logs").select("*").eq("horse_id", str(h_obj["id"])).order("created_at", desc=True).execute()
                h_logs = l_res.data if l_res.data else []
            except Exception:
                h_logs = []

            if h_logs:
                for l in h_logs:
                    st.markdown(f"**[{l.get('created_at','')[:10]}] - `{l.get('modality')}` ({l.get('duration_minutes')} mins)**")
                    st.caption(f"Clinical Observations: {l.get('session_notes')}")
                    st.divider()
            else:
                st.write("No session records found for this horse yet.")
        else:
            st.info("No horses linked to your account yet.")

    with c_tab2:
        st.subheader("Request Corridor Visit (Subject to Admin Confirmation)")
        with st.form("client_portal_book_form"):
            b_h = st.selectbox("Select Horse", [h["name"] for h in client_horses] if client_horses else ["No horse registered"])
            b_d = st.date_input("Preferred Date", datetime.date.today() + datetime.timedelta(days=1))
            b_mod = st.selectbox("Therapy Modality", ["Equitron-Pro (HECT)", "HaloEQ2 (Halotherapy)", "Peak Performance Combo"])
            b_notes = st.text_area("Observations for Paige (e.g. slight right stifle stiffness, prep for show)")

            if st.form_submit_button("Submit Tentative Booking Request"):
                if client_horses:
                    sel_h = next(h for h in client_horses if h["name"] == b_h)
                    try:
                        supabase.table("appointments").insert({
                            "appointment_date": str(b_d),
                            "horse_id": str(sel_h["id"]),
                            "barn_id": str(sel_h.get("barn_id")),
                            "distance_from_base_km": 30.0,
                            "travel_fee": 0.0,
                            "status": "Tentative",
                        }).execute()
                        st.success(f"Tentative booking submitted for {b_h} on {b_d}! Waiting for Paige to confirm.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Booking error: {e}")

    with c_tab3:
        st.subheader("Visual Progress Gallery (Before & After Stance / Gait)")
        if client_horses:
            chosen_h_gal = st.selectbox("Select Animal for Media", [h["name"] for h in client_horses], key="client_gal_h")
            h_gal_obj = next(h for h in client_horses if h["name"] == chosen_h_gal)

            try:
                m_res = supabase.table("horse_media_records").select("*").eq("horse_id", str(h_gal_obj["id"])).order("record_date", desc=True).execute()
                h_media = m_res.data if m_res.data else []
            except Exception:
                h_media = []

            if h_media:
                for m in h_media:
                    st.markdown(f"**{m.get('caption', 'Clinical Scan')}** — `{m.get('stage_category')}` ({m.get('record_date')})")
                    if m.get("media_type") == "Image":
                        st.image(m.get("media_url"), use_container_width=True)
                    else:
                        st.markdown(f"🔗 [Watch Movement Video]({m.get('media_url')})")
                    st.divider()
            else:
                st.info("No photos or videos logged for this animal yet.")
    else:
        st.info("Please register a horse profile first.")

    with c_tab4:
        st.subheader("Account Statements & Payment Submission")
        try:
            pmts_res = supabase.table("client_payments").select("*").eq("owner_name", matched_owner_name).execute()
            my_payments = pmts_res.data if pmts_res.data else []
        except Exception:
            my_payments = []

        total_paid_client = sum(float(p.get("amount_paid", 0)) for p in my_payments)
        st.metric("Total Payments Settled", f"${total_paid_client:,.2f} CAD")

        st.markdown("### Remit Payment via e-Transfer")
        st.info("""
        * **e-Transfer Recipient:** `paige@equusperformance.ca`
        * **Business Name:** Equus Performance Therapeutics
        * **Notes:** Please include your horse's name in the e-Transfer description.
        """)

        with st.form("client_log_payment_form"):
            st.markdown("#### Confirm Payment Sent")
            p_amt = st.number_input("Amount Sent ($ CAD)", min_value=10.0, value=60.0, step=10.0)
            p_ref = st.text_input("e-Transfer Reference / Confirmation Number*")
            if st.form_submit_button("Submit Payment Confirmation"):
                if p_ref:
                    try:
                        supabase.table("client_payments").insert({
                            "payment_date": str(datetime.date.today()),
                            "owner_name": matched_owner_name,
                            "amount_paid": float(p_amt),
                            "payment_method": "e-Transfer",
                            "reference_number": p_ref,
                            "notes": f"Submitted directly by client via portal for {matched_owner_name}",
                        }).execute()
                        st.success(f"Payment of ${p_amt:.2f} logged! Paige will verify against bank records.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
                else:
                    st.warning("Please enter your e-Transfer confirmation number.")

    st.stop()


# ====================================================
# B. SPECIALIST / ADMIN WORKFLOW (PAIGE)
# ====================================================
CATEGORY_WORKFLOWS = {
    "🐎 Daily Clinical Hub": [
        "Log Treatments & Live Feed",
        "Clinical Progression & Biofeedback",
        "Photo & Video Progress Gallery",
        "Veterinary Clinical Reports",
        "Client Health Portal",
    ],
    "🗓️ Dispatch & Corridors": [
        "Interactive Booking Calendar",
        "Corridor Calendar & Daily Run-Sheet",
        "Smart Route Booking & Mileage",
        "Client Re-booking & SMS Reminders",
    ],
    "👥 User & Client Management": [
        "Landing Page & Pricing CMS",
        "User Database & Credentials",
        "Manage Clients & Appointments",
        "Public Intake & Barn QR Code",
        "Signed Waivers & Onboarding",
        "Facility Retainers & Reconciliation",
        "Pre-Paid Packages & Credit Passes",
        "Trainer & Referral Incentives",
    ],
    "📊 Accounting & Bookkeeping": [
        "Accounts Payable (A/P) Bills",
        "Accounts Receivable (A/R) Aging",
        "HST / Sales Tax (CRA ITC Report)",
        "Chart of Accounts & General Ledger",
        "Accountant Handoff Export Pack",
        "Email Invoice Dispatcher",
        "Monthly Invoicing & PDF Statements",
        "Corridor Travel & Fuel Expenses",
        "Executive P&L Snapshot",
    ],
}

selected_category = st.sidebar.selectbox("📂 Workspace Section", list(CATEGORY_WORKFLOWS.keys()))
page = st.sidebar.radio("📌 Select Module", CATEGORY_WORKFLOWS[selected_category])

# ----------------------------------------------------
# 1. INTERACTIVE BOOKING CALENDAR FOR PAIGE
# ----------------------------------------------------
if page == "Interactive Booking Calendar":
    st.title("📅 Interactive Master Booking Calendar")
    st.markdown("Visually review scheduled and tentative client bookings, approve requests, and click through to edit or reschedule.")

    try:
        appts_res = supabase.table("appointments").select("*").order("appointment_date").execute()
        all_appts = appts_res.data if appts_res.data else []
    except Exception:
        all_appts = []

    horse_map = {h["id"]: h for h in horses}

    status_filter = st.radio("Filter Bookings", ["All Bookings", "⚠️ Tentative Requests (Needs Confirmation)", "✅ Confirmed Bookings"], horizontal=True)

    filtered_appts = all_appts
    if status_filter == "⚠️ Tentative Requests (Needs Confirmation)":
        filtered_appts = [a for a in all_appts if a.get("status") == "Tentative"]
    elif status_filter == "✅ Confirmed Bookings":
        filtered_appts = [a for a in all_appts if a.get("status") == "Confirmed"]

    if filtered_appts:
        for a in filtered_appts:
            h_info = horse_map.get(a.get("horse_id"), {})
            b_info = h_info.get("barn_details", {})
            st_col = "orange" if a.get("status") == "Tentative" else "green"

            with st.container():
                c_cal1, c_cal2, c_cal3 = st.columns([3, 2, 2])
                with c_cal1:
                    st.markdown(f"### 📅 {a.get('appointment_date')} — **{h_info.get('name', 'Unknown Horse')}**")
                    st.caption(f"Owner: **{h_info.get('owner_name', 'N/A')}** | Facility: **{b_info.get('name', 'No Barn')}**")
                with c_cal2:
                    st.markdown(f"**Status:** :{st_col}[{a.get('status', 'Tentative')}]")
                    st.caption(f"Travel Fee: ${float(a.get('travel_fee', 0)):.2f} CAD ({a.get('distance_from_base_km', 30)} km)")
                with c_cal3:
                    if a.get("status") == "Tentative":
                        if st.button("✅ Confirm Booking", key=f"conf_{a['id']}"):
                            supabase.table("appointments").update({"status": "Confirmed"}).eq("id", a["id"]).execute()
                            st.success("Booking confirmed!")
                            st.rerun()

                    if st.button("🗑️ Cancel / Delete", key=f"del_{a['id']}"):
                        supabase.table("appointments").delete().eq("id", a["id"]).execute()
                        st.warning("Booking deleted.")
                        st.rerun()
                st.divider()
    else:
        st.info("No bookings match the selected filter.")

# ----------------------------------------------------
# 2. CLIENT RE-BOOKING & SMS DISPATCH
# ----------------------------------------------------
elif page == "Client Re-booking & SMS Reminders":
    st.title("💬 Automated Client SMS Dispatch & Notifications")
    st.markdown("Generate 1-click SMS appointment confirmations, post-treatment follow-ups, and route dispatch notices.")

    if horses:
        col_r1, col_r2 = st.columns([1, 1])

        with col_r1:
            st.subheader("1. Message Builder")
            horse_pick = {f"{h['name']} ({h['owner_name']}) | {h['barn_details']['name']}": h for h in horses}
            chosen_h_label = st.selectbox("Select Horse / Owner", list(horse_pick.keys()))
            h_rem = horse_pick[chosen_h_label]

            try:
                ow_res = supabase.table("owners").select("phone_number").eq("full_name", h_rem.get("owner_name")).execute()
                def_phone = ow_res.data[0].get("phone_number", "") if ow_res.data else ""
            except Exception:
                def_phone = ""

            phone_num = st.text_input("Owner Mobile Number", value=def_phone, placeholder="6135551234")

            reminder_type = st.selectbox(
                "Message Template",
                [
                    "Appointment Confirmation & ETA",
                    "48-Hour Post-Equitron Recovery Check-In",
                    "Bi-Weekly Maintenance Re-Booking Prompt",
                    "Group Barn Route Booking Callout",
                ],
            )

            appt_date_txt = st.date_input("Appointment Date", datetime.date.today())
            arrival_window = st.selectbox(
                "Estimated Arrival Window",
                [
                    "Morning (9:00 AM - 11:00 AM)",
                    "Midday (11:00 AM - 1:00 PM)",
                    "Afternoon (1:00 PM - 3:30 PM)",
                    "Late Afternoon (3:30 PM - 5:30 PM)",
                ],
            )

        with col_r2:
            st.subheader("2. SMS Preview & 1-Click Dispatch")

            owner_first = (
                h_rem.get("owner_name", "there").split()[0]
                if h_rem.get("owner_name")
                else "there"
            )
            horse_n = h_rem.get("name", "your horse")
            barn_n = h_rem.get("barn_details", {}).get("name", "the barn")

            if reminder_type == "Appointment Confirmation & ETA":
                message_body = (
                    f"Hi {owner_first}! Confirming our Equus Performance session for {horse_n} on "
                    f"{appt_date_txt.strftime('%A, %b %d')} at {barn_n}. Our estimated arrival window is {arrival_window}. "
                    f"Please ensure {horse_n} is brought in and dry. Looking forward to seeing you!"
                )
            elif reminder_type == "48-Hour Post-Equitron Recovery Check-In":
                message_body = (
                    f"Hi {owner_first}! Just checking in on {horse_n} following our Equitron session. "
                    f"How is their topline and movement feeling under saddle? Let me know if you noticed any relaxed biofeedback changes!"
                )
            elif reminder_type == "Bi-Weekly Maintenance Re-Booking Prompt":
                message_body = (
                    f"Hi {owner_first}! It has been about two weeks since {horse_n}'s last cellular therapy session. "
                    f"We are scheduling our upcoming corridor run to {barn_n}. Would you like to reserve a spot to maintain their peak performance?"
                )
            else:
                message_body = (
                    f"Hi {owner_first}! We are opening our route dispatch to {barn_n} for {appt_date_txt.strftime('%A, %b %d')}. "
                    f"If we group 3 or more horses together, travel mileage fees are 100% waived! Let me know if you'd like to include {horse_n}."
                )

            st.text_area("SMS Body", value=message_body, height=140, key="sms_preview_box")

            clean_phone = "".join(filter(str.isdigit, phone_num))
            if len(clean_phone) == 10:
                clean_phone = "1" + clean_phone

            if clean_phone:
                encoded_msg = urllib.parse.quote(message_body)
                sms_url = f"sms:{clean_phone}?body={encoded_msg}"

                st.markdown(f"""
                    <a href="{sms_url}" target="_blank">
                        <button style="
                            background-color: #0284c7;
                            color: white;
                            border: none;
                            padding: 12px 24px;
                            font-size: 16px;
                            border-radius: 8px;
                            cursor: pointer;
                            font-weight: bold;
                            width: 100%;
                        ">💬 Send Direct SMS</button>
                    </a>
                    """, unsafe_allow_html=True)
            else:
                st.caption("💡 Enter a phone number above to enable 1-click SMS dispatching.")
    else:
        st.info("Please register a horse profile first.")

# ----------------------------------------------------
# 3. CORRIDOR CALENDAR & DAILY RUN-SHEET
# ----------------------------------------------------
elif page == "Corridor Calendar & Daily Run-Sheet":
    st.title("📅 Corridor Schedule & Daily Dispatch Run-Sheet")
    st.markdown("Organize weekly corridor runs, track stop order, and generate daily mobile dispatch sheets.")

    try:
        appts_res = supabase.table("appointments").select("*").order("appointment_date").execute()
        all_appts = appts_res.data if appts_res.data else []
    except Exception:
        all_appts = []

    horse_map = {h["id"]: h for h in horses}

    col_d1, col_d2 = st.columns([1, 2])

    with col_d1:
        st.subheader("Select Run-Sheet Date")
        selected_run_date = st.date_input("Dispatch Date", datetime.date.today(), key="run_date_picker")
        day_of_week = selected_run_date.strftime("%A")
        corridor_match = {
            "Monday": "Ottawa Metro & Russell Home Corridor",
            "Tuesday": "Kingston Corridor (South - Hwy 416/401)",
            "Wednesday": "Pembroke & Upper Valley Corridor (North - Hwy 17)",
            "Thursday": "Montreal Corridor (East - Hwy 417)",
            "Friday": "Flagship Barn Dedicated Intensive",
        }.get(day_of_week, "Custom / Weekend Route")
        st.info(f"📍 **Scheduled Corridor:** {corridor_match}")

    with col_d2:
        daily_appts = [a for a in all_appts if a.get("appointment_date") == str(selected_run_date)]
        st.subheader(f"Daily Stop Itinerary: {selected_run_date.strftime('%b %d, %Y')} ({len(daily_appts)} Stops)")

        if daily_appts:
            for idx, appt in enumerate(daily_appts, start=1):
                h_info = horse_map.get(appt.get("horse_id"), {})
                b_info = h_info.get("barn_details", {})
                with st.container():
                    c_s1, c_s2 = st.columns([3, 1])
                    with c_s1:
                        st.markdown(f"**Stop {idx}: {h_info.get('name', 'Horse')}** (Owner: {h_info.get('owner_name', 'N/A')})")
                        st.caption(f"📍 Facility: **{b_info.get('name', 'Barn')}** | Distance: {appt.get('distance_from_base_km', 0)} km | Fee: ${float(appt.get('travel_fee', 0)):.2f}")
                    with c_s2:
                        new_status = st.selectbox(
                            "Status",
                            ["Tentative", "Confirmed", "En Route", "Completed", "Rescheduled"],
                            index=["Tentative", "Confirmed", "En Route", "Completed", "Rescheduled"].index(
                                appt.get("status", "Confirmed")
                            ) if appt.get("status") in ["Tentative", "Confirmed", "En Route", "Completed", "Rescheduled"] else 1,
                            key=f"status_select_{appt['id']}",
                        )
                        if new_status != appt.get("status"):
                            supabase.table("appointments").update({"status": new_status}).eq("id", appt["id"]).execute()
                            st.rerun()
                    st.divider()

            run_sheet_df = pd.DataFrame([
                {
                    "Stop": i + 1,
                    "Horse": horse_map.get(a.get("horse_id"), {}).get("name", ""),
                    "Owner": horse_map.get(a.get("horse_id"), {}).get("owner_name", ""),
                    "Barn / Facility": horse_map.get(a.get("horse_id"), {}).get("barn_details", {}).get("name", ""),
                    "Distance (km)": a.get("distance_from_base_km", 0),
                    "Travel Fee": f"${float(a.get('travel_fee', 0)):.2f}",
                    "Status": a.get("status", "Confirmed"),
                }
                for i, a in enumerate(daily_appts)
            ])

            csv_sheet = run_sheet_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Export Daily Dispatch Run-Sheet (CSV)",
                data=csv_sheet,
                file_name=f"EquusOS_RunSheet_{selected_run_date}.csv",
                mime="text/csv",
            )
        else:
            st.info(f"No appointments booked for {selected_run_date.strftime('%A, %B %d, %Y')}.")

# ----------------------------------------------------
# 4. SMART ROUTE BOOKING & MILEAGE
# ----------------------------------------------------
elif page == "Smart Route Booking & Mileage":
    st.title("Smart Route Corridor Dispatcher")
    st.markdown("Optimize travel routes and automatically calculate mileage fees outside the 30km radius.")

    col1, col2 = st.columns(2)
    with col1:
        with st.form("booking_form"):
            st.subheader("Book Confirmed Route Appointment")
            if horses:
                horse_opts = {f"{h['name']} ({h['barn_details']['name']})": h for h in horses}
                h_choice = st.selectbox("Select Horse", list(horse_opts.keys()))
                chosen_horse = horse_opts[h_choice]

                app_date = st.date_input("Appointment Date", datetime.date.today())
                distance = st.number_input("Estimated Distance from Base (km)", min_value=0.0, value=35.0, step=1.0)

                if st.form_submit_button("Confirm Booking"):
                    try:
                        barn_id_val = chosen_horse.get("barn_id")
                        query = supabase.table("appointments").select("id").eq("appointment_date", str(app_date))
                        if barn_id_val:
                            query = query.eq("barn_id", str(barn_id_val))

                        appts_res = query.execute()
                        same_day_count = (len(appts_res.data) if appts_res.data else 0) + 1
                        travel_fee, is_waived, reason = calculate_travel_fee(float(distance), same_day_count)

                        payload = {
                            "appointment_date": str(app_date),
                            "horse_id": str(chosen_horse["id"]),
                            "distance_from_base_km": float(distance),
                            "travel_fee": float(travel_fee),
                            "status": "Confirmed",
                        }
                        if barn_id_val:
                            payload["barn_id"] = str(barn_id_val)

                        supabase.table("appointments").insert(payload).execute()
                        st.success(f"Appointment Confirmed! Travel Fee: ${travel_fee:.2f} CAD ({reason})")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Booking Error: {err}")

    with col2:
        st.subheader("Designated Corridor Days")
        st.info("""
        * **Monday:** Ottawa Metro & Russell Home Base
        * **Tuesday:** Kingston Corridor (South)
        * **Wednesday:** Pembroke / Valley Corridor (North)
        * **Thursday:** Montreal Corridor (East)
        * **Friday:** Flagship Barn Dedicated Intensive
        """)

# ----------------------------------------------------
# 5. LANDING PAGE & PRICING CONTENT CMS
# ----------------------------------------------------
elif page == "Landing Page & Pricing CMS":
    st.title("🌐 Landing Page Content & Pricing Editor")
    st.markdown("Edit headlines, therapy session pricing, service descriptions, promotional banners, and corridor routes live on your landing page.")

    with st.form("cms_editor_form"):
        st.subheader("1. Announcement Banner & Main Headlines")
        c_banner = st.text_input("Top Announcement Banner (Leave blank to hide)", value=site_content.get("announcement_banner", ""))
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            c_hero_t = st.text_input("Hero Main Title", value=site_content.get("hero_title", ""))
            c_badge1 = st.text_input("Badge 1", value=site_content.get("hero_badge_1", ""))
            c_badge2 = st.text_input("Badge 2", value=site_content.get("hero_badge_2", ""))
        with col_h2:
            c_hero_sub = st.text_input("Hero Subtitle", value=site_content.get("hero_subtitle", ""))
            c_badge3 = st.text_input("Badge 3", value=site_content.get("hero_badge_3", ""))

        st.divider()

        st.subheader("2. Equitron-Pro (HECT) Content & Pricing")
        col_ec1, col_ec2 = st.columns(2)
        with col_ec1:
            c_hect_t = st.text_input("Equitron Title", value=site_content.get("hect_title", ""))
            c_hect_p = st.text_input("Equitron Price Headline", value=site_content.get("hect_price", ""))
            c_hect_d = st.text_area("Equitron Overview Description", value=site_content.get("hect_desc", ""), height=100)
        with col_ec2:
            c_hect_b1 = st.text_input("Equitron Bullet 1", value=site_content.get("hect_bullet_1", ""))
            c_hect_b2 = st.text_input("Equitron Bullet 2", value=site_content.get("hect_bullet_2", ""))
            c_hect_b3 = st.text_input("Equitron Bullet 3", value=site_content.get("hect_bullet_3", ""))

        st.divider()

        st.subheader("3. HaloEQ2 (Halotherapy) Content & Pricing")
        col_hc1, col_hc2 = st.columns(2)
        with col_hc1:
            c_halo_t = st.text_input("HaloEQ2 Title", value=site_content.get("halo_title", ""))
            c_halo_p = st.text_input("HaloEQ2 Price Headline", value=site_content.get("halo_price", ""))
            c_halo_d = st.text_area("HaloEQ2 Overview Description", value=site_content.get("halo_desc", ""), height=100)
        with col_hc2:
            c_halo_b1 = st.text_input("HaloEQ2 Bullet 1", value=site_content.get("halo_bullet_1", ""))
            c_halo_b2 = st.text_input("HaloEQ2 Bullet 2", value=site_content.get("halo_bullet_2", ""))
            c_halo_b3 = st.text_input("HaloEQ2 Bullet 3", value=site_content.get("halo_bullet_3", ""))

        st.divider()

        st.subheader("4. Corridor Routes & Travel Days")
        c_corridors = st.text_area("Regional Corridor Schedule (Markdown Bullet Points)", value=site_content.get("corridor_routes", ""), height=130)

        if st.form_submit_button("💾 Save & Publish Changes Live to Landing Page", use_container_width=True):
            updated_payload = {
                "announcement_banner": c_banner,
                "hero_title": c_hero_t,
                "hero_subtitle": c_hero_sub,
                "hero_badge_1": c_badge1,
                "hero_badge_2": c_badge2,
                "hero_badge_3": c_badge3,
                "hect_title": c_hect_t,
                "hect_price": c_hect_p,
                "hect_desc": c_hect_d,
                "hect_bullet_1": c_hect_b1,
                "hect_bullet_2": c_hect_b2,
                "hect_bullet_3": c_hect_b3,
                "halo_title": c_halo_t,
                "halo_price": c_halo_p,
                "halo_desc": c_halo_d,
                "halo_bullet_1": c_halo_b1,
                "halo_bullet_2": c_halo_b2,
                "halo_bullet_3": c_halo_b3,
                "corridor_routes": c_corridors,
            }
            ok, msg = update_site_content(updated_payload)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

# ----------------------------------------------------
# 6. USER DATABASE & ACCESS MANAGEMENT
# ----------------------------------------------------
elif page == "User Database & Credentials":
    st.title("👥 User Database & Access Management")
    st.markdown("View all registered user accounts, reset passwords, change user roles, and provision admin or clinician credentials.")

    try:
        users_res = supabase.table("app_users").select("*").order("created_at", desc=True).execute()
        all_app_users = users_res.data if users_res.data else []
    except Exception:
        all_app_users = []

    col_u1, col_u2 = st.columns([1, 1])

    with col_u1:
        with st.expander("➕ Provision New User Account", expanded=True):
            with st.form("create_app_user_form"):
                u_name = st.text_input("Full Name*")
                u_email = st.text_input("Email Address (Username)*")
                u_pwd = st.text_input("Temporary Password*", type="password")
                u_role = st.selectbox("Role", ["Client", "Admin", "Clinician Associate"])
                u_phone = st.text_input("Phone Number")

                if st.form_submit_button("Create Database User"):
                    if u_name and u_email and u_pwd:
                        ok, msg = register_db_user(u_email, u_pwd, u_name, role=u_role, phone=u_phone)
                        if ok:
                            st.success(f"Created {u_role} account for {u_name} ({u_email})!")
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.warning("Please fill in all required fields.")

    with col_u2:
        with st.expander("🔑 Reset User Password / Change Status", expanded=True):
            if all_app_users:
                user_dict = {f"{u['full_name']} ({u['email']}) - [{u['role']}]": u for u in all_app_users}
                sel_u_label = st.selectbox("Select User Account", list(user_dict.keys()))
                target_u = user_dict[sel_u_label]

                with st.form("admin_pwd_reset_form"):
                    new_u_pwd = st.text_input("Set New Password", type="password")
                    new_u_status = st.selectbox("Account Status", ["active", "suspended"], index=0 if target_u.get("status") == "active" else 1)
                    new_u_role = st.selectbox("Assign Role", ["Client", "Admin", "Clinician Associate"], index=["Client", "Admin", "Clinician Associate"].index(target_u.get("role", "Client")))

                    if st.form_submit_button("Update User Credentials"):
                        try:
                            update_payload = {"status": new_u_status, "role": new_u_role}
                            if new_u_pwd:
                                update_payload["password_hash"] = hash_password(new_u_pwd)

                            supabase.table("app_users").update(update_payload).eq("id", target_u["id"]).execute()
                            st.success(f"Updated account for {target_u['full_name']}!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error updating user: {e}")
            else:
                st.info("No app users registered in the database yet.")

    st.subheader("Registered App Users & Roles")
    if all_app_users:
        user_table = [
            {
                "Full Name": u.get("full_name"),
                "Email / Username": u.get("email"),
                "Role": u.get("role"),
                "Phone": u.get("phone", ""),
                "Status": "🟢 Active" if u.get("status") == "active" else "🔴 Suspended",
                "Created Date": str(u.get("created_at", ""))[:10],
            }
            for u in all_app_users
        ]
        st.dataframe(pd.DataFrame(user_table), use_container_width=True)

# ----------------------------------------------------
# 7. MANAGE CLIENTS & APPOINTMENTS
# ----------------------------------------------------
elif page == "Manage Clients & Appointments":
    st.title("👥 Master Client Profile & Booking Manager")
    st.markdown("Manage client profiles, book sessions on their behalf, and cancel or reschedule visits.")

    try:
        w_res = supabase.table("client_waivers").select("*").order("created_at", desc=True).execute()
        all_clients = w_res.data if w_res.data else []
    except Exception:
        all_clients = []

    client_lookup = {f"{c['owner_name']} ({c['client_email']}) - Horse: {c['horse_name']}": c for c in all_clients}

    if client_lookup:
        col_m1, col_m2 = st.columns([1, 1])

        with col_m1:
            st.subheader("1. Edit Client Details & Manage Profile")
            sel_client_lbl = st.selectbox("Select Client", list(client_lookup.keys()))
            active_c = client_lookup[sel_client_lbl]

            with st.form("edit_client_form"):
                new_owner = st.text_input("Owner Name", value=active_c.get("owner_name", ""))
                new_email = st.text_input("Email Address", value=active_c.get("client_email", ""))
                new_vet = st.text_input("Primary Veterinarian", value=active_c.get("primary_veterinarian", ""))
                new_phone = st.text_input("Vet Phone", value=active_c.get("vet_phone", ""))
                
                if st.form_submit_button("Save Updated Client Info"):
                    try:
                        supabase.table("client_waivers").update({
                            "owner_name": new_owner,
                            "client_email": new_email,
                            "primary_veterinarian": new_vet,
                            "vet_phone": new_phone,
                        }).eq("id", active_c["id"]).execute()
                        st.success("Client information updated successfully!")
                        st.rerun()
                    except Exception as ex:
                        st.error(f"Error updating: {ex}")

        with col_m2:
            st.subheader("2. Book / Reschedule / Cancel for Client")
            client_horse_match = next((h for h in horses if h.get("name", "").lower() == active_c.get("horse_name", "").lower()), None)
            
            with st.form("paige_booking_client_form"):
                bk_date = st.date_input("Appointment Date", datetime.date.today())
                bk_dist = st.number_input("Distance from Base (km)", min_value=0.0, value=30.0, step=5.0)
                bk_action = st.selectbox("Action", ["Book Confirmed Appointment", "Cancel Existing Booking"])

                if st.form_submit_button("Execute Appointment Action"):
                    if client_horse_match:
                        if bk_action == "Book Confirmed Appointment":
                            try:
                                supabase.table("appointments").insert({
                                    "appointment_date": str(bk_date),
                                    "horse_id": str(client_horse_match["id"]),
                                    "barn_id": str(client_horse_match.get("barn_id")),
                                    "distance_from_base_km": float(bk_dist),
                                    "travel_fee": 0.0 if bk_dist <= 30 else round((bk_dist - 30)*0.73, 2),
                                    "status": "Confirmed",
                                }).execute()
                                st.success(f"Booked confirmed session for {client_horse_match['name']} on {bk_date}!")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error: {e}")
                        else:
                            try:
                                supabase.table("appointments").delete().eq("horse_id", str(client_horse_match["id"])).eq("appointment_date", str(bk_date)).execute()
                                st.warning(f"Cancelled appointment for {client_horse_match['name']} on {bk_date}.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error cancelling: {e}")
                    else:
                        st.error("Horse profile not found in database.")
    else:
        st.info("No registered clients found in the database.")

# ----------------------------------------------------
# 8. PUBLIC INTAKE & BARN QR CODE
# ----------------------------------------------------
elif page == "Public Intake & Barn QR Code":
    st.title("📲 Public Self-Serve Intake & Barn QR Generator")
    st.markdown("Generate printable QR codes and shareable onboarding links for horse owners to access the portal on their smartphones.")

    col_q1, col_q2 = st.columns([1, 1])

    with col_q1:
        st.subheader("Generate Barn QR Code")
        app_base_url = st.text_input("Your Streamlit App URL", value="https://equusos.streamlit.app")
        intake_url = f"{app_base_url.rstrip('/')}"
        st.info(f"🔗 **Public Member Portal URL:** `{intake_url}`")
        encoded_url = urllib.parse.quote(intake_url)
        qr_image_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_url}&bgcolor=ffffff&color=1e293b&margin=10"
        st.image(qr_image_url, caption="Scan to open member portal & intake", width=250)

    with col_q2:
        st.subheader("Printable Barn Notice Preview")
        st.markdown(f"""
        <div style="border: 2px solid #334155; border-radius: 12px; padding: 24px; text-align: center; background-color: #f8fafc; color: #0f172a;">
            <h2 style="margin: 0; color: #0f172a;">🐎 EQUUS PERFORMANCE</h2>
            <p style="margin: 4px 0 16px 0; font-size: 14px; color: #64748b;">CELLULAR REGENERATION & HALOTHERAPY</p>
            <hr style="border: 0; height: 1px; background: #cbd5e1; margin-bottom: 16px;">
            <p style="font-weight: bold; margin-bottom: 12px;">Scan to Access Client Portal & Registration</p>
            <img src="{qr_image_url}" width="180" style="border-radius: 8px; margin-bottom: 12px;"/>
            <p style="font-size: 12px; color: #475569;">Or visit: <br><code>{intake_url}</code></p>
            <p style="font-size: 11px; color: #94a3b8; margin-top: 12px;">Equus Performance Therapeutics | Paige Cummings</p>
        </div>
        """, unsafe_allow_html=True)

# ----------------------------------------------------
# 9. SIGNED WAIVERS & ONBOARDING
# ----------------------------------------------------
elif page == "Signed Waivers & Onboarding":
    st.title("Client Onboarding & Legal Liability Waiver")
    st.markdown("New clients must complete this intake form and execute the liability acknowledgment prior to receiving treatment.")

    with st.form("client_waiver_form"):
        st.subheader("1. Owner & Horse Details")
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            owner_name = st.text_input("Owner Full Name")
            client_email = st.text_input("Email Address")
            horse_name = st.text_input("Horse Competition Name")
        with col_w2:
            primary_vet = st.text_input("Primary Veterinarian Name")
            vet_phone = st.text_input("Veterinarian Contact Number")

        st.subheader("2. Modality Consent Selection")
        consent_hect = st.checkbox("Consent for High-Energy Cell Treatment (Equitron-Pro / HECT)")
        consent_halo = st.checkbox("Consent for Clinical Dry Salt Halotherapy (HaloEQ2)")

        st.subheader("3. Terms & Liability Acknowledgment")
        st.markdown("""
        > **Scope of Practice & Release of Liability:**  
        > Equus Performance Therapeutics provides non-invasive complementary equine wellness, cellular regeneration, and respiratory recovery support. These services do not replace formal veterinary diagnosis, medicine, or surgery. The undersigned owner confirms that the animal is free of acute, contagious infectious diseases, and releases Paige Cummings and Equus Performance Therapeutics from liability arising from complementary therapy applications.
        """)

        waiver_agreed = st.checkbox("I have read, understood, and agree to the terms of service and liability waiver.")
        signature_name = st.text_input("Electronic Signature (Type Full Legal Name)")

        if st.form_submit_button("Submit Intake & Signed Waiver"):
            if owner_name and client_email and horse_name and signature_name:
                if not waiver_agreed:
                    st.error("You must check the waiver agreement box to complete onboarding.")
                else:
                    try:
                        modalities_chosen = []
                        if consent_hect:
                            modalities_chosen.append("Equitron-Pro (HECT)")
                        if consent_halo:
                            modalities_chosen.append("HaloEQ2 (Halotherapy)")

                        supabase.table("client_waivers").insert({
                            "owner_name": owner_name,
                            "client_email": client_email,
                            "horse_name": horse_name,
                            "primary_veterinarian": primary_vet,
                            "vet_phone": vet_phone,
                            "modality_consent": modalities_chosen,
                            "waiver_agreed": waiver_agreed,
                            "signature_name": signature_name,
                        }).execute()

                        st.success(f"Waiver successfully executed and archived for {horse_name} (Owner: {owner_name})!")
                    except Exception as e:
                        st.error(f"Error saving waiver: {e}")
            else:
                st.warning("Please fill in all required contact fields and provide your electronic signature.")

# ----------------------------------------------------
# 10. FACILITY RETAINERS & RECONCILIATION
# ----------------------------------------------------
elif page == "Facility Retainers & Reconciliation":
    st.title("🏛️ Facility Retainer & Intensive Reconciliation")
    st.markdown("Manage facility partner contracts, monitor promotional minute allowances vs. standard overages, and generate master facility statements.")

    if barns:
        barn_pick = {b["name"]: b for b in barns}
        chosen_bname = st.selectbox("Select Partner Facility", list(barn_pick.keys()))
        chosen_b = barn_pick[chosen_bname]

        f_horses = [h for h in horses if h.get("barn_id") == chosen_b["id"]]
        f_horse_ids = [h["id"] for h in f_horses]

        try:
            all_l_res = supabase.table("treatment_logs").select("*").execute()
            all_l = all_l_res.data if all_l_res.data else []
            facility_l = [l for l in all_l if l.get("horse_id") in f_horse_ids]
        except Exception:
            facility_l = []

        mktg_horses = [h for h in f_horses if h.get("is_marketing_tier", False)]
        tot_mins = sum(int(l.get("duration_minutes", 0)) for l in facility_l)
        tot_billed = sum(float(l.get("calculated_fee", 0)) for l in facility_l)

        waived_promo_mins = sum(min(int(mh.get("minutes_used_this_month", 0)), 200) for mh in mktg_horses)
        waived_promo_value = waived_promo_mins * 2.0

        c_b1, c_b2, c_b3, c_b4 = st.columns(4)
        c_b1.metric("Active Stabled Horses", len(f_horses))
        c_b2.metric("Total Facility Therapy", f"{tot_mins:,} Mins")
        c_b3.metric("Waived Promo Value", f"${waived_promo_value:,.2f} CAD")
        c_b4.metric("Net Facility Billable", f"${tot_billed:,.2f} CAD")

        st.divider()
        recon_rows = []
        for h in f_horses:
            h_used = int(h.get("minutes_used_this_month", 0))
            is_mktg = h.get("is_marketing_tier", False)
            h_logs = [l for l in facility_l if l.get("horse_id") == h["id"]]
            h_billed = sum(float(l.get("calculated_fee", 0)) for l in h_logs)
            waived_for_h = (min(h_used, 200) * 2.0) if is_mktg else 0.0

            recon_rows.append({
                "Horse Name": h.get("name", "Unknown"),
                "Owner": h.get("owner_name", "Unknown"),
                "Tier": "🌟 Marketing Promo Tier (200 Free Mins)" if is_mktg else "Standard Tier ($1.00 Baseline)",
                "Minutes Used": h_used,
                "Waived Promo": f"${waived_for_h:.2f}",
                "Total Billable": f"${h_billed:.2f}",
            })

        if recon_rows:
            st.dataframe(pd.DataFrame(recon_rows), use_container_width=True)
            facility_pdf_bytes = create_facility_reconciliation_pdf(chosen_b, recon_rows, tot_billed, waived_promo_value)
            st.download_button(
                label="📄 Export Master Facility Retainer & Reconciliation Statement (PDF)",
                data=bytes(facility_pdf_bytes),
                file_name=f"EquusOS_Facility_Statement_{chosen_bname.replace(' ', '_')}_{datetime.date.today()}.pdf",
                mime="application/pdf",
            )
    else:
        st.info("No facilities registered.")

# ----------------------------------------------------
# 11. PRE-PAID PACKAGES & CREDIT PASSES
# ----------------------------------------------------
elif page == "Pre-Paid Packages & Credit Passes":
    st.title("🎟️ Pre-Paid Multi-Session Packages & Passes")
    st.markdown("Manage pre-paid treatment bundles, track punch-card credit balances, and redeem session credits.")

    try:
        pkg_res = supabase.table("client_packages").select("*").order("created_at", desc=True).execute()
        all_packages = pkg_res.data if pkg_res.data else []
    except Exception:
        all_packages = []

    horse_map = {h["id"]: h for h in horses}
    col_pk1, col_pk2 = st.columns([1, 1])

    with col_pk1:
        with st.expander("➕ Enroll Client in Multi-Session Package", expanded=True):
            if horses:
                with st.form("new_package_form"):
                    horse_picker_pkg = {f"{h['name']} ({h['owner_name']})": h for h in horses}
                    chosen_hpkg = st.selectbox("Select Horse", list(horse_picker_pkg.keys()))
                    target_h = horse_picker_pkg[chosen_hpkg]

                    pkg_type = st.selectbox("Package Tier", [
                        "5-Session Equitron Rehab Pack ($275 CAD)",
                        "10-Session Performance Maintenance Pack ($520 CAD)",
                        "5-Session HaloEQ2 Pulmonary Reset ($225 CAD)",
                        "3-Session Acute Injury Intensive ($165 CAD)",
                    ])
                    default_credits = 10 if "10-Session" in pkg_type else (3 if "3-Session" in pkg_type else 5)
                    default_price = 520.0 if "10-Session" in pkg_type else (165.0 if "3-Session" in pkg_type else 275.0)

                    c_credits = st.number_input("Total Credits in Pass", min_value=1, max_value=50, value=default_credits)
                    c_price = st.number_input("Package Price (CAD)", min_value=0.0, step=25.0, value=default_price)
                    p_status = st.selectbox("Payment Status", ["Paid via e-Transfer", "Pending Payment"])
                    pkg_notes = st.text_area("Pass Notes / Terms")

                    if st.form_submit_button("Create Pre-Paid Package"):
                        try:
                            supabase.table("client_packages").insert({
                                "owner_name": target_h.get("owner_name", "Unknown"),
                                "horse_id": str(target_h["id"]),
                                "package_name": pkg_type,
                                "total_credits": int(c_credits),
                                "remaining_credits": int(c_credits),
                                "package_price": float(c_price),
                                "payment_status": p_status,
                                "notes": pkg_notes,
                            }).execute()
                            st.success(f"Package created for {target_h['name']} with {c_credits} credits!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error creating package: {e}")
            else:
                st.info("Please register a horse profile first.")

    with col_pk2:
        with st.expander("⚡ 1-Click Credit Redemption", expanded=True):
            active_packages = [p for p in all_packages if int(p.get("remaining_credits", 0)) > 0]
            if active_packages:
                pkg_options = {
                    f"{p.get('package_name')} - {horse_map.get(p.get('horse_id'), {}).get('name', 'Horse')} ({p.get('remaining_credits')}/{p.get('total_credits')} Credits Left)": p
                    for p in active_packages
                }
                sel_pkg_label = st.selectbox("Select Active Package to Redeem", list(pkg_options.keys()))
                chosen_pkg = pkg_options[sel_pkg_label]

                if st.button("✅ Redeem 1 Pre-Paid Session Credit"):
                    new_balance = int(chosen_pkg.get("remaining_credits", 1)) - 1
                    supabase.table("client_packages").update({"remaining_credits": new_balance}).eq("id", chosen_pkg["id"]).execute()
                    st.success(f"Redeemed 1 credit! New balance: {new_balance} credits remaining.")
                    st.rerun()
            else:
                st.write("No active pre-paid packages with remaining credits.")

# ----------------------------------------------------
# 12. TRAINER & REFERRAL INCENTIVES
# ----------------------------------------------------
elif page == "Trainer & Referral Incentives":
    st.title("🤝 Trainer & Barn Manager Referral Incentives")
    st.markdown("Track referring coaches, barn managers, and veterinary advocates.")

    try:
        ref_res = supabase.table("referral_partners").select("*").order("created_at", desc=True).execute()
        referral_partners = ref_res.data if ref_res.data else []
    except Exception:
        referral_partners = []

    try:
        ref_logs_res = supabase.table("referral_commissions").select("*").order("created_at", desc=True).execute()
        ref_commissions = ref_logs_res.data if ref_logs_res.data else []
    except Exception:
        ref_commissions = []

    col_rf1, col_rf2 = st.columns([1, 1])

    with col_rf1:
        with st.expander("➕ Register Referral Partner / Coach", expanded=True):
            with st.form("add_partner_form"):
                p_name = st.text_input("Partner / Trainer Full Name*")
                p_role = st.selectbox("Role", ["Head Trainer / Coach", "Barn Manager", "Veterinarian", "Equine Bodyworker / Farrier", "Client Advocate"])
                p_email = st.text_input("Email Address", placeholder="trainer@barn.ca")
                p_phone = st.text_input("Phone Number")
                rew_type = st.selectbox("Incentive Type", ["Cash Split (10% of Referred Billings)", "Comped Session Credits (1 Free 20-min Session per 5 Referred)", "Fixed Referral Fee ($15 per New Client)"])
                p_notes = st.text_area("Partnership Notes")

                if st.form_submit_button("Save Referral Partner"):
                    if p_name:
                        supabase.table("referral_partners").insert({
                            "partner_name": p_name,
                            "role": p_role,
                            "email": p_email,
                            "phone": p_phone,
                            "incentive_type": rew_type,
                            "notes": p_notes,
                        }).execute()
                        st.success(f"Registered referral partner: {p_name}!")
                        st.rerun()

    with col_rf2:
        with st.expander("🎯 Log Client Referral & Compute Payout", expanded=True):
            if referral_partners and horses:
                with st.form("log_referral_form"):
                    partner_lookup = {f"{p['partner_name']} ({p['role']})": p for p in referral_partners}
                    sel_p = st.selectbox("Referring Partner", list(partner_lookup.keys()))
                    chosen_partner = partner_lookup[sel_p]

                    horse_lookup_ref = {f"{h['name']} (Owner: {h['owner_name']})": h for h in horses}
                    sel_h = st.selectbox("Referred Equine Client", list(horse_lookup_ref.keys()))
                    chosen_h = horse_lookup_ref[sel_h]

                    ref_date = st.date_input("Referral Date", datetime.date.today())
                    session_rev = st.number_input("Session Value / Package Billed ($)", min_value=0.0, value=60.0, step=10.0)

                    earned_amount = round(session_rev * 0.10, 2) if "10%" in chosen_partner.get("incentive_type", "") else 15.00
                    earned_credits = 1 if "Comped" in chosen_partner.get("incentive_type", "") else 0
                    ref_notes = st.text_input("Notes / Payout Ref")

                    if st.form_submit_button("Record Referral Credit"):
                        supabase.table("referral_commissions").insert({
                            "partner_id": chosen_partner["id"],
                            "horse_id": chosen_h["id"],
                            "referral_date": str(ref_date),
                            "session_value": float(session_rev),
                            "commission_amount": float(earned_amount),
                            "earned_credits": int(earned_credits),
                            "payout_status": "Pending",
                            "notes": ref_notes,
                        }).execute()
                        st.success(f"Logged referral commission for {chosen_partner['partner_name']}!")
                        st.rerun()

# ----------------------------------------------------
# 13. ACCOUNTS PAYABLE (A/P) BILLS
# ----------------------------------------------------
elif page == "Accounts Payable (A/P) Bills":
    st.title("📑 Accounts Payable (A/P) & Vendor Bills")
    st.markdown("Track supplier invoices, operating payables, bill due dates, payment records, and Input Tax Credits (ITCs).")

    try:
        bills_res = supabase.table("vendor_bills").select("*").order("due_date").execute()
        all_bills = bills_res.data if bills_res.data else []
    except Exception:
        all_bills = []

    total_ap_outstanding = sum(float(b.get("total_amount", 0)) - float(b.get("amount_paid", 0)) for b in all_bills if b.get("status") != "Paid")
    total_ap_paid = sum(float(b.get("amount_paid", 0)) for b in all_bills)
    total_itc_tax = sum(float(b.get("tax_hst", 0)) for b in all_bills)

    c_ap1, c_ap2, c_ap3 = st.columns(3)
    c_ap1.metric("Outstanding Accounts Payable", f"${total_ap_outstanding:,.2f} CAD", delta=f"-${total_ap_outstanding:,.2f}" if total_ap_outstanding > 0 else "All Bills Paid", delta_color="inverse")
    c_ap2.metric("Total Vendor Bills Settled", f"${total_ap_paid:,.2f} CAD")
    c_ap3.metric("Eligible HST/GST Tax Credits (ITCs)", f"${total_itc_tax:,.2f} CAD")

    col_b1, col_b2 = st.columns([1, 1])

    with col_b1:
        with st.expander("➕ Enter New Vendor Bill", expanded=True):
            with st.form("new_vendor_bill_form"):
                v_name = st.text_input("Vendor / Supplier Name*", placeholder="e.g. Equitron Canada, Shell, Salt Co.")
                v_inv_no = st.text_input("Invoice / Bill #", placeholder="INV-2026-08")
                v_date = st.date_input("Bill Date", datetime.date.today())
                v_due = st.date_input("Due Date", datetime.date.today() + datetime.timedelta(days=30))
                v_cat = st.selectbox("Expense Category / Account", [
                    "5010 - Vehicle Fuel & Travel Expense",
                    "5020 - Vehicle Maintenance & Repairs",
                    "5030 - Equipment Sinking Fund & Maintenance",
                    "5040 - Consumables & Clinical Supplies",
                    "5050 - Insurance (Commercial & Equine Liability)",
                    "5060 - Marketing, Software & Technology",
                    "5070 - Professional Fees & Accounting",
                    "1510 - Capital Asset (Equipment Purchase)",
                ])
                v_sub = st.number_input("Subtotal Amount ($ CAD)", min_value=0.0, step=10.0, value=100.0)
                v_tax = st.number_input("HST / Tax Paid ($ CAD - 13% ON)", min_value=0.0, step=1.0, value=round(v_sub * 0.13, 2))
                v_tot = v_sub + v_tax
                st.caption(f"**Total Bill Amount:** ${v_tot:.2f} CAD")
                v_notes = st.text_area("Bill Description / Notes")

                if st.form_submit_button("Save Vendor Bill to A/P"):
                    if v_name and v_tot > 0:
                        supabase.table("vendor_bills").insert({
                            "vendor_name": v_name,
                            "bill_number": v_inv_no,
                            "bill_date": str(v_date),
                            "due_date": str(v_due),
                            "category": v_cat.split(" - ")[1],
                            "account_code": v_cat.split(" - ")[0],
                            "subtotal": float(v_sub),
                            "tax_hst": float(v_tax),
                            "total_amount": float(v_tot),
                            "amount_paid": 0.0,
                            "status": "Unpaid",
                            "notes": v_notes,
                        }).execute()
                        st.success(f"Vendor bill from {v_name} recorded!")
                        st.rerun()

    with col_b2:
        with st.expander("💳 1-Click Pay Vendor Bill", expanded=True):
            unpaid_bills = [b for b in all_bills if b.get("status") != "Paid"]
            if unpaid_bills:
                bill_opts = {f"{b['vendor_name']} (Due: {b['due_date']}) - Rem: ${float(b['total_amount']) - float(b['amount_paid']):.2f} CAD": b for b in unpaid_bills}
                sel_b_label = st.selectbox("Select Bill to Pay", list(bill_opts.keys()))
                target_b = bill_opts[sel_b_label]
                rem_bal = float(target_b["total_amount"]) - float(target_b["amount_paid"])
                
                with st.form("pay_bill_form"):
                    p_amt = st.number_input("Payment Amount ($ CAD)", min_value=1.0, max_value=rem_bal, value=rem_bal, step=10.0)
                    p_method = st.selectbox("Payment Method", ["e-Transfer", "Credit Card", "Cheque", "Debit"])
                    p_ref = st.text_input("Confirmation / Cheque #")
                    p_dt = st.date_input("Payment Date", datetime.date.today())

                    if st.form_submit_button("Execute Bill Payment"):
                        supabase.table("vendor_payments").insert({
                            "bill_id": target_b["id"],
                            "payment_date": str(p_dt),
                            "payment_method": p_method,
                            "amount_paid": float(p_amt),
                            "reference_number": p_ref,
                            "notes": f"Paid {p_method} ref: {p_ref}",
                        }).execute()

                        new_paid = float(target_b["amount_paid"]) + float(p_amt)
                        new_status = "Paid" if new_paid >= float(target_b["total_amount"]) else "Partially Paid"
                        supabase.table("vendor_bills").update({"amount_paid": new_paid, "status": new_status}).eq("id", target_b["id"]).execute()
                        st.success(f"Payment of ${p_amt:.2f} applied to {target_b['vendor_name']}!")
                        st.rerun()
            else:
                st.info("No unpaid vendor bills pending in A/P.")

# ----------------------------------------------------
# 14. ACCOUNTS RECEIVABLE (A/R) AGING
# ----------------------------------------------------
elif page == "Accounts Receivable (A/R) Aging":
    st.title("📈 Accounts Receivable (A/R) & Aging Summary")
    st.markdown("Monitor outstanding client balances and aging schedules (Current, 1-30, 31-60, 61-90, 90+ days).")

    try:
        logs_res = supabase.table("treatment_logs").select("*").execute()
        all_logs = logs_res.data if logs_res.data else []
    except Exception:
        all_logs = []

    try:
        pmts_res = supabase.table("client_payments").select("*").execute()
        all_pmts = pmts_res.data if pmts_res.data else []
    except Exception:
        all_pmts = []

    horse_id_to_owner = {h["id"]: h.get("owner_name", "Unknown") for h in horses}
    owners = sorted(list(set([h.get("owner_name") for h in horses if h.get("owner_name")] + [p.get("owner_name") for p in all_pmts])))

    total_billed = sum(float(l.get("calculated_fee", 0)) for l in all_logs)
    total_received = sum(float(p.get("amount_paid", 0)) for p in all_pmts)
    total_ar = total_billed - total_received

    c_ar1, c_ar2, c_ar3 = st.columns(3)
    c_ar1.metric("Total Invoiced", f"${total_billed:,.2f} CAD")
    c_ar2.metric("Total Collected", f"${total_received:,.2f} CAD")
    c_ar3.metric("Net A/R Balance Outstanding", f"${total_ar:,.2f} CAD", delta=f"-${total_ar:,.2f}" if total_ar > 0 else "All Accounts Current", delta_color="inverse")

    st.subheader("Client A/R Aging Analysis Schedule")
    aging_rows = []
    today = datetime.date.today()

    for o in owners:
        o_logs = [l for l in all_logs if horse_id_to_owner.get(l.get("horse_id")) == o]
        o_billed = sum(float(l.get("calculated_fee", 0)) for l in o_logs)
        o_paid = sum(float(p.get("amount_paid", 0)) for p in all_pmts if p.get("owner_name") == o)
        o_balance = o_billed - o_paid

        cur_amt, d1_30, d31_60, d61_90, d90_plus = 0.0, 0.0, 0.0, 0.0, 0.0
        if o_balance > 0:
            if o_logs:
                oldest_date_str = str(o_logs[-1].get("created_at", ""))[:10]
                try:
                    oldest_date = datetime.datetime.strptime(oldest_date_str, "%Y-%m-%d").date()
                    age_days = (today - oldest_date).days
                except Exception:
                    age_days = 0

                if age_days <= 0:
                    cur_amt = o_balance
                elif age_days <= 30:
                    d1_30 = o_balance
                elif age_days <= 60:
                    d31_60 = o_balance
                elif age_days <= 90:
                    d61_90 = o_balance
                else:
                    d90_plus = o_balance
            else:
                cur_amt = o_balance

        aging_rows.append({
            "Client / Owner": o,
            "Total Invoiced": f"${o_billed:,.2f}",
            "Total Paid": f"${o_paid:,.2f}",
            "Current Due": f"${cur_amt:,.2f}",
            "1 - 30 Days": f"${d1_30:,.2f}",
            "31 - 60 Days": f"${d31_60:,.2f}",
            "61 - 90 Days": f"${d61_90:,.2f}",
            "90+ Days Overdue": f"${d90_plus:,.2f}",
            "Total Balance": f"${o_balance:,.2f}",
            "Status": "✅ Paid in Full" if o_balance <= 0 else "⚠️ Overdue",
        })

    if aging_rows:
        df_aging = pd.DataFrame(aging_rows)
        st.dataframe(df_aging, use_container_width=True)
        csv_ar = df_aging.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export A/R Aging Schedule (CSV)",
            data=csv_ar,
            file_name=f"EquusOS_AR_Aging_Report_{datetime.date.today()}.csv",
            mime="text/csv",
        )

# ----------------------------------------------------
# 15. HST / SALES TAX & CRA ITC REPORT
# ----------------------------------------------------
elif page == "HST / Sales Tax (CRA ITC Report)":
    st.title("🏛️ Canadian HST / GST & Input Tax Credit (ITC) Report")
    st.markdown("Track sales tax collected on therapeutic services and corridor travel against ITCs paid on business expenses for CRA filings.")

    try:
        logs_res = supabase.table("treatment_logs").select("calculated_fee").execute()
        gross_therapy = sum(float(l.get("calculated_fee", 0)) for l in logs_res.data) if logs_res.data else 0.0
    except Exception:
        gross_therapy = 0.0

    try:
        appts_res = supabase.table("appointments").select("travel_fee").execute()
        gross_travel = sum(float(a.get("travel_fee", 0)) for a in appts_res.data) if appts_res.data else 0.0
    except Exception:
        gross_travel = 0.0

    try:
        bills_res = supabase.table("vendor_bills").select("subtotal, tax_hst").execute()
        vendor_tax_itcs = sum(float(b.get("tax_hst", 0)) for b in bills_res.data) if bills_res.data else 0.0
    except Exception:
        vendor_tax_itcs = 0.0

    tax_rate = st.sidebar.number_input("Tax Rate (%) - Default Ontario HST", min_value=0.0, max_value=25.0, value=13.0, step=1.0)
    taxable_revenue = gross_therapy + gross_travel
    hst_collected = round(taxable_revenue * (tax_rate / 100.0), 2)
    itcs_paid = round(vendor_tax_itcs, 2)
    net_tax_remittance = hst_collected - itcs_paid

    c_tx1, c_tx2, c_tx3 = st.columns(3)
    c_tx1.metric(f"HST Collected on Sales ({tax_rate}%)", f"${hst_collected:,.2f} CAD")
    c_tx2.metric("Input Tax Credits (ITCs Paid)", f"${itcs_paid:,.2f} CAD")
    c_tx3.metric("Net Remittance to CRA", f"${net_tax_remittance:,.2f} CAD", delta=f"${net_tax_remittance:,.2f}" if net_tax_remittance >= 0 else "CRA Refund Due", delta_color="normal" if net_tax_remittance >= 0 else "inverse")

    st.subheader("CRA Sales Tax Breakdown Ledger")
    tax_summary_data = [
        {"CRA Line Item": "Line 101 - Total Sales & Revenue", "Amount (CAD)": f"${taxable_revenue:,.2f}"},
        {"CRA Line Item": "Line 105 - Total HST/GST Collected or Collectible", "Amount (CAD)": f"${hst_collected:,.2f}"},
        {"CRA Line Item": "Line 108 - Total Input Tax Credits (ITCs on Purchases)", "Amount (CAD)": f"-${itcs_paid:,.2f}"},
        {"CRA Line Item": "Line 109 - Net Tax Payable / (Refund Eligible)", "Amount (CAD)": f"${net_tax_remittance:,.2f}"},
    ]
    st.dataframe(pd.DataFrame(tax_summary_data), use_container_width=True)

# ----------------------------------------------------
# 16. CHART OF ACCOUNTS & GENERAL LEDGER
# ----------------------------------------------------
elif page == "Chart of Accounts & General Ledger":
    st.title("📚 Chart of Accounts & General Ledger")
    st.markdown("Standard double-entry chart of accounts structuring Assets (1000s), Liabilities (2000s), Equity (3000s), Revenue (4000s), and Expenses (5000s).")

    try:
        coa_res = supabase.table("chart_of_accounts").select("*").order("account_code").execute()
        all_accounts = coa_res.data if coa_res.data else []
    except Exception:
        all_accounts = []

    col_coa1, col_coa2 = st.columns([1, 2])

    with col_coa1:
        with st.expander("➕ Add New Account to Ledger", expanded=True):
            with st.form("new_coa_form"):
                a_code = st.text_input("Account Code (e.g. 5080)")
                a_name = st.text_input("Account Name")
                a_type = st.selectbox("Account Type", ["Asset", "Liability", "Equity", "Revenue", "Expense"])
                a_desc = st.text_area("Description")

                if st.form_submit_button("Save Account to Chart of Accounts"):
                    if a_code and a_name:
                        supabase.table("chart_of_accounts").insert({
                            "account_code": a_code,
                            "account_name": a_name,
                            "account_type": a_type,
                            "description": a_desc,
                        }).execute()
                        st.success(f"Added account {a_code} - {a_name}!")
                        st.rerun()

    with col_coa2:
        st.subheader("Active Chart of Accounts")
        if all_accounts:
            st.dataframe(pd.DataFrame(all_accounts)[["account_code", "account_name", "account_type", "description"]].rename(
                columns={
                    "account_code": "Code",
                    "account_name": "Account Name",
                    "account_type": "Classification",
                    "description": "Ledger Description",
                }
            ), use_container_width=True)

# ----------------------------------------------------
# 17. ACCOUNTANT HANDOFF EXPORT PACK
# ----------------------------------------------------
elif page == "Accountant Handoff Export Pack":
    st.title("💼 Accountant Year-End & Period Handoff Pack")
    st.markdown("1-Click export of complete bookkeeping packages: General Ledger, Trial Balance, P&L, Balance Sheet, A/R Aging, A/P Aging, and Sales Tax schedules.")

    try:
        l_res = supabase.table("treatment_logs").select("calculated_fee").execute()
        total_session_rev = sum(float(l.get("calculated_fee", 0)) for l in l_res.data) if l_res.data else 0.0
    except Exception:
        total_session_rev = 0.0

    try:
        a_res = supabase.table("appointments").select("travel_fee").execute()
        total_travel_rev = sum(float(a.get("travel_fee", 0)) for a in a_res.data) if a_res.data else 0.0
    except Exception:
        total_travel_rev = 0.0

    try:
        p_res = supabase.table("client_payments").select("amount_paid").execute()
        total_collected = sum(float(p.get("amount_paid", 0)) for p in p_res.data) if p_res.data else 0.0
    except Exception:
        total_collected = 0.0

    try:
        b_res = supabase.table("vendor_bills").select("subtotal, tax_hst, total_amount, amount_paid, category").execute()
        total_bills_amt = sum(float(b.get("total_amount", 0)) for b in b_res.data) if b_res.data else 0.0
        total_bills_paid = sum(float(b.get("amount_paid", 0)) for b in b_res.data) if b_res.data else 0.0
        total_itc_tax = sum(float(b.get("tax_hst", 0)) for b in b_res.data) if b_res.data else 0.0
    except Exception:
        total_bills_amt, total_bills_paid, total_itc_tax = 0.0, 0.0, 0.0

    total_revenue = total_session_rev + total_travel_rev
    ar_balance = total_revenue - total_collected
    ap_balance = total_bills_amt - total_bills_paid
    operating_expenses = total_bills_amt - total_itc_tax
    net_income = total_revenue - operating_expenses

    trial_balance_data = [
        {"Account Code": "1010", "Account Name": "Operating Bank Account", "Debit ($ CAD)": f"${total_collected - total_bills_paid:,.2f}", "Credit ($ CAD)": "$0.00"},
        {"Account Code": "1050", "Account Name": "Accounts Receivable (A/R)", "Debit ($ CAD)": f"${ar_balance:,.2f}", "Credit ($ CAD)": "$0.00"},
        {"Account Code": "2010", "Account Name": "Accounts Payable (A/P)", "Debit ($ CAD)": "$0.00", "Credit ($ CAD)": f"${ap_balance:,.2f}"},
        {"Account Code": "2050", "Account Name": "HST Collected on Sales", "Debit ($ CAD)": "$0.00", "Credit ($ CAD)": f"${total_revenue * 0.13:,.2f}"},
        {"Account Code": "2060", "Account Name": "HST Input Tax Credits Paid", "Debit ($ CAD)": f"${total_itc_tax:,.2f}", "Credit ($ CAD)": "$0.00"},
        {"Account Code": "4010", "Account Name": "Therapy Session Revenue", "Debit ($ CAD)": "$0.00", "Credit ($ CAD)": f"${total_session_rev:,.2f}"},
        {"Account Code": "4020", "Account Name": "Corridor Mileage Revenue", "Debit ($ CAD)": "$0.00", "Credit ($ CAD)": f"${total_travel_rev:,.2f}"},
        {"Account Code": "5000", "Account Name": "Operating Expenses (Total)", "Debit ($ CAD)": f"${operating_expenses:,.2f}", "Credit ($ CAD)": "$0.00"},
    ]

    st.subheader("📑 Period Trial Balance Summary")
    st.dataframe(pd.DataFrame(trial_balance_data), use_container_width=True)

    tb_csv = pd.DataFrame(trial_balance_data).to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download Complete Trial Balance & General Ledger (CSV)",
        data=tb_csv,
        file_name=f"EquusOS_Trial_Balance_For_Accountant_{datetime.date.today()}.csv",
        mime="text/csv",
    )

# ----------------------------------------------------
# 18. EMAIL INVOICE DISPATCHER
# ----------------------------------------------------
elif page == "Email Invoice Dispatcher":
    st.title("📧 Direct Email Statement & Receipt Dispatcher")
    st.markdown("Dispatch professional PDF invoices directly to clients and barn managers with pre-filled e-Transfer instructions.")

    if barns:
        col_em1, col_em2 = st.columns([1, 1])
        with col_em1:
            barn_options = {b["name"]: b["id"] for b in barns}
            sel_b_name = st.selectbox("Select Barn / Facility to Bill", list(barn_options.keys()), key="email_b_select")
            sel_b_id = barn_options[sel_b_name]
            fac_horses = [h for h in horses if h.get("barn_id") == sel_b_id]

            recipient = st.text_input("Recipient Email Address", placeholder="owner@barn.ca")
            email_subj = st.text_input("Email Subject", value=f"Equus Performance Statement - {sel_b_name} ({datetime.date.today().strftime('%B %Y')})")
            email_body_input = st.text_area("Email Message Body", value="Attached is your therapy statement from Equus Performance Therapeutics. Payment via e-Transfer to paige@equusperformance.ca", height=140)

        with col_em2:
            fac_h_ids = [h["id"] for h in fac_horses]
            try:
                l_res = supabase.table("treatment_logs").select("*").order("created_at", desc=True).execute()
                all_l = l_res.data if l_res.data else []
            except Exception:
                all_l = []

            fac_logs = [l for l in all_l if l.get("horse_id") in fac_h_ids]
            h_dict = {h["id"]: h for h in fac_horses}
            inv_rows = []
            tot_amt = 0.0

            for l in fac_logs:
                h = h_dict.get(l.get("horse_id"), {})
                fee = float(l.get("calculated_fee", 0))
                tot_amt += fee
                inv_rows.append({
                    "Date": l.get("created_at", "")[:10],
                    "Horse Name": h.get("name", "Unknown"),
                    "Owner": h.get("owner_name", "Unknown"),
                    "Modality": l.get("modality", ""),
                    "Duration (Mins)": l.get("duration_minutes", 0),
                    "Fee (CAD)": f"${fee:.2f}",
                    "Notes": l.get("session_notes", ""),
                })

            if inv_rows:
                pdf_bytes_obj = bytes(create_pdf_invoice(sel_b_name, inv_rows, tot_amt))
                file_name_str = f"Equus_Invoice_{sel_b_name.replace(' ', '_')}_{datetime.date.today()}.pdf"

                if st.button("🚀 Send Official Invoice & PDF Attachment"):
                    if recipient and "@" in recipient:
                        with st.spinner("Sending email..."):
                            success, msg_result = send_email_with_pdf(recipient, email_subj, email_body_input, pdf_bytes_obj, file_name_str)
                            if success:
                                st.success(msg_result)
                            else:
                                st.error(msg_result)

# ----------------------------------------------------
# 19. MONTHLY INVOICING & PDF STATEMENTS
# ----------------------------------------------------
elif page == "Monthly Invoicing & PDF Statements":
    st.title("Monthly Invoicing & Billing Summary")
    st.markdown("Generate monthly billing breakdowns and export professional PDF statements for barns and owners.")

    if barns:
        barn_opts = {b["name"]: b["id"] for b in barns}
        chosen_barn_name = st.selectbox("Select Barn / Facility", list(barn_opts.keys()))
        chosen_barn_id = barn_opts[chosen_barn_name]

        facility_horses = [h for h in horses if h.get("barn_id") == chosen_barn_id]
        facility_horse_ids = [h["id"] for h in facility_horses]

        try:
            logs_res = supabase.table("treatment_logs").select("*").order("created_at", desc=True).execute()
            all_logs = logs_res.data if logs_res.data else []
        except Exception:
            all_logs = []

        facility_logs = [l for l in all_logs if l.get("horse_id") in facility_horse_ids]
        horse_dict = {h["id"]: h for h in facility_horses}

        if facility_logs:
            invoice_rows = []
            total_billed = 0.0
            for l in facility_logs:
                h = horse_dict.get(l.get("horse_id"), {})
                fee = float(l.get("calculated_fee", 0))
                total_billed += fee
                invoice_rows.append({
                    "Date": l.get("created_at", "")[:10],
                    "Horse Name": h.get("name", "Unknown"),
                    "Owner": h.get("owner_name", "Unknown"),
                    "Modality": l.get("modality", ""),
                    "Duration (Mins)": l.get("duration_minutes", 0),
                    "Fee (CAD)": f"${fee:.2f}",
                    "Notes": l.get("session_notes", ""),
                })

            st.dataframe(pd.DataFrame(invoice_rows), use_container_width=True)
            pdf_output = create_pdf_invoice(chosen_barn_name, invoice_rows, total_billed)
            st.download_button(
                label="📄 Download Professional PDF Invoice",
                data=bytes(pdf_output),
                file_name=f"EquusOS_Invoice_{chosen_barn_name.replace(' ', '_')}_{datetime.date.today()}.pdf",
                mime="application/pdf",
            )
        else:
            st.info(f"No treatment sessions on record for {chosen_barn_name}.")

# ----------------------------------------------------
# 20. CORRIDOR TRAVEL & FUEL EXPENSES
# ----------------------------------------------------
elif page == "Corridor Travel & Fuel Expenses":
    st.title("🚗 Corridor Travel & Operational Expense Tracker")
    st.markdown("Log travel expenses, track fuel and maintenance costs, and analyze net profitability across regional corridors.")

    try:
        exp_res = supabase.table("corridor_expenses").select("*").order("expense_date", desc=True).execute()
        all_expenses = exp_res.data if exp_res.data else []
    except Exception:
        all_expenses = []

    try:
        appts_res = supabase.table("appointments").select("travel_fee").execute()
        total_travel_collected = sum(float(a.get("travel_fee", 0)) for a in appts_res.data) if appts_res.data else 0.0
    except Exception:
        total_travel_collected = 0.0

    total_expenses_logged = sum(float(e.get("amount", 0)) for e in all_expenses)
    net_travel_margin = total_travel_collected - total_expenses_logged

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Expenses Logged", f"${total_expenses_logged:,.2f} CAD")
    m2.metric("Mileage Fees Collected", f"${total_travel_collected:,.2f} CAD")
    m3.metric("Net Travel Margin", f"${net_travel_margin:,.2f} CAD", delta=f"${net_travel_margin:,.2f}", delta_color="normal" if net_travel_margin >= 0 else "inverse")

    with st.expander("⛽ Log Travel Expense", expanded=True):
        with st.form("log_expense_form"):
            e_date = st.date_input("Expense Date", datetime.date.today())
            e_corridor = st.selectbox("Regional Corridor", [
                "Monday: Ottawa Metro & Russell",
                "Tuesday: Kingston Corridor (South)",
                "Wednesday: Pembroke / Valley (North)",
                "Thursday: Montreal Corridor (East)",
                "Friday: Flagship Dedicated",
                "General / Fleet Operations",
            ])
            e_category = st.selectbox("Expense Category", ["Fuel", "Vehicle Maintenance / Tires", "Parking & Tolls", "Equipment Consumables / Salt", "Other Operational"])
            e_amount = st.number_input("Amount (CAD)", min_value=0.0, step=5.0, value=75.0)
            e_receipt = st.text_input("Receipt Ref / Vendor Name (Optional)")
            e_notes = st.text_area("Expense Notes")

            if st.form_submit_button("Record Operational Expense"):
                if e_amount > 0:
                    supabase.table("corridor_expenses").insert({
                        "expense_date": str(e_date),
                        "corridor": e_corridor,
                        "category": e_category,
                        "amount": float(e_amount),
                        "receipt_ref": e_receipt,
                        "notes": e_notes,
                    }).execute()
                    st.success(f"Recorded ${e_amount:.2f} CAD for {e_category} on {e_corridor}!")
                    st.rerun()

# ----------------------------------------------------
# 21. EXECUTIVE P&L SNAPSHOT
# ----------------------------------------------------
elif page == "Executive P&L Snapshot":
    st.title("📊 Executive P&L Financial Performance")
    st.markdown("Comprehensive profit & loss income statement tracking gross session revenue, travel fees, operating overhead, and maintenance sinking reserves.")

    try:
        logs_res = supabase.table("treatment_logs").select("*").execute()
        all_logs = logs_res.data if logs_res.data else []
    except Exception:
        all_logs = []

    try:
        appts_res = supabase.table("appointments").select("*").execute()
        all_appts = appts_res.data if appts_res.data else []
    except Exception:
        all_appts = []

    try:
        exp_res = supabase.table("corridor_expenses").select("*").execute()
        all_expenses = exp_res.data if exp_res.data else []
    except Exception:
        all_expenses = []

    gross_session_rev = sum(float(l.get("calculated_fee", 0)) for l in all_logs)
    gross_travel_rev = sum(float(a.get("travel_fee", 0)) for a in all_appts)
    total_gross_rev = gross_session_rev + gross_travel_rev

    equitron_mins = sum(int(l.get("duration_minutes", 0)) for l in all_logs if l.get("modality") in ["Equitron-Pro (HECT)", "Peak Performance Combo"])
    maintenance_reserve_allocation = equitron_mins * 0.12

    total_operating_expenses = sum(float(e.get("amount", 0)) for e in all_expenses)
    total_deductions_reserves = total_operating_expenses + maintenance_reserve_allocation
    net_ebitda = total_gross_rev - total_deductions_reserves
    profit_margin = (net_ebitda / total_gross_rev * 100) if total_gross_rev > 0 else 0.0

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    col_f1.metric("Total Gross Revenue", f"${total_gross_rev:,.2f} CAD")
    col_f2.metric("Operating Overhead", f"${total_operating_expenses:,.2f} CAD")
    col_f3.metric("Maintenance Reserve Fund", f"${maintenance_reserve_allocation:,.2f} CAD")
    col_f4.metric("Net EBITDA Profit", f"${net_ebitda:,.2f} CAD", delta=f"{profit_margin:.1f}% Margin", delta_color="normal" if net_ebitda >= 0 else "inverse")

    st.divider()
    pnl_data = [
        {"Line Item": "🟢 Equitron & Halo Session Revenue", "Amount (CAD)": f"${gross_session_rev:,.2f}"},
        {"Line Item": "🟢 Regional Travel & Mileage Collected", "Amount (CAD)": f"${gross_travel_rev:,.2f}"},
        {"Line Item": "👉 Total Gross Operating Revenue", "Amount (CAD)": f"${total_gross_rev:,.2f}"},
        {"Line Item": "🔴 Vehicle Fuel & Mobile Travel Expenses", "Amount (CAD)": f"-${sum(float(e.get('amount', 0)) for e in all_expenses if e.get('category') == 'Fuel'):,.2f}"},
        {"Line Item": "🔴 Vehicle Upkeep & Tolls", "Amount (CAD)": f"-${sum(float(e.get('amount', 0)) for e in all_expenses if e.get('category') in ['Vehicle Maintenance / Tires', 'Parking & Tolls']):,.2f}"},
        {"Line Item": "🔴 Consumables & General Overhead", "Amount (CAD)": f"-${sum(float(e.get('amount', 0)) for e in all_expenses if e.get('category') in ['Equipment Consumables / Salt', 'Other Operational']):,.2f}"},
        {"Line Item": "🟡 Sinking Fund Reserve (22k-Min Recertification @ $0.12/min)", "Amount (CAD)": f"-${maintenance_reserve_allocation:,.2f}"},
        {"Line Item": "🏁 Net Operating Profit (Pre-Tax EBITDA)", "Amount (CAD)": f"${net_ebitda:,.2f}"},
    ]
    st.dataframe(pd.DataFrame(pnl_data), use_container_width=True)

    csv_pnl = pd.DataFrame(pnl_data).to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Export P&L Financial Statement (CSV)",
        data=csv_pnl,
        file_name=f"EquusOS_PNL_Statement_{datetime.date.today()}.csv",
        mime="text/csv",
    )
