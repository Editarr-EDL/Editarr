import logging
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, IPvAnyNetwork, model_validator
from pydantic_settings import BaseSettings

from scanner.runtime.runtime_paths import get_runtime_paths
from scanner.runtime.runtime_support import configure_model_cache_environment

logger = logging.getLogger(__name__)

CookieSameSite = Literal["lax", "strict"]

_COOKIE_TOKEN_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_AUTH_ENVIRONMENT_FIELDS = {
    "auth_trusted_proxy_networks": "EDITARR_AUTH_TRUSTED_PROXY_NETWORKS",
    "auth_session_idle_seconds": "EDITARR_AUTH_SESSION_IDLE_SECONDS",
    "auth_session_absolute_seconds": "EDITARR_AUTH_SESSION_ABSOLUTE_SECONDS",
    "auth_remember_me_idle_seconds": "EDITARR_AUTH_REMEMBER_ME_IDLE_SECONDS",
    "auth_remember_me_seconds": "EDITARR_AUTH_REMEMBER_ME_SECONDS",
    "auth_cookie_name": "EDITARR_AUTH_COOKIE_NAME",
    "auth_cookie_samesite": "EDITARR_AUTH_COOKIE_SAMESITE",
    "auth_cookie_secure": "EDITARR_AUTH_COOKIE_SECURE",
    "auth_login_username_attempts": "EDITARR_AUTH_LOGIN_USERNAME_ATTEMPTS",
    "auth_login_address_attempts": "EDITARR_AUTH_LOGIN_ADDRESS_ATTEMPTS",
    "auth_login_window_seconds": "EDITARR_AUTH_LOGIN_WINDOW_SECONDS",
}


def _sqlite_database_url(data_dir: Path) -> str:
    """Return the default SQLite URL for a resolved durable-data directory."""

    return f"sqlite:///{data_dir / 'scanner.db'}"


def _default_data_dir() -> Path:
    return Path(get_runtime_paths().data_dir)


def _default_log_dir() -> Path:
    return Path(get_runtime_paths().log_dir)


def _default_cache_dir() -> Path:
    return Path(get_runtime_paths().cache_dir)


def _default_model_cache() -> Path:
    return Path(get_runtime_paths().model_cache)


def _default_frames_dir() -> Path:
    return Path(get_runtime_paths().temp_dir) / "editarr-scans"


class Settings(BaseSettings):
    # ``None`` derives paths from the resolved data root during
    # ``validate_authentication_configuration``, including injected RuntimePaths.
    database_url: str | None = None
    data_dir: Path = Field(default_factory=_default_data_dir)
    frames_dir: Path | None = None
    exports_dir: Path | None = None
    qwen_replays_dir: Path | None = None
    log_dir: Path = Field(default_factory=_default_log_dir)
    cache_dir: Path = Field(default_factory=_default_cache_dir)
    model_cache: Path = Field(default_factory=_default_model_cache)

    # Uncredentialed CORS uses EDITARR_CORS_ORIGINS; credentialed CORS uses only
    # the configured deployment origin.
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    cors_allow_credentials: bool = False

    community_service_url: str | None = None
    community_service_audience: str | None = None
    community_publication_request_bytes: int | None = Field(default=None, gt=0, le=3_000_000)
    community_preview_bounds: dict[str, int] | None = None
    community_preview_source_limit: int | None = Field(default=None, gt=0, le=1000)
    community_download_bounds: dict[str, int] | None = None
    community_download_response_bytes: int | None = Field(default=None, gt=0, le=3_000_000)

    # Every protected request needs a session or API key. Trusted proxies only
    # identify the forwarded client address for login throttling.
    auth_trusted_proxy_networks: list[IPvAnyNetwork] = []

    # Session and cookie policy. All lifetimes are enforced server-side.
    auth_session_idle_seconds: int = 30 * 60
    auth_session_absolute_seconds: int = 8 * 60 * 60
    auth_remember_me_idle_seconds: int = 7 * 24 * 60 * 60
    auth_remember_me_seconds: int = 30 * 24 * 60 * 60
    auth_cookie_name: str = "editarr_session"
    auth_cookie_samesite: CookieSameSite = "lax"
    auth_cookie_secure: bool = True

    # Failed-credential throttling has independent username and client-address
    # buckets over the same sliding window.
    auth_login_username_attempts: int = 5
    auth_login_address_attempts: int = 25
    auth_login_window_seconds: int = 15 * 60

    # Visual scanning
    frame_interval_seconds: float = 1.0
    extracted_frame_max_height: int = 512
    visual_batch_size: int = 8
    nudity_confidence_threshold: float = 0.6
    nudity_risk_labels: list[str] = [
        "FEMALE_BREAST_EXPOSED",
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "BUTTOCKS_EXPOSED",
        "ANUS_EXPOSED",
    ]

    # Audio scanning
    whisper_model: str = "base"
    audio_filter: str = "better_profanity"
    # Include a small lead-in and tail after each detected audio word. A
    # deployment can set the default using EDITARR_PROFANITY_PAD_SECONDS.
    profanity_pad_seconds: float = Field(default=0.1, ge=0, allow_inf_nan=False)

    # Scene-aware visual scans can optionally gate fine scans with coarse samples.
    coarse_scan_interval_seconds: float = 5.0
    scene_detection_threshold: float = 27.0  # PySceneDetect ContentDetector threshold

    # Image Safety Gate (optional video model stack)
    image_safety_gate_wd_variant: str = "eva02-large"
    image_safety_gate_sensitivity: float = 0.6
    image_safety_gate_nsfw_threshold: float = 0.35
    image_safety_gate_nsfl_threshold: float = 0.5

    # SigLIP2 Moderation Heads (optional video model stack)
    siglip2_moderation_nsfw_threshold: float = 0.5
    siglip2_moderation_violence_threshold: float = 0.5

    # Shieldstral (optional macOS MLX video model). Prompts and routing are in
    # scanner/pipeline/shieldstral_policies.py. Screens flag a frame at P(yes) >= threshold.
    shieldstral_sexual_threshold: float = Field(default=0.4, ge=0, le=1)
    shieldstral_violence_threshold: float = Field(default=0.4, ge=0, le=1)
    # Routing for sexual-positive frames only chooses families. The sex-act route is
    # provisional: the evaluation corpus contained no real sex-act footage.
    shieldstral_nudity_route_threshold: float = Field(default=0.25, ge=0, le=1)
    shieldstral_sex_route_threshold: float = Field(default=0.5, ge=0, le=1)
    # Frames taller than this are downscaled, keeping aspect ratio, before scoring.
    shieldstral_max_image_height: int = Field(default=288, gt=0)
    # Fine passes score one frame per interval and bisect changes between samples.
    shieldstral_fine_sample_interval_seconds: float = Field(
        default=0.5, gt=0, allow_inf_nan=False
    )

    model_config = {"env_prefix": "EDITARR_"}

    @model_validator(mode="after")
    def validate_authentication_configuration(self):
        """Validate auth settings and reject unsafe combinations before service startup."""
        if self.database_url is None:
            self.database_url = _sqlite_database_url(self.data_dir)
        if self.frames_dir is None:
            self.frames_dir = _default_frames_dir()
        if self.exports_dir is None:
            self.exports_dir = self.data_dir / "exports"
        if self.qwen_replays_dir is None:
            self.qwen_replays_dir = self.data_dir / "qwen-replays"
        positive_fields = (
            "auth_session_idle_seconds",
            "auth_session_absolute_seconds",
            "auth_remember_me_idle_seconds",
            "auth_remember_me_seconds",
            "auth_login_username_attempts",
            "auth_login_address_attempts",
            "auth_login_window_seconds",
        )
        for field_name in positive_fields:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")

        if self.auth_session_idle_seconds > self.auth_session_absolute_seconds:
            raise ValueError(
                "auth_session_idle_seconds must not exceed auth_session_absolute_seconds"
            )
        if self.auth_remember_me_idle_seconds > self.auth_remember_me_seconds:
            raise ValueError(
                "auth_remember_me_idle_seconds must not exceed auth_remember_me_seconds"
            )
        if not _COOKIE_TOKEN_PATTERN.fullmatch(self.auth_cookie_name):
            raise ValueError("auth_cookie_name must be a valid HTTP cookie token")
        if not self.auth_cookie_secure and not self.is_environment_override("auth_cookie_secure"):
            raise ValueError(
                "auth_cookie_secure may be false only through EDITARR_AUTH_COOKIE_SECURE"
            )
        if not self.auth_cookie_secure:
            logger.warning(
                "INSECURE AUTH COOKIE CONFIGURATION: EDITARR_AUTH_COOKIE_SECURE=false; "
                "use only for local HTTP development"
            )
        return self

    def is_environment_override(self, field_name: str) -> bool:
        """Return whether an authentication setting comes from its environment variable."""
        try:
            environment_name = _AUTH_ENVIRONMENT_FIELDS[field_name]
        except KeyError as error:
            raise ValueError(
                f"{field_name} is not an authentication environment setting"
            ) from error
        normalized_name = environment_name.casefold()
        return any(name.casefold() == normalized_name for name in os.environ)


# Runtime paths have already been initialized by the command bootstrap when Editarr
# is launched normally.  Passing the resolved data directory explicitly prevents
# pydantic-settings from applying EDITARR_DATA_DIR a second time; the resolver owns
# its validation and documented CLI/environment precedence.  Explicit leaf overrides
# (notably EDITARR_DATABASE_URL) remain pydantic-settings configuration.
# Do this immediately after the native paths are initialized, before any dynamic model
# import can occur.  Explicit supported operator settings remain untouched.
configure_model_cache_environment()
settings = Settings(
    data_dir=_default_data_dir(),
    log_dir=_default_log_dir(),
    cache_dir=_default_cache_dir(),
    model_cache=_default_model_cache(),
)
