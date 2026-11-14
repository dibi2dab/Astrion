#!/usr/bin/env node
/**
 * Verify that the saved Google browser session can open the Blogger editor.
 *
 * Usage: node web/sessionCheck.mjs [payload.json]
 *
 * Exit codes:
 *   0 = session and required controls are usable
 *   2 = session is usable but the editor UI changed
 *   3 = the saved session is missing or expired
 */

import {
  WebError,
  assertStorageState,
  configFromPayload,
  emit,
  launchContext,
  nowIso,
  openEditorProbe,
  readPayload,
  resolveConfig,
  resolveLocator,
  saveArtifacts,
  waitForEditorReady,
} from './lib.mjs';
import { LIST_SELECTORS, REQUIRED_SELECTORS, inspectEditor } from './inspect.mjs';

function loadConfig() {
  if (process.argv[2]) {
    return configFromPayload(readPayload());
  }
  return resolveConfig();
}

async function main() {
  let config = null;
  let browser = null;
  let page = null;
  try {
    config = loadConfig();
    assertStorageState(config);
    const launched = await launchContext(config);
    browser = launched.browser;
    page = launched.page;

    const probe = await openEditorProbe(page, config);
    await waitForEditorReady(page, config);
    const inspection = await inspectEditor(page, config);

    // The "Create New Post" button lives on the posts list, not the editor.
    await page.goto(config.postsUrl, { waitUntil: 'domcontentloaded', timeout: config.timeoutMs });
    await page.waitForTimeout(1200);
    const listSelectors = {};
    for (const key of LIST_SELECTORS) {
      listSelectors[key] = (await resolveLocator(page, key, { visible: true }))?.selector || '';
    }

    // A published post has no "Save" menu item and its primary button reads
    // "Update", so those controls can only be verified on a draft.
    const needsDraftControls = probe.status === 'draft';
    const skipped = needsDraftControls ? [] : ['saveDraftButton', 'publishButton'];
    const broken = [
      ...inspection.broken.filter((key) => !skipped.includes(key)),
      ...LIST_SELECTORS.filter((key) => !listSelectors[key]),
    ];

    const result = {
      ok: broken.length === 0,
      backend: 'web',
      session: 'ok',
      blogId: config.blogId,
      blogUrl: config.blogUrl,
      url: page.url(),
      probe,
      selectors: {
        ...inspection.selectors,
        ...listSelectors,
        ...Object.fromEntries(skipped.map((key) => [key, ''])),
      },
      required: REQUIRED_SELECTORS.filter((key) => !skipped.includes(key)),
      skipped,
      broken,
      optionalMissing: inspection.optionalMissing,
      checkedAt: nowIso(),
    };
    if (!result.ok) {
      result.error = `required editor controls are missing: ${broken.join(', ')}`;
      result.errorCode = 'BLOGGER_SELECTOR_MISSING';
      process.exitCode = 2;
    }
    emit(result);
  } catch (err) {
    const artifacts =
      page && config
        ? await saveArtifacts(page, config, 'session-check', {
            error: err?.message || String(err),
            code: err?.code || 'BLOGGER_SESSION_CHECK_FAILED',
          })
        : '';
    const code = err?.code || 'BLOGGER_SESSION_CHECK_FAILED';
    emit({
      ok: false,
      backend: 'web',
      error: err instanceof WebError ? err.message : String(err?.message || err),
      errorCode: code,
      artifacts,
      checkedAt: nowIso(),
    });
    process.exitCode = code === 'BLOGGER_SESSION_REQUIRED' || code === 'BLOGGER_SESSION_EXPIRED' ? 3 : 2;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

main().catch((err) => {
  emit({
    ok: false,
    error: String(err?.message || err),
    errorCode: err?.code || 'BLOGGER_NODE_ERROR',
    checkedAt: nowIso(),
  });
  process.exitCode = 1;
});
