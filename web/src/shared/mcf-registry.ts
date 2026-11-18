import { useEffect, useState } from "react";
import { fetchMcfRegistry } from "../api/api-service";
import type { McfRegistry } from "../api/api-mcf-types";

let pending: Promise<McfRegistry> | null = null;

/** Load the MCF registry once per page; a failed load is forgotten so the next caller retries. */
export function loadMcfRegistry(): Promise<McfRegistry> {
  pending ??= fetchMcfRegistry().catch((reason: unknown) => {
    pending = null;
    throw reason;
  });
  return pending;
}

/** Forget the cached registry so the next load fetches again; tests call this between cases. */
export function forgetMcfRegistry() {
  pending = null;
}

/** Return the shared MCF registry, or the error message when it could not be loaded. */
export function useMcfRegistry(): { registry: McfRegistry | null; error: string | null } {
  const [state, setState] = useState<{ registry: McfRegistry | null; error: string | null }>({ registry: null, error: null });
  useEffect(() => {
    let active = true;
    loadMcfRegistry().then(
      (registry) => { if (active) setState({ registry, error: null }); },
      (reason: unknown) => { if (active) setState({ registry: null, error: reason instanceof Error ? reason.message : "Unable to load MCF categories." }); },
    );
    return () => { active = false; };
  }, []);
  return state;
}
