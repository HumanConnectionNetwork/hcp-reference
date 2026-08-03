from abc import ABC, abstractmethod
from collections.abc import Iterable
from uuid import UUID

from app.models.humanitarian_record import HumanitarianRecord


class RecordStorage(ABC):
    """
    Contrato abstracto de almacenamiento para Humanitarian Records.

    Los servicios de aplicación dependen de esta interfaz y no de una
    tecnología concreta de persistencia.

    Esto permite utilizar, sin modificar la lógica HCP:

    - almacenamiento JSON para desarrollo y compatibilidad;
    - PostgreSQL para producción;
    - implementaciones temporales para pruebas;
    - otros nodos de almacenamiento en el futuro.

    La capa de almacenamiento es responsable únicamente de persistir y
    recuperar Humanitarian Records canónicos.

    No debe:

    - ejecutar correlaciones;
    - construir Humanitarian Cases;
    - interpretar identidades;
    - transformar formularios;
    - aplicar reglas de presentación;
    - modificar silenciosamente el contenido del registro.
    """

    @abstractmethod
    def create(
        self,
        record: HumanitarianRecord,
    ) -> HumanitarianRecord:
        """
        Persiste un nuevo Humanitarian Record.

        La implementación debe almacenar el registro completo y devolver
        una representación equivalente al documento persistido.

        El método debe ser atómico: si ocurre un error, el registro no debe
        quedar guardado parcialmente.

        Args:
            record:
                Humanitarian Record canónico que será persistido.

        Returns:
            El Humanitarian Record persistido.

        Raises:
            RecordAlreadyExistsError:
                Si otro registro ya utiliza el mismo identificador.

            StorageError:
                Si el registro no puede persistirse o si el almacenamiento
                no está disponible.
        """

    @abstractmethod
    def get_by_id(
        self,
        record_id: UUID,
    ) -> HumanitarianRecord:
        """
        Recupera un Humanitarian Record por su identificador.

        La implementación debe reconstruir y validar el documento canónico
        antes de devolverlo. Los datos almacenados nunca deben entregarse como
        un diccionario sin validar.

        Args:
            record_id:
                UUID del reporte solicitado.

        Returns:
            Humanitarian Record correspondiente al identificador.

        Raises:
            RecordNotFoundError:
                Si no existe un registro con el identificador indicado.

            StorageError:
                Si los datos persistidos no pueden leerse, reconstruirse o
                validarse.
        """

    @abstractmethod
    def list_all(
        self,
    ) -> list[HumanitarianRecord]:
        """
        Devuelve todos los Humanitarian Records disponibles en el nodo local.

        Este método permanece en el contrato para conservar compatibilidad con
        el motor actual de búsqueda y correlación.

        La colección devuelta:

        - no debe exponer estructuras internas mutables;
        - debe contener modelos HumanitarianRecord validados;
        - debe tener un orden determinístico cuando la implementación pueda
          garantizarlo.

        En PostgreSQL, una implementación inicial puede ordenar por
        observation.observed_at y posteriormente sustituirse por consultas
        especializadas e indexadas sin cambiar los servicios superiores.

        Returns:
            Lista independiente de Humanitarian Records validados.

        Raises:
            StorageError:
                Si los registros no pueden recuperarse o reconstruirse.
        """

    @abstractmethod
    def exists(
        self,
        record_id: UUID,
    ) -> bool:
        """
        Indica si existe un Humanitarian Record con el UUID suministrado.

        La implementación debe utilizar una consulta de existencia eficiente.
        En PostgreSQL no debe cargar el documento JSONB completo únicamente
        para responder esta operación.

        Args:
            record_id:
                UUID que será comprobado.

        Returns:
            True cuando el registro existe; False en caso contrario.

        Raises:
            StorageError:
                Si no fue posible consultar el almacenamiento.
        """

    def create_many(
        self,
        records: Iterable[HumanitarianRecord],
    ) -> list[HumanitarianRecord]:
        """
        Persiste varios Humanitarian Records.

        Esta implementación predeterminada utiliza create() individualmente
        para mantener compatibilidad inmediata con JsonRecordStorage y otras
        implementaciones existentes.

        PostgresRecordStorage podrá sobrescribir este método para ejecutar una
        importación transaccional y eficiente, especialmente durante la
        migración desde hcp_records.json.

        Importante:
            La implementación predeterminada no garantiza que todo el lote se
            revierta si uno de los registros falla. Una implementación de base
            de datos debe sobrescribirla cuando necesite atomicidad completa.

        Args:
            records:
                Colección iterable de registros canónicos.

        Returns:
            Lista de registros persistidos en el mismo orden recibido.

        Raises:
            RecordAlreadyExistsError:
                Si algún identificador ya existe.

            StorageError:
                Si alguno de los registros no puede persistirse.
        """
        created_records: list[HumanitarianRecord] = []

        for record in records:
            created_records.append(
                self.create(record)
            )

        return created_records

    def count(
        self,
    ) -> int:
        """
        Devuelve la cantidad total de registros disponibles.

        La implementación predeterminada conserva compatibilidad usando
        list_all().

        PostgresRecordStorage debe sobrescribir este método con una consulta
        COUNT(*) para evitar cargar todos los registros en memoria.

        Returns:
            Número total de Humanitarian Records.

        Raises:
            StorageError:
                Si el almacenamiento no puede consultarse.
        """
        return len(
            self.list_all()
        )

    def close(
        self,
    ) -> None:
        """
        Libera recursos administrados por la implementación.

        Las implementaciones que no mantienen conexiones abiertas pueden
        conservar este comportamiento vacío.

        Una implementación PostgreSQL puede sobrescribirlo para cerrar:

        - conexiones;
        - pools;
        - sesiones;
        - otros recursos de infraestructura.

        El método debe poder ejecutarse más de una vez sin producir errores.
        """

    def __enter__(
        self,
    ) -> "RecordStorage":
        """
        Permite utilizar el almacenamiento como administrador de contexto.
        """
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """
        Libera los recursos al abandonar un administrador de contexto.
        """
        self.close()
