import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AudioTrackField, type AudioTrackInventory } from "./audio-track-field";

afterEach(cleanup);
const inventory: AudioTrackInventory = {
  file_version: "file-v1",
  selection: null, selection_stale: false,
  tracks: ["Main", "Commentary"].map((title, position) => ({
    stream_index: position + 1, language: "eng", title, codec: "aac", channels: 2,
    disposition: 0, start_seconds: 0, duration_seconds: 5, time_base: "1/48000",
  })),
};

it("distinguishes same-language tracks and emits exact file-bound selection", () => {
  const onChange = vi.fn();
  render(<AudioTrackField inventory={inventory} selection={null} onChange={onChange} onRetry={vi.fn()} />);
  expect(screen.getByRole("option", { name: /Stream 1.*eng.*Main/ })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Stream 2.*eng.*Commentary/ })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Audio track" })).toHaveValue("");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
  expect(onChange).toHaveBeenCalledWith({ file_version: "file-v1", track: inventory.tracks[1] });
});

it("never carries an index to another file or substitutes changed metadata", () => {
  const onChange = vi.fn();
  const selection = { file_version: "file-v1", track: inventory.tracks[0] };
  const { rerender } = render(<AudioTrackField inventory={inventory} selection={selection} onChange={onChange} onRetry={vi.fn()} />);
  expect(screen.getByRole("combobox")).toHaveValue("1");
  for (const changed of [
    { ...inventory, file_version: "file-v2" },
    { ...inventory, tracks: [{ ...inventory.tracks[0], title: "Different" }] },
    { ...inventory, tracks: [inventory.tracks[1]] },
  ]) {
    rerender(<AudioTrackField inventory={changed} selection={selection} onChange={onChange} onRetry={vi.fn()} />);
    expect(screen.getByRole("combobox")).toHaveValue("");
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a track again");
  }
  expect(onChange).not.toHaveBeenCalled();
});

it("shows loading, error/retry, empty, and unknown labels honestly", () => {
  const onRetry = vi.fn();
  const props = { inventory: null, selection: null, onChange: vi.fn(), onRetry };
  const { rerender } = render(<AudioTrackField {...props} loading />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading");
  expect(screen.getByRole("combobox")).toBeDisabled();
  rerender(<AudioTrackField {...props} error="Probe failed" />);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledOnce();
  rerender(<AudioTrackField {...props} inventory={{ file_version: "empty", tracks: [], selection: null, selection_stale: false }} />);
  expect(screen.getByRole("status")).toHaveTextContent("No audio tracks");
  rerender(<AudioTrackField {...props} inventory={{ ...inventory, tracks: [{ ...inventory.tracks[0], language: null, title: null }] }} />);
  expect(screen.getByRole("option", { name: /Unknown language.*Untitled/ })).toBeInTheDocument();
});