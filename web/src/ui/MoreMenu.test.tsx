import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MoreMenu } from "./MoreMenu";

afterEach(cleanup);

const label = "More actions for Fight at 1:00";
function renderMenu({ onEdit = vi.fn(), onDelete = vi.fn(), deleteDisabled = false } = {}) {
  render(<><MoreMenu label={label} items={[
    { key: "edit", label: "Edit", accessibleName: "Edit filter f1", onSelect: onEdit },
    { key: "archive", label: "Archive", disabled: true, onSelect: vi.fn() },
    { key: "delete", label: "Delete", accessibleName: "Delete filter f1", danger: true, disabled: deleteDisabled, onSelect: onDelete },
  ]} /><button type="button">Elsewhere</button></>);
  return { onEdit, onDelete };
}
const trigger = () => screen.getByRole("button", { name: label });
const item = (name: string) => screen.getByRole("menuitem", { name });

it("opens a named menu on its first item, skips disabled items with the arrow keys and closes on Escape", () => {
  renderMenu();
  expect(trigger()).toHaveAttribute("aria-haspopup", "menu");
  expect(trigger()).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger());
  expect(trigger()).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("menu", { name: label })).toBeInTheDocument();
  expect(item("Edit filter f1")).toHaveFocus();
  expect(item("Archive")).toBeDisabled();
  fireEvent.keyDown(item("Edit filter f1"), { key: "ArrowDown" });
  expect(item("Delete filter f1")).toHaveFocus();
  fireEvent.keyDown(item("Delete filter f1"), { key: "ArrowDown" });
  expect(item("Edit filter f1")).toHaveFocus();
  fireEvent.keyDown(item("Edit filter f1"), { key: "End" });
  expect(item("Delete filter f1")).toHaveFocus();
  fireEvent.keyDown(item("Delete filter f1"), { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger()).toHaveAttribute("aria-expanded", "false");
  expect(trigger()).toHaveFocus();
  fireEvent.keyDown(trigger(), { key: "ArrowDown" });
  expect(item("Edit filter f1")).toHaveFocus();
});

it("runs a chosen action once and returns focus to the button", () => {
  const { onEdit, onDelete } = renderMenu();
  fireEvent.click(trigger());
  fireEvent.click(item("Delete filter f1"));
  expect(onDelete).toHaveBeenCalledTimes(1);
  expect(onEdit).not.toHaveBeenCalled();
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger()).toHaveFocus();
});

it("keeps a blocked action visible but unavailable", () => {
  const { onDelete } = renderMenu({ deleteDisabled: true });
  fireEvent.click(trigger());
  expect(item("Delete filter f1")).toBeDisabled();
  fireEvent.click(item("Delete filter f1"));
  expect(onDelete).not.toHaveBeenCalled();
});

it("closes on Tab and on a press outside, letting another control keep focus", () => {
  renderMenu();
  fireEvent.click(trigger());
  fireEvent.keyDown(item("Edit filter f1"), { key: "Tab" });
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(trigger());
  const elsewhere = screen.getByRole("button", { name: "Elsewhere" });
  fireEvent.pointerDown(elsewhere);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger()).not.toHaveFocus();
  fireEvent.click(trigger());
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger()).toHaveFocus();
});

it("closes with Escape when every action is unavailable", () => {
  render(<MoreMenu label={label} items={[{ key: "delete", label: "Delete", disabled: true, onSelect: vi.fn() }]} />);
  fireEvent.click(trigger());
  const menu = screen.getByRole("menu", { name: label });
  expect(menu).toHaveFocus();
  fireEvent.keyDown(menu, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(trigger()).toHaveFocus();
  fireEvent.click(trigger());
  trigger().focus();
  fireEvent.keyDown(trigger(), { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
});
