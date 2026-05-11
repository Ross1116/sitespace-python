from scripts.backtest_activity_asset_multiasset_gold import (
    MultiLabelCase,
    build_candidate_generator_cases,
    run_hour_backtest,
    score_multilabel_cases,
)


def test_multilabel_scoring_tracks_partial_hits_and_role_recall():
    cases = [
        MultiLabelCase(
            activity_number="1",
            activity_name="Install panels",
            expected_asset_types=frozenset({"crane", "ewp"}),
            predicted_asset_types=frozenset({"crane"}),
            role_by_asset_type={"crane": "lead", "ewp": "support"},
        ),
        MultiLabelCase(
            activity_number="2",
            activity_name="Milestone",
            expected_asset_types=frozenset(),
            predicted_asset_types=frozenset(),
            role_by_asset_type={},
        ),
    ]

    metrics = score_multilabel_cases(cases)

    assert metrics["exact_matches"] == 1
    assert metrics["true_positive_count"] == 1
    assert metrics["expected_positive_count"] == 2
    assert metrics["recall"] == 0.5
    assert metrics["precision"] == 1.0
    assert metrics["per_role"]["lead"]["recall"] == 1.0
    assert metrics["per_role"]["support"]["recall"] == 0.0


def test_candidate_generator_cases_include_dropped_rows_as_false_positives():
    rows = [
        {
            "activity_number": "10",
            "activity_name": "Concrete pour",
            "generated_asset_type": "concrete_pump",
            "reviewed_asset_type": "concrete_pump",
            "reviewed_role": "lead",
            "reviewed_is_required": "True",
        },
        {
            "activity_number": "10",
            "activity_name": "Concrete pour",
            "generated_asset_type": "telehandler",
            "reviewed_asset_type": "telehandler",
            "reviewed_role": "incidental",
            "reviewed_is_required": "False",
        },
    ]

    cases = build_candidate_generator_cases(rows)
    metrics = score_multilabel_cases(cases)

    assert cases[0].expected_asset_types == frozenset({"concrete_pump"})
    assert cases[0].predicted_asset_types == frozenset({"concrete_pump", "telehandler"})
    assert metrics["false_positive_counts"]["telehandler"] == 1


def test_hour_backtest_scores_each_reviewed_requirement_row():
    rows = [
        {
            "activity_number": "20",
            "activity_name": "Concrete pour floor slab Zone [A] pour (1)",
            "asset_type": "concrete_pump",
            "asset_role": "lead",
            "estimated_total_hours": "8.0",
            "profile_days_used": "1",
            "duration_days": "1",
        }
    ]

    cases = run_hour_backtest(rows)

    assert len(cases) == 1
    assert cases[0].asset_type == "concrete_pump"
    assert cases[0].reviewed_total_hours == 8.0
    assert cases[0].fallback_total_hours > 0.0
    assert abs(cases[0].absolute_error - abs(cases[0].fallback_total_hours - 8.0)) < 1e-6
