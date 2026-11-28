import type { ReactNode } from "react";
import type { ContributorRef, ItemRef } from "../../../api/api-community-operator-labels";
import { hue, initials } from "./format";

/** A contributor's initials on a stable colour; `size` is "", "lg" or "xl". */
export function Avatar({ person, size = "" }: { person: { contributorId?: string | null; displayName: string }; size?: "" | "lg" | "xl" }) {
  return <span className={`av av-c${hue(person.contributorId ?? person.displayName, 10)}${size ? ` ${size}` : ""}`} aria-hidden="true">
    {initials(person.displayName)}</span>;
}
/** Overlapping avatars with a "+n" overflow and an accessible list of names. */
export function AvatarStack({ people, max = 4 }: { people: ContributorRef[]; max?: number }) {
  return <span className="stack" role="img" aria-label={people.map(person => person.displayName).join(", ") || "No contributors"}>
    {people.slice(0, max).map(person => <Avatar key={person.contributorId} person={person} />)}
    {people.length > max && <span className="more" aria-hidden="true">+{people.length - max}</span>}</span>;
}
/** A generated poster tile (no artwork is stored) coloured by work ID. */
export function Poster({ item, episode = false, className = "" }: { item: ItemRef; episode?: boolean; className?: string }) {
  return <span className={`poster poster-g${hue(item.workId, 8)}${episode ? " ep" : ""}${className ? ` ${className}` : ""}`} aria-hidden="true">
    {initials(item.title)}</span>;
}
export function PageHead({ eyebrow, title, intro, children }: { eyebrow: string; title: ReactNode; intro?: ReactNode; children?: ReactNode }) {
  return <div className="head"><div><p className="eyebrow">{eyebrow}</p><h1 id="page-title" tabIndex={-1}>{title}</h1>{intro && <p>{intro}</p>}</div>{children}</div>;
}
/** A single-choice button group; the selected button has `aria-pressed="true"`. */
export function Segmented<T extends string>({ label, value, options, onChange, className = "seg" }: { label: string; value: T;
  options: readonly (readonly [T, ReactNode])[]; onChange: (value: T) => void; className?: string }) {
  return <div className={className} role="group" aria-label={label}>{options.map(([option, text]) =>
    <button key={option} type="button" aria-pressed={option === value} onClick={() => onChange(option)}>{text}</button>)}</div>;
}
/** A card section header with a title, optional muted note and trailing content. */
export function PanelHead({ title, note, children, level = 2 }: { title: ReactNode; note?: ReactNode; children?: ReactNode; level?: 2 | 3 }) {
  const Heading = level === 2 ? "h2" : "h3";
  return <div className="panel-head"><Heading>{title}{note && <small>{note}</small>}</Heading>{children}</div>;
}
/** Up/down vote counts, e.g. "▲12 ▼1". */
export function Votes({ up, down }: { up: number; down: number }) {
  return <span className="num"><span className="up">▲{up}</span> <span className="down">▼{down}</span></span>;
}
