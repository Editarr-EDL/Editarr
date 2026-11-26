import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InfoButton } from "./InfoButton";
import { ModalDialog } from "./ModalDialog";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  // InfoButton closes tooltips whose anchor is off screen, so give the anchor a real box.
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 120, 24));
});

it("closes an open tooltip on Escape without closing its dialog, then closes on the next Escape", () => {
  const onClose = vi.fn();
  render(<ModalDialog labelledBy="heading" onClose={onClose}>
    <h2 id="heading">Dialog</h2>
    <InfoButton label="About this">Tooltip text</InfoButton>
  </ModalDialog>);
  const trigger = screen.getByRole("button", { name: "About this" });
  fireEvent.focus(trigger);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Tooltip text");
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
});
