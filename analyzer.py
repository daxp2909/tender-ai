"""
TenderAI — Analyzer Module (Improved MVP)
==========================================
Handles:
  - PDF text extraction with status reporting
  - AI clarifying question generation
  - Full tender analysis with structured JSON report
  - Citation verification (Python-verified, not AI-guessed)
  - 4-score system (Eligibility, Fit, Risk, Document Readiness)
  - Text chunking for long tenders

Uses ai_providers.py for AI calls (Groq primary, Gemini fallback).
"""

import os
import re
import pdfplumber
from ai_providers import call_ai, call_ai_json, LAST_ERROR


# ── Constants ─────────────────────────────────────────────────
PROMPT_TEXT_LIMIT      = 12000   # chars sent to full analysis prompt
QUESTION_TEXT_LIMIT    = 6000    # chars sent to question-generation prompt
MAX_QUESTIONS          = 6       # max clarifying questions
MIN_TEXT_LENGTH        = 100     # minimum chars to consider extraction OK


# ================================================================
# PDF EXTRACTION
# ================================================================

def extract_text_from_pdf(pdf_file):
    """
    Extract text page by page with line numbers.
    Returns dict with success status, text, page count, and structured pages.
    """
    try:
        pages = []
        with pdfplumber.open(pdf_file) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()

                if not page_text or not page_text.strip():
                    continue

                lines = []
                for line_num, line in enumerate(page_text.split("\n"), start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    lines.append({
                        "line_num": line_num,
                        "text": stripped,
                        "is_heading": _is_section_heading(stripped)
                    })

                pages.append({
                    "page": i + 1,
                    "lines": lines,
                    "full_text": page_text.strip()
                })

        # Determine extraction status
        total_text = "".join(p["full_text"] for p in pages)
        if len(total_text.strip()) < MIN_TEXT_LENGTH:
            return {
                "success": False,
                "text": "",
                "page_count": len(pages),
                "pages": pages,
                "status": "low_text",
                "error": "Extracted text is too short. PDF may be scanned/image-based. Please paste tender text manually."
            }

        return {
            "success": True,
            "text": total_text,
            "page_count": len(pages),
            "pages": pages,
            "status": "ok",
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "text": "",
            "page_count": 0,
            "pages": [],
            "status": "failed",
            "error": f"Could not read PDF: {str(e)}"
        }


def _is_section_heading(line):
    """Detect if a line is likely a section heading."""
    line = line.strip()
    if not line:
        return False
    if re.match(r'^(\d+\.)+\s+\w+', line):
        return True
    if re.match(r'^(Section|SECTION|Clause|CLAUSE|Part|PART)\s+[\d\w]', line):
        return True
    if line.isupper() and 3 < len(line) < 80:
        return True
    if len(line) < 60 and not line.endswith(('.', ',', ';', ':')):
        if line[0].isupper():
            return True
    return False


def format_pages_for_prompt(pages, limit=PROMPT_TEXT_LIMIT):
    """
    Format pages into clearly marked text for AI prompt.
    Every line is numbered so AI can quote precisely.
    """
    output = ""
    for page in pages:
        output += f"\n\n{'='*50}\n"
        output += f"PAGE {page['page']}\n"
        output += f"{'='*50}\n"
        for line in page["lines"]:
            prefix = "[HEADING] " if line["is_heading"] else ""
            output += f"L{line['line_num']:03d}: {prefix}{line['text']}\n"

    if len(output) > limit:
        print(f"[analyzer] Prompt text truncated: {len(output)} → {limit} chars")
        return output[:limit]

    return output


# ================================================================
# CITATION VERIFICATION
# ================================================================

def find_citation(quote, pages):
    """
    Given an exact quote from AI, search all pages
    and find the real page number, line number, and
    nearest section heading. 100% Python verified.
    """
    if not quote or not pages:
        return {"found": False, "page": None, "line": None, "section": "", "quote": ""}

    quote_clean = quote.strip().lower()
    search_variants = [
        quote_clean,
        quote_clean[:60],
        quote_clean[:40],
        quote_clean[:25],
    ]

    for variant in search_variants:
        if len(variant) < 10:
            continue
        for page in pages:
            nearest_heading = None
            for line in page["lines"]:
                if line["is_heading"]:
                    nearest_heading = line["text"]
                if variant in line["text"].lower():
                    return {
                        "found": True,
                        "page": page["page"],
                        "line": line["line_num"],
                        "section": nearest_heading or "",
                        "quote": line["text"]
                    }

    return {"found": False, "page": None, "line": None, "section": "", "quote": quote}


def verify_citations(result, pages):
    """
    Go through every quote in the result,
    find its real location using Python text search,
    and attach verified citation objects.
    """

    def resolve(quote):
        if not quote:
            return {"found": False, "page": None, "line": None, "section": "", "quote": ""}
        citation = find_citation(quote, pages)
        return citation

    # Executive summary citations (if any)
    exec_sum = result.get("executive_summary", {})

    # Eligibility match
    for item in result.get("eligibility_match", []):
        raw_quote = item.get("citation", {})
        if isinstance(raw_quote, dict) and raw_quote.get("quote"):
            item["citation"] = resolve(raw_quote["quote"])
        elif isinstance(raw_quote, str) and raw_quote:
            item["citation"] = resolve(raw_quote)
        else:
            item["citation"] = {"found": False, "page": None, "line": None, "section": "", "quote": ""}

    # Financial requirements citations
    fin = result.get("financial_requirements", {})
    fin_citations = []
    for field in ["turnover_quote", "emd_quote", "pg_quote", "payment_quote"]:
        q = fin.get(field)
        if q:
            fin_citations.append(resolve(q))
    fin["citations"] = fin_citations if fin_citations else []

    # Document readiness citations
    for doc in result.get("document_readiness", []):
        raw_quote = doc.get("citation", {})
        if isinstance(raw_quote, dict) and raw_quote.get("quote"):
            doc["citation"] = resolve(raw_quote["quote"])
        elif isinstance(raw_quote, str) and raw_quote:
            doc["citation"] = resolve(raw_quote)
        else:
            doc["citation"] = {"found": False, "page": None, "line": None, "section": "", "quote": ""}

    # Risk analysis citations
    for risk in result.get("risk_analysis", []):
        raw_quote = risk.get("citation", {})
        if isinstance(raw_quote, dict) and raw_quote.get("quote"):
            risk["citation"] = resolve(raw_quote["quote"])
        elif isinstance(raw_quote, str) and raw_quote:
            risk["citation"] = resolve(raw_quote)
        else:
            risk["citation"] = {"found": False, "page": None, "line": None, "section": "", "quote": ""}

    return result


# ================================================================
# CLARIFYING QUESTIONS (CALL 1)
# ================================================================

def extract_questions(tender_text, company_profile):
    """
    First AI call — find what critical info is missing
    and generate smart specific questions.
    """
    if len(tender_text) > QUESTION_TEXT_LIMIT:
        print(f"[analyzer] Question prompt truncated: {len(tender_text)} → {QUESTION_TEXT_LIMIT} chars")

    prompt = f"""You are an expert Indian government tender analyst.

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {company_profile.get('sub_domains', 'N/A')}
- Annual Turnover: Rs {company_profile.get('annual_turnover_lakhs', company_profile.get('turnover', 0))} Lakhs
- Experience: {company_profile.get('years_experience', company_profile.get('experience', 0))} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}
- GST Available: {'Yes' if company_profile.get('gst_available') else 'No'}
- MSME Available: {'Yes' if company_profile.get('msme_available') else 'No'}
- ISO Available: {'Yes' if company_profile.get('iso_available') else 'No'}
- GeM Seller: {'Yes' if company_profile.get('gem_seller') else 'No'}
- Similar Work Certificates: {'Yes' if company_profile.get('similar_work_certificates_available') else 'No'}
- Audited Financials: {'Yes' if company_profile.get('audited_financials_available') else 'No'}
- Bank Solvency: {'Yes' if company_profile.get('bank_solvency_available') else 'No'}

TENDER DOCUMENT:
{tender_text[:QUESTION_TEXT_LIMIT]}

Based on what this tender requires vs what the company profile provides,
generate up to {MAX_QUESTIONS} specific questions about MISSING information that affects eligibility.

IMPORTANT RULES:
1. Each question MUST have a stable id: q1, q2, q3, etc.
2. Only include options array for yes_no (always ["Yes","No"]) or select types.
3. Ask about things NOT already in the company profile.
4. Focus on mandatory eligibility criteria.
5. Return ONLY valid JSON. No markdown. No explanation.

{{
  "tender_title": "brief tender title",
  "tender_type": "L1 or QCBS or REVERSE_AUCTION or DIRECT or GEM",
  "questions": [
    {{
      "id": "q1",
      "question": "specific question to ask user",
      "why_needed": "brief reason why this affects analysis",
      "input_type": "text or number or yes_no or select",
      "options": ["option1", "option2"]
    }}
  ]
}}"""

    result = call_ai_json(prompt, max_tokens=2000)
    if result is None:
        return {"success": False, "error": LAST_ERROR or "AI returned invalid response. Please try again."}

    # Ensure stable IDs
    for idx, q in enumerate(result.get("questions", [])):
        if "id" not in q or not q["id"]:
            q["id"] = f"q{idx+1}"

    # Limit to MAX_QUESTIONS
    result["questions"] = result["questions"][:MAX_QUESTIONS]

    return {"success": True, "data": result}


# ================================================================
# FULL ANALYSIS (CALL 2)
# ================================================================

def analyze_tender(tender_text, company_profile, answers=None, pages=None):
    """
    Second AI call — full structured analysis.
    Returns the new comprehensive report JSON schema.
    AI returns exact quotes, Python verifies locations.
    """
    # Build answers section
    answers_text = ""
    if answers:
        answers_text = "\n\nADDITIONAL INFO FROM USER:\n"
        for q_id, answer in answers.items():
            answers_text += f"- {q_id}: {answer}\n"

    # Get company profile values (support both old and new field names)
    turnover = company_profile.get('annual_turnover_lakhs', company_profile.get('turnover', 0))
    experience = company_profile.get('years_experience', company_profile.get('experience', 0))

    prompt = f"""You are an expert Indian government tender analyst with deep knowledge of:
- GeM (Government e-Marketplace) portal tenders
- L1 (Lowest Bidder) based tenders
- QCBS (Quality and Cost Based Selection) tenders
- Reverse Auction tenders
- Direct/Nomination based tenders
- Indian procurement rules (GFR 2017, CVC guidelines)

CRITICAL INSTRUCTION ABOUT CITATIONS:
For every finding, return the EXACT quote from the document (copy word for word, max 20 words).
Do NOT guess or paraphrase — copy exactly as it appears.
If information is not in the document, set quote to empty string "" and found to false.
Only include section name if you are confident about it. If uncertain, leave section as empty string.

COMPANY PROFILE:
- Company Name: {company_profile.get('company_name', 'N/A')}
- Domain: {company_profile.get('domain', 'N/A')}
- Sub Domains: {company_profile.get('sub_domains', 'N/A')}
- Annual Turnover: Rs {turnover} Lakhs
- Experience: {experience} years
- Employees: {company_profile.get('employee_count', 0)}
- Certifications: {company_profile.get('certifications', 'None')}
- GST Available: {'Yes' if company_profile.get('gst_available') else 'No'}
- MSME Available: {'Yes' if company_profile.get('msme_available') else 'No'}
- ISO Available: {'Yes' if company_profile.get('iso_available') else 'No'}
- GeM Seller: {'Yes' if company_profile.get('gem_seller') else 'No'}
- Similar Work Certificates: {'Yes' if company_profile.get('similar_work_certificates_available') else 'No'}
- Audited Financials: {'Yes' if company_profile.get('audited_financials_available') else 'No'}
- Bank Solvency: {'Yes' if company_profile.get('bank_solvency_available') else 'No'}
- Past Govt Tender Experience: {'Yes' if company_profile.get('past_government_tender_experience') else 'No'}
- OEM Authorization: {company_profile.get('oem_authorization', 'N/A')}
- Locations Served: {company_profile.get('locations_served', 'N/A')}
- Preferred Tender Size: {company_profile.get('preferred_tender_size', 'N/A')}
{answers_text}

TENDER DOCUMENT (lines are numbered for reference):
{tender_text}

Return ONLY valid JSON. No markdown. No explanation. No code fences.

The JSON must follow this EXACT structure:

{{
  "executive_summary": {{
    "tender_title": "",
    "buyer": "",
    "tender_value": "",
    "deadline": "",
    "location": "",
    "scope_summary": "2-3 sentence summary of what this tender is about",
    "overall_recommendation": "Bid / Bid with Caution / Do Not Bid / Bid with Partner / Need More Information",
    "confidence_level": "High / Medium / Low",
    "confidence_reason": "why this confidence level"
  }},
  "scores": {{
    "eligibility_score": 0,
    "company_fit_score": 0,
    "risk_score": 0,
    "document_readiness_score": 0
  }},
  "bid_decision": {{
    "decision": "Bid / Bid with Caution / Do Not Bid / Bid with Partner / Need More Information",
    "reason": "clear reason for the decision",
    "critical_blockers": ["blocker1", "blocker2"]
  }},
  "mandatory_eligibility": {{
    "status": "Pass / Fail / Partial / Needs Verification",
    "failed_criteria": ["criteria that failed"],
    "passed_criteria": ["criteria that passed"],
    "needs_verification": ["criteria that need manual check"]
  }},
  "eligibility_match": [
    {{
      "criterion": "criterion name",
      "mandatory": true,
      "required": "what tender requires",
      "company_has": "what company has",
      "status": "PASS / FAIL / PARTIAL / NEEDS_VERIFICATION",
      "note": "brief explanation",
      "citation": {{
        "found": true,
        "page": 1,
        "line": null,
        "section": "",
        "quote": "exact quote from document"
      }}
    }}
  ],
  "technical_fit": {{
    "matched_capabilities": ["what company can do well"],
    "weak_areas": ["where company falls short"],
    "unclear_requirements": ["requirements that need clarification"]
  }},
  "financial_requirements": {{
    "turnover_requirement": "what tender needs",
    "company_turnover_status": "PASS / FAIL / CLOSE",
    "emd_amount": "amount or Not mentioned",
    "emd_exemption_possible": "Yes/No and reason",
    "performance_guarantee": "percentage or Not mentioned",
    "payment_terms": "terms or Not mentioned",
    "working_capital_risk": "Low/Medium/High and reason",
    "citations": []
  }},
  "document_readiness": [
    {{
      "document": "document name",
      "required": true,
      "company_has": "Yes / No / Unknown",
      "status": "Ready / Missing / Needs Verification",
      "priority": "High / Medium / Low",
      "citation": {{
        "found": true,
        "page": 1,
        "line": null,
        "section": "",
        "quote": "exact quote"
      }}
    }}
  ],
  "risk_analysis": [
    {{
      "risk": "risk description",
      "risk_level": "High / Medium / Low",
      "reason": "why this is a risk",
      "mitigation": "how to reduce this risk",
      "citation": {{
        "found": false,
        "page": null,
        "line": null,
        "section": "",
        "quote": ""
      }}
    }}
  ],
  "pre_bid_questions": [
    {{
      "question": "question to ask the buyer",
      "why_to_ask": "why this question is important"
    }}
  ],
  "action_plan": [
    {{
      "priority": "High / Medium / Low",
      "action": "what to do",
      "timeline": "Today / Before Pre-Bid / Before Submission / Later"
    }}
  ],
  "recommendations": ["rec 1", "rec 2", "rec 3"],
  "summary": "3-4 sentence summary with clear bid/no-bid advice",
  "disclaimer": "This is an AI-assisted tender analysis and should be manually verified before final bid submission."
}}

SCORING RULES:
- eligibility_score (0-100): Strictly based on mandatory criteria. 100 = all pass, 0 = all fail.
- company_fit_score (0-100): Weighted: mandatory eligibility 30, technical fit 20, similar experience 15, financial capacity 15, document readiness 10, location 5, risk 5.
- risk_score (0-100): Higher = riskier. Each High risk = +30, Medium = +15, Low = +5. Cap at 100.
- document_readiness_score (0-100): Percentage of required documents marked Ready.

BID DECISION RULES:
- If mandatory eligibility fails badly: "Do Not Bid"
- If mandatory eligibility partially passes but gaps can be fixed: "Bid with Caution"
- If eligibility passes and risk is low/medium: "Bid"
- If company lacks one major requirement but partner can solve: "Bid with Partner"
- If key data missing: "Need More Information"

IMPORTANT: Do not hallucinate. If information is not in the document, say "Not found" or "Needs manual verification". Do not provide legal guarantee."""

    result = call_ai_json(prompt, max_tokens=6000)
    if result is None:
        return {"success": False, "error": LAST_ERROR or "Analysis failed. Please try again."}

    # ── Python verifies all citations ─────────────────────
    if pages:
        result = verify_citations(result, pages)

    # ── Compute scores from AI result (sanity check / override) ──
    result = compute_scores(result)

    return {"success": True, "data": result}


# ================================================================
# SCORE COMPUTATION
# ================================================================

def compute_scores(result):
    """
    Compute and validate the 4 scores based on the analysis result.
    AI provides initial scores; Python validates/adjusts them.
    """
    scores = result.get("scores", {})

    # ── 1. Eligibility Score ──────────────────────────────
    # Based strictly on mandatory criteria pass/fail
    eligibility_items = result.get("eligibility_match", [])
    mandatory_items = [e for e in eligibility_items if e.get("mandatory", True)]

    if mandatory_items:
        passed = sum(1 for e in mandatory_items if e.get("status") == "PASS")
        eligibility_score = int((passed / len(mandatory_items)) * 100)
    else:
        eligibility_score = scores.get("eligibility_score", 50)

    # ── 2. Company Fit Score (weighted) ───────────────────
    eligibility_weight    = 30
    technical_weight      = 20
    experience_weight     = 15
    financial_weight      = 15
    document_weight       = 10
    location_weight       = 5
    risk_weight           = 5

    # Eligibility component (0-100)
    elig_component = eligibility_score

    # Technical fit component
    tech_fit = result.get("technical_fit", {})
    matched = len(tech_fit.get("matched_capabilities", []))
    weak = len(tech_fit.get("weak_areas", []))
    total_tech = matched + weak
    tech_component = int((matched / total_tech) * 100) if total_tech > 0 else 50

    # Experience component
    mand_status = result.get("mandatory_eligibility", {}).get("status", "")
    if "Pass" in mand_status:
        exp_component = 80
    elif "Partial" in mand_status:
        exp_component = 50
    else:
        exp_component = 20

    # Financial component
    fin = result.get("financial_requirements", {})
    fin_status = fin.get("company_turnover_status", "")
    if "PASS" in fin_status:
        fin_component = 80
    elif "CLOSE" in fin_status:
        fin_component = 50
    else:
        fin_component = 20

    # Document readiness component
    docs = result.get("document_readiness", [])
    required_docs = [d for d in docs if d.get("required", True)]
    if required_docs:
        ready_docs = sum(1 for d in required_docs if d.get("status") == "Ready")
        doc_component = int((ready_docs / len(required_docs)) * 100)
    else:
        doc_component = 50

    # Location component (simplified)
    loc_component = 70  # neutral default

    # Risk component (inverse — low risk = high score)
    risk_items = result.get("risk_analysis", [])
    risk_score_raw = 0
    for r in risk_items:
        level = r.get("risk_level", "Low")
        if level == "High":
            risk_score_raw += 30
        elif level == "Medium":
            risk_score_raw += 15
        else:
            risk_score_raw += 5
    risk_score_raw = min(risk_score_raw, 100)
    risk_component = 100 - risk_score_raw  # inverse: low risk = high score

    company_fit_score = (
        (elig_component * eligibility_weight) +
        (tech_component * technical_weight) +
        (exp_component * experience_weight) +
        (fin_component * financial_weight) +
        (doc_component * document_weight) +
        (loc_component * location_weight) +
        (risk_component * risk_weight)
    ) // 100

    company_fit_score = min(100, max(0, company_fit_score))

    # ── 3. Risk Score (higher = riskier) ──────────────────
    risk_score = risk_score_raw

    # ── 4. Document Readiness Score ───────────────────────
    document_readiness_score = doc_component

    # Override scores with Python-computed values
    result["scores"] = {
        "eligibility_score": eligibility_score,
        "company_fit_score": company_fit_score,
        "risk_score": risk_score,
        "document_readiness_score": document_readiness_score
    }

    # ── Bid decision logic ────────────────────────────────
    # Override if AI got it wrong based on scores
    mand_elig = result.get("mandatory_eligibility", {})
    mand_status = mand_elig.get("status", "")
    failed = mand_elig.get("failed_criteria", [])

    if eligibility_score < 30:
        decision = "Do Not Bid"
        reason = "Multiple mandatory eligibility criteria not met."
    elif eligibility_score < 60:
        if len(failed) <= 2 and risk_score < 50:
            decision = "Bid with Caution"
            reason = "Some mandatory criteria need attention but may be addressable."
        else:
            decision = "Do Not Bid"
            reason = "Significant eligibility gaps and high risk."
    elif eligibility_score >= 60 and risk_score > 60:
        if company_fit_score >= 50:
            decision = "Bid with Caution"
            reason = "Eligible but significant risks present."
        else:
            decision = "Bid with Partner"
            reason = "Eligible but need partner to address gaps."
    elif eligibility_score >= 60 and risk_score <= 60:
        decision = "Bid"
        reason = "Eligible with manageable risk level."
    else:
        decision = result.get("bid_decision", {}).get("decision", "Need More Information")
        reason = result.get("bid_decision", {}).get("reason", "Insufficient data for recommendation.")

    result["bid_decision"] = {
        "decision": decision,
        "reason": reason,
        "critical_blockers": mand_elig.get("failed_criteria", [])
    }

    # Update executive summary recommendation
    if "executive_summary" in result:
        result["executive_summary"]["overall_recommendation"] = decision

    # Confidence level
    if eligibility_score >= 80 and risk_score <= 30:
        confidence = "High"
    elif eligibility_score >= 50:
        confidence = "Medium"
    else:
        confidence = "Low"

    if "executive_summary" in result:
        result["executive_summary"]["confidence_level"] = confidence

    result["scores"]["confidence_level"] = confidence

    return result
