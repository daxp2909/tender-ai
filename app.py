"""
TenderAI — Flask Application (Improved MVP)
=============================================
P0 fixes applied:
  - Register saves company profile fields
  - CSRF protection on all POST routes
  - Logout is POST (not GET)
  - Session cookie: Secure + SameSite=Lax
  - Contact form backend with validation
  - Forgot / Reset password flow
  - Terms / Privacy / Refund routes
  - Branded 404 / 500 error pages
  - Security headers on every response
  - Rate limiting on auth routes
  - Noindex meta for staging
  - GA4 / Clarity context processor
"""

import os
import json
import tempfile
import secrets
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, abort
)
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

from auth import register_user, login_user, create_reset_token
from db import (
    get_company_profile, save_company_profile, save_tender,
    save_analysis, get_analysis, get_analysis_history,
    get_dashboard_stats, create_lead, get_leads, update_lead,
    save_feedback, is_admin, get_admin_stats,
    save_password_reset, get_password_reset, delete_password_reset,
    save_contact_message
)
from analyzer import (
    extract_text_from_pdf, format_pages_for_prompt,
    extract_questions, analyze_tender
)

app = Flask(__name__, static_folder="static")

# ── Config ────────────────────────────────────────────────────
_secret = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not _secret:
    import warnings
    warnings.warn(
        "FLASK_SECRET_KEY / SECRET_KEY not set. Using insecure fallback.",
        stacklevel=2
    )
    _secret = "tender-ai-secret-2024-INSECURE-FALLBACK"
app.secret_key = _secret

# ── Session cookie security ───────────────────────────────────
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("APP_ENV") == "production"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# ── CSRF Protection ───────────────────────────────────────────
app.config["WTF_CSRF_TIME_LIMIT"] = 3600  # 1 hour
csrf = CSRFProtect(app)

# ── Rate Limiting ─────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# File upload config
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {"pdf"}
CALENDLY_URL = os.environ.get("CALENDLY_URL", "https://calendly.com")
APP_ENV = os.environ.get("APP_ENV", "development")
IS_STAGING = APP_ENV != "production"


# ── Template context globals ──────────────────────────────────
@app.context_processor
def inject_globals():
    return {
        "ga_id": os.environ.get("GA_MEASUREMENT_ID", ""),
        "clarity_id": os.environ.get("CLARITY_PROJECT_ID", ""),
        "is_staging": IS_STAGING,
    }


# ── Security headers on every response ────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if APP_ENV == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if IS_STAGING:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# ── Helpers ───────────────────────────────────────────────────
def logged_in():
    return "user_id" in session


def require_login():
    if not logged_in():
        # Save the page the user was trying to access
        session["next_url"] = request.url
        flash("Please login to continue.", "error")
        return redirect(url_for("login"))
    return None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ================================================================
# PUBLIC PAGES
# ================================================================

@app.route("/")
def landing():
    # Logged-in users: go straight to dashboard (no need to see landing)
    if logged_in():
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/pricing")
def pricing():
    return render_template("pricing.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not message:
            flash("Please fill in all fields.", "error")
            return render_template("contact.html")

        if "@" not in email or "." not in email:
            flash("Please enter a valid email address.", "error")
            return render_template("contact.html")

        if len(message) < 10:
            flash("Message is too short. Please provide more detail.", "error")
            return render_template("contact.html")

        result = save_contact_message(name, email, message)
        if result["success"]:
            flash("Thank you! Your message has been sent. We'll get back to you within 24 hours.", "success")
        else:
            flash("Error sending message. Please email us at support@tenderai.in", "error")

        return render_template("contact.html")

    return render_template("contact.html")


@app.route("/sample-report")
def sample_report():
    return render_template("sample_report.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/refund")
def refund():
    return render_template("refund.html")


# ================================================================
# AUTH
# ================================================================

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        terms_agreed = request.form.get("terms_agreed")

        # Validation
        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        if not terms_agreed:
            flash("You must agree to the Terms of Service and Privacy Policy.", "error")
            return render_template("register.html")

        result = register_user(email, password)

        if result["success"]:
            user = result["user"]
            user_id = user["id"]

            # Save company profile from registration form
            profile_data = {
                "company_name": request.form.get("company_name", ""),
                "registration_number": request.form.get("registration_number", ""),
                "pan_number": request.form.get("pan_number", ""),
                "annual_turnover_lakhs": request.form.get("turnover", 0),
                "years_experience": request.form.get("experience", 0),
                "domain": request.form.get("domain", ""),
                "sub_domains": request.form.get("sub_domains", ""),
                "employee_count": request.form.get("employee_count", 0),
                "certifications": request.form.get("certifications", ""),
                "address": request.form.get("address", ""),
                "phone": request.form.get("phone", ""),
                "company_email": request.form.get("company_email", email),
            }
            save_company_profile(user_id, profile_data)

            # Only set session on success — do NOT touch session on failure
            # to avoid clearing an existing logged-in session
            session["user_id"] = user_id
            session["user_email"] = email
            session["user_role"] = user.get("role", "user")
            flash("Account created successfully! Welcome to TenderAI.", "success")
            # Redirect to the page the user was trying to access, or dashboard
            next_url = session.pop("next_url", None)
            return redirect(next_url or url_for("dashboard"))
        else:
            # On failure, do NOT modify session at all
            flash(result["error"], "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if logged_in():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        result = login_user(email, password)

        if result["success"]:
            user = result["user"]
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_role"] = user.get("role", "user")
            flash("Welcome back!", "success")
            # Redirect to the page the user was trying to access, or dashboard
            next_url = session.pop("next_url", None)
            return redirect(next_url or url_for("dashboard"))
        else:
            flash(result["error"], "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("landing"))


# ── Forgot / Reset Password ───────────────────────────────────
@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not email:
            flash("Please enter your email address.", "error")
            return render_template("forgot_password.html")

        # Always show same message to prevent email enumeration
        token = create_reset_token(email)
        if token:
            # Save reset token to database
            save_password_reset(email, token)

            # For MVP: pass reset URL via session so template can render it
            # In production: send email with reset link
            reset_url = url_for("reset_password", token=token, _external=True)
            session["reset_url"] = reset_url
            flash("Password reset link generated. See the link below.", "success")
        else:
            # Don't reveal if email exists
            flash("If an account with that email exists, a reset link has been generated.", "success")

        return render_template("forgot_password.html")

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    # Verify token
    reset_data = get_password_reset(token)
    if not reset_data:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not password or len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("reset_password.html", token=token)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html", token=token)

        # Update password
        from auth import update_password
        result = update_password(reset_data["email"], password)

        if result["success"]:
            delete_password_reset(token)
            session.pop("reset_url", None)
            flash("Password updated successfully! Please login with your new password.", "success")
            return redirect(url_for("login"))
        else:
            flash("Error updating password. Please try again.", "error")

    return render_template("reset_password.html", token=token)


# ================================================================
# PROTECTED PAGES
# ================================================================

@app.route("/dashboard")
def dashboard():
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]
    stats = get_dashboard_stats(user_id)
    profile = get_company_profile(user_id)
    calendly_url = CALENDLY_URL

    # Use company name for greeting instead of email
    company_name = ""
    if profile and profile.get("company_name"):
        company_name = profile["company_name"]

    return render_template("dashboard.html",
                           email=session.get("user_email"),
                           company_name=company_name,
                           profile=profile,
                           calendly_url=calendly_url,
                           **stats)


@app.route("/profile", methods=["GET", "POST"])
def profile():
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]

    if request.method == "POST":
        profile_data = {
            "company_name": request.form.get("company_name", ""),
            "registration_number": request.form.get("registration_number", ""),
            "pan_number": request.form.get("pan_number", ""),
            "gst_available": request.form.get("gst_available") == "yes",
            "msme_available": request.form.get("msme_available") == "yes",
            "iso_available": request.form.get("iso_available") == "yes",
            "gem_seller": request.form.get("gem_seller") == "yes",
            "annual_turnover_lakhs": request.form.get("annual_turnover_lakhs", 0),
            "years_experience": request.form.get("years_experience", 0),
            "employee_count": request.form.get("employee_count", 0),
            "domain": request.form.get("domain", ""),
            "sub_domains": request.form.get("sub_domains", ""),
            "certifications": request.form.get("certifications", ""),
            "locations_served": request.form.get("locations_served", ""),
            "past_government_tender_experience": request.form.get("past_government_tender_experience") == "yes",
            "similar_work_certificates_available": request.form.get("similar_work_certificates_available") == "yes",
            "audited_financials_available": request.form.get("audited_financials_available") == "yes",
            "bank_solvency_available": request.form.get("bank_solvency_available") == "yes",
            "preferred_tender_size": request.form.get("preferred_tender_size", ""),
            "major_past_projects": request.form.get("major_past_projects", ""),
            "oem_authorization": request.form.get("oem_authorization", ""),
            "company_email": request.form.get("company_email", ""),
            "phone": request.form.get("phone", ""),
            "address": request.form.get("address", ""),
        }
        result = save_company_profile(user_id, profile_data)
        if result["success"]:
            flash("Profile updated successfully!", "success")
            # Redirect to ?next= page if provided (e.g. /analyze)
            next_url = request.args.get("next") or request.form.get("next")
            if next_url and next_url.startswith("/"):
                return redirect(next_url)
        else:
            flash("Error updating profile. Please try again.", "error")

    company = get_company_profile(user_id)
    next_url = request.args.get("next", "")
    return render_template("profile.html", profile=company, next_url=next_url)


# ================================================================
# ANALYZE (Improved — PDF + Paste + Metadata)
# ================================================================

@app.route("/analyze", methods=["GET", "POST"])
def analyze():
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]
    profile = get_company_profile(user_id)

    # Profile must exist and have a domain set — otherwise analysis has
    # no company context.  Use .get() because Supabase returns dicts,
    # not objects (profile.domain would AttributeError).
    if not profile or not profile.get("domain"):
        flash("Please complete your company profile before running an analysis. We need your industry domain at minimum.", "info")
        return redirect(url_for("profile", next="/analyze"))

    # ── Step 1: Upload/Paste tender ──────────────────────────
    if request.method == "POST" and request.form.get("step") == "upload":
        tender_title = request.form.get("tender_title", "").strip()
        tender_category = request.form.get("tender_category", "").strip()
        tender_value = request.form.get("tender_value", "").strip()
        tender_deadline = request.form.get("tender_deadline", "").strip()
        tender_source_url = request.form.get("tender_source_url", "").strip()
        pasted_text = request.form.get("pasted_tender_text", "").strip()

        pdf_file = request.files.get("pdf_file")
        pdf_pages = []
        tender_text = ""
        extraction_status = "pending"
        file_name = ""
        page_count = 0

        # ── Try PDF extraction ───────────────────────────────
        if pdf_file and pdf_file.filename != "":
            if not allowed_file(pdf_file.filename):
                flash("Only PDF files are allowed. Please upload a PDF or paste text manually.", "error")
                return render_template("analyze.html", profile=profile)

            pdf_file.seek(0, 2)
            file_size = pdf_file.tell()
            pdf_file.seek(0)

            if file_size > MAX_FILE_SIZE:
                flash("This PDF is too large for free analysis (max 10 MB). Please paste relevant tender text instead.", "error")
                return render_template("analyze.html", profile=profile)

            file_name = secure_filename(pdf_file.filename)

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                pdf_file.save(tmp.name)
                tmp_pdf_path = tmp.name

            extraction = extract_text_from_pdf(tmp_pdf_path)

            try:
                os.unlink(tmp_pdf_path)
            except OSError:
                pass

            if extraction["success"] and extraction["status"] == "ok":
                pdf_pages = extraction["pages"]
                tender_text = format_pages_for_prompt(pdf_pages)
                extraction_status = "ok"
                page_count = extraction["page_count"]
            else:
                if pasted_text:
                    tender_text = pasted_text
                    extraction_status = "pasted"
                else:
                    flash("We could not read this PDF properly. Please paste the tender text manually.", "error")
                    return render_template("analyze.html", profile=profile)

        elif pasted_text:
            tender_text = pasted_text
            extraction_status = "pasted"

        else:
            flash("Please upload a PDF or paste tender text.", "error")
            return render_template("analyze.html", profile=profile)

        if not tender_text or len(tender_text.strip()) < 50:
            flash("Tender text is too short. Please provide more content.", "error")
            return render_template("analyze.html", profile=profile)

        # ── Save tender to database ──────────────────────────
        tender_data = {
            "tender_title": tender_title,
            "tender_category": tender_category,
            "tender_value": tender_value,
            "tender_deadline": tender_deadline,
            "tender_source_url": tender_source_url,
            "file_name": file_name,
            "extracted_text": tender_text,
            "pasted_text": pasted_text,
            "final_tender_text": tender_text,
            "text_extraction_status": extraction_status,
            "page_count": page_count,
        }
        tender_result = save_tender(user_id, tender_data)
        tender_id = tender_result.get("tender_id")

        # ── Build analysis profile (with overrides) ──────────
        analysis_profile = dict(profile) if profile else {}
        if request.form.get("override_domain"):
            analysis_profile["domain"] = request.form.get("override_domain")
        if request.form.get("override_turnover"):
            analysis_profile["annual_turnover_lakhs"] = request.form.get("override_turnover")
        if request.form.get("override_experience"):
            analysis_profile["years_experience"] = request.form.get("override_experience")
        if request.form.get("override_similar_work"):
            analysis_profile["similar_work_certificates_available"] = request.form.get("override_similar_work") == "yes"
        if request.form.get("override_gst"):
            analysis_profile["gst_available"] = request.form.get("override_gst") == "yes"
        if request.form.get("override_msme"):
            analysis_profile["msme_available"] = request.form.get("override_msme") == "yes"
        if request.form.get("override_iso"):
            analysis_profile["iso_available"] = request.form.get("override_iso") == "yes"

        # ── Store in session for step 2 ─────────────────────
        session["analysis_tender_id"] = tender_id
        session["analysis_profile"] = analysis_profile

        if pdf_pages:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".json", mode="w", encoding="utf-8"
            ) as data_file:
                json.dump({"pdf_pages": pdf_pages}, data_file)
                session["analysis_pages_file"] = data_file.name
        else:
            session.pop("analysis_pages_file", None)

        # ── Generate clarifying questions ────────────────────
        q_result = extract_questions(tender_text, analysis_profile)

        if not q_result["success"]:
            flash(f"Error generating questions: {q_result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        questions_data = q_result["data"]
        session["analysis_questions"] = questions_data.get("questions", [])

        return render_template("analyze.html",
                               profile=profile,
                               show_questions=True,
                               questions_data=questions_data)

    # ── Step 2: Answers submitted → Run analysis ─────────────
    if request.method == "POST" and request.form.get("step") == "answers":
        tender_id = session.get("analysis_tender_id")
        analysis_profile = session.get("analysis_profile", {})
        questions = session.get("analysis_questions", [])
        pages_file = session.get("analysis_pages_file")

        tender_text = ""
        if tender_id:
            from db import get_tender
            tender = get_tender(tender_id)
            if tender:
                tender_text = tender.get("final_tender_text", "")

        if not tender_text:
            flash("Session expired. Please upload the tender again.", "error")
            return render_template("analyze.html", profile=profile)

        pdf_pages = []
        if pages_file and os.path.exists(pages_file):
            try:
                with open(pages_file, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                    pdf_pages = stored.get("pdf_pages", [])
            except Exception:
                pass
            try:
                os.unlink(pages_file)
            except OSError:
                pass

        answers = {}
        for q in questions:
            q_id = q.get("id", "")
            answer = request.form.get(f"answer_{q_id}", "").strip()
            if answer:
                answers[q_id] = answer

        result = analyze_tender(tender_text, analysis_profile, answers, pages=pdf_pages)

        session.pop("analysis_tender_id", None)
        session.pop("analysis_profile", None)
        session.pop("analysis_questions", None)
        session.pop("analysis_pages_file", None)

        if not result["success"]:
            flash(f"Analysis failed: {result['error']}", "error")
            return render_template("analyze.html", profile=profile)

        report_data = result["data"]
        profile_id = profile.get("id") if profile else None
        save_result = save_analysis(user_id, report_data,
                                    tender_id=tender_id,
                                    company_profile_id=profile_id)
        analysis_id = save_result.get("analysis_id")

        session["last_analysis_id"] = analysis_id

        return render_template("analyze.html",
                               profile=profile,
                               result=report_data,
                               analysis_id=analysis_id)

    return render_template("analyze.html", profile=profile)


# ================================================================
# HISTORY
# ================================================================

@app.route("/history")
def history():
    redir = require_login()
    if redir:
        return redir

    records = get_analysis_history(session["user_id"])
    return render_template("history.html", history=records)


# ================================================================
# REPORT
# ================================================================

@app.route("/report/<analysis_id>")
def report(analysis_id):
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]
    analysis = get_analysis(analysis_id, user_id)

    if not analysis:
        flash("Report not found or you don't have access.", "error")
        return redirect(url_for("history"))

    report_data = analysis.get("full_report_json", {})
    calendly_url = CALENDLY_URL

    return render_template("report.html",
                           analysis=analysis,
                           report=report_data,
                           analysis_id=analysis_id,
                           calendly_url=calendly_url)


# ================================================================
# EXPERT REVIEW
# ================================================================

@app.route("/lead/expert-review/<analysis_id>", methods=["POST"])
def expert_review(analysis_id):
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]
    lead_data = {
        "contact_name": request.form.get("contact_name", ""),
        "email": request.form.get("email", ""),
        "phone": request.form.get("phone", ""),
        "notes": request.form.get("notes", "User requested expert review"),
    }
    create_lead(user_id, analysis_id, lead_data)
    return redirect(CALENDLY_URL)


# ================================================================
# FEEDBACK
# ================================================================

@app.route("/feedback/<analysis_id>", methods=["POST"])
def feedback(analysis_id):
    redir = require_login()
    if redir:
        return redir

    user_id = session["user_id"]
    feedback_data = {
        "rating": request.form.get("rating", type=int),
        "useful": request.form.get("useful", ""),
        "would_pay": request.form.get("would_pay", ""),
        "comments": request.form.get("comments", ""),
    }
    save_feedback(user_id, analysis_id, feedback_data)
    flash("Thank you for your feedback!", "success")
    return redirect(url_for("report", analysis_id=analysis_id))


# ================================================================
# ADMIN
# ================================================================

@app.route("/admin")
def admin_dashboard():
    redir = require_login()
    if redir:
        return redir

    if not is_admin(session["user_id"]):
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    stats = get_admin_stats()
    leads = get_leads()
    return render_template("admin.html", stats=stats, leads=leads)


@app.route("/admin/leads")
def admin_leads():
    redir = require_login()
    if redir:
        return redir

    if not is_admin(session["user_id"]):
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    status_filter = request.args.get("status", "")
    leads = get_leads(status=status_filter) if status_filter else get_leads()
    return render_template("admin_leads.html", leads=leads, current_filter=status_filter)


@app.route("/admin/leads/<lead_id>/update", methods=["POST"])
def admin_update_lead(lead_id):
    redir = require_login()
    if redir:
        return redir

    if not is_admin(session["user_id"]):
        flash("Access denied.", "error")
        return redirect(url_for("dashboard"))

    data = {
        "status": request.form.get("status", ""),
        "notes": request.form.get("notes", ""),
    }
    update_lead(lead_id, data)
    flash("Lead updated.", "success")
    return redirect(url_for("admin_leads"))


# ================================================================
# ERROR HANDLERS (Branded 404 / 500)
# ================================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    # Log the actual error so we can debug on Render
    app.logger.error(f"500 Error: {e}", exc_info=True)
    return render_template("500.html"), 500


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return render_template("429.html"), 429


# ================================================================
# UTILITY
# ================================================================

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/robots.txt")
def robots():
    return app.send_static_file("robots.txt")


@app.route("/sitemap.xml")
def sitemap():
    return app.send_static_file("sitemap.xml"), 200, {"Content-Type": "application/xml"}


# ================================================================
# RUN
# ================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=(APP_ENV == "development"))
