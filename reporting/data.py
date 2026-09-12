"""Deterministic chart and table data derived from approved V7 evidence."""
from __future__ import annotations

from contracts.business import BusinessInsight, ClaimType, EvidenceRecord
from contracts.reporting import ChartDatum, ChartSpec, RenderedChart, RenderedTable, TableSpec


def evidence_catalog(insight: BusinessInsight) -> dict[str, EvidenceRecord]:
    """Index the immutable evidence embedded in a BusinessInsight."""
    return {record.evidence_ref: record for record in insight.evidence}


def claim_catalog(insight: BusinessInsight) -> dict[str, object]:
    """Assign stable aliases to the ordered immutable V7 claims."""
    return {f"claim:{index}": claim for index, claim in enumerate(insight.claims)}


def eligible_chart_types(claim_type: ClaimType) -> set[str]:
    """Return presentation types that cannot distort a structured claim."""
    return {
        ClaimType.METRIC_VALUE: {"KPI"},
        ClaimType.METRIC_CHANGE: {"KPI", "COMPARISON_BAR"},
        ClaimType.TOP_CONTRIBUTOR: {"BAR", "COMPARISON_BAR"},
        ClaimType.OFFSETTING_CONTRIBUTOR: {"BAR"},
        ClaimType.CONCENTRATION: {"BAR"},
        ClaimType.TREND_DIRECTION: {"LINE"},
        ClaimType.QA_CAVEAT: set(),
    }[claim_type]


def build_chart(spec: ChartSpec, insight: BusinessInsight) -> RenderedChart:
    """Materialize chart values only from referenced evidence objects."""
    catalog = evidence_catalog(insight)
    records = [catalog[reference] for reference in spec.evidence_refs]
    data: list[ChartDatum] = []
    if spec.chart_type.value == "KPI":
        value = records[0].value
        if isinstance(value, dict):
            value = value.get("analysis_period_value", value.get(insight.claims[0].metric or "value", value.get("value")))
        data = [ChartDatum(x=spec.title, y=value)]
    elif spec.chart_type.value == "COMPARISON_BAR":
        row = next(record.value for record in records if isinstance(record.value, dict))
        if "analysis_period_value" in row and len(records) > 1 and records[0].group_value is not None:
            for record in records:
                data.extend([
                    ChartDatum(x=record.group_value, y=record.value["comparison_period_value"], series="comparison"),
                    ChartDatum(x=record.group_value, y=record.value["analysis_period_value"], series="analysis"),
                ])
        elif "analysis_period_value" in row:
            data = [ChartDatum(x="comparison", y=row["comparison_period_value"]),
                    ChartDatum(x="analysis", y=row["analysis_period_value"])]
        else:
            value = row.get("absolute_change", row.get(spec.y_field))
            data = [ChartDatum(x=records[0].group_value, y=value)]
    elif spec.chart_type.value == "LINE":
        data = [ChartDatum(x=record.value["period"], y=record.value["value"]) for record in records]
    elif records and records[0].evidence_type == "CONCENTRATION":
        value = records[0].value
        data = [ChartDatum(x="top 1", y=value["top_1_share"]),
                ChartDatum(x="top 3", y=value["top_3_share"]),
                ChartDatum(x="top 5", y=value["top_5_share"])]
    else:
        for record in records:
            value = record.value
            if isinstance(value, dict):
                y = value.get(spec.y_field)
                if y is None:
                    y = value.get("contribution_percent", value.get("absolute_change", value.get("value")))
            else:
                y = value
            data.append(ChartDatum(x=record.group_value, y=y))
    if spec.sort.value != "NONE":
        data.sort(key=lambda item: float("-inf") if item.y is None else item.y,
                  reverse=spec.sort.value == "DESC")
    if spec.limit: data = data[:spec.limit]
    return RenderedChart(
        chart_id=spec.chart_id, chart_type=spec.chart_type, title=spec.title,
        claim_ids=spec.claim_ids, evidence_refs=spec.evidence_refs, data=data,
        x_field=spec.x_field, y_field=spec.y_field, unit=spec.unit, notes=spec.notes,
    )


def build_table(spec: TableSpec, insight: BusinessInsight) -> RenderedTable:
    """Materialize a tabular projection directly from approved evidence."""
    catalog = evidence_catalog(insight)
    rows: list[dict[str, object]] = []
    for reference in spec.evidence_refs:
        record = catalog[reference]
        source = record.value if isinstance(record.value, dict) else {"value": record.value}
        row = {column: source.get(column) for column in spec.columns}
        if "group" in spec.columns: row["group"] = record.group_value
        rows.append(row)
    if spec.sort_by and spec.sort.value != "NONE":
        rows.sort(key=lambda row: (row.get(spec.sort_by) is not None, row.get(spec.sort_by)),
                  reverse=spec.sort.value == "DESC")
    if spec.limit: rows = rows[:spec.limit]
    return RenderedTable(
        table_id=spec.table_id, title=spec.title, claim_ids=spec.claim_ids,
        evidence_refs=spec.evidence_refs, columns=spec.columns, rows=rows,
    )
