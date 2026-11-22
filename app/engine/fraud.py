"""Fraud indicators. Pattern hits are WARNINGS for the reader and triage signals for staff, never a verdict."""
import re

INDICATORS = [
    ("guaranteed_visa", r"guarantee[sd]?\s+(a\s+)?(visa|job|work permit|sponsorship)|100%\s*(visa|approval|success)", "Nobody can guarantee a visa; only the immigration authority decides."),
    ("pay_for_job", r"(pay|fee|deposit).{0,40}(job offer|placement|contract|sponsorship)|(job offer|placement|sponsorship).{0,40}(pay|fee|deposit)", "Be very cautious about paying for a job offer or sponsorship. Check recruitment-fee laws for the country."),
    ("personal_account", r"(send|transfer|deposit).{0,40}(personal|private)\s+(bank\s+)?account|western union|moneygram|crypto|bitcoin|gift card", "Payments to personal accounts, wire services or crypto are a common scam pattern."),
    ("whatsapp_only", r"whatsapp\s+(only|me)|contact.{0,20}only.{0,20}whatsapp|telegram", "Recruiters who only use chat apps are harder to verify."),
    ("pressure", r"(urgent|limited slots|last chance|act now|pay today|within 24 hours)", "Pressure to act quickly is a manipulation tactic."),
    ("sensitive_early", r"(send|share).{0,30}(passport number|bank (login|pin)|password|ssn|social security)", "Legitimate employers do not need banking credentials or logins."),
    ("no_interview", r"no interview|no experience.{0,20}(high|big) salary|easy visa", "Offers that skip normal selection and promise high pay are suspicious."),
]
NOTICES = [
    "A job offer does not automatically guarantee a visa.",
    "An employer's sponsorship claim must be verified with the employer and the immigration authority.",
    "Immigration authorities make official visa decisions.",
    "Never submit false or altered documents.",
    "Recruitment fees must be checked against the law of the country concerned.",
    "No organization should be trusted solely because it appears on this platform.",
]
CATEGORIES = {"fraudulent_organization", "fake_job_offer", "suspicious_recruiter", "misleading_sponsorship", "financial_scam", "other"}


def scan_text(text: str):
    hits = []
    for key, rx, why in INDICATORS:
        if re.search(rx, text or "", re.I):
            hits.append({"indicator": key, "why": why})
    return hits
