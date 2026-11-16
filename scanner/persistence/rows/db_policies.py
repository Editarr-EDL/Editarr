"""Per-user filter policy, local media access, and export scope storage vocabulary."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import validates

from scanner.models import MCF_CATEGORY_TO_FAMILY, McfCategoryFamily, McfChannel, Severity

from .db_core import Base, _mcf_rule_pair_constraint, _string_enum


class ExportScope(StrEnum):
    """Where an export record's file lives.

    `legacy` records adjacent `Movie.edl` or `Movie.mcf` files. The current plugin
    serves adjacent EDL only; the multi-user selector will ignore legacy sidecars.
    `default` and `personal` are the MU-01 export layout.
    """

    LEGACY = "legacy"
    DEFAULT = "default"
    PERSONAL = "personal"


class FilterPreferenceMode(StrEnum):
    LIVE = "live"
    COPY = "copy"


class FilterEnforcementKind(StrEnum):
    """An enforced instance selection rule (target is its ID) or severity (a subcategory)."""

    SELECTION_RULE = "selection_rule"
    SEVERITY = "severity"


class MediaSourceKind(StrEnum):
    """A local media source. Jellyfin libraries are the only kind."""

    JELLYFIN_LIBRARY = "jellyfin_library"


LOCAL_MEDIA_DEFAULT_SCOPE_ID = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _known_subcategory(value):
    if value is not None and value not in MCF_CATEGORY_TO_FAMILY:
        raise ValueError("Unknown MCF subcategory")
    return value


def _preference_owner():
    return Column(
        Integer,
        ForeignKey("user_filter_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserFilterPreferenceRow(Base):
    """A user's filter mode and revisions, created with the account.

    The account starts in `filter_settings.default_mode`. `revision` guards edits;
    `policy_revision` changes with the user's enforced policy and is what export
    provenance records.
    """

    __tablename__ = "user_filter_preferences"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_user_filter_preferences_revision"),
        CheckConstraint("policy_revision >= 0", name="ck_user_filter_preferences_policy_revision"),
    )

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    mode = Column(_string_enum(FilterPreferenceMode), nullable=False)
    revision = Column(Integer, nullable=False, default=0)
    policy_revision = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=_now)


class UserFilterEnforcementRow(Base):
    """One instance rule or subcategory severity the owner enforces on a user."""

    __tablename__ = "user_filter_enforcements"
    __table_args__ = (
        CheckConstraint("length(target) > 0", name="ck_user_filter_enforcements_target"),
    )

    user_id = Column(
        Integer,
        ForeignKey("user_filter_preferences.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind = Column(_string_enum(FilterEnforcementKind), primary_key=True)
    target = Column(String, primary_key=True)


class UserFilterCopyRuleRow(Base):
    """One ordered selection rule of a user's independent copy of the instance defaults.

    Fields and checks match `filter_selection_rules`. A copied rule may keep its
    instance rule ID, so the key is user and ID; nothing links it to the instance row.
    """

    __tablename__ = "user_filter_copy_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "position", name="uq_user_filter_copy_rules_position"),
        CheckConstraint("position >= 0", name="ck_user_filter_copy_rules_position"),
        CheckConstraint(
            "disposition IN ('allow', 'deny')", name="ck_user_filter_copy_rules_disposition"
        ),
        CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name="ck_user_filter_copy_rules_source",
        ),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_user_filter_copy_rules_channel",
        ),
        _mcf_rule_pair_constraint("ck_user_filter_copy_rules_subcategory_family"),
        CheckConstraint(
            "evidence_source IS NOT NULL OR category IS NOT NULL OR "
            "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
            "severity IS NOT NULL OR mcf_channel IS NOT NULL",
            name="ck_user_filter_copy_rules_has_matcher",
        ),
    )

    user_id = _preference_owner()
    id = Column(String, primary_key=True)
    position = Column(Integer, nullable=False)
    disposition = Column(String, nullable=False)
    evidence_source = Column(String, nullable=True)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    label_contains = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)

    @validates("subcategory")
    def validate_subcategory(self, _key, value):
        return _known_subcategory(value)


class UserFilterCopySeverityRow(Base):
    """One subcategory severity of a user's independent copy, ordered as instance defaults are."""

    __tablename__ = "user_filter_copy_severities"
    __table_args__ = (
        UniqueConstraint("user_id", "position", name="uq_user_filter_copy_severities_position"),
        CheckConstraint("position >= 0", name="ck_user_filter_copy_severities_position"),
    )

    user_id = _preference_owner()
    subcategory = Column(String, primary_key=True)
    position = Column(Integer, nullable=False)
    severity = Column(_string_enum(Severity), nullable=False)

    @validates("subcategory")
    def validate_subcategory(self, _key, value):
        return _known_subcategory(value)


class UserFilterSeverityOverrideRow(Base):
    """A live-mode user's severity for one subcategory in place of the instance default."""

    __tablename__ = "user_filter_severity_overrides"

    user_id = _preference_owner()
    subcategory = Column(String, primary_key=True)
    severity = Column(_string_enum(Severity), nullable=False)

    @validates("subcategory")
    def validate_subcategory(self, _key, value):
        return _known_subcategory(value)


class UserFilterRuleOverrideRow(Base):
    """A live-mode user's change to one inherited instance selection rule.

    `disabled` switches the rule off. A non-null `disposition` means the user edited
    the rule in place, so later owner edits to it stop reaching this user; the fields
    then carry the instance rule checks. Deleting the row resets the rule to the owner's.
    """

    __tablename__ = "user_filter_rule_overrides"
    __table_args__ = (
        CheckConstraint(
            "disabled = 1 OR disposition IS NOT NULL", name="ck_user_filter_rule_overrides_change"
        ),
        CheckConstraint(
            "disposition IS NULL OR disposition IN ('allow', 'deny')",
            name="ck_user_filter_rule_overrides_disposition",
        ),
        CheckConstraint(
            "disposition IS NOT NULL OR (evidence_source IS NULL AND category IS NULL AND "
            "subcategory IS NULL AND label_contains IS NULL AND severity IS NULL AND "
            "mcf_channel IS NULL)",
            name="ck_user_filter_rule_overrides_fields",
        ),
        CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name="ck_user_filter_rule_overrides_source",
        ),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_user_filter_rule_overrides_channel",
        ),
        _mcf_rule_pair_constraint("ck_user_filter_rule_overrides_subcategory_family"),
        CheckConstraint(
            "disposition IS NULL OR evidence_source IS NOT NULL OR category IS NOT NULL OR "
            "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
            "severity IS NOT NULL OR mcf_channel IS NOT NULL",
            name="ck_user_filter_rule_overrides_has_matcher",
        ),
    )

    user_id = _preference_owner()
    rule_id = Column(String, primary_key=True)
    disabled = Column(Boolean, nullable=False)
    disposition = Column(String, nullable=True)
    evidence_source = Column(String, nullable=True)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    label_contains = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)

    @validates("subcategory")
    def validate_subcategory(self, _key, value):
        return _known_subcategory(value)


class UserFilterPersonalRuleRow(Base):
    """A selection rule a live-mode user added, in the user's display order.

    Fields and checks match `filter_selection_rules`. Its ID never equals an instance
    rule ID, so it can join the inherited rules in one effective list.
    """

    __tablename__ = "user_filter_personal_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "position", name="uq_user_filter_personal_rules_position"),
        CheckConstraint("position >= 0", name="ck_user_filter_personal_rules_position"),
        CheckConstraint(
            "disposition IN ('allow', 'deny')", name="ck_user_filter_personal_rules_disposition"
        ),
        CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN ('audio', 'visual')",
            name="ck_user_filter_personal_rules_source",
        ),
        CheckConstraint(
            "mcf_channel IS NULL OR mcf_channel IN ('both', 'video', 'audio')",
            name="ck_user_filter_personal_rules_channel",
        ),
        _mcf_rule_pair_constraint("ck_user_filter_personal_rules_subcategory_family"),
        CheckConstraint(
            "evidence_source IS NOT NULL OR category IS NOT NULL OR "
            "subcategory IS NOT NULL OR label_contains IS NOT NULL OR "
            "severity IS NOT NULL OR mcf_channel IS NOT NULL",
            name="ck_user_filter_personal_rules_has_matcher",
        ),
    )

    user_id = _preference_owner()
    id = Column(String, primary_key=True)
    position = Column(Integer, nullable=False)
    disposition = Column(String, nullable=False)
    evidence_source = Column(String, nullable=True)
    category = Column(_string_enum(McfCategoryFamily), nullable=True)
    subcategory = Column(String, nullable=True)
    label_contains = Column(String, nullable=True)
    severity = Column(_string_enum(Severity), nullable=True)
    mcf_channel = Column(_string_enum(McfChannel), nullable=True)

    @validates("subcategory")
    def validate_subcategory(self, _key, value):
        return _known_subcategory(value)


class MediaAccessScopeRow(Base):
    """Local media access: the default (ID 1, no user) or one user's individual override.

    A user without a row inherits the default live.
    """

    __tablename__ = "media_access_scopes"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR id = 1", name="ck_media_access_scopes_local_default"
        ),
        UniqueConstraint("user_id", name="uq_media_access_scopes_user_id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    all_sources = Column(Boolean, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=_now)


class MediaAccessScopeSourceRow(Base):
    """One source granted by a scope whose `all_sources` is false."""

    __tablename__ = "media_access_scope_sources"
    __table_args__ = (
        CheckConstraint(
            "(source_kind = 'jellyfin_library') = (length(source_id) > 0)",
            name="ck_media_access_scope_sources_source_id",
        ),
    )

    scope_id = Column(
        Integer, ForeignKey("media_access_scopes.id", ondelete="CASCADE"), primary_key=True
    )
    source_kind = Column(_string_enum(MediaSourceKind), primary_key=True)
    source_id = Column(String, primary_key=True)
