class HCPReferenceError(Exception):
    """
    Base exception for the HCP Reference Implementation.

    All application-specific exceptions should inherit from this class so
    callers can distinguish expected application errors from unexpected
    runtime failures.
    """


class RecordNotFoundError(HCPReferenceError):
    """
    Raised when a Humanitarian Record cannot be found by its identifier.
    """

    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        super().__init__(f"Humanitarian Record not found: {record_id}")


class RecordAlreadyExistsError(HCPReferenceError):
    """
    Raised when attempting to store a Humanitarian Record whose identifier
    already exists.
    """

    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        super().__init__(f"Humanitarian Record already exists: {record_id}")


class StorageError(HCPReferenceError):
    """
    Raised when the local storage implementation cannot read or write data.
    """


class InvalidStorageDataError(StorageError):
    """
    Raised when persisted data exists but does not contain a valid JSON
    collection of Humanitarian Records.
    """


class QueryProcessingError(HCPReferenceError):
    """
    Raised when a structured HCP Query cannot be processed.
    """


class CorrelationProcessingError(HCPReferenceError):
    """
    Raised when local correlation cannot be completed.
    """

