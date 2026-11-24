"""Which categories a completed scan stage examined, for submission coverage (§5.2).

Coverage creates implicit negatives on other contributors' filters, so a stage covers
only the categories its recorded model or filter actually detects. A model whose
categories are not listed here (such as the general Qwen VL detectors) covers nothing.
"""

import json

# Visual `video_model` values and the top-level categories their detectors emit.
VISUAL_MODEL_CATEGORIES = {
    "nudenet": ("nudity",),
    "image_safety_gate": ("nudity", "violence"),
    "siglip2_moderation_nsfw": ("nudity",),
    "siglip2_moderation_violence": ("violence",),
    "shieldstral": ("nudity", "sex", "violence"),
    "d1-3b": ("drugs", "nudity", "sex", "violence"),
}
# Audio `audio_filter` values; each flags language only.
AUDIO_FILTER_CATEGORIES = {
    "better_profanity": ("language",),
    "glin_profanity": ("language",),
    "alt_profanity_check": ("language",),
    "alt-profanity-check": ("language",),
    "qwen": ("language",),
}


def stage_categories(stage: str, effective_settings_json: str | None) -> tuple[str, ...]:
    """The categories a stage covered, from its recorded effective settings; () if unknown."""
    try:
        settings = json.loads(effective_settings_json or "{}")
    except ValueError:
        return ()
    if not isinstance(settings, dict):
        return ()
    if stage == "visual":
        return VISUAL_MODEL_CATEGORIES.get(settings.get("video_model"), ())
    if stage == "audio_scan":
        return AUDIO_FILTER_CATEGORIES.get(settings.get("audio_filter"), ())
    return ()
