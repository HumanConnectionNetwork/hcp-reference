from typing import Annotated

from fastapi import Depends, Request

from app.services.case_builder import HumanitarianCaseBuilder
from app.services.correlation import CorrelationService
from app.services.records import RecordService
from app.services.search import SearchService
from app.storage.base import RecordStorage


def get_record_storage(
    request: Request,
) -> RecordStorage:
    """
    Devuelve el almacenamiento compartido por la aplicación FastAPI.

    La instancia se crea durante el lifespan de la aplicación y se guarda en:

        request.app.state.record_storage

    Según la configuración del entorno, puede ser:

        JSONRecordStorage
        PostgresRecordStorage

    Los routers y servicios no necesitan conocer la implementación concreta.
    """
    storage = getattr(
        request.app.state,
        "record_storage",
        None,
    )

    if not isinstance(
        storage,
        RecordStorage,
    ):
        raise RuntimeError(
            "RecordStorage has not been initialized in application state"
        )

    return storage


def get_record_service(
    storage: Annotated[
        RecordStorage,
        Depends(get_record_storage),
    ],
) -> RecordService:
    """
    Crea un RecordService conectado al almacenamiento de la aplicación.

    El servicio es ligero; la instancia compartida y costosa es el storage,
    especialmente su Engine y pool PostgreSQL.
    """
    return RecordService(
        storage
    )


def get_search_service(
    storage: Annotated[
        RecordStorage,
        Depends(get_record_storage),
    ],
) -> SearchService:
    """
    Crea un SearchService conectado al almacenamiento configurado.

    SearchService conserva la misma lógica independientemente de si los
    registros provienen de JSON o PostgreSQL.
    """
    return SearchService(
        storage
    )


def get_correlation_service() -> CorrelationService:
    """
    Devuelve el servicio de correlación.

    CorrelationService no administra conexiones ni estado persistente, por lo
    que puede crearse de forma segura para cada solicitud.
    """
    return CorrelationService()


def get_case_builder() -> HumanitarianCaseBuilder:
    """
    Devuelve el constructor de Humanitarian Cases.

    HumanitarianCaseBuilder no mantiene conexiones ni estado mutable entre
    solicitudes.
    """
    return HumanitarianCaseBuilder()


RecordStorageDependency = Annotated[
    RecordStorage,
    Depends(get_record_storage),
]

RecordServiceDependency = Annotated[
    RecordService,
    Depends(get_record_service),
]

SearchServiceDependency = Annotated[
    SearchService,
    Depends(get_search_service),
]

CorrelationServiceDependency = Annotated[
    CorrelationService,
    Depends(get_correlation_service),
]

CaseBuilderDependency = Annotated[
    HumanitarianCaseBuilder,
    Depends(get_case_builder),
]
