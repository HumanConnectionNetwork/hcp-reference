from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.postgres_store import metadata


config = context.config


if config.config_file_name is not None:
    fileConfig(
        config.config_file_name
    )


target_metadata = metadata


def get_database_url() -> str:
    """
    Devuelve la URL PostgreSQL utilizada por Alembic.

    La credencial se obtiene exclusivamente desde la variable de entorno
    DATABASE_URL. No se almacena en alembic.ini.

    Formato esperado:

        postgresql+psycopg://usuario:contraseña@host:puerto/base_de_datos

    Para Supabase puede utilizarse una conexión directa o la URL del pool,
    según el entorno de despliegue.
    """
    database_url = os.getenv(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required to run Alembic migrations"
        )

    return database_url


def process_revision_directives(
    migration_context,
    revision,
    directives,
) -> None:
    """
    Evita generar migraciones vacías durante --autogenerate.

    Si Alembic no detecta cambios reales en el esquema, elimina la directiva
    de migración para no llenar migrations/versions con archivos inútiles.
    """
    del migration_context
    del revision

    if not getattr(
        config.cmd_opts,
        "autogenerate",
        False,
    ):
        return

    script = directives[0]

    if script.upgrade_ops.is_empty():
        directives[:] = []


def run_migrations_offline() -> None:
    """
    Ejecuta migraciones sin crear una conexión activa.

    Alembic genera SQL a partir de la URL y del Metadata configurado.
    """
    database_url = get_database_url()

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        version_table="alembic_version",
        process_revision_directives=(
            process_revision_directives
        ),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Ejecuta migraciones utilizando una conexión PostgreSQL real.
    """
    configuration = (
        config.get_section(
            config.config_ini_section
        )
        or {}
    )

    configuration[
        "sqlalchemy.url"
    ] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
            version_table="alembic_version",
            process_revision_directives=(
                process_revision_directives
            ),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()