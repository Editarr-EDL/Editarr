"""Bounded model and hand-edit summaries derived from signed contribution evidence."""

import re

from pydantic import BaseModel, ConfigDict

MAX_SUMMARY_MODELS = 16
MODEL_TOKEN = (
    r"^[A-Za-z0-9](?:[A-Za-z0-9._:+@-]{0,63}"
    r"|[A-Za-z0-9._+@-]{0,63}/[A-Za-z0-9][A-Za-z0-9._+@-]{0,63})$"
)
_model_token = re.compile(MODEL_TOKEN)
_per_scene_context = re.compile(r"visual_qwen:scene_[0-9]+")
_language_detectors = {"alt_profanity_check", "better_profanity", "glin_profanity"}


class ManualFilterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    added: bool | None
    edited: bool | None


def contribution_metadata(artifact) -> dict:
    """Derive opt-in fields without changing previously signed summaries."""
    if artifact.manual_filter_tracking is None:
        return {}
    provenance = [
        artifact.provenance.model_dump(),
        *(source.provenance for source in artifact.sources or []),
    ]
    models = set()
    for recorded in provenance:
        for stage in recorded["stages"]:
            if stage["state"] not in {"complete", "partial"}:
                continue
            capability, model_id = stage["capability"], stage["model_id"]
            if _per_scene_context.fullmatch(capability):
                capability = "visual_qwen"
            if model_id and _model_token.fullmatch(model_id) and _model_token.fullmatch(capability):
                revision = stage["model_revision"] or ""
                models.add(
                    (capability, model_id, revision if _model_token.fullmatch(revision) else "")
                )
            detector = stage["settings"].get("audio_filter")
            if (
                capability == "audio_scan"
                and isinstance(detector, str)
                and detector in _language_detectors
            ):
                models.add(("audio_filter", detector, ""))
    counts = {}
    for field in ("added", "edited"):
        values = [getattr(finding.manual, field) for finding in artifact.findings]
        counts[field] = None if None in values else sum(values)
    return {
        "models": [
            {"capability": capability, "model_id": model_id, "model_revision": revision or None}
            for capability, model_id, revision in sorted(models)[:MAX_SUMMARY_MODELS]
        ],
        "manual_filter_counts": counts,
    }
