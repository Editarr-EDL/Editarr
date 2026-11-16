"""Adapters for supported profanity detection packages."""

from collections.abc import Callable
from importlib import import_module


def _contains_profanity_better_profanity(text: str) -> bool:
    """Load the word list once and test text with the configured adapter."""
    from scanner.pipeline import audio as root

    if not root._better_profanity_loaded:
        root.profanity.load_censor_words()
        root._better_profanity_loaded = True
    return root.profanity.contains_profanity(text)


def _coerce_glin_result(value: object) -> bool:
    """Convert the supported detector response shapes into one boolean decision."""
    if hasattr(value, "item") and callable(getattr(value, "item")):
        try:
            return _coerce_glin_result(value.item())
        except Exception:
            pass
    if hasattr(value, "tolist") and callable(getattr(value, "tolist")):
        try:
            return _coerce_glin_result(value.tolist())
        except Exception:
            pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"true", "profane", "profanity", "1", "yes"}
    if isinstance(value, dict):
        for key in (
            "contains_profanity",
            "is_profane",
            "has_profanity",
            "profanity"
        ):
            if key in value:
                return _coerce_glin_result(value[key])
        return False
    if isinstance(value, (list, tuple)):
        return any(_coerce_glin_result(item) for item in value)
    return False


def _load_glin_detector() -> Callable[[str], bool]:
    """Load an installed GLiN package and adapt its callable detector API."""
    module = None
    for module_name in ("glin_profanity", "glin.profanity"):
        try:
            module = import_module(module_name)
            break
        except ModuleNotFoundError:
            continue
    if module is None:
        raise RuntimeError("glin-profanity is not installed on this scanner host")

    direct_candidates = (
        "contains_profanity",
        "is_profane",
        "has_profanity",
        "check_profanity",
        "predict",
    )
    for candidate in direct_candidates:
        fn = getattr(module, candidate, None)
        if callable(fn):
            return lambda text, fn=fn: _coerce_glin_result(fn(text))

    class_candidates = ("Filter", "ProfanityFilter", "Profanity", "GlinProfanity")
    method_candidates = (
        "contains_profanity",
        "is_profane",
        "has_profanity",
        "check_profanity",
        "matches",
        "predict",
    )
    for class_name in class_candidates:
        cls = getattr(module, class_name, None)
        if not callable(cls):
            continue
        instance = cls()
        for method_name in method_candidates:
            method = getattr(instance, method_name, None)
            if callable(method):
                return lambda text, method=method: _coerce_glin_result(method(text))

    raise RuntimeError("glin-profanity does not expose a supported profanity detector API")


def _load_alt_profanity_check_detector() -> Callable[[str], bool]:
    """Load the optional profanity-check package and adapt its detector API."""
    try:
        module = import_module("profanity_check")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "alt-profanity-check is required but could not be imported on this scanner host"
        ) from exc

    direct_candidates = (
        "contains_profanity",
        "is_profane",
        "has_profanity",
        "check_profanity",
        "predict",
        "predict_prob",
    )
    for candidate in direct_candidates:
        fn = getattr(module, candidate, None)
        if callable(fn):
            if candidate in {"predict", "predict_prob"}:
                return lambda text, fn=fn: _coerce_glin_result(fn([text]))
            return lambda text, fn=fn: _coerce_glin_result(fn(text))

    class_candidates = (
        "Filter",
        "ProfanityFilter",
        "Profanity",
        "AltProfanityFilter",
        "AltProfanity",
    )
    method_candidates = (
        "contains_profanity",
        "is_profane",
        "has_profanity",
        "check_profanity",
        "matches",
        "predict",
    )
    for class_name in class_candidates:
        cls = getattr(module, class_name, None)
        if not callable(cls):
            continue
        instance = cls()
        for method_name in method_candidates:
            method = getattr(instance, method_name, None)
            if callable(method):
                return lambda text, method=method: _coerce_glin_result(method(text))

    raise RuntimeError("alt-profanity-check does not expose a supported profanity detector API")


def _audio_filter_detector(filter_name: str | None) -> Callable[[str], bool]:
    """Resolve and cache the selected profanity detector by filter name."""
    from scanner.pipeline import audio as root

    selected_filter = root._normalize_audio_filter(filter_name)
    detector = root._audio_filter_detectors.get(selected_filter)
    if detector:
        return detector
    if selected_filter == "better_profanity":
        detector = root._contains_profanity_better_profanity
    elif selected_filter == "alt_profanity_check":
        detector = root._load_alt_profanity_check_detector()
    else:
        detector = root._load_glin_detector()
    root._audio_filter_detectors[selected_filter] = detector
    return detector




