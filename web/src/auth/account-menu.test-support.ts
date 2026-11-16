import { fireEvent, screen, within } from "@testing-library/react";

/** Open the sidebar account menu and return it. */
export async function openAccountMenu(username?: string | RegExp) {
  const name = username === undefined ? /^Account menu for / : typeof username === "string" ? `Account menu for ${username}` : username;
  fireEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("menu");
}

/** Sign out through the sidebar account menu. */
export async function signOutThroughMenu(username?: string | RegExp) {
  const menu = await openAccountMenu(username);
  fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign Out" }));
}
