"""Connection settings, filters, mappings, and media catalog rows."""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    relationship,
    validates,
)

from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    McfCategoryFamily,
    McfChannel,
    Severity,
)

from .db_archive import ArchivedSource
from .db_core import (
    Base,
    _mcf_rule_pair_constraint,
    _string_enum,
)
from .db_policies import FilterPreferenceMode


class JellyfinSettingsRow(Base):
    """Singleton Editarr-to-Jellyfin connection configuration.

    The API key deliberately has no relationship or response serializer. It is
    stored server-side in the application data store and is only read by the
    Jellyfin adapter.
    """

    __tablename__ = "jellyfin_settings"

    id = Column(Integer, primary_key=True, default=1)
    server_url = Column(String, nullable=True)
    api_key = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class FilterSettingsRow(Base):
    __tablename__ = "filter_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_filter_settings_singleton_id"),
        CheckConstraint("revision >= 0", name="ck_filter_settings_revision"),
    )

    id = Column(Integer, primary_key=True, default=1)
    revision = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    # Applies to users whose own preference row has no mode.
    default_mode = Column(
        _string_enum(FilterPreferenceMode),
        nullable=False,
        default=FilterPreferenceMode.LIVE,
        server_default=FilterPreferenceMode.LIVE.value,
    )

    selection_rules = relationship(
        "FilterSelectionRuleRow",
        back_populates="settings",
        cascade="all, delete-orphan",
        order_by="FilterSelectionRuleRow.position",
    )
    severity_defaults = relationship(
        "McfSeverityDefaultRow",
        back_populates="settings",
        cascade="all, delete-orphan",
        order_by="McfSeverityDefaultRow.position",
    )


class FilterSelectionRuleRow(Base):
    __tablename__ = "filter_selection_rules"
    __table_args__ = (
        UniqueConstraint("settings_id", "position", name="uq_filter_rules_position"),
        CheckConstraint("position >= 0", name="ck_filter_rules_position"),
        CheckConstraint("disposition IN ('allow', 'deny')", name="ck_filter_rules_disposition"),
        CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name="ck_filter_rules_source",
        ),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_filter_rules_channel",
        ),
        _mcf_rule_pair_constraint("ck_filter_rules_subcategory_family"),
        CheckConstraint(
            "evidence_source IS NOT NULL OR category IS NOT NULL OR "
            "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
            "severity IS NOT NULL OR mcf_channel IS NOT NULL",
            name="ck_filter_rules_has_matcher",
        ),
    )

    id = Column(String, primary_key=True)
    settings_id = Column(
        Integer,
        ForeignKey("filter_settings.id", ondelete="CASCADE"),
        nullable=False,
        default=1,
    )
    position = Column(Integer, nullable=False)
    disposition = Column(String, nullable=False)
    evidence_source = Column(String, nullable=True)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    label_contains = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)

    settings = relationship("FilterSettingsRow", back_populates="selection_rules")

    @validates("subcategory")
    def validate_subcategory(self, key, value):
        if value is not None and value not in MCF_CATEGORY_TO_FAMILY:
            raise ValueError("Unknown MCF subcategory")
        return value


class McfSeverityDefaultRow(Base):
    __tablename__ = "mcf_severity_defaults"
    __table_args__ = (
        UniqueConstraint("settings_id", "position", name="uq_mcf_severity_position"),
        UniqueConstraint("settings_id", "subcategory", name="uq_mcf_severity_subcategory"),
        CheckConstraint("position >= 0", name="ck_mcf_severity_position"),
    )

    settings_id = Column(
        Integer, ForeignKey("filter_settings.id", ondelete="CASCADE"), primary_key=True
    )
    subcategory = Column(String, primary_key=True)
    position = Column(Integer, nullable=False)
    severity = Column(_string_enum(Severity), nullable=False)

    settings = relationship("FilterSettingsRow", back_populates="severity_defaults")

    @validates("subcategory")
    def validate_subcategory(self, key, value):
        if value not in MCF_CATEGORY_TO_FAMILY:
            raise ValueError("Unknown MCF subcategory")
        return value


class PathMappingRow(Base):
    __tablename__ = "path_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    remote_path = Column(String, nullable=False)
    local_path = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class MediaItemRow(Base):
    """Stable Jellyfin media identity, separate from a scan execution."""

    __tablename__ = "media_items"
    __table_args__ = (UniqueConstraint("server_id", "jellyfin_id"),)

    id = Column(String, primary_key=True)
    server_id = Column(String, nullable=False)
    jellyfin_id = Column(String, nullable=False)
    library_id = Column(String, nullable=True)
    item_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    path = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    confirmed_identity_json = Column(Text, nullable=True)
    archived_source = Column(_string_enum(ArchivedSource), nullable=True)
