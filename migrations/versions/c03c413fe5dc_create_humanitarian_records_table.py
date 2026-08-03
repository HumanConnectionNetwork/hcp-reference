"""create humanitarian records table

Revision ID: c03c413fe5dc
Revises:
Create Date: 2026-08-03 14:35:05.675973
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c03c413fe5dc"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """
    Create the initial PostgreSQL schema for Humanitarian Records.

    The complete canonical HCP document is stored in record_payload as JSONB.
    Important descriptive, spatial and temporal fields are also projected
    into relational columns for indexed candidate selection.
    """
    op.create_table(
        "humanitarian_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "source_client",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "subject_type",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "reported_label",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "reported_label_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "estimated_age",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "recognition_features",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "recognition_features_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "species",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "species_normalized",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column(
            "animal_size",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "breed",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "breed_normalized",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "event_type",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "reported_by",
            sa.String(length=80),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "country_code",
            sa.String(length=2),
            nullable=True,
        ),
        sa.Column(
            "admin_level_1",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "admin_level_1_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "admin_level_2",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "admin_level_2_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "locality",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "locality_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "district",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "district_normalized",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "legacy_reported_location",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "record_payload",
            postgresql.JSONB(
                astext_type=sa.Text(),
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_humanitarian_records",
        ),
    )

    op.create_index(
        "ix_humanitarian_records_country",
        "humanitarian_records",
        ["country_code"],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_country_region",
        "humanitarian_records",
        [
            "country_code",
            "admin_level_1_normalized",
        ],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_estimated_age",
        "humanitarian_records",
        ["estimated_age"],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_observed_at",
        "humanitarian_records",
        ["observed_at"],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_reported_label_normalized",
        "humanitarian_records",
        ["reported_label_normalized"],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_spatial_context",
        "humanitarian_records",
        [
            "subject_type",
            "country_code",
            "admin_level_1_normalized",
            "locality_normalized",
        ],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_spatial_time",
        "humanitarian_records",
        [
            "subject_type",
            "country_code",
            "admin_level_1_normalized",
            "locality_normalized",
            "observed_at",
        ],
        unique=False,
    )

    op.create_index(
        "ix_humanitarian_records_subject_type",
        "humanitarian_records",
        ["subject_type"],
        unique=False,
    )


def downgrade() -> None:
    """
    Remove the initial Humanitarian Records schema.
    """
    op.drop_index(
        "ix_humanitarian_records_subject_type",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_spatial_time",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_spatial_context",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_reported_label_normalized",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_observed_at",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_estimated_age",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_country_region",
        table_name="humanitarian_records",
    )

    op.drop_index(
        "ix_humanitarian_records_country",
        table_name="humanitarian_records",
    )

    op.drop_table(
        "humanitarian_records"
    )