import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OperatorAccessError } from "../runtime";
import { renderOperator } from "../test-render";
import { useMutation } from "./backup-mutation";
import { configErrors, destinationError } from "./schedule-form";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Probe({ send }: { send: () => Promise<string> }) {
  const mutation = useMutation();
  return <><button disabled={mutation.busy} onClick={() => mutation.run(send, value => value)}>Send</button>
    <p role={mutation.failed ? "alert" : "status"}>{mutation.message}</p></>;
}

it("settles when the API wrapper throws synchronously", async () => {
  renderOperator(<Probe send={() => { throw new OperatorAccessError(400); }} />);
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Refused: check the values and try again.");
  expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
});

it("applies the service's destination rules before saving", () => {
  for (const valid of ["", "nightly", " nightly/2026 ", "a.b/c_d/e-f/g"]) expect(destinationError(valid)).toBeNull();
  for (const invalid of ["night\u0000ly", "night\u0007ly", "a\\b", "/abs", "a/../b", "a/b/c/d/e", ".hidden", "staged", "staged/x", "x".repeat(65),
    "a".repeat(513), "spaces here"]) expect(destinationError(invalid)).not.toBeNull();
  expect(configErrors({ enabled: true, frequency: "daily", atUtc: "03:30", keepLast: 14, keepWeekly: 8, destination: "a\u0000", integrityCheck: true }))
    .toHaveProperty("destination");
});
