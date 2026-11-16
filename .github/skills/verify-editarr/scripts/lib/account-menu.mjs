/** The sidebar account menu button, optionally for an exact account name. */
export function accountMenuButton(page, name) {
  return name === undefined
    ? page.getByRole("button", { name: /^Account menu for / })
    : page.getByRole("button", { name: `Account menu for ${name}`, exact: true });
}

/** Sign out through the sidebar account menu. */
export async function signOut(page) {
  await accountMenuButton(page).click();
  await page.getByRole("menuitem", { name: "Sign Out", exact: true }).click();
}
