export interface NameValidation {
  normalized: string | null;
  error: string | null;
}

// Reject control characters before trimming and count Unicode code points.
const DISALLOWED_NAME_CHARACTER = /[\p{Cc}\p{Cs}]/u;

export function validateDisplayName(value: string, required: boolean): NameValidation {
  if (DISALLOWED_NAME_CHARACTER.test(value)) {
    return { normalized: null, error: "Name contains a disallowed Unicode character." };
  }
  const normalized = value.trim();
  if ([...normalized].length > 100) {
    return { normalized: null, error: "Name must be at most 100 Unicode code points." };
  }
  if (!normalized) {
    return {
      normalized: null,
      error: required ? "Name is required." : null,
    };
  }
  return { normalized, error: null };
}
