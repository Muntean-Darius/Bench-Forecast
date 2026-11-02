"""One-off test-data quality fix (variant C of the RAGAS story).

The demo seeder assigned real, non-anonymized CVs to synthetic employees:
profiles contain other people's websites, LinkedIn/GitHub handles, emails and
phones, and no reference to the synthetic employee identity. RAGAS
faithfulness correctly scores such contexts 0.

This script, run once, without reseeding (real forecast runs and HITL events
are preserved):

  1. strips real-world PII from every employee profile (URLs, emails, phones,
     social handles),
  2. binds the profile to its synthetic owner with a "Profile of <name>"
     prefix, mirroring production (an employee uploads their own CV).

It is idempotent: profiles already prefixed are left untouched.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from src.database.database import get_sync_db
from src.database.models import Employee

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(
    r"\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?:\s*(?:ext|x|ex)\.?\s*\d+)?", re.IGNORECASE
)
SOCIAL_LINE_RE = re.compile(
    r"(?im)^\s*(?:website|linkedin|github|portfolio|email|phone|address|contact)"
    r"(?:\s+and\s+links)?\s*:?[^\n]*$"
)
SOCIAL_INLINE_RE = re.compile(
    r"(?:linkedin|github|website|portfolio)\s*:?\s*\S+", re.IGNORECASE
)
WHITESPACE_RE = re.compile(r"[ \t]+")
MULTINL_RE = re.compile(r"\n{3,}")


def anonymize(text: str) -> str:
    text = URL_RE.sub("", text)
    text = EMAIL_RE.sub("", text)
    text = PHONE_RE.sub("", text)
    text = SOCIAL_LINE_RE.sub("", text)
    text = SOCIAL_INLINE_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text)
    text = MULTINL_RE.sub("\n\n", text)
    return text.strip()


def main() -> None:
    prefix = "Profile of {name}:"
    fixed = 0
    skipped = 0
    with get_sync_db() as db:
        employees = db.execute(select(Employee)).scalars().all()
        for emp in employees:
            if not emp.profile_text:
                continue
            name = (emp.full_name or f"{emp.first_name or ''} {emp.last_name or ''}").strip()
            if emp.profile_text.startswith("Profile of"):
                skipped += 1
                continue
            scrubbed = anonymize(emp.profile_text)
            emp.profile_text = prefix.format(name=name) + " " + scrubbed
            fixed += 1
        db.commit()
    print(f"Anonymized + bound identity: {fixed} profiles (skipped: {skipped}).")


if __name__ == "__main__":
    main()
