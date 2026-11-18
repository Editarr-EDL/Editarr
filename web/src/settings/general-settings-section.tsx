import type { ReactNode } from "react";
import "../styles/general-settings.css";

const icons = {
  deployment: "M3 4h18v6H3zM3 14h18v6H3zM7 7h.01M7 17h.01",
  macos: "M3 3h18v13H3zM12 16v5m-4 0h8",
  backups: "M3 4h18v4H3zM5 8v12h14V8m-9 4h4",
  mappings: "M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-6M3 15h9m-4-4 4 4-4 4",
  diagnostics: "m4 6 6 6-6 6m10 0h6",
  about: "M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
};

type GeneralSettingsSectionId = keyof typeof icons;

/** A native disclosure that keeps its settings and drafts mounted when collapsed. */
export function GeneralSettingsSection({ id, title, value, attention = false, initiallyOpen, bodyClassName, children }: {
  id: GeneralSettingsSectionId;
  title: string;
  value?: ReactNode;
  attention?: boolean;
  initiallyOpen?: boolean;
  bodyClassName?: string;
  children: ReactNode;
}) {
  return <details className="general-settings-section" name="general-settings" open={initiallyOpen || undefined}>
    <summary className="general-settings-summary">
      <h2>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={icons[id]} />
        </svg>
        <span>{title}</span>
      </h2>
      {value && <span className={attention ? "general-settings-value needs-attention" : "general-settings-value"}>{value}</span>}
      <span className="general-settings-chevron" aria-hidden="true" />
    </summary>
    <div className={`general-settings-section-body${bodyClassName ? ` ${bodyClassName}` : ""}`}>{children}</div>
  </details>;
}
