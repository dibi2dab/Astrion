#!/usr/bin/env node
/**
 * Look up a post in the Blogger posts list by title.
 *
 * Usage: node web/findPost.mjs <payload.json>
 *
 * Payload: { "title": "...", "blogId": "...", "blogUrl": "...", "storageStatePath": "..." }
 *
 * Result (last stdout line):
 *   __ASTRION_RESULT__{"ok":true,"found":true,"postId":"...","status":"draft"}
 *
 * This is used after an ambiguous save/publish so a retry can reconcile
 * instead of creating a duplicate post.
 */

import {
  WebError,
  assertStorageState,
  configFromPayload,
  emit,
  findPostInList,
  launchContext,
  nowIso,
  readPayload,
  saveArtifacts,
} from './lib.mjs';

const STOP_CODES = new Set(['BLOGGER_SESSION_REQUIRED', 'BLOGGER_SESSION_EXPIRED']);

async function main() {
  let payload = null;
  let config = null;
  let browser = null;
  let page = null;

  try {
    payload = readPayload();
    config = configFromPayload(payload);
    assertStorageState(config);

    const title = String(payload.title || '').trim();
    if (!title) throw new WebError('payload.title is required', 'BLOGGER_BAD_PAYLOAD');

    const launched = await launchContext(config);
    browser = launched.browser;
    page = launched.page;

    const found = await findPostInList(page, config, title);
    emit({
      ok: true,
      found: Boolean(found),
      postId: found?.id || '',
      url: found?.url || '',
      editUrl: found?.editUrl || '',
      status: found?.status || '',
      title: found?.title || '',
      query: title,
      checkedAt: nowIso(),
    });
  } catch (err) {
    const code = err?.code || 'BLOGGER_FIND_FAILED';
    const artifacts =
      page && config
        ? await saveArtifacts(page, config, 'find-failed', {
            error: err?.message || String(err),
            code,
            title: payload?.title || '',
          })
        : '';
    emit({
      ok: false,
      found: false,
      error: String(err?.message || err),
      errorCode: code,
      artifacts,
      checkedAt: nowIso(),
    });
    process.exitCode = STOP_CODES.has(code) ? 3 : 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

main().catch((err) => {
  emit({
    ok: false,
    found: false,
    error: String(err?.message || err),
    errorCode: err?.code || 'BLOGGER_NODE_ERROR',
    checkedAt: nowIso(),
  });
  process.exitCode = 1;
});
