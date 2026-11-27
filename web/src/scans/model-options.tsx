import type { ScanModelCapability } from "../api/api-system-types";

/** The capabilities default: the recommended model, otherwise the first one offered. */
export function defaultModelId(models: ScanModelCapability[]): string | null {
  return (models.find((model) => model.recommended) ?? models[0])?.id ?? null;
}

/**
 * Keep a valid current choice, otherwise fall back to the capabilities default.
 * An empty or unknown current value is replaced; an empty list leaves it unchanged.
 */
export function resolveModelChoice(current: string, models: ScanModelCapability[]): string {
  if (models.some((model) => model.id === current)) return current;
  return defaultModelId(models) ?? current;
}

