import { useState, type ReactNode } from "react";

export function PolicySection({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <details className="account-policy-section" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{title}</summary>{open && children}
  </details>;
}