import type { ScanModelCapability } from "../api/api-system-types";

/** Option text for a model choice, marking the recommended choice. */
export function modelOptionLabel(model: ScanModelCapability): string {
  return model.recommended ? `${model.display_name} (recommended)` : model.display_name;
}

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

/** Render the `<option>` list shared by every model select. */
export function ModelOptions({ models }: { models: ScanModelCapability[] | undefined }) {
  return <>{(models || []).map((model) => <option key={model.id} value={model.id}>{modelOptionLabel(model)}</option>)}</>;
}
