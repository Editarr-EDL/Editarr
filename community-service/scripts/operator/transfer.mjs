import { CliError } from './transport.mjs';

export async function transferPage(cursor, read, commit) {
  const page = await read(cursor);
  if (!page || page.cursor !== cursor || !Object.hasOwn(page, 'data') ||
      (page.nextCursor !== null && (typeof page.nextCursor !== 'string' || !page.nextCursor ||
        page.nextCursor.length > 1024 || page.nextCursor === cursor))) throw new CliError('invalid_transfer_page');
  await commit(page);
  return { nextCursor: page.nextCursor, complete: page.nextCursor === null };
}