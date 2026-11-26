import { describe, expect, it } from "vitest";
import { titleLetter } from "./title-letters";

describe("titleLetter", () => {
  it("folds case and accents like the server and groups everything outside A–Z under #", () => {
    const letters = Object.fromEntries(["Zèbra", "Ωmega", "straße", "ßeta", "2001", "Élan", "Ägypten", "Ярость", "!Bang", "Beta"]
      .map((name) => [name, titleLetter({ name, sort_name: null })]));
    expect(letters).toEqual({
      Zèbra: "Z", Ωmega: "#", straße: "S", ßeta: "S", 2001: "#", Élan: "E", Ägypten: "A", Ярость: "#", "!Bang": "#", Beta: "B",
    });
    expect(titleLetter({ name: "The Thing", sort_name: "thing" })).toBe("T");
  });
});
