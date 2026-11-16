export type EntityType = "movie" | "series" | "episode";
export type IdentityProvider = "tmdb" | "tvdb";
export interface ProviderIdentifier {
  provider: IdentityProvider;
  entity_type: EntityType;
  value: string;
  provenance: string;
}
export interface EpisodeNumber {
  scheme: "aired" | "absolute" | "dvd" | "anime";
  season: number | null;
  number: number;
}
export interface MediaIdentity {
  entity_type: EntityType;
  identifiers: ProviderIdentifier[];
  series_identifiers: ProviderIdentifier[];
  episode_numbers: EpisodeNumber[];
  episode_span: EpisodeNumber[];
  release: {
    scene_name: string | null;
    group: string | null;
    edition: string | null;
    quality: string | null;
    size_bytes: number | null;
    duration: { seconds: number; source: "file_probe" | "jellyfin_ticks"; precision_seconds: number | null } | null;
    audio: { language: string | null; role: "main" | "commentary" | "descriptive" | "unknown"; codec: string | null; channels: number | null } | null;
  };
  input_requirement: string | null;
}
