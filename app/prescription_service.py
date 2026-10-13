"""Literal prescription field candidates; no medical advice or correction."""

from __future__ import annotations

import re


_DOSE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|mL|units?|IU|%)\b", re.IGNORECASE)
_FREQUENCY = re.compile(
    r"\b(?:three\s+times\s+daily|four\s+times\s+daily|once\s+daily|twice\s+daily|three\s+times|four\s+times|every\s+\d+\s+hours?|once|twice|daily|nightly|weekly|OD|BD|BID|TID|QID|SOS)\b",
    re.IGNORECASE,
)
_DURATION = re.compile(r"\b(?:for\s+)?\d+\s*(?:days?|weeks?|months?)\b", re.IGNORECASE)
_INSTRUCTIONS = re.compile(r"\b(?:after|before|with|without)\s+(?:food|meals?|breakfast|lunch|dinner)\b|\bas directed\b", re.IGNORECASE)


def extract_candidates(pages: list[dict]) -> dict:
    candidates = []
    for page in pages:
        for line in (page.get("text") or "").splitlines():
            line = line.strip()
            dose = _DOSE.search(line)
            frequency = _FREQUENCY.search(line)
            duration = _DURATION.search(line)
            instructions = _INSTRUCTIONS.search(line)
            if not dose and not frequency and not duration:
                continue
            candidate_name = line[: dose.start()].strip(" :-,.") if dose else ""
            candidates.append(
                {
                    "page": page["page"],
                    "recognized_line": line,
                    "medicine_candidate": candidate_name,
                    "dosage_candidate": dose.group(0) if dose else "",
                    "frequency_candidate": frequency.group(0) if frequency else "",
                    "duration_candidate": duration.group(0) if duration else "",
                    "instructions_candidate": instructions.group(0) if instructions else "",
                    "requires_review": True,
                }
            )
    return {
        "candidates": candidates,
        "original_text": "\n\n".join(
            f"--- Page {page['page']} ---\n{page.get('text', '')}"
            for page in pages if page.get("text")
        ),
        "warning": "These are unverified OCR candidates. A qualified human must check the original prescription before any clinical use. Ambiguous text has not been corrected and missing fields remain blank.",
    }
