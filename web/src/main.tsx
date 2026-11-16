import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthenticationGate } from "./auth/auth-gate";
import { AdministratorLogin } from "./auth/auth-login";
import { JellyfinSetup } from "./auth/auth-setup";
import { initializeRuntimeUrls } from "./shared/runtime-urls";
import "./styles.css";

const root = document.getElementById("root")!;

try {
  initializeRuntimeUrls();
  createRoot(root).render(
    <StrictMode>
      <AuthenticationGate setupRequired={<JellyfinSetup />} unauthenticated={<AdministratorLogin />}><App /></AuthenticationGate>
    </StrictMode>
  );
} catch (error) {
  root.setAttribute("role", "alert");
  root.textContent = error instanceof Error ? error.message : "Editarr runtime bootstrap is invalid.";
}
