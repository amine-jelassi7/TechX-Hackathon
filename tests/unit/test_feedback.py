import pytest
from pydantic import ValidationError

from app.services.feedback import WEIGHTS, FeedbackInput, score_feedback


def test_feedback_weights_sum_to_one_hundred():
    assert sum(WEIGHTS.values()) == 100


def test_all_fives_score_one_hundred():
    score, dimensions, category = score_feedback(
        {**{k: 5 for k in WEIGHTS if k != "recommend"}, "recommend": 10}
    )
    assert score == 100
    assert all(value == 100 for value in dimensions.values())
    assert category == "promoter"


def test_all_ones_and_zero_nps_score_zero():
    score, _, category = score_feedback(
        {**{k: 1 for k in WEIGHTS if k != "recommend"}, "recommend": 0}
    )
    assert score == 0
    assert category == "detractor"


def test_missing_values_renormalize_weights():
    score, _, _ = score_feedback({"ease": 5, "recommend": 10})
    assert score == 100


@pytest.mark.parametrize(("value", "category"), [(6, "detractor"), (7, "passive"), (9, "promoter")])
def test_nps_categories(value, category):
    _, _, got = score_feedback({"recommend": value})
    assert got == category


def test_feedback_rejects_invalid_scale_and_unknown_fields():
    with pytest.raises(ValidationError):
        FeedbackInput(session_id="session_1", ease=6)
    with pytest.raises(ValidationError):
        FeedbackInput(session_id="session_1", ease=4, card_number="no")


def test_feedback_requires_at_least_one_numeric_answer():
    with pytest.raises(ValueError, match="at least one"):
        score_feedback({})
