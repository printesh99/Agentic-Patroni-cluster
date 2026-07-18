from __future__ import annotations

from typing import Iterable

from .schema import Claim, EvidenceItem


def validate_claims(
    claims: Iterable[Claim], evidence_items: Iterable[EvidenceItem]
) -> tuple[list[Claim], list[str]]:
    evidence_ids = {item.id for item in evidence_items}
    valid: list[Claim] = []
    unsupported: list[str] = []
    for claim in claims:
        missing = [evidence_id for evidence_id in claim.evidence_ids
                   if evidence_id not in evidence_ids]
        if claim.type == "fact" and (not claim.evidence_ids or missing):
            unsupported.append(claim.id)
            continue
        valid.append(claim)
    return valid, unsupported
