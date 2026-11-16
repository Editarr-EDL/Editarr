"""Shared SQLAlchemy metadata and schema constraints."""


from sqlalchemy import (
    CheckConstraint,
    Enum,
    column,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
)

from scanner.models import (
    MCF_CATEGORY_REGISTRY,
)


class Base(DeclarativeBase):
    pass


def _string_enum(enum_type):
    return Enum(
        enum_type,
        values_callable=lambda values: [value.value for value in values],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


def _mcf_pair_constraint(name: str) -> CheckConstraint:
    """Build a database check limiting category and subcategory to registry pairs."""
    family_clauses = []
    for family, categories in MCF_CATEGORY_REGISTRY.items():
        tokens = ", ".join(f"'{token}'" for token, _label in categories)
        family_clauses.append(
            f"(category = '{family.value}' AND subcategory IN ({tokens}))"
        )
    return CheckConstraint(
        "(category IS NULL AND subcategory IS NULL) OR "
        "(category IS NOT NULL AND subcategory IS NOT NULL AND "
        f"({' OR '.join(family_clauses)}))",
        name=name,
    )


def _mcf_rule_pair_constraint(name: str) -> CheckConstraint:
    family_clauses = []
    for family, categories in MCF_CATEGORY_REGISTRY.items():
        tokens = ", ".join(f"'{token}'" for token, _label in categories)
        family_clauses.append(
            f"(category = '{family.value}' AND subcategory IN ({tokens}))"
        )
    return CheckConstraint(
        "subcategory IS NULL OR (category IS NOT NULL AND "
        f"({' OR '.join(family_clauses)}))",
        name=name,
    )


def _name_constraint(field: str, *, required: bool, name: str) -> CheckConstraint:
    """Enforce the normalized name contract even for direct SQLite writes."""
    value = column(field)
    whitespace = "".join(chr(code) for code in range(0x3001) if chr(code).isspace())
    controls = "*[" + chr(1) + "-" + chr(31) + chr(127) + "-" + chr(159) + "]*"
    valid = (
        (func.length(value) >= 1)
        & (func.length(value) <= 100)
        & (value == func.trim(value, whitespace))
        & (func.instr(value, func.char(0)) == 0)
        & ~value.op("GLOB")(controls)
    )
    return CheckConstraint(valid if required else value.is_(None) | valid, name=name)


