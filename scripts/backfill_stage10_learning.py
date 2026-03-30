"""
Replay historical activity_work_profiles into Stage 10 project-local cache rows,
then rebuild the global item_knowledge_base tier.

Usage:
    python -m scripts.backfill_stage10_learning
    python -m scripts.backfill_stage10_learning --delete-unreferenced-legacy
"""

from __future__ import annotations

import argparse
import json

from app.core.database import SessionLocal
from app.services.work_profile_service import backfill_project_local_context_profiles


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Stage 10 local/global learning state")
    parser.add_argument(
        "--delete-unreferenced-legacy",
        action="store_true",
        help="Delete legacy null-project item_context_profiles rows after successful repointing",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = backfill_project_local_context_profiles(
            db,
            delete_unreferenced_legacy=args.delete_unreferenced_legacy,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
