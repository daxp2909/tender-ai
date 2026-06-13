"""
TenderAI — Database Operations (db.py)
========================================
Handles all Supabase database operations:
  - Company profiles (expanded fields)
  - Tenders (save/get uploaded/pasted tenders)
  - Analyses (save/get full reports with JSONB)
  - Leads (expert-review pipeline)
  - Feedback (post-report ratings)
  - Admin (leads dashboard, user management)

Uses SUPABASE_SERVICE_ROLE_KEY (bypasses RLS).
"""

import os
from supabase import create_client, Client


def get_admin_client() -> Client:
    """Get Supabase client with service role key (bypasses RLS)."""
    url = os.environ.get("SUPABASE_URL")
    # Support both env var names for compatibility
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")

    if not url:
        raise EnvironmentError("SUPABASE_URL is not set.")
    if not key:
        raise EnvironmentError("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SERVICE_KEY) is not set.")

    return create_client(url, key)


# ================================================================
# COMPANY PROFILES
# ================================================================

def get_company_profile(user_id: str) -> dict | None:
    """Get company profile for a user."""
    try:
        admin = get_admin_client()
        result = admin.table("company_profiles") \
                      .select("*") \
                      .eq("user_id", user_id) \
                      .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[db] Error fetching profile: {e}")
        return None


def save_company_profile(user_id: str, data: dict) -> dict:
    """Create or update company profile. Returns {success, error?}."""
    try:
        admin = get_admin_client()

        existing = admin.table("company_profiles") \
                        .select("id") \
                        .eq("user_id", user_id) \
                        .execute()

        # Normalize sub_domains: accept string or list
        sub_domains = data.get("sub_domains", [])
        if isinstance(sub_domains, str):
            sub_domains = [s.strip() for s in sub_domains.split(",") if s.strip()]

        profile_data = {
            "user_id": user_id,
            "company_name": data.get("company_name", ""),
            "registration_number": data.get("registration_number", ""),
            "pan_number": data.get("pan_number", ""),
            "gst_available": bool(data.get("gst_available", False)),
            "msme_available": bool(data.get("msme_available", False)),
            "iso_available": bool(data.get("iso_available", False)),
            "gem_seller": bool(data.get("gem_seller", False)),
            "annual_turnover_lakhs": float(data.get("annual_turnover_lakhs", data.get("turnover", 0)) or 0),
            "years_experience": float(data.get("years_experience", data.get("experience", 0)) or 0),
            "employee_count": int(data.get("employee_count", 0) or 0),
            "domain": data.get("domain", ""),
            "sub_domains": sub_domains,
            "certifications": data.get("certifications", ""),
            "locations_served": data.get("locations_served", ""),
            "past_government_tender_experience": bool(data.get("past_government_tender_experience", False)),
            "similar_work_certificates_available": bool(data.get("similar_work_certificates_available", False)),
            "audited_financials_available": bool(data.get("audited_financials_available", False)),
            "bank_solvency_available": bool(data.get("bank_solvency_available", False)),
            "preferred_tender_size": data.get("preferred_tender_size", ""),
            "major_past_projects": data.get("major_past_projects", ""),
            "oem_authorization": data.get("oem_authorization", ""),
            "company_email": data.get("company_email", ""),
            "phone": data.get("phone", ""),
            "address": data.get("address", ""),
        }

        if existing.data:
            admin.table("company_profiles") \
                 .update(profile_data) \
                 .eq("user_id", user_id) \
                 .execute()
        else:
            admin.table("company_profiles") \
                 .insert(profile_data) \
                 .execute()

        return {"success": True}
    except Exception as e:
        print(f"[db] Error saving profile: {e}")
        return {"success": False, "error": str(e)}


# ================================================================
# TENDERS
# ================================================================

def save_tender(user_id: str, data: dict) -> dict:
    """Save a tender (uploaded or pasted). Returns {success, tender_id?}."""
    try:
        admin = get_admin_client()
        record = {
            "user_id": user_id,
            "tender_title": data.get("tender_title", ""),
            "tender_category": data.get("tender_category", ""),
            "tender_value": data.get("tender_value", ""),
            "tender_deadline": data.get("tender_deadline", ""),
            "tender_source_url": data.get("tender_source_url", ""),
            "file_name": data.get("file_name", ""),
            "file_url": data.get("file_url", ""),
            "extracted_text": data.get("extracted_text", ""),
            "pasted_text": data.get("pasted_text", ""),
            "final_tender_text": data.get("final_tender_text", ""),
            "text_extraction_status": data.get("text_extraction_status", "pending"),
            "page_count": data.get("page_count", 0),
        }
        result = admin.table("tenders").insert(record).execute()
        tender_id = result.data[0]["id"] if result.data else None
        return {"success": True, "tender_id": tender_id}
    except Exception as e:
        print(f"[db] Error saving tender: {e}")
        return {"success": False, "error": str(e)}


def get_tender(tender_id: str) -> dict | None:
    """Get a single tender by ID."""
    try:
        admin = get_admin_client()
        result = admin.table("tenders") \
                      .select("*") \
                      .eq("id", tender_id) \
                      .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[db] Error fetching tender: {e}")
        return None


# ================================================================
# ANALYSES
# ================================================================

def save_analysis(user_id: str, data: dict, tender_id: str = None, company_profile_id: str = None) -> dict:
    """
    Save a full analysis report.
    data = the full report JSON from analyzer.py
    Returns {success, analysis_id?}
    """
    try:
        admin = get_admin_client()

        exec_sum = data.get("executive_summary", {})
        scores = data.get("scores", {})
        bid = data.get("bid_decision", {})

        record = {
            "user_id": user_id,
            "tender_id": tender_id,
            "company_profile_id": company_profile_id,
            "status": "completed",
            "fit_score": scores.get("company_fit_score", 0),
            "eligibility_score": scores.get("eligibility_score", 0),
            "risk_score": scores.get("risk_score", 0),
            "document_readiness_score": scores.get("document_readiness_score", 0),
            "recommendation": bid.get("decision", ""),
            "confidence_level": scores.get("confidence_level", exec_sum.get("confidence_level", "")),
            "tender_title": exec_sum.get("tender_title", ""),
            "tender_value": exec_sum.get("tender_value", ""),
            "tender_deadline": exec_sum.get("deadline", ""),
            "location": exec_sum.get("location", ""),
            "full_report_json": data,
            "summary": data.get("summary", ""),
        }

        result = admin.table("analyses").insert(record).execute()
        analysis_id = result.data[0]["id"] if result.data else None
        return {"success": True, "analysis_id": analysis_id}
    except Exception as e:
        print(f"[db] Error saving analysis: {e}")
        return {"success": False, "error": str(e)}


def get_analysis(analysis_id: str, user_id: str = None) -> dict | None:
    """
    Get a single analysis by ID.
    If user_id is provided, only return if it belongs to that user.
    """
    try:
        admin = get_admin_client()
        query = admin.table("analyses").select("*").eq("id", analysis_id)
        if user_id:
            query = query.eq("user_id", user_id)
        result = query.execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[db] Error fetching analysis: {e}")
        return None


def get_analysis_history(user_id: str) -> list:
    """Get all analyses for a user, newest first."""
    try:
        admin = get_admin_client()
        result = admin.table("analyses") \
                      .select("id, tender_title, tender_value, tender_deadline, location, "
                              "fit_score, eligibility_score, risk_score, document_readiness_score, "
                              "recommendation, confidence_level, status, summary, created_at") \
                      .eq("user_id", user_id) \
                      .order("created_at", desc=True) \
                      .execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[db] Error fetching history: {e}")
        return []


def get_dashboard_stats(user_id: str) -> dict:
    """Get dashboard stats for a user."""
    try:
        history = get_analysis_history(user_id)
        total_analyzed = len(history)
        avg_elig = 0
        avg_fit = 0
        last_analysis = None

        if history:
            elig_scores = [h["eligibility_score"] for h in history if h.get("eligibility_score")]
            fit_scores = [h["fit_score"] for h in history if h.get("fit_score")]
            avg_elig = round(sum(elig_scores) / len(elig_scores)) if elig_scores else 0
            avg_fit = round(sum(fit_scores) / len(fit_scores)) if fit_scores else 0
            last_analysis = history[0]

        return {
            "total_analyzed": total_analyzed,
            "avg_eligibility_score": avg_elig,
            "avg_fit_score": avg_fit,
            "last_analysis": last_analysis,
            "recent_history": history[:5]
        }
    except Exception as e:
        print(f"[db] Error getting stats: {e}")
        return {
            "total_analyzed": 0,
            "avg_eligibility_score": 0,
            "avg_fit_score": 0,
            "last_analysis": None,
            "recent_history": []
        }


# ================================================================
# LEADS
# ================================================================

def create_lead(user_id: str, analysis_id: str, data: dict) -> dict:
    """Create a lead when user clicks Expert Review."""
    try:
        admin = get_admin_client()

        # Get analysis details
        analysis = get_analysis(analysis_id, user_id)
        profile = get_company_profile(user_id)

        record = {
            "user_id": user_id,
            "analysis_id": analysis_id,
            "company_name": data.get("company_name", profile.get("company_name", "") if profile else ""),
            "contact_name": data.get("contact_name", ""),
            "email": data.get("email", profile.get("company_email", "") if profile else ""),
            "phone": data.get("phone", profile.get("phone", "") if profile else ""),
            "tender_title": data.get("tender_title", analysis.get("tender_title", "") if analysis else ""),
            "tender_deadline": data.get("tender_deadline", analysis.get("tender_deadline", "") if analysis else ""),
            "fit_score": data.get("fit_score", analysis.get("fit_score") if analysis else None),
            "recommendation": data.get("recommendation", analysis.get("recommendation", "") if analysis else ""),
            "status": "expert_review_requested",
            "notes": data.get("notes", ""),
        }

        result = admin.table("leads").insert(record).execute()
        lead_id = result.data[0]["id"] if result.data else None
        return {"success": True, "lead_id": lead_id}
    except Exception as e:
        print(f"[db] Error creating lead: {e}")
        return {"success": False, "error": str(e)}


def get_leads(status: str = None) -> list:
    """Get all leads (for admin). Optionally filter by status."""
    try:
        admin = get_admin_client()
        query = admin.table("leads").select("*")
        if status:
            query = query.eq("status", status)
        result = query.order("created_at", desc=True).execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[db] Error fetching leads: {e}")
        return []


def update_lead(lead_id: str, data: dict) -> dict:
    """Update a lead (status, notes). Admin only."""
    try:
        admin = get_admin_client()
        update_data = {}
        if "status" in data:
            update_data["status"] = data["status"]
        if "notes" in data:
            update_data["notes"] = data["notes"]

        admin.table("leads") \
             .update(update_data) \
             .eq("id", lead_id) \
             .execute()
        return {"success": True}
    except Exception as e:
        print(f"[db] Error updating lead: {e}")
        return {"success": False, "error": str(e)}


# ================================================================
# FEEDBACK
# ================================================================

def save_feedback(user_id: str, analysis_id: str, data: dict) -> dict:
    """Save user feedback on a report."""
    try:
        admin = get_admin_client()
        record = {
            "user_id": user_id,
            "analysis_id": analysis_id,
            "rating": data.get("rating"),
            "useful": data.get("useful", ""),
            "would_pay": data.get("would_pay", ""),
            "comments": data.get("comments", ""),
        }
        admin.table("feedback").insert(record).execute()
        return {"success": True}
    except Exception as e:
        print(f"[db] Error saving feedback: {e}")
        return {"success": False, "error": str(e)}


def get_feedback_for_analysis(analysis_id: str) -> list:
    """Get all feedback for an analysis."""
    try:
        admin = get_admin_client()
        result = admin.table("feedback") \
                      .select("*") \
                      .eq("analysis_id", analysis_id) \
                      .execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"[db] Error fetching feedback: {e}")
        return []


# ================================================================
# ADMIN
# ================================================================

def is_admin(user_id: str) -> bool:
    """Check if a user has admin role."""
    try:
        admin = get_admin_client()
        result = admin.table("users") \
                      .select("role") \
                      .eq("id", user_id) \
                      .execute()
        if result.data:
            return result.data[0].get("role") == "admin"
        return False
    except Exception as e:
        print(f"[db] Error checking admin: {e}")
        return False


def get_admin_stats() -> dict:
    """Get overall stats for admin dashboard."""
    try:
        admin = get_admin_client()

        total_users = len(admin.table("users").select("id").execute().data)
        total_analyses = len(admin.table("analyses").select("id").execute().data)
        total_leads = len(admin.table("leads").select("id").execute().data)
        new_leads = len(admin.table("leads").select("id").eq("status", "new").execute().data)

        return {
            "total_users": total_users,
            "total_analyses": total_analyses,
            "total_leads": total_leads,
            "new_leads": new_leads,
        }
    except Exception as e:
        print(f"[db] Error getting admin stats: {e}")
        return {
            "total_users": 0,
            "total_analyses": 0,
            "total_leads": 0,
            "new_leads": 0,
        }


# ================================================================
# LEGACY COMPATIBILITY
# ================================================================

def save_tender_analysis_legacy(user_id: str, data: dict) -> dict:
    """
    Save to old tender_history table for backward compatibility.
    Also saves to new analyses table.
    """
    # Save to new analyses table
    new_result = save_analysis(user_id, data)

    # Also save to legacy tender_history (if table exists)
    try:
        admin = get_admin_client()
        exec_sum = data.get("executive_summary", {})
        scores = data.get("scores", {})

        legacy_record = {
            "user_id": user_id,
            "project_name": exec_sum.get("tender_title", data.get("project_name", "Unknown")),
            "project_value": float(exec_sum.get("tender_value", "0").replace(",", "").replace("Rs", "").strip() or 0),
            "location": exec_sum.get("location", data.get("location", "")),
            "deadline": exec_sum.get("deadline", data.get("deadline", "")),
            "eligibility_score": scores.get("eligibility_score", data.get("eligibility_score", 0)),
            "summary": data.get("summary", ""),
            "recommendations": data.get("recommendations", []),
            "overall_eligibility": data.get("mandatory_eligibility", {}).get("status", ""),
            "bid_recommendation": data.get("bid_decision", {}).get("decision", ""),
            "eligibility_criteria": data.get("eligibility_match", []),
            "documents_required": data.get("document_readiness", []),
            "red_flags": data.get("risk_analysis", []),
        }
        admin.table("tender_history").insert(legacy_record).execute()
    except Exception as e:
        print(f"[db] Legacy save skipped (table may not exist): {e}")

    return new_result
