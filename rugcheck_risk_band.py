"""Validate and label provider risk scores. Higher values mean greater risk."""

import math


def normalize_risk_score(value):
    """Return a finite 0–100 risk score, or None for unusable provider data."""
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 100 else None


def risk_band(score):
    """Label a normalized score for reporting. Admission is evaluated separately."""
    if score is None:
        return None
    if score <= 30:
        return "low"
    if score <= 60:
        return "medium"
    return "high"
