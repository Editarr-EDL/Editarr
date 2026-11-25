import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { MyVote, VoteValue } from "../api/api-community-master-types";
import { VoteMenuButton } from "./vote-menu-button";

afterEach(cleanup);

function Harness({ initial = null, busy, unavailable, onChoose = vi.fn() }: {
  initial?: MyVote; busy?: boolean; unavailable?: string | null; onChoose?: (vote: VoteValue) => void;
}) {
  const [value, setValue] = useState<MyVote>(initial);
  return <><VoteMenuButton value={value} name="Punching at 10:00" busy={busy} unavailable={unavailable}
    onChoose={(vote) => { onChoose(vote); setValue(vote === "none" ? null : vote); }} /><button type="button">Elsewhere</button></>;
}
const pill = () => screen.getByRole("button", { name: /^Your vote/ });
const item = (name: string) => screen.getByRole("menuitemradio", { name });

it("shows each vote state on the pill", () => {
  const { rerender } = render(<VoteMenuButton value={null} name="Punching at 10:00" onChoose={vi.fn()} />);
  expect(pill()).toHaveAccessibleName("Your vote: none. Change vote for Punching at 10:00");
  expect(pill()).toHaveAttribute("data-vote", "none");
  expect(pill()).toHaveTextContent("Vote▾");
  rerender(<VoteMenuButton value="up" name="Punching at 10:00" onChoose={vi.fn()} />);
  expect(pill()).toHaveAccessibleName("Your vote: Thumbs up. Change vote for Punching at 10:00");
  expect(pill()).toHaveAttribute("data-vote", "up");
  expect(pill()).toHaveTextContent("👍");
  rerender(<VoteMenuButton value="down" name="Punching at 10:00" onChoose={vi.fn()} />);
  expect(pill()).toHaveAttribute("data-vote", "down");
  expect(pill()).toHaveTextContent("👎");
});

it("opens a menu on the checked choice, moves with the arrow keys and closes on Escape", () => {
  render(<Harness initial="down" />);
  expect(pill()).toHaveAttribute("aria-haspopup", "menu");
  fireEvent.click(pill());
  expect(pill()).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("menu", { name: "Vote for Punching at 10:00" })).toBeInTheDocument();
  expect(item("Thumbs down")).toHaveAttribute("aria-checked", "true");
  expect(item("Thumbs up")).toHaveAttribute("aria-checked", "false");
  expect(item("Thumbs down")).toHaveFocus();
  fireEvent.keyDown(item("Thumbs down"), { key: "ArrowRight" });
  expect(item("No vote")).toHaveFocus();
  fireEvent.keyDown(item("No vote"), { key: "ArrowRight" });
  expect(item("Thumbs up")).toHaveFocus();
  fireEvent.keyDown(item("Thumbs up"), { key: "ArrowLeft" });
  expect(item("No vote")).toHaveFocus();
  fireEvent.keyDown(item("No vote"), { key: "Home" });
  expect(item("Thumbs up")).toHaveFocus();
  fireEvent.keyDown(item("Thumbs up"), { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pill()).toHaveAttribute("aria-expanded", "false");
  expect(pill()).toHaveFocus();
  fireEvent.keyDown(pill(), { key: "ArrowDown" });
  expect(screen.getByRole("menu")).toBeInTheDocument();
});

it("votes, clears the vote and ignores choosing the current vote", () => {
  const onChoose = vi.fn();
  render(<Harness onChoose={onChoose} />);
  fireEvent.click(pill());
  expect(item("No vote")).toHaveFocus();
  fireEvent.click(item("Thumbs up"));
  expect(onChoose).toHaveBeenLastCalledWith("up");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pill()).toHaveFocus();
  expect(pill()).toHaveAttribute("data-vote", "up");
  fireEvent.click(pill());
  fireEvent.click(item("Thumbs up"));
  expect(onChoose).toHaveBeenCalledTimes(1);
  fireEvent.click(pill());
  fireEvent.click(item("No vote"));
  expect(onChoose).toHaveBeenLastCalledWith("none");
  expect(pill()).toHaveAttribute("data-vote", "none");
});

it("closes on an outside click, returning focus unless another control was clicked", () => {
  render(<Harness />);
  fireEvent.click(pill());
  act(() => { fireEvent.pointerDown(document.body); });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pill()).toHaveFocus();
  fireEvent.click(pill());
  act(() => { fireEvent.pointerDown(screen.getByRole("button", { name: "Elsewhere" })); });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pill()).not.toHaveFocus();
});

it("shows a pending vote as busy and an unavailable one with its reason", () => {
  const { rerender } = render(<VoteMenuButton value="up" name="Punching at 10:00" busy onChoose={vi.fn()} />);
  expect(pill()).toHaveAttribute("aria-disabled", "true");
  expect(pill()).toHaveAttribute("aria-busy", "true");
  fireEvent.click(pill());
  expect(screen.queryByRole("menu")).toBeNull();
  expect(pill()).toHaveAccessibleName("Your vote: Thumbs up. Saving vote for Punching at 10:00");
  rerender(<VoteMenuButton value={null} name="Punching at 10:00" unavailable="Your account can’t vote." onChoose={vi.fn()} />);
  expect(pill()).toBeEnabled();
  expect(pill()).toHaveAttribute("aria-disabled", "true");
  expect(pill()).toHaveAccessibleDescription("Your account can’t vote.");
  expect(pill()).toHaveAttribute("title", "Your account can’t vote.");
  fireEvent.click(pill());
  fireEvent.keyDown(pill(), { key: "ArrowDown" });
  expect(screen.queryByRole("menu")).toBeNull();
});
