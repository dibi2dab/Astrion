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
  openEditor,
  readPayload,
  resolveConfig,
  saveArtifacts,
  waitForEditorReady,
} from './lib.mjs';
import { inspectEditor } from './inspect.mjs';

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

    await openEditor(page, config);
    await waitForEditorReady(page, config);
    const inspection = await inspectEditor(page, config);

    const result = {
      ok: inspection.broken.length === 0,
      backend: 'web',
      session: 'ok',
      blogId: config.blogId,
      blogUrl: config.blogUrl,
      url: page.url(),
      selectors: inspection.selectors,
      broken: inspection.broken,
      optionalMissing: inspection.optionalMissing,
      checkedAt: nowIso(),
    };
    if (!result.ok) {
      result.error = `required editor controls are missing: ${inspection.broken.join(', ')}`;
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
