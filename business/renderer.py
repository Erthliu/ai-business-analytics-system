"""Deterministic rendering of authoritative structured business claims."""
from __future__ import annotations

from contracts.business import BusinessClaim, ClaimType, EvidenceRecord
from contracts.computed_analysis import ComputedAnalysis


def _number(value: float | int | None) -> str:
    if value is None: return "undefined"
    if isinstance(value, int) or float(value).is_integer(): return f"{int(value):,}"
    return f"{float(value):,.2f}".rstrip("0").rstrip(".")


class BusinessRenderer:
    """Render facts only from evidence selected by a structured claim."""

    def render(self, claim: BusinessClaim, catalog: dict[str, EvidenceRecord], analysis: ComputedAnalysis) -> str:
        evidence = [catalog[reference] for reference in claim.evidence_refs]
        metric = claim.metric or analysis.primary_metric
        label = metric.replace("_", " ").title()
        if claim.claim_type == ClaimType.METRIC_VALUE:
            return f"{label} was {_number(evidence[0].value)}."
        if claim.claim_type == ClaimType.METRIC_CHANGE:
            row = next(item.value for item in evidence if item.evidence_type == "RESULT_ROW")
            change, percent = row.get("absolute_change"), row.get("percentage_change")
            direction = "increased" if change > 0 else ("decreased" if change < 0 else "was unchanged")
            periods = [item.value for item in evidence if item.evidence_type == "PERIOD"]
            period_text = ""
            if len(periods) == 2:
                period_text = f" from {periods[1]['start']} through {periods[1]['end']} to {periods[0]['start']} through {periods[0]['end']}"
            if change == 0: return f"{label} was unchanged{period_text}."
            percent_text = "" if percent is None else f" ({_number(abs(percent))}%)"
            return f"{label} {direction} by {_number(abs(change))}{percent_text}{period_text}."
        if claim.claim_type in {ClaimType.TOP_CONTRIBUTOR, ClaimType.OFFSETTING_CONTRIBUTOR}:
            record = evidence[0]; group = record.value
            if claim.claim_type == ClaimType.OFFSETTING_CONTRIBUTOR:
                return f"{record.group_value} moved opposite to the overall {label.lower()} change and offset {_number(group['contribution_percent'])}% of its magnitude."
            if "contribution_percent" in group:
                direction = "decline" if group.get("absolute_change", 0) < 0 else "increase"
                return f"{record.group_value} showed the largest {label.lower()} {direction} for {record.dimension}, accounting for {_number(group['contribution_percent'])}% of the observed change magnitude."
            if "absolute_change" in group:
                if group["absolute_change"] is None:
                    return f"{label} change for {record.group_value} is undefined; that {record.dimension} group is absent in one comparison period."
                direction = "decline" if group["absolute_change"] < 0 else "increase"
                return f"{record.group_value} showed the largest {label.lower()} {direction} among {record.dimension} groups."
            return f"{record.group_value} ranked highest for {label.lower()} among {record.dimension} groups at {_number(group[label.lower().replace(' ', '_')])}."
        if claim.claim_type == ClaimType.CONCENTRATION:
            record = evidence[0]
            return f"The top three aligned contributors for {record.dimension} represented {_number(record.value['top_3_share'])}% of aligned {label.lower()} movement."
        if claim.claim_type == ClaimType.TREND_DIRECTION:
            first, last = evidence[0].value, evidence[-1].value
            direction = "increased" if last["value"] > first["value"] else ("decreased" if last["value"] < first["value"] else "was unchanged")
            return f"{label} {direction} from {_number(first['value'])} in {first['period']} to {_number(last['value'])} in {last['period']}."
        if claim.claim_type == ClaimType.QA_CAVEAT:
            value = evidence[0].value
            message = value.get("message", str(value)) if isinstance(value, dict) else str(value)
            return f"Caveat: {message}"
        return str(evidence[0].value)
