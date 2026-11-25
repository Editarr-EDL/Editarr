"""Core data models for Editarr scans and integration state."""

import unicodedata
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def canonicalize_username(value: str) -> tuple[str, str]:
    """Return the validated display and lookup values for an account's local username."""

    normalized = unicodedata.normalize("NFKC", value)
    if any(_disallowed(character) for character in normalized):
        raise ValueError("Value contains a disallowed Unicode character")
    display_username = normalized.strip()
    if not 1 <= len(display_username) <= 64:
        raise ValueError("Username must be between 1 and 64 Unicode code points")
    return display_username, unicodedata.normalize("NFKC", display_username.casefold())


def _disallowed(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("C") or category in {"Zl", "Zp"}


def normalize_scan_name(value: str | None) -> str | None:
    """Validate before trimming so whitespace controls cannot disappear unnoticed."""
    if value is None:
        return None
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("Name contains a disallowed Unicode character")
    value = value.strip()
    if len(value) > 100:
        raise ValueError("Name must be at most 100 Unicode code points")
    return value or None


def normalize_review_name(value: str) -> str:
    name = normalize_scan_name(value)
    if name is None:
        raise ValueError("Review name is required")
    return name


class ScanOrigin(str, Enum):
    GENERATED = "generated"
    MANUAL = "manual"
    IMPORTED = "imported"


class ScanMode(str, Enum):
    CONTENT = "content"
    CREDITS_DETECTION = "credits_detection"


class SynchronizationEndMode(str, Enum):
    BEFORE_CREDITS = "before_credits"
    FILE_END = "file_end"


class CreditsDetectionMethod(str, Enum):
    CHAPTERS = "chapters"
    VISUAL_TEXT = "visual_text"
    HYBRID = "hybrid"
    HYBRID_QWEN = "hybrid_qwen"


class CreditsEvidenceKind(str, Enum):
    CHAPTER_MARKER = "chapter_marker"
    SCENE_BOUNDARY = "scene_boundary"
    TEXT_DENSITY = "text_density"
    CREDIT_MOTION = "credit_motion"
    BLACK_FRAME = "black_frame"
    QWEN = "qwen"


class CreditsConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewSource(str, Enum):
    JELLYFIN = "jellyfin"


class ReviewScanRole(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    BOTH = "both"


ScanName = Annotated[str | None, AfterValidator(normalize_scan_name)]
ReviewName = Annotated[str, AfterValidator(normalize_review_name)]
ReviewFilterId = Annotated[str, AfterValidator(lambda value: str(UUID(value)))]
ReviewRevision = Annotated[int, Field(ge=0, strict=True)]


class ReviewWriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanNameUpdate(ReviewWriteModel):
    name: ScanName


class McfCategoryFamily(str, Enum):
    COMMERCIAL_CONTENT = "commercial_content"
    DISCRIMINATION = "discrimination"
    DISPENSABLE_SCENES = "dispensable_scenes"
    DRUGS = "drugs"
    FEAR = "fear"
    LANGUAGE = "language"
    NUDITY = "nudity"
    SEX = "sex"
    VIOLENCE = "violence"


MCF_CATEGORY_REGISTRY: dict[McfCategoryFamily, tuple[tuple[str, str], ...]] = {
    McfCategoryFamily.COMMERCIAL_CONTENT: (
        ("commercial", "Commercial content (General)"),
        ("advertBreak", "Advert break"),
        ("consumerism", "Consumerism"),
        ("productPlacement", "Product placement"),
    ),
    McfCategoryFamily.DISCRIMINATION: (
        ("discrimination", "Discrimination (General)"),
        ("adultism", "Adultism"),
        ("antisemitism", "Antisemitism"),
        ("genderism", "Genderism"),
        ("homophobia", "Homophobia"),
        ("misandry", "Misandry"),
        ("misogyny", "Misogyny"),
        ("racism", "Racism"),
        ("sexism", "Sexism"),
        ("supremacism", "Supremacism"),
        ("transphobia", "Transphobia"),
        ("xenophobia", "Xenophobia"),
    ),
    McfCategoryFamily.DISPENSABLE_SCENES: (
        ("dispensable", "Dispensable scenes (General)"),
        ("idiocy", "Idiocy"),
        ("tedious", "Tedious scene"),
    ),
    McfCategoryFamily.DRUGS: (
        ("drugs", "Drugs (General)"),
        ("alcohol", "Alcohol"),
        ("antipsychotics", "Antipsychotics"),
        ("cigarettes", "Cigarettes"),
        ("depressants", "Depressants"),
        ("gambling", "Gambling"),
        ("hallucinogens", "Hallucinogens"),
        ("stimulants", "Stimulants"),
    ),
    McfCategoryFamily.FEAR: (
        ("fear", "Fear (General)"),
        ("accident", "Accident"),
        ("acrophobia", "Acrophobia"),
        ("aliens", "Aliens"),
        ("arachnophobia", "Arachnophobia"),
        ("astraphobia", "Astraphobia"),
        ("aviophobia", "Aviophobia"),
        ("chemophobia", "Chemophobia"),
        ("claustrophobia", "Claustrophobia"),
        ("coulrophobia", "Coulrophobia"),
        ("cynophobia", "Cynophobia"),
        ("death", "Death"),
        ("dentophobia", "Dentophobia"),
        ("emetophobia", "Emetophobia"),
        ("enochlophobia", "Enochlophobia"),
        ("explosion", "Explosion"),
        ("fire", "Fire"),
        ("gerascophobia", "Gerascophobia"),
        ("ghosts", "Ghosts"),
        ("grave", "Grave"),
        ("hemophobia", "Hemophobia"),
        ("hylophobia", "Hylophobia"),
        ("melissophobia", "Melissophobia"),
        ("misophonia", "Misophonia"),
        ("musophobia", "Musophobia"),
        ("mysophobia", "Mysophobia"),
        ("nosocomephobia", "Nosocomephobia"),
        ("nyctophobia", "Nyctophobia"),
        ("siderodromophobia", "Siderodromophobia"),
        ("thalassophobia", "Thalassophobia"),
        ("vampires", "Vampires"),
    ),
    McfCategoryFamily.LANGUAGE: (
        ("language", "Language (General)"),
        ("blasphemy", "Blasphemy"),
        ("nameCalling", "Name-calling"),
        ("sexualDialogue", "Sexual dialogue"),
        ("swearing", "Swearing"),
        ("vulgarity", "Vulgarity"),
    ),
    McfCategoryFamily.NUDITY: (
        ("nudity", "Nudity (General)"),
        ("bareButtocks", "Bare buttocks"),
        ("exposedGenitalia", "Exposed genitalia"),
        ("fullNudity", "Full nudity"),
        ("toplessness", "Toplessness"),
    ),
    McfCategoryFamily.SEX: (
        ("sex", "Sex (General)"),
        ("adultery", "Adultery"),
        ("analSex", "Anal sex"),
        ("coitus", "Coitus"),
        ("kissing", "Kissing"),
        ("masturbation", "Masturbation"),
        ("objectification", "Objectification"),
        ("oralSex", "Oral sex"),
        ("premaritalSex", "Premarital sex"),
        ("promiscuity", "Promiscuity"),
        ("prostitution", "Prostitution"),
    ),
    McfCategoryFamily.VIOLENCE: (
        ("violence", "Violence (General)"),
        ("choking", "Choking"),
        ("crueltyToAnimals", "Cruelty to animals"),
        ("culturalViolence", "Cultural violence"),
        ("desecration", "Desecration"),
        ("emotionalViolence", "Emotional violence"),
        ("kicking", "Kicking"),
        ("massacre", "Massacre"),
        ("murder", "Murder"),
        ("punching", "Punching"),
        ("rape", "Rape"),
        ("slapping", "Slapping"),
        ("slavery", "Slavery"),
        ("stabbing", "Stabbing"),
        ("torture", "Torture"),
        ("warfare", "Warfare"),
        ("weapons", "Weapons"),
    ),
}
MCF_CATEGORY_TO_FAMILY = {
    token: family
    for family, categories in MCF_CATEGORY_REGISTRY.items()
    for token, _label in categories
}
MCF_CATEGORY_LABELS = {
    token: label for categories in MCF_CATEGORY_REGISTRY.values() for token, label in categories
}

MCF_CATEGORY_FAMILY_LABELS = {
    McfCategoryFamily.COMMERCIAL_CONTENT: "Commercial content",
    McfCategoryFamily.DISCRIMINATION: "Discrimination",
    McfCategoryFamily.DISPENSABLE_SCENES: "Dispensable scenes",
    McfCategoryFamily.DRUGS: "Drugs",
    McfCategoryFamily.FEAR: "Fear",
    McfCategoryFamily.LANGUAGE: "Language",
    McfCategoryFamily.NUDITY: "Nudity",
    McfCategoryFamily.SEX: "Sex",
    McfCategoryFamily.VIOLENCE: "Violence",
}

MAX_PERSISTED_INTEGER = 9_223_372_036_854_775_807


McfCategory = Enum(
    "McfCategory",
    {token: token for token in MCF_CATEGORY_TO_FAMILY},
    type=str,
    module="scanner.models",
)


class McfChannel(str, Enum):
    BOTH = "both"
    VIDEO = "video"
    AUDIO = "audio"


def channel_for_source(source: str) -> McfChannel:
    return McfChannel.AUDIO if source == "audio" else McfChannel.VIDEO


# The channel each MCF family is analyzed on: audio analysis yields only spoken
# language; every other family is judged from the picture.
MCF_FAMILY_CHANNELS: dict[McfCategoryFamily, McfChannel] = {
    family: McfChannel.AUDIO if family == McfCategoryFamily.LANGUAGE else McfChannel.VIDEO
    for family in MCF_CATEGORY_REGISTRY
}


class McfSourceVersion(str, Enum):
    V1_0_0 = "1.0.0"
    V1_1_0 = "1.1.0"


class McfWorkType(str, Enum):
    MOVIE = "movie"
    SERIES = "series"
    EPISODE = "episode"


class ExportFormat(str, Enum):
    EDL = "edl"
    MCF = "mcf"


class McfCategoryOption(BaseModel):
    token: McfCategory
    label: str


class McfCategoryFamilyOption(BaseModel):
    token: McfCategoryFamily
    label: str
    categories: list[McfCategoryOption]


class McfRegistry(BaseModel):
    families: list[McfCategoryFamilyOption]


class EditAction(str, Enum):
    """Kodi Simple EDL actions Editarr can create from a finding."""

    MUTE = "mute"
    SKIP = "skip"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _validate_classification_pair(
    category: McfCategoryFamily | None,
    subcategory: str | None,
    *,
    required: bool,
) -> None:
    """Require a complete category pair and check subcategory membership."""
    if (category is None) != (subcategory is None):
        raise ValueError("Category and subcategory must be populated or null together")
    if required and category is None:
        raise ValueError("Category and subcategory are required")
    if subcategory is not None and MCF_CATEGORY_TO_FAMILY[subcategory] != category:
        raise ValueError("Subcategory does not belong to category")


def _validate_mcf_comment(value: str | None) -> str | None:
    if value is None:
        return None
    if not value or "\n" in value or "\r" in value or "-->" in value:
        raise ValueError("MCF comment must be non-empty, single-line, and exclude '-->'")
    return value


class FindingCreate(BaseModel):
    video_id: str
    category: McfCategoryFamily
    subcategory: McfCategory
    start_seconds: float
    end_seconds: float | None = None
    severity: Severity | None = None
    # Where a visual severity came from; kept in scan caches and logs, not the database.
    severity_source: Literal["detector", "description_model", "confidence"] | None = None
    mcf_channel: McfChannel | None = None
    mcf_comment: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    label: str | None = None
    details: str | None = None
    source: str  # "visual" | "audio"
    selected: bool = True
    edit_action: EditAction | None = None
    group_key: str | None = None

    @field_validator("mcf_comment")
    @classmethod
    def validate_mcf_comment(cls, value):
        return _validate_mcf_comment(value)

    @model_validator(mode="after")
    def validate_classification(self):
        _validate_classification_pair(self.category, self.subcategory, required=True)
        return self
