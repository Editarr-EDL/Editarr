/** Explains what a category's severity level does. Shared by My filters and Server defaults. */
export function SeverityHelp() {
  return <p className="filter-help">
    Scans tag each filter with a category. When a filter doesn't come with its own severity, Editarr gives it the level set here.
    Rules that match on severity, like "Swearing rated High", use that level. Severity never changes what a scene is tagged as.
  </p>;
}

/** Explains how selection rules apply to a review. Shared by My filters and Server defaults. */
export function RulesHelp() {
  return <p className="filter-help">
    Checked top to bottom when a review opens. <strong className="rule-word-select">Select</strong> marks matching filters to cut.{" "}
    <strong className="rule-word-keep">Keep</strong> leaves them in the movie. If both match, Select wins.
  </p>;
}
