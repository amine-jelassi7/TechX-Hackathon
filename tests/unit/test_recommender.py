import pytest
from pydantic import ValidationError

from app.schemas import FitQuizAnswers
from app.services import recommender

BASE = {
    "goal": "build-ai-products",
    "level": "beginner",
    "budget": "low",
    "format": "live-cohort",
    "audience": "student",
    "pace": "part-time",
}


def answers(**overrides) -> FitQuizAnswers:
    return FitQuizAnswers(**{**BASE, **overrides})


def test_catalog_validates_and_options_are_covered():
    catalog = recommender.load_catalog()
    assert len(catalog.products) >= 4
    ids = [p.id for p in catalog.products]
    assert len(ids) == len(set(ids))
    for question in recommender.QUESTIONS:
        key = question["key"]
        values = {option["value"] for option in question["options"]}
        for product in catalog.products:
            assert set(getattr(product.match, key)) <= values
        covered = set().union(*(set(getattr(product.match, key)) for product in catalog.products))
        assert covered == values


def test_flagship_answers_rank_bootcamp_first():
    results = recommender.recommend(answers())
    assert next(r.id for r in results) == "prod_ai_bootcamp"
    assert results[0].fit == 100
    assert results[0].band == "Strong fit"
    assert len(results) == 3
    assert [r.fit for r in results] == sorted([r.fit for r in results], reverse=True)


def test_data_answers_rank_the_sprint_first():
    results = recommender.recommend(answers(
        goal="analyze-data", level="some-code", budget="medium",
        format="live-cohort", audience="startup", pace="full-time",
    ))
    assert results[0].id == "prod_data_sprint"
    assert results[0].fit == 100


def test_automation_answers_rank_the_studio_first():
    results = recommender.recommend(answers(
        goal="automate-work", level="developer", budget="medium",
        format="self-paced", audience="startup", pace="weekend",
    ))
    assert results[0].id == "prod_automation_studio"


def test_enterprise_answers_rank_enablement_first():
    results = recommender.recommend(answers(
        goal="train-team", level="developer", budget="high",
        format="workshop", audience="enterprise", pace="full-time",
    ))
    assert results[0].id == "prod_enterprise_enablement"
    assert results[0].fit == 100


def test_perfect_match_lists_every_signal_as_a_reason():
    result = recommender.recommend(answers())[0]
    assert len(result.reasons) == len(recommender.WEIGHTS)
    assert all(":" in reason for reason in result.reasons)


def test_no_matching_signal_scores_zero():
    tracked = answers(
        goal="train-team", level="developer", budget="high",
        format="workshop", audience="enterprise", pace="full-time",
    )
    results = recommender.recommend(tracked, top_k=len(recommender.load_catalog().products))
    bootcamp = next(r for r in results if r.id == "prod_ai_bootcamp")
    assert bootcamp.fit == 0
    assert bootcamp.reasons == []


def test_top_k_limits_results():
    assert len(recommender.recommend(answers(), top_k=2)) == 2


def test_unknown_option_is_rejected():
    with pytest.raises(ValidationError):
        FitQuizAnswers(**{**BASE, "goal": "world-domination"})


def test_extra_answer_is_rejected():
    with pytest.raises(ValidationError):
        FitQuizAnswers(**{**BASE, "email": "prospect@example.test"})
