import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { McfRegistry, ReviewFilter, ReviewMcfDocument, Scan } from "../api";
import {
  McfClassificationFields,
  McfDocumentEditor,
  McfFilterSummary,
  McfMetadataSourceSelector,
  classificationIsValid,
  formatMcfTimestamp,
} from "./review-mcf";

const registry: McfRegistry = {
  families: [
    { token: "language", label: "Language", categories: [{ token: "language", label: "Language (General)" }, { token: "swearing", label: "Swearing" }] },
    { token: "violence", label: "Violence", categories: [{ token: "violence", label: "Violence (General)" }, { token: "weapons", label: "Weapons" }, { token: "rape", label: "Rape" }] },
    { token: "sex", label: "Sex", categories: [{ token: "sexualRape", label: "Rape" }] },
    { token: "nudity", label: "Nudity", categories: [{ token: "nudity", label: "Nudity (General)" }, { token: "exposedGenitalia", label: "Exposed genitalia" }] },
  ],
};

const document: ReviewMcfDocument = {
  title: "Episode",
  year: 2026,
  work_type: "episode",
  season: 2,
  episode: 4,
  source_url: "https://example.com/work",
  imdb_url: "https://www.imdb.com/title/tt1/",
  release: "Web release",
  comment: null,
  material_start_seconds: 0,
  material_end_seconds: 60,
};

const scan = {
  id: "scan-1",
  scan_id: 1,
  name: "Imported source",
  mcf_document: { ...document, source_version: "1.1.0" },
} as Scan;

const filter: ReviewFilter = {
  id: "filter-1",
  source_scan_id: "scan-1",
  source_finding_id: 1,
  source: "visual",
  category: "violence",
  subcategory: "weapons",
  severity: "high",
  mcf_channel: "video",
  mcf_comment: "Weapon shown",
  label: "Weapon",
  details: null,
  group_key: null,
  start_seconds: 1,
  end_seconds: 2,
  confidence: 1,
  edit_action: "skip",
  selected: true,
};

describe("review MCF controls", () => {
  afterEach(cleanup);

  it("edits conditional metadata and displays immutable imported provenance", () => {
    const onChange = vi.fn();
    render(<McfDocumentEditor document={document} scans={[scan]} onChange={onChange} />);

    fireEvent.click(screen.getByText("MCF document metadata"));
    expect(screen.getByLabelText("MCF season")).toHaveValue(2);
    expect(screen.getByLabelText("MCF episode")).toHaveValue(4);
    fireEvent.change(screen.getByLabelText("MCF source URL"), { target: { value: "relative" } });
    expect(onChange).toHaveBeenCalledWith({ ...document, source_url: "relative" });
    fireEvent.click(screen.getByText(/MCF metadata · Imported source · 1.1.0/));
    expect(screen.getByText("Web release")).toBeVisible();
  });

  it("formats and parses material bounds as MCF timestamps", () => {
    const onChange = vi.fn();
    render(<McfDocumentEditor document={{ ...document, material_end_seconds: 3723.456 }} scans={[]} onChange={onChange} />);

    expect(formatMcfTimestamp(3723.456)).toBe("01:02:03.456");
    expect(screen.getByLabelText("MCF material start")).toHaveValue("00:00:00.000");
    expect(screen.getByLabelText("MCF material end")).toHaveValue("01:02:03.456");
    fireEvent.change(screen.getByLabelText("MCF material start"), { target: { value: "00:00:01.250" } });
    expect(onChange).toHaveBeenCalledWith({ ...document, material_end_seconds: 3723.456, material_start_seconds: 1.25 });
  });

  it("shows descriptive subcategory options for the selected family and writes category tokens", () => {
    const onChange = vi.fn();
    const { rerender } = render(<McfClassificationFields controlId="Filter one" registry={registry} value={filter} onChange={onChange} allowClear />);

    const subcategory = screen.getByRole("combobox", { name: "Filter one subcategory" });
    expect(subcategory).toHaveDisplayValue("Weapons");
    expect(screen.queryByRole("option", { name: "Exposed genitalia" })).not.toBeInTheDocument();
    expect(classificationIsValid(registry, "violence", "weapons")).toBe(true);
    expect(classificationIsValid(registry, "language", "weapons")).toBe(false);
    fireEvent.change(screen.getByLabelText("Filter one family"), { target: { value: "nudity" } });
    expect(onChange).toHaveBeenCalledWith({ category: "nudity", subcategory: null });
    rerender(<McfClassificationFields controlId="Filter one" registry={registry}
      value={{ ...filter, category: "nudity", subcategory: "nudity" }} onChange={onChange} allowClear />);
    expect(subcategory).toHaveDisplayValue("Nudity (General)");
    expect(screen.getByRole("option", { name: "Exposed genitalia" })).toHaveValue("exposedGenitalia");
    fireEvent.change(subcategory, { target: { value: "exposedGenitalia" } });
    expect(onChange).toHaveBeenCalledWith({ subcategory: "exposedGenitalia" });
    fireEvent.click(screen.getByLabelText("Medium"));
    expect(onChange).toHaveBeenCalledWith({ severity: "medium" });
    expect(screen.queryByLabelText("Filter one channel")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Filter one MCF comment")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear classification" }));
    expect(onChange).toHaveBeenCalledWith({ category: null, subcategory: null });
  });

  it("shows complete and unclassified summaries and requires an explicit metadata candidate", () => {
    const onChange = vi.fn();
    render(<>
      <McfFilterSummary filter={filter} registry={registry} />
      <McfFilterSummary filter={{ ...filter, category: null, subcategory: null }} registry={registry} />
      <McfMetadataSourceSelector candidates={[
        { kind: "scan", scan_id: "scan-1", label: "Imported source", mcf_document: document },
        { kind: "catalog", scan_id: null, label: "Catalog defaults", mcf_document: { ...document, title: "Catalog" } },
      ]} value={null} onChange={onChange} />
    </>);

    expect(screen.getByText("Violence / Weapons")).toBeVisible();
    expect(screen.getByText("weapons")).toBeVisible();
    expect(screen.getByText("Needs classification")).toBeVisible();
    expect(screen.getByText("MCF metadata · Episode")).toBeVisible();
    expect(screen.getByText("Catalog metadata · Catalog")).toBeVisible();
    fireEvent.click(screen.getByLabelText(/Catalog defaults/));
    expect(onChange).toHaveBeenCalledWith({ kind: "catalog" });
  });

  it("gives the two Rape classifications distinct values in their respective families", () => {
    const onChange = vi.fn();
    const { rerender } = render(<McfClassificationFields controlId="Filter one" registry={registry} value={filter} onChange={onChange} />);
    expect(screen.getByRole("option", { name: "Rape" })).toHaveValue("rape");

    rerender(<McfClassificationFields controlId="Filter one" registry={registry}
      value={{ ...filter, category: "sex", subcategory: null }} onChange={onChange} />);
    expect(screen.getByRole("option", { name: "Rape" })).toHaveValue("sexualRape");
    fireEvent.change(screen.getByLabelText("Filter one subcategory"), { target: { value: "sexualRape" } });
    expect(onChange).toHaveBeenCalledWith({ subcategory: "sexualRape" });
  });
});
