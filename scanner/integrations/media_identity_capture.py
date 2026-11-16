"""Allowlisted, pseudonymized provider records for reproducible identity fixtures."""

from __future__ import annotations


class MediaCaptureSanitizer:
    """Keep field presence and identifier equality without disclosing a library."""

    def __init__(self) -> None:
        self._identifiers: dict[tuple[str, str], int] = {}

    def _identifier(self, namespace: str, value: object) -> object:
        if value in (None, "", 0, "0"):
            return value
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        key = namespace, str(value)
        number = self._identifiers.setdefault(key, len(self._identifiers) + 1)
        if namespace == "imdb":
            return f"tt{number:07d}"
        return str(number) if isinstance(value, str) else number

    def jellyfin(self, item: dict[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        if item.get("Type") in ("Movie", "Series", "Episode"):
            result["Type"] = item["Type"]
        if "Name" in item:
            result["Name"] = "Sanitized item"
        for field in ("Id", "SeriesId", "ParentId"):
            if field in item:
                result[field] = self._identifier("jellyfin_local", item[field])
        for field in ("ParentIndexNumber", "IndexNumber", "IndexNumberEnd", "RunTimeTicks"):
            if field in item and isinstance(item[field], int) and not isinstance(item[field], bool):
                result[field] = item[field]
        providers = item.get("ProviderIds")
        if isinstance(providers, dict):
            result["ProviderIds"] = {
                field: self._identifier(namespace, providers[field])
                for field, namespace in (("Tmdb", "tmdb"), ("Tvdb", "tvdb"), ("Imdb", "imdb"))
                if field in providers
            }
        sources = item.get("MediaSources")
        if isinstance(sources, list):
            result["MediaSources"] = [
                self._jellyfin_source(source) for source in sources if isinstance(source, dict)
            ]
        return result

    def _jellyfin_source(self, source: dict[str, object]) -> dict[str, object]:
        result: dict[str, object] = {}
        for field in ("RunTimeTicks", "Size"):
            if (
                field in source
                and isinstance(source[field], int)
                and not isinstance(source[field], bool)
            ):
                result[field] = source[field]
        for field in ("Name", "SceneName", "ReleaseGroup", "Edition"):
            if field in source:
                result[field] = "Sanitized" if source[field] else source[field]
        return result
