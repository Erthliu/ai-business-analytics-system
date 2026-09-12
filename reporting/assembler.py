"""Deterministic assembly and Markdown rendering of V8 reports."""
from __future__ import annotations

from contracts.business import BusinessInsight, ClaimType
from contracts.critic import CriticReview
from contracts.reporting import (
    ReportArtifact, ReportProvenance, ReportSpec, ReportStatus,
    RenderedSection, RenderedStatement, SectionType,
)
from reporting.data import build_chart, build_table, claim_catalog
from reporting.validator import ReportValidator


class ReportAssembler:
    """Create authoritative report content exclusively from approved V7 content."""

    renderer_version = "8.0.0"

    def __init__(self) -> None:
        self.validator = ReportValidator()

    def assemble(self, spec: ReportSpec, insight: BusinessInsight,
                 review: CriticReview) -> ReportArtifact:
        """Bind a validated presentation specification to approved content."""
        self.validator.validate_spec(spec, insight, review)
        claims = claim_catalog(insight)
        statements_by_claim = {
            f"claim:{index}": item.statement
            for index, item in enumerate(insight.key_insights)
        }
        sections: list[RenderedSection] = []
        for section in spec.sections:
            statements: list[RenderedStatement] = []
            informational: list[str] = []
            if section.section_type == SectionType.EXECUTIVE_SUMMARY:
                statements = [RenderedStatement(
                    text=insight.executive_summary, claim_ids=section.claim_ids,
                    evidence_refs=section.evidence_refs,
                )]
            elif section.section_type == SectionType.PRIMARY_RESULT:
                claim_id = section.claim_ids[0]
                statements = [RenderedStatement(
                    text=insight.headline if claim_id == "claim:0" else statements_by_claim[claim_id],
                    claim_ids=[claim_id], evidence_refs=claims[claim_id].evidence_refs,
                )]
            elif section.section_type in {SectionType.KEY_DRIVERS, SectionType.TREND, SectionType.RANKING}:
                statements = [RenderedStatement(
                    text=statements_by_claim[claim_id], claim_ids=[claim_id],
                    evidence_refs=claims[claim_id].evidence_refs,
                ) for claim_id in section.claim_ids]
            elif section.section_type == SectionType.QA_CAVEATS:
                informational = list(insight.caveats)
            elif section.section_type == SectionType.UNANSWERED_QUESTIONS:
                informational = list(insight.unanswered_questions)
            elif section.section_type == SectionType.METHODOLOGY:
                informational = [
                    "Approved structured claims are presented without new calculations.",
                    f"QA review status: {review.overall_status}.",
                    f"Report rules version: {spec.report_rules_version}.",
                ]
            sections.append(RenderedSection(
                section_id=section.section_id, section_type=section.section_type,
                title=section.title, statements=statements,
                informational_items=informational,
            ))
        artifact = ReportArtifact(
            report_spec_id=spec.report_spec_id,
            business_insight_id=insight.insight_id,
            critic_review_id=insight.critic_review_id,
            analysis_id=insight.analysis_id,
            investigation_id=insight.investigation_id,
            dataset_version_id=insight.dataset_version_id,
            status=ReportStatus.COMPLETE_WITH_CAVEATS if insight.caveats else ReportStatus.COMPLETE,
            title=spec.report_title,
            rendered_sections=sections,
            charts=[build_chart(item, insight) for item in spec.chart_specs],
            tables=[build_table(item, insight) for item in spec.table_specs],
            caveats=list(insight.caveats),
            unanswered_questions=list(insight.unanswered_questions),
            provenance=ReportProvenance(
                business_insight_id=insight.insight_id,
                critic_review_id=insight.critic_review_id,
                analysis_id=insight.analysis_id,
                investigation_id=insight.investigation_id,
                dataset_version_id=insight.dataset_version_id,
                claim_evidence={claim_id: list(claim.evidence_refs) for claim_id, claim in claims.items()},
            ),
            renderer_version=self.renderer_version,
            report_rules_version=spec.report_rules_version,
        )
        self.validator.validate_artifact(artifact, spec, insight, review)
        return artifact

    @staticmethod
    def to_markdown(artifact: ReportArtifact) -> str:
        """Render a validated structured artifact as deterministic Markdown."""
        lines = [f"# {artifact.title}", ""]
        for section in artifact.rendered_sections:
            lines.extend([f"## {section.title}", ""])
            for statement in section.statements:
                lines.extend([statement.text, ""])
            for item in section.informational_items:
                lines.append(f"- {item}")
            if section.informational_items: lines.append("")
            if section.section_type == SectionType.EVIDENCE_TABLE:
                for table in artifact.tables:
                    lines.extend([f"### {table.title}", "", f"| {' | '.join(table.columns)} |",
                                  f"| {' | '.join(['---'] * len(table.columns))} |"])
                    for row in table.rows:
                        values = ["" if row.get(column) is None else str(row[column]).replace("|", "\\|").replace("\n", " ")
                                  for column in table.columns]
                        lines.append(f"| {' | '.join(values)} |")
                    lines.append("")
        if artifact.charts:
            lines.extend(["## Chart Data", ""])
            for chart in artifact.charts:
                lines.append(f"### {chart.title} ({chart.chart_type})")
                for point in chart.data: lines.append(f"- {point.x}: {point.y}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
