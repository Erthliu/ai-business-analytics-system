"""Run the V1 CSV ingestion and validation demonstration."""

from pathlib import Path

from config.logging_config import configure_logging
from config.settings import load_settings
from pipeline.execution import execute_pipeline

PROJECT_ROOT = Path(__file__).resolve().parent
SAMPLE_CSV = PROJECT_ROOT / "data" / "sample" / "sales.csv"
REQUIRED_COLUMNS = (
    "order_id", "order_date", "customer_id", "product_id", "region",
    "quantity", "unit_price", "revenue",
)
NUMERIC_COLUMNS = ("quantity", "unit_price", "revenue")


def main() -> None:
    """Run the bundled sales sample with durable execution metadata."""
    settings = load_settings()
    configure_logging(settings.log_level)
    database_url = settings.require_database_url()

    result = execute_pipeline(
        SAMPLE_CSV,
        database_url,
        required_columns=REQUIRED_COLUMNS,
        numeric_columns=NUMERIC_COLUMNS,
        unique_columns=("order_id",),
        date_columns=("order_date",),
        numeric_ranges={
            "quantity": (0, None),
            "unit_price": (0, None),
            "revenue": (0, None),
        },
        apply_sales_policy=True,
    )

    print("V1.2 pipeline summary")
    print(f"Run ID: {result.run_id}")
    print(f"Run status: {result.status}")
    print(f"Idempotent replay: {result.idempotent_replay}")
    print(f"Dataset version: {result.dataset_version or 'existing version'}")
    if result.validation_result is None:
        return
    validation = result.validation_result
    print(f"Rows: {validation.statistics['row_count']}")
    print(f"Validation outcome: {validation.outcome}")
    print(f"Warnings: {len(validation.warnings)}")
    for message in validation.errors + validation.warnings:
        print(f"- {message}")


if __name__ == "__main__":
    main()
