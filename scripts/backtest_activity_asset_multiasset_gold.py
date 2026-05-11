from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Keep this standalone script importable in local environments where .env
# contains server-oriented values that are irrelevant to offline backtesting.
os.environ["DEBUG"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./backtest_activity_asset_multiasset_gold.db")
os.environ.setdefault("JWT_SECRET", "local-backtest-secret")
os.environ.setdefault("SECRET_KEY", "local-backtest-secret")
os.environ.setdefault("AI_API_KEY", "local-backtest-never-calls-ai")
os.environ["AI_ENABLED"] = "false"

logging.getLogger("app.core.database").setLevel(logging.ERROR)

from app.core.constants import get_max_hours_for_type  # noqa: E402
from app.services.ai_service import (  # noqa: E402
    _deterministic_asset_requirement_candidates,
    keyword_classify_activity_name,
)
from app.services.work_profile_service import (  # noqa: E402
    build_compressed_context,
    build_default_profile,
)


DEFAULT_GOLD_DIR = REPO_ROOT / "data" / "gold"
DEFAULT_REQUIREMENTS_PATH = DEFAULT_GOLD_DIR / "activity_asset_gold_requirements_multiasset.csv"
DEFAULT_ACTIVITY_SUMMARY_PATH = DEFAULT_GOLD_DIR / "activity_asset_gold_activity_multiasset_summary.csv"
DEFAULT_CANDIDATE_REVIEW_PATH = DEFAULT_GOLD_DIR / "activity_asset_gold_candidate_review.csv"

ASSET_TYPES = (
    "crane",
    "hoist",
    "loading_bay",
    "ewp",
    "concrete_pump",
    "excavator",
    "forklift",
    "telehandler",
    "compactor",
)


@dataclass(frozen=True)
class MultiLabelCase:
    activity_number: str
    activity_name: str
    expected_asset_types: frozenset[str]
    predicted_asset_types: frozenset[str]
    role_by_asset_type: dict[str, str]

    @property
    def true_positive_asset_types(self) -> frozenset[str]:
        return self.expected_asset_types & self.predicted_asset_types

    @property
    def false_negative_asset_types(self) -> frozenset[str]:
        return self.expected_asset_types - self.predicted_asset_types

    @property
    def false_positive_asset_types(self) -> frozenset[str]:
        return self.predicted_asset_types - self.expected_asset_types

    @property
    def is_exact_match(self) -> bool:
        return self.expected_asset_types == self.predicted_asset_types

    @property
    def has_any_required_hit(self) -> bool:
        return bool(self.true_positive_asset_types)


@dataclass(frozen=True)
class HourCase:
    activity_number: str
    activity_name: str
    asset_type: str
    asset_role: str
    duration_days: int
    reviewed_total_hours: float
    fallback_total_hours: float

    @property
    def error(self) -> float:
        return self.fallback_total_hours - self.reviewed_total_hours

    @property
    def absolute_error(self) -> float:
        return abs(self.error)


def _canonical_text(value: object) -> str:
    return str(value or "").strip().lower()


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 1) -> int:
    try:
        return max(1, int(float(value or default)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: object) -> bool:
    return _canonical_text(value) in {"true", "1", "yes", "y"}


def _split_asset_set(value: object) -> frozenset[str]:
    text = str(value or "").strip()
    if not text:
        return frozenset()
    return frozenset(_canonical_text(part) for part in text.split("|") if _canonical_text(part))


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_activity_cases_from_current_keyword_classifier(
    activity_summary_rows: Iterable[dict[str, str]],
    requirement_rows: Iterable[dict[str, str]],
) -> list[MultiLabelCase]:
    role_by_activity: dict[str, dict[str, str]] = defaultdict(dict)
    for row in requirement_rows:
        activity_number = str(row.get("activity_number") or "")
        asset_type = _canonical_text(row.get("asset_type"))
        if asset_type:
            role_by_activity[activity_number][asset_type] = _canonical_text(row.get("asset_role"))

    cases: list[MultiLabelCase] = []
    for row in activity_summary_rows:
        activity_number = str(row.get("activity_number") or "")
        activity_name = str(row.get("activity_name") or "")
        expected = _split_asset_set(row.get("reviewed_asset_types"))
        predicted = keyword_classify_activity_name(activity_name)
        predicted_set = frozenset({predicted}) if predicted and predicted != "none" else frozenset()
        cases.append(
            MultiLabelCase(
                activity_number=activity_number,
                activity_name=activity_name,
                expected_asset_types=expected,
                predicted_asset_types=predicted_set,
                role_by_asset_type=role_by_activity.get(activity_number, {}),
            )
        )
    return cases


def build_activity_cases_from_runtime_deterministic_extractor(
    activity_summary_rows: Iterable[dict[str, str]],
    requirement_rows: Iterable[dict[str, str]],
) -> list[MultiLabelCase]:
    role_by_activity: dict[str, dict[str, str]] = defaultdict(dict)
    for row in requirement_rows:
        activity_number = str(row.get("activity_number") or "")
        asset_type = _canonical_text(row.get("asset_type"))
        if asset_type:
            role_by_activity[activity_number][asset_type] = _canonical_text(row.get("asset_role"))

    cases: list[MultiLabelCase] = []
    for row in activity_summary_rows:
        activity_number = str(row.get("activity_number") or "")
        activity_name = str(row.get("activity_name") or "")
        expected = _split_asset_set(row.get("reviewed_asset_types"))
        predicted_set = frozenset(
            item.asset_type
            for item in _deterministic_asset_requirement_candidates(
                {"id": activity_number, "name": activity_name},
            )
            if item.asset_type and item.asset_type != "none"
        )
        cases.append(
            MultiLabelCase(
                activity_number=activity_number,
                activity_name=activity_name,
                expected_asset_types=expected,
                predicted_asset_types=predicted_set,
                role_by_asset_type=role_by_activity.get(activity_number, {}),
            )
        )
    return cases


def build_candidate_generator_cases(
    candidate_review_rows: Iterable[dict[str, str]],
) -> list[MultiLabelCase]:
    grouped: dict[str, dict[str, object]] = {}
    for row in candidate_review_rows:
        activity_number = str(row.get("activity_number") or "")
        group = grouped.setdefault(
            activity_number,
            {
                "activity_name": str(row.get("activity_name") or ""),
                "expected": set(),
                "predicted": set(),
                "roles": {},
            },
        )

        generated_asset_type = _canonical_text(row.get("generated_asset_type"))
        if generated_asset_type and generated_asset_type != "none":
            group["predicted"].add(generated_asset_type)  # type: ignore[union-attr]

        reviewed_asset_type = _canonical_text(row.get("reviewed_asset_type"))
        if _as_bool(row.get("reviewed_is_required")) and reviewed_asset_type and reviewed_asset_type != "none":
            group["expected"].add(reviewed_asset_type)  # type: ignore[union-attr]
            group["roles"][reviewed_asset_type] = _canonical_text(row.get("reviewed_role"))  # type: ignore[index]

    return [
        MultiLabelCase(
            activity_number=activity_number,
            activity_name=str(group["activity_name"]),
            expected_asset_types=frozenset(group["expected"]),  # type: ignore[arg-type]
            predicted_asset_types=frozenset(group["predicted"]),  # type: ignore[arg-type]
            role_by_asset_type=dict(group["roles"]),  # type: ignore[arg-type]
        )
        for activity_number, group in sorted(grouped.items(), key=lambda kv: _as_int(kv[0]))
    ]


def score_multilabel_cases(cases: Iterable[MultiLabelCase]) -> dict[str, object]:
    cases = list(cases)
    total_activities = len(cases)
    exact_matches = sum(case.is_exact_match for case in cases)
    required_activities = [case for case in cases if case.expected_asset_types]
    any_required_hits = sum(case.has_any_required_hit for case in required_activities)
    expected_positive_count = sum(len(case.expected_asset_types) for case in cases)
    predicted_positive_count = sum(len(case.predicted_asset_types) for case in cases)
    true_positive_count = sum(len(case.true_positive_asset_types) for case in cases)
    one_label_recall_ceiling_count = sum(min(1, len(case.expected_asset_types)) for case in cases)
    one_label_exact_ceiling_count = sum(1 for case in cases if len(case.expected_asset_types) <= 1)

    precision = true_positive_count / predicted_positive_count if predicted_positive_count else 0.0
    recall = true_positive_count / expected_positive_count if expected_positive_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0

    per_asset: dict[str, dict[str, float | int]] = {}
    asset_types = sorted(
        set(ASSET_TYPES)
        | {asset for case in cases for asset in case.expected_asset_types}
        | {asset for case in cases for asset in case.predicted_asset_types}
    )
    for asset_type in asset_types:
        tp = sum(asset_type in case.true_positive_asset_types for case in cases)
        fp = sum(asset_type in case.false_positive_asset_types for case in cases)
        fn = sum(asset_type in case.false_negative_asset_types for case in cases)
        asset_precision = tp / (tp + fp) if tp + fp else 0.0
        asset_recall = tp / (tp + fn) if tp + fn else 0.0
        per_asset[asset_type] = {
            "gold": tp + fn,
            "predicted": tp + fp,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": asset_precision,
            "recall": asset_recall,
            "f1": (
                2 * asset_precision * asset_recall / (asset_precision + asset_recall)
                if asset_precision + asset_recall
                else 0.0
            ),
        }

    per_role: dict[str, dict[str, float | int]] = {}
    for role in ("lead", "support", "incidental"):
        gold = 0
        tp = 0
        for case in cases:
            for asset_type, asset_role in case.role_by_asset_type.items():
                if asset_role != role:
                    continue
                gold += 1
                if asset_type in case.predicted_asset_types:
                    tp += 1
        per_role[role] = {
            "gold": gold,
            "tp": tp,
            "recall": tp / gold if gold else 0.0,
        }

    false_negative_counts = Counter(
        asset_type
        for case in cases
        for asset_type in case.false_negative_asset_types
    )
    false_positive_counts = Counter(
        asset_type
        for case in cases
        for asset_type in case.false_positive_asset_types
    )

    return {
        "total_activities": total_activities,
        "required_activities": len(required_activities),
        "exact_matches": exact_matches,
        "any_required_hits": any_required_hits,
        "expected_positive_count": expected_positive_count,
        "predicted_positive_count": predicted_positive_count,
        "true_positive_count": true_positive_count,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "one_label_recall_ceiling": (
            one_label_recall_ceiling_count / expected_positive_count
            if expected_positive_count
            else 0.0
        ),
        "one_label_exact_match_ceiling": (
            one_label_exact_ceiling_count / total_activities
            if total_activities
            else 0.0
        ),
        "per_asset": per_asset,
        "per_role": per_role,
        "false_negative_counts": false_negative_counts,
        "false_positive_counts": false_positive_counts,
    }


def run_hour_backtest(
    requirement_rows: Iterable[dict[str, str]],
    *,
    duration_column: str = "profile_days_used",
) -> list[HourCase]:
    logging.getLogger("app.core.constants").setLevel(logging.ERROR)

    cases: list[HourCase] = []
    for row in requirement_rows:
        asset_type = _canonical_text(row.get("asset_type"))
        if not asset_type:
            continue
        activity_name = str(row.get("activity_name") or "")
        duration_days = _as_int(row.get(duration_column) or row.get("duration_days"))
        reviewed_total = _as_float(row.get("estimated_total_hours"))
        max_hours = get_max_hours_for_type(None, asset_type)
        compressed_context = build_compressed_context(activity_name)
        fallback_total, _distribution, _norm = build_default_profile(
            asset_type,
            duration_days,
            max_hours,
            compressed_context=compressed_context,
        )
        cases.append(
            HourCase(
                activity_number=str(row.get("activity_number") or ""),
                activity_name=activity_name,
                asset_type=asset_type,
                asset_role=_canonical_text(row.get("asset_role")),
                duration_days=duration_days,
                reviewed_total_hours=reviewed_total,
                fallback_total_hours=float(fallback_total),
            )
        )
    return cases


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    widths = [
        max(len(str(header)), *(len(str(row[idx])) for row in rows)) if rows else len(str(header))
        for idx, header in enumerate(headers)
    ]
    print("  ".join(str(header).ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(row)))


def _short_name(name: str, limit: int = 64) -> str:
    name = " ".join(name.split())
    if len(name) <= limit:
        return name
    return f"{name[: limit - 3]}..."


def print_multilabel_report(
    title: str,
    cases: list[MultiLabelCase],
    *,
    max_examples: int,
) -> None:
    metrics = score_multilabel_cases(cases)
    total = int(metrics["total_activities"])
    required = int(metrics["required_activities"])
    exact = int(metrics["exact_matches"])
    any_hits = int(metrics["any_required_hits"])
    true_positive_count = int(metrics["true_positive_count"])
    expected_positive_count = int(metrics["expected_positive_count"])
    predicted_positive_count = int(metrics["predicted_positive_count"])

    print(title)
    print(f"Activities: {total}")
    print(f"Activities with required assets: {required}")
    print(f"Exact set match: {exact}/{total} ({_pct(exact / total if total else 0.0)})")
    print(f"Any required asset hit: {any_hits}/{required} ({_pct(any_hits / required if required else 0.0)})")
    print(
        "Requirement recall: "
        f"{true_positive_count}/{expected_positive_count} ({_pct(float(metrics['recall']))})"
    )
    print(
        "Predicted-label precision: "
        f"{true_positive_count}/{predicted_positive_count} ({_pct(float(metrics['precision']))})"
    )
    print(f"F1: {_pct(float(metrics['f1']))}")
    print(
        "One-label backend ceiling: "
        f"recall {_pct(float(metrics['one_label_recall_ceiling']))}, "
        f"exact set {_pct(float(metrics['one_label_exact_match_ceiling']))}"
    )
    print()

    print("Per-asset precision/recall")
    per_asset = metrics["per_asset"]
    _print_table(
        ["asset_type", "gold", "pred", "tp", "fp", "fn", "precision", "recall", "f1"],
        [
            [
                asset_type,
                values["gold"],
                values["predicted"],
                values["tp"],
                values["fp"],
                values["fn"],
                _pct(float(values["precision"])),
                _pct(float(values["recall"])),
                _pct(float(values["f1"])),
            ]
            for asset_type, values in per_asset.items()
            if values["gold"] or values["predicted"]
        ],
    )
    print()

    print("Recall by role")
    per_role = metrics["per_role"]
    _print_table(
        ["role", "gold", "tp", "recall"],
        [
            [role, values["gold"], values["tp"], _pct(float(values["recall"]))]
            for role, values in per_role.items()
        ],
    )
    print()

    false_negative_counts: Counter[str] = metrics["false_negative_counts"]  # type: ignore[assignment]
    false_positive_counts: Counter[str] = metrics["false_positive_counts"]  # type: ignore[assignment]
    print("Missed required assets")
    _print_table(
        ["asset_type", "missed"],
        [[asset_type, count] for asset_type, count in sorted(false_negative_counts.items())],
    )
    print()
    print("Extra predicted assets")
    _print_table(
        ["asset_type", "extra"],
        [[asset_type, count] for asset_type, count in sorted(false_positive_counts.items())],
    )
    print()

    misses = [
        case
        for case in cases
        if case.expected_asset_types and case.false_negative_asset_types
    ]
    print(f"Missed/partial examples (first {min(max_examples, len(misses))})")
    _print_table(
        ["activity", "expected", "predicted", "missed", "name"],
        [
            [
                case.activity_number,
                "|".join(sorted(case.expected_asset_types)) or "-",
                "|".join(sorted(case.predicted_asset_types)) or "-",
                "|".join(sorted(case.false_negative_asset_types)) or "-",
                _short_name(case.activity_name),
            ]
            for case in misses[:max_examples]
        ],
    )
    print()


def print_hour_report(cases: list[HourCase], *, max_examples: int) -> None:
    print("Fallback hour backtest per reviewed asset requirement")
    print(f"Requirement rows: {len(cases)}")
    print(f"Overall MAE: {_mean(case.absolute_error for case in cases):.2f}h")
    print()

    by_asset: dict[str, list[HourCase]] = defaultdict(list)
    by_role: dict[str, list[HourCase]] = defaultdict(list)
    for case in cases:
        by_asset[case.asset_type].append(case)
        by_role[case.asset_role].append(case)

    print("MAE by asset type")
    _print_table(
        ["asset_type", "rows", "mae", "avg_reviewed", "avg_fallback"],
        [
            [
                asset_type,
                len(asset_cases),
                f"{_mean(case.absolute_error for case in asset_cases):.2f}h",
                f"{_mean(case.reviewed_total_hours for case in asset_cases):.2f}h",
                f"{_mean(case.fallback_total_hours for case in asset_cases):.2f}h",
            ]
            for asset_type, asset_cases in sorted(by_asset.items())
        ],
    )
    print()

    print("MAE by asset role")
    _print_table(
        ["role", "rows", "mae", "avg_reviewed", "avg_fallback"],
        [
            [
                role or "-",
                len(role_cases),
                f"{_mean(case.absolute_error for case in role_cases):.2f}h",
                f"{_mean(case.reviewed_total_hours for case in role_cases):.2f}h",
                f"{_mean(case.fallback_total_hours for case in role_cases):.2f}h",
            ]
            for role, role_cases in sorted(by_role.items())
        ],
    )
    print()

    overestimates = sorted(
        [case for case in cases if case.error > 0],
        key=lambda case: case.error,
        reverse=True,
    )
    underestimates = sorted(
        [case for case in cases if case.error < 0],
        key=lambda case: case.error,
    )

    print(f"Largest overestimates (first {min(max_examples, len(overestimates))})")
    _print_table(
        ["activity", "asset", "role", "reviewed", "fallback", "error", "name"],
        [
            [
                case.activity_number,
                case.asset_type,
                case.asset_role,
                f"{case.reviewed_total_hours:.1f}",
                f"{case.fallback_total_hours:.1f}",
                f"+{case.error:.1f}",
                _short_name(case.activity_name),
            ]
            for case in overestimates[:max_examples]
        ],
    )
    print()

    print(f"Largest underestimates (first {min(max_examples, len(underestimates))})")
    _print_table(
        ["activity", "asset", "role", "reviewed", "fallback", "error", "name"],
        [
            [
                case.activity_number,
                case.asset_type,
                case.asset_role,
                f"{case.reviewed_total_hours:.1f}",
                f"{case.fallback_total_hours:.1f}",
                f"{case.error:.1f}",
                _short_name(case.activity_name),
            ]
            for case in underestimates[:max_examples]
        ],
    )
    print()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest current backend behavior against reviewed multi-asset gold rows.",
    )
    parser.add_argument(
        "--requirements-path",
        type=Path,
        default=DEFAULT_REQUIREMENTS_PATH,
        help="Path to activity_asset_gold_requirements_multiasset.csv.",
    )
    parser.add_argument(
        "--activity-summary-path",
        type=Path,
        default=DEFAULT_ACTIVITY_SUMMARY_PATH,
        help="Path to activity_asset_gold_activity_multiasset_summary.csv.",
    )
    parser.add_argument(
        "--candidate-review-path",
        type=Path,
        default=DEFAULT_CANDIDATE_REVIEW_PATH,
        help="Path to activity_asset_gold_candidate_review.csv.",
    )
    parser.add_argument(
        "--duration-column",
        default="profile_days_used",
        choices=["profile_days_used", "duration_days"],
        help="Gold CSV duration column used for fallback profile generation.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=15,
        help="Maximum example rows printed for misses and large errors.",
    )
    parser.add_argument("--no-keyword", action="store_true", help="Skip current keyword classifier scoring.")
    parser.add_argument("--no-candidates", action="store_true", help="Skip generated candidate scoring.")
    parser.add_argument("--no-hours", action="store_true", help="Skip fallback hour scoring.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    requirement_rows = load_csv_rows(args.requirements_path)

    sections_printed = 0
    if not args.no_keyword:
        activity_summary_rows = load_csv_rows(args.activity_summary_path)
        keyword_cases = build_activity_cases_from_current_keyword_classifier(
            activity_summary_rows,
            requirement_rows,
        )
        print_multilabel_report(
            "Current keyword classifier vs multi-asset activity gold",
            keyword_cases,
            max_examples=args.max_examples,
        )
        sections_printed += 1

        print("=" * 80)
        print()
        runtime_cases = build_activity_cases_from_runtime_deterministic_extractor(
            activity_summary_rows,
            requirement_rows,
        )
        print_multilabel_report(
            "Runtime deterministic multi-asset extractor vs activity gold",
            runtime_cases,
            max_examples=args.max_examples,
        )
        sections_printed += 1

    if not args.no_candidates:
        if sections_printed:
            print("=" * 80)
            print()
        candidate_rows = load_csv_rows(args.candidate_review_path)
        candidate_cases = build_candidate_generator_cases(candidate_rows)
        print_multilabel_report(
            "Generated candidate set vs reviewed multi-asset gold",
            candidate_cases,
            max_examples=args.max_examples,
        )
        sections_printed += 1

    if not args.no_hours:
        if sections_printed:
            print("=" * 80)
            print()
        hour_cases = run_hour_backtest(requirement_rows, duration_column=args.duration_column)
        print_hour_report(hour_cases, max_examples=args.max_examples)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
