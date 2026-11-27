import { afterEach, expect, it } from "vitest";
import { fitPopupVertically, popupBounds, scrollingAncestor } from "./use-anchored-popup";

afterEach(() => { document.body.innerHTML = ""; });

it("finds the list that scrolls, skipping wrappers that only clip", () => {
  document.body.innerHTML = `<div id="list" style="overflow-y: auto"><div id="hidden" style="overflow: hidden">
    <div style="overflow: clip"><button id="trigger">⋯</button></div></div></div>`;
  expect(scrollingAncestor(document.getElementById("trigger")!)).toBe(document.getElementById("list"));
  document.getElementById("list")!.style.overflowY = "visible";
  expect(scrollingAncestor(document.getElementById("trigger")!)).toBeNull();
});

it("fits a popup below when it can, otherwise on the roomier side, capped to that space", () => {
  const bounds = { top: 100, bottom: 800 };
  expect(fitPopupVertically({ top: 200, bottom: 250 }, 300, bounds)).toEqual({ top: 256, maxHeight: 544, room: 544 });
  expect(fitPopupVertically({ top: 600, bottom: 650 }, 560, bounds)).toEqual({ top: 100, maxHeight: 494, room: 494 });
  expect(fitPopupVertically({ top: 400, bottom: 450 }, 560, bounds)).toEqual({ top: 456, maxHeight: 344, room: 344 });
});

it("narrows popup bounds to the trigger's scroll container", () => {
  document.body.innerHTML = `<div id="body" style="overflow-y: auto"><button id="trigger">Change</button></div>`;
  const container = document.getElementById("body")!;
  container.getBoundingClientRect = () => ({ top: 120, bottom: 700, height: 580 }) as DOMRect;
  expect(popupBounds(document.getElementById("trigger")!)).toEqual({ top: 120, bottom: Math.min(700, window.innerHeight - 8) });
});
