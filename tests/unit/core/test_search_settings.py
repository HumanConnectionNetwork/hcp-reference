import pytest

from app.core.search_settings import SearchSettings


def test_search_settings_uses_expected_defaults() -> None:
    """
    SearchSettings should expose conservative default limits for candidate
    retrieval.
    """
    settings = SearchSettings()

    assert settings.candidate_fetch_limit == 100
    assert settings.candidate_multiplier == 5
    assert settings.max_candidate_fetch_limit == 500


def test_search_settings_accepts_valid_custom_values() -> None:
    """
    SearchSettings should allow node-specific candidate-window values when
    every configured limit is valid.
    """
    settings = SearchSettings(
        candidate_fetch_limit=50,
        candidate_multiplier=3,
        max_candidate_fetch_limit=300,
    )

    assert settings.candidate_fetch_limit == 50
    assert settings.candidate_multiplier == 3
    assert settings.max_candidate_fetch_limit == 300


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_message"),
    [
        (
            "candidate_fetch_limit",
            0,
            "candidate_fetch_limit must be greater than or equal to 1",
        ),
        (
            "candidate_multiplier",
            0,
            "candidate_multiplier must be greater than or equal to 1",
        ),
    ],
)
def test_search_settings_rejects_non_positive_values(
    field_name: str,
    field_value: int,
    expected_message: str,
) -> None:
    """
    Candidate retrieval values that control minimum size and scaling must be
    positive.
    """
    values = {
        "candidate_fetch_limit": 100,
        "candidate_multiplier": 5,
        "max_candidate_fetch_limit": 500,
    }

    values[field_name] = field_value

    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        SearchSettings(**values)


def test_search_settings_rejects_maximum_below_default_limit() -> None:
    """
    The maximum candidate limit cannot be smaller than the baseline candidate
    retrieval limit.
    """
    with pytest.raises(
        ValueError,
        match=(
            "max_candidate_fetch_limit must be greater than or equal to "
            "candidate_fetch_limit"
        ),
    ):
        SearchSettings(
            candidate_fetch_limit=100,
            candidate_multiplier=5,
            max_candidate_fetch_limit=99,
        )


def test_search_settings_accepts_maximum_equal_to_default_limit() -> None:
    """
    A fixed-size candidate window is valid when the maximum equals the
    baseline limit.
    """
    settings = SearchSettings(
        candidate_fetch_limit=100,
        candidate_multiplier=5,
        max_candidate_fetch_limit=100,
    )

    assert settings.candidate_fetch_limit == 100
    assert settings.max_candidate_fetch_limit == 100


def test_search_settings_is_immutable() -> None:
    """
    Search configuration should not change after service construction.
    """
    settings = SearchSettings()

    with pytest.raises(
        AttributeError,
    ):
        settings.candidate_fetch_limit = 200  # type: ignore[misc]
