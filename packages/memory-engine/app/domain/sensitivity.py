"""
Deterministic regex-based sensitivity scanning.
Classifies text into 'normal', 'private', or 'secret' in <1ms without calling an LLM.
"""
from __future__ import annotations
import re
from typing import Literal

SensitivityLevel = Literal["normal", "private", "secret"]

# Secret patterns (credentials, OTPs, financial payment card numbers, API keys)
OTP_PATTERN = re.compile(r"\b(?:otp|one[-\s]?time[-\s]?(?:password|code)|verification\s*code|pin)\b[^\d\n]{0,20}(\d{4,8})\b", re.IGNORECASE)
PASSWORD_PATTERN = re.compile(r"\b(?:password|passwd|pwd|secret_key|api_secret)\b\s*(?:is|:|=)\s*([^\s,;]+)", re.IGNORECASE)
API_KEY_PATTERN = re.compile(r"\b(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36,}|gho_[a-zA-Z0-9]{36,}|glpat-[a-zA-Z0-9\-]{20,}|Bearer\s+[a-zA-Z0-9_\-\.]{25,})\b")

# Private patterns (PII: PAN, Aadhaar, Indian IFSC, phone numbers, email addresses)
PAN_PATTERN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
AADHAAR_PATTERN = re.compile(r"\b[2-9]\d{3}\s\d{4}\s\d{4}\b")
IFSC_PATTERN = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
PHONE_PATTERN = re.compile(r"(?:\+91[\-\s]?)?[6789]\d{9}\b|\b(?:\+1[\-\s]?)?\(?\d{3}\)?[\-\s]?\d{3}[\-\s]?\d{4}\b")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def _luhn_checksum(card_number: str) -> bool:
    """Verifies Luhn checksum for 13 to 19 digit credit card numbers."""
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


def scan(text: str) -> SensitivityLevel:
    """
    Scans raw text and returns sensitivity level:
    - 'secret': passwords, OTPs, validated credit cards, API keys.
    - 'private': PII (phone, email, PAN, Aadhaar, IFSC).
    - 'normal': general information.
    """
    if not text:
        return "normal"

    # 1. Check for Secrets
    if OTP_PATTERN.search(text):
        return "secret"
    if PASSWORD_PATTERN.search(text):
        return "secret"
    if API_KEY_PATTERN.search(text):
        return "secret"

    # Check for Credit Card numbers (Luhn check)
    candidate_cards = re.findall(r"\b(?:\d[ -]*?){13,19}\b", text)
    for cand in candidate_cards:
        clean_digits = re.sub(r"\D", "", cand)
        if 13 <= len(clean_digits) <= 19 and _luhn_checksum(clean_digits):
            return "secret"

    # 2. Check for Private PII
    if PAN_PATTERN.search(text):
        return "private"
    if AADHAAR_PATTERN.search(text):
        return "private"
    if IFSC_PATTERN.search(text):
        return "private"
    if EMAIL_PATTERN.search(text):
        return "private"
    if PHONE_PATTERN.search(text):
        return "private"

    return "normal"
