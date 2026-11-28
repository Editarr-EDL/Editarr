import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ReviewFilterTimeFields } from "./review-filter-time-fields";

it("edits hundredths in both fields without rounding an unchanged endpoint", () => {
  const onChange = vi.fn();
  const onInvalid = vi.fn();
  function Form() {
    const [value, setValue] = useState({ start_seconds: 3723.456, end_seconds: 3723.789 as number | null });
    return <ReviewFilterTimeFields controlId="Filter" value={value} onInvalidChange={onInvalid}
      onChange={(timing) => { setValue(timing); onChange(timing); }} />;
  }
  render(<Form />);
  const start = screen.getByRole("textbox", { name: "Filter start time" });
  const end = screen.getByRole("textbox", { name: "Filter end time" });
  expect(start).toHaveValue("01:02:03.46");
  expect(end).toHaveValue("01:02:03.79");
  fireEvent.change(end, { target: { value: "01:02:03.95" } });
  expect(onChange).toHaveBeenLastCalledWith({ start_seconds: 3723.456, end_seconds: 3723.95 });
  fireEvent.change(start, { target: { value: "01:02:04.25" } });
  expect(onInvalid).toHaveBeenLastCalledWith(true);
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(screen.getByText("End time must be after start time.")).toBeVisible();
  fireEvent.change(end, { target: { value: "01:02:05.25" } });
  expect(onInvalid).toHaveBeenLastCalledWith(false);
  expect(onChange).toHaveBeenLastCalledWith({ start_seconds: 3724.25, end_seconds: 3725.25 });
  fireEvent.change(end, { target: { value: "" } });
  expect(onChange).toHaveBeenLastCalledWith({ start_seconds: 3724.25, end_seconds: null });
});
