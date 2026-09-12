"""CLI for read-only profiling of an eligible retained dataset version."""

import argparse
import json

from config.settings import load_settings
from profiling.profiler import profile_dataset_version


def main() -> None:
    """Print a concise profile summary and optional JSON payload."""
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_version_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL must be set.")
    profile = profile_dataset_version(args.dataset_version_id, settings.database_url)
    if args.json:
        print(json.dumps(profile.model_dump(mode="json"), indent=2))
    else:
        print(f"Profile {profile.profile_id}: {profile.row_count} rows, {profile.column_count} columns")
        print(f"Deterministic findings: {len(profile.deterministic_findings)}")


if __name__ == "__main__":
    main()
