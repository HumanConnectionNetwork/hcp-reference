```python
from abc import ABC, abstractmethod
from uuid import UUID

from app.models.humanitarian_record import HumanitarianRecord


class RecordStorage(ABC):
    """
    Abstract storage contract for Humanitarian Records.

    Application services depend on this interface instead of a concrete
    persistence mechanism. This keeps protocol behavior independent from
    local storage technology.
    """

    @abstractmethod
    def create(self, record: HumanitarianRecord) -> HumanitarianRecord:
        """
        Persist a new Humanitarian Record.

        Raises:
            RecordAlreadyExistsError:
                If another record already uses the same identifier.

            StorageError:
                If the record cannot be persisted.
        """

    @abstractmethod
    def get_by_id(self, record_id: UUID) -> HumanitarianRecord:
        """
        Retrieve one Humanitarian Record by its identifier.

        Raises:
            RecordNotFoundError:
                If no record exists with the supplied identifier.

            StorageError:
                If persisted data cannot be read.
        """

    @abstractmethod
    def list_all(self) -> list[HumanitarianRecord]:
        """
        Return all locally stored Humanitarian Records.

        The returned collection should not expose mutable internal storage
        state.

        Raises:
            StorageError:
                If persisted data cannot be read.
        """

    @abstractmethod
    def exists(self, record_id: UUID) -> bool:
        """
        Return whether a Humanitarian Record exists locally.

        Raises:
            StorageError:
                If persisted data cannot be read.
        """
```

