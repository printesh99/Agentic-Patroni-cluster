"""Structured RCA prompt helpers."""
from __future__ import annotations


def rca_prompt(incident: dict, snippets: list[dict]) -> str:
    return (
        "Use only the provided incident evidence and runbook snippets. "
        "Separate facts from assumptions. Mark destructive actions as approval-required.\n\n"
        f"Incident: {incident}\n\nRunbooks: {snippets}\n\n"
        "Return: Severity, Incident category, What happened, Evidence, Likely root cause, "
        "Immediate safe checks, Recommended runbook, Actions requiring approval, "
        "Business impact, Confidence score."
    )
