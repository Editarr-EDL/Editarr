import { afterEach, expect, it } from "vitest";
import { scrollingAncestor } from "./use-anchored-popup";

afterEach(() => { document.body.innerHTML = ""; });

it("finds the list that scrolls, skipping wrappers that only clip", () => {
  document.body.innerHTML = `<div id="list" style="overflow-y: auto"><div id="hidden" style="overflow: hidden">
    <div style="overflow: clip"><button id="trigger">⋯</button></div></div></div>`;
  expect(scrollingAncestor(document.getElementById("trigger")!)).toBe(document.getElementById("list"));
  document.getElementById("list")!.style.overflowY = "visible";
  expect(scrollingAncestor(document.getElementById("trigger")!)).toBeNull();
});
