#!/usr/bin/env node
/**
 * Create a Blogger post through the web editor.
 *
 * Usage: node web/publishBlogger.mjs <payload.json>
 *
 * Payload:
 *   {
 *     "title": "...",
 *     "contentHtml": "<div class=\"astrion-post\">...</div>",
 *     "labels": ["Physics", "Optics"],
 *     "mode": "draft" | "publish",
 *     "blogId": "...", "blogUrl": "...",
 *     "storageStatePath": ".state/blogger_storage_state.json",
 *     "headless": true, "timeoutMs": 45000,
 *     "debug": false
 *   }
 *
 * Result (last stdout line):
 *   __ASTRION_RESULT__{"ok":true,"postId":"...","url":"...","editUrl":"..."}
 */

import fs from 'node:fs';
import {
  WebError,
  clickDialogButton,
  clickLocator,
  configFromPayload,
  emit,
  extractPostId,
  findPostInList,
  launchContext,
  openEditor,
  readBodyText,
  readPayload,
  resolveLocator,
  saveArtifacts,
  assertStorageState,
  nowIso,
  waitForEditorReady,
  waitForLocator,
} from './lib.mjs';

const LABEL_LIMIT = 10;

function asArray(value) {
  return Array.isArray(value) ? value.filter((item) => String(item || '').trim()) : [];
}

async function setTitle(page, config, title) {
  const target = await waitForLocator(page, 'titleInput', { timeoutMs: 15000 });
  if (!target) {
    throw new WebError(
      'the post title field was not found in the Blogger editor',
      'BLOGGER_SELECTOR_MISSING'
    );
  }
  const expected = String(title || '').trim();
  const tagName = await target.locator
    .evaluate((element) => element.tagName.toLowerCase())
    .catch(() => 'input');

  for (let attempt = 0; attempt < 3; attempt += 1) {
    await target.locator.click({ timeout: 8000 }).catch(() => {});
    if (tagName === 'input' || tagName === 'textarea') {
      await target.locator.fill('', { timeout: 5000 }).catch(() => {});
      await target.locator.fill(expected, { timeout: 8000 }).catch(() => {});
    } else {
      await page.keyboard.press('Control+A').catch(() => {});
      await page.keyboard.press('Delete').catch(() => {});
      await page.keyboard.insertText(expected).catch(() => {});
    }
    await target.locator
      .evaluate((element, value) => {
        if (!('value' in element)) return;
        element.value = value;
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
      }, expected)
      .catch(() => {});
    await page.waitForTimeout(250);

    const current =
      tagName === 'input' || tagName === 'textarea'
        ? await target.locator.inputValue().catch(() => '')
        : await target.locator.innerText().catch(() => '');
    if (String(current).trim() === expected) return target.selector;
  }
  throw new WebError(
    `the post title could not be entered (wanted "${expected}")`,
    'BLOGGER_TITLE_NOT_SET'
  );
}

async function readEditorValue(locator) {
  const tagName = await locator.evaluate((element) => element.tagName.toLowerCase()).catch(() => '');
  if (tagName === 'input' || tagName === 'textarea') {
    return locator.inputValue().catch(() => '');
  }
  return locator.innerText().catch(() => '');
}

async function switchToHtmlView(page) {
  const already = await resolveLocator(page, 'htmlViewTextarea', { visible: true });
  if (already) return already;

  const button = await resolveLocator(page, 'htmlViewButton', { visible: true });
  if (!button) return null;
  await button.locator.click({ timeout: 8000 });
  await page.waitForTimeout(500);
  await clickDialogButton(page, [/^ok$/i, /^continue$/i, /^switch$/i, /^yes$/i]);
  return waitForLocator(page, 'htmlViewTextarea', { timeoutMs: 10000 });
}

async function setBody(page, config, html, warnings) {
  const htmlTarget = await switchToHtmlView(page);
  if (htmlTarget) {
    await htmlTarget.locator.click({ timeout: 8000 }).catch(() => {});
    await page.keyboard.press('Control+A').catch(() => {});
    await htmlTarget.locator.fill(html, { timeout: 15000 }).catch(async () => {
      await page.keyboard.insertText(html);
    });
    await htmlTarget.locator
      .evaluate((element, value) => {
        if ('value' in element) {
          element.value = value;
          element.dispatchEvent(new Event('input', { bubbles: true }));
          element.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }, html)
      .catch(() => {});

    const length = String(await readEditorValue(htmlTarget.locator)).length;
    if (length >= Math.min(html.length * 0.6, 200)) {
      return { mode: 'html', selector: htmlTarget.selector, length };
    }
    warnings.push('HTML view did not retain the body; trying the compose editor');
  }

  const editor = await resolveLocator(page, 'composeEditor', { visible: true });
  if (!editor) {
    throw new WebError(
      'neither the HTML view nor the rich-text body editor was found',
      'BLOGGER_SELECTOR_MISSING'
    );
  }
  await editor.locator.click({ timeout: 8000 });
  await page.keyboard.press('Control+A').catch(() => {});
  const inserted = await editor.locator
    .evaluate((element, value) => {
      element.focus();
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(element);
      selection?.removeAllRanges();
      selection?.addRange(range);
      let ok = false;
      try {
        ok = document.execCommand('insertHTML', false, value);
      } catch {
        ok = false;
      }
      if (!ok) element.innerHTML = value;
      element.dispatchEvent(
        new InputEvent('input', {
          bubbles: true,
          inputType: 'insertFromPaste',
          data: null,
        })
      );
      return element.innerText.length;
    }, html)
    .catch(() => 0);

  if (inserted < 40) {
    throw new WebError(
      'the post body could not be inserted into the Blogger editor',
      'BLOGGER_BODY_NOT_SET'
    );
  }
  return { mode: 'compose', selector: editor.selector, length: inserted };
}

async function openLabels(page) {
  const direct = await resolveLocator(page, 'labelsInput', { visible: true });
  if (direct) return direct;

  for (const key of ['labelsSectionButton']) {
    const button = await resolveLocator(page, key, { visible: true });
    if (!button) continue;
    await button.locator.click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    const input = await waitForLocator(page, 'labelsInput', { timeoutMs: 3000 });
    if (input) return input;
  }

  // Sidebar sections sometimes need two clicks: "Post settings" then "Labels".
  const settings = await page.getByRole('button', { name: /post settings/i }).first();
  if (await firstVisibleSafe(settings)) {
    await settings.click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
    return waitForLocator(page, 'labelsInput', { timeoutMs: 3000 });
  }
  return null;
}

async function firstVisibleSafe(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    if (await locator.nth(index).isVisible().catch(() => false)) return locator.nth(index);
  }
  return null;
}

async function applyLabels(page, labels) {
  const wanted = [...new Set(labels.map((label) => String(label).trim()).filter(Boolean))].slice(
    0,
    LABEL_LIMIT
  );
  if (wanted.length === 0) return { added: 0, skipped: true };

  let added = 0;
  for (const label of wanted) {
    const input = await openLabels(page);
    if (!input) {
      return {
        added,
        skipped: true,
        warning: 'the labels field was not found; the post was saved without labels',
      };
    }
    try {
      await input.locator.click({ timeout: 5000 });
      await input.locator.fill(label, { timeout: 5000 });
      await page.keyboard.press('Enter');
      await page.waitForTimeout(200);
      added += 1;
    } catch {
      break;
    }
  }
  return {
    added,
    skipped: added === 0,
    warning: added < wanted.length ? `only ${added}/${wanted.length} labels were applied` : '',
  };
}

async function waitForConfirmation(page, config, mode) {
  const deadline = Date.now() + Math.min(config.timeoutMs, 30000);
  while (Date.now() < deadline) {
    const postId = extractPostId(page.url(), config.blogId);
    if (postId) return { postId, editUrl: page.url(), how: 'url' };

    const text = await readBodyText(page);
    const confirmed =
      mode === 'draft'
        ? /draft saved|saved just now|changes saved/i.test(text)
        : /published|your post is live|post published/i.test(text);
    if (confirmed) {
      return { postId: '', editUrl: '', how: 'toast' };
    }
    await page.waitForTimeout(500);
  }
  return { postId: '', editUrl: '', how: 'unconfirmed' };
}

async function saveDraft(page, config, title, warnings) {
  let clicked = false;
  const save = await resolveLocator(page, 'saveDraftButton', { visible: true });
  if (save) {
    await save.locator.click({ timeout: 10000 });
    clicked = true;
  } else {
    // Some editor revisions put Save behind an overflow menu.
    const overflow = await firstVisibleSafe(
      page.getByRole('button', { name: /more options|more|options/i })
    );
    if (overflow) {
      await overflow.click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(400);
      const menuSave = await resolveLocator(page, 'saveDraftButton', { visible: true });
      if (menuSave) {
        await menuSave.locator.click({ timeout: 8000 });
        clicked = true;
      }
    }
  }

  if (!clicked) {
    // Last resort: open the publish dialog if it offers a "Save draft" option.
    const publish = await resolveLocator(page, 'publishButton', { visible: true });
    if (publish) {
      await publish.locator.click({ timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(700);
      clicked = await clickDialogButton(page, [/^save draft$/i, /^save$/i]);
    }
  }

  if (!clicked) {
    throw new WebError(
      'the Save draft control was not found. Astrion will not click Publish while a draft was requested.',
      'BLOGGER_DRAFT_BUTTON_MISSING'
    );
  }

  await page.waitForTimeout(700);
  await clickDialogButton(page, [/^save draft$/i, /^save$/i, /^ok$/i, /^continue$/i]);
  const confirmation = await waitForConfirmation(page, config, 'draft');
  const found = await findPostInList(page, config, title).catch((err) => {
    if (err?.code?.startsWith('BLOGGER_SESSION')) throw err;
    warnings.push(`post-list confirmation failed: ${err.message}`);
    return null;
  });

  if (found) {
    return {
      postId: found.id || confirmation.postId,
      editUrl: found.editUrl || confirmation.editUrl,
      url: found.url || '',
      how: 'post-list',
    };
  }
  if (confirmation.how !== 'unconfirmed') {
    return { postId: confirmation.postId, editUrl: confirmation.editUrl, url: '', how: confirmation.how };
  }
  throw new WebError(
    'Save was clicked but the editor did not confirm the draft. It may still have been saved; ' +
      'the next run reconciles by title before creating another post.',
    'BLOGGER_SAVE_UNCONFIRMED'
  );
}

async function publishLive(page, config, title, warnings) {
  const publish = await resolveLocator(page, 'publishButton', { visible: true });
  if (!publish) {
    throw new WebError(
      'the Publish control was not found in the Blogger editor',
      'BLOGGER_SELECTOR_MISSING'
    );
  }
  await publish.locator.click({ timeout: 10000 });
  await page.waitForTimeout(800);

  const confirmed = await clickDialogButton(page, [
    /^publish$/i,
    /^publish now$/i,
    /^confirm$/i,
    /^ok$/i,
  ]);
  if (!confirmed) {
    const fallback = await firstVisibleSafe(page.getByRole('button', { name: /^publish$/i }));
    if (!fallback) {
      throw new WebError(
        'the publish confirmation dialog did not expose a Publish button',
        'BLOGGER_SELECTOR_MISSING'
      );
    }
    await fallback.click({ timeout: 8000 });
  }

  const confirmation = await waitForConfirmation(page, config, 'publish');
  const found = await findPostInList(page, config, title).catch((err) => {
    if (err?.code?.startsWith('BLOGGER_SESSION')) throw err;
    warnings.push(`post-list confirmation failed: ${err.message}`);
    return null;
  });
  if (found) {
    return {
      postId: found.id || confirmation.postId,
      editUrl: found.editUrl || confirmation.editUrl,
      url: found.url || '',
      how: 'post-list',
    };
  }
  if (confirmation.how !== 'unconfirmed') {
    return { postId: confirmation.postId, editUrl: confirmation.editUrl, url: '', how: confirmation.how };
  }
  throw new WebError(
    'Publish was clicked but the editor did not confirm the live post. ' +
      'The next run reconciles by title before creating another post.',
    'BLOGGER_SAVE_UNCONFIRMED'
  );
}

async function main() {
  let payload = null;
  let config = null;
  let page = null;
  let browser = null;
  const warnings = [];

  try {
    payload = readPayload();
    config = configFromPayload(payload);
    assertStorageState(config);

    const title = String(payload.title || '').trim();
    const html = String(payload.contentHtml || '');
    if (!title) throw new WebError('payload.title is required', 'BLOGGER_BAD_PAYLOAD');
    if (!html) throw new WebError('payload.contentHtml is required', 'BLOGGER_BAD_PAYLOAD');
    const mode = payload.mode === 'publish' ? 'publish' : 'draft';

    const launched = await launchContext(config);
    browser = launched.browser;
    page = launched.page;

    await openEditor(page, config);
    await waitForEditorReady(page, config);

    const titleSelector = await setTitle(page, config, title);
    const body = await setBody(page, config, html, warnings);
    const labelResult = await applyLabels(page, asArray(payload.labels));
    if (labelResult.warning) warnings.push(labelResult.warning);

    const saved =
      mode === 'draft'
        ? await saveDraft(page, config, title, warnings)
        : await publishLive(page, config, title, warnings);

    if (!saved.postId) {
      warnings.push('the editor did not expose the Blogger post id; state stores an empty id');
    }

    const result = {
      ok: true,
      mode,
      postId: saved.postId || '',
      url: saved.url || '',
      editUrl: saved.editUrl || (saved.postId ? `${config.editorUrl}/${saved.postId}` : ''),
      status: mode === 'draft' ? 'DRAFT' : 'LIVE',
      publishedAt: mode === 'publish' ? nowIso() : '',
      title,
      titleSelector,
      bodyMode: body.mode,
      labelsApplied: labelResult.added,
      savedBy: saved.how,
      warnings,
      checkedAt: nowIso(),
    };
    emit(result);
  } catch (err) {
    const artifacts =
      page && config
        ? await saveArtifacts(page, config, 'publish-failed', {
            error: err?.message || String(err),
            code: err?.code || 'BLOGGER_PUBLISH_FAILED',
            title: payload?.title || '',
          })
        : '';
    emit({
      ok: false,
      error: String(err?.message || err),
      errorCode: err?.code || 'BLOGGER_PUBLISH_FAILED',
      artifacts,
      warnings,
      checkedAt: nowIso(),
    });
    process.exitCode = 1;
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
