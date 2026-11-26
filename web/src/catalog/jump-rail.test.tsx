import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JellyfinMediaItem } from "../api/api-media-types";
import { useVisibleIndex, type JumpAnchor } from "./jump-rail";

const items = Array.from({ length: 10 }, (_, index) => ({ id: `item-${index}`, name: `Item ${index}` }) as JellyfinMediaItem);

function Harness({ anchor }: { anchor: JumpAnchor | null }) {
  const container = useRef<HTMLDivElement>(null);
  const visible = useVisibleIndex(container, items, 0, true, 0, anchor);
  return <div ref={container}>
    <output>{visible}</output>
    {items.map((item, index) => <div key={item.id} data-catalog-index={index} />)}
  </div>;
}

describe("useVisibleIndex", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("reports the jumped-to title until the page scrolls, recording the position of every jump", async () => {
    let scrollY = 0;
    vi.spyOn(window, "scrollY", "get").mockImplementation(() => scrollY);
    // One title per 100px row, the first one at the top; the page can't scroll title 3 to the top.
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      const top = Number((this as HTMLElement).dataset?.catalogIndex ?? 0) * 100 - scrollY;
      return { top, bottom: top + 90, height: 90, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
    });
    const shown = () => screen.getByRole("status").textContent;
    const { rerender } = render(<Harness anchor={{ id: "item-3", scrollY: 0 }} />);
    expect(shown()).toBe("3");

    scrollY = 120;
    act(() => { window.dispatchEvent(new Event("scroll")); });
    await waitFor(() => expect(shown()).toBe("1"));

    // Jumping to the same title again keeps it current at the new position.
    rerender(<Harness anchor={{ id: "item-3", scrollY: 120 }} />);
    expect(shown()).toBe("3");
  });
});
