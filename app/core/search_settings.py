from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchSettings:
    """
    Runtime configuration for preliminary HCP candidate retrieval.

    These values control only how many records SearchService requests from
    RecordStorage before applying semantic candidate evaluation.

    They do not modify:

    - HCP correlation rules;
    - compatibility percentages;
    - spatial or descriptive evidence;
    - Humanitarian Case construction;
    - persistence-specific filtering logic.
    """

    candidate_fetch_limit: int = 100
    candidate_multiplier: int = 5
    max_candidate_fetch_limit: int = 500

    def __post_init__(self) -> None:
        """
        Validate the candidate-window configuration.
        """
        if self.candidate_fetch_limit < 1:
            raise ValueError(
                "candidate_fetch_limit must be greater than or equal to 1"
            )

        if self.candidate_multiplier < 1:
            raise ValueError(
                "candidate_multiplier must be greater than or equal to 1"
            )

        if self.max_candidate_fetch_limit < self.candidate_fetch_limit:
            raise ValueError(
                "max_candidate_fetch_limit must be greater than or equal to "
                "candidate_fetch_limit"
            )
