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
  configFromPayload,
  editorUrlFor,
  emit,
  extractPostId,
  findPostInList,
  launchContext,
  openEditor,
  openPublishMenu,
  readBodyText,
  readPayload,
  resolveLocator,
  saveArtifacts,
  assertStorageState,
  nowIso,
  waitForEditorReady,
  waitForEditorSync,
  waitForLocator,
  waitForSaved,
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

async function setBody(page, config, html, warnings, ready) {
  const frameBody = ready?.body || null;
  if (frameBody) {
    await frameBody.locator.click({ timeout: 8000 }).catch(() => {});
    const inserted = await frameBody.locator
      .evaluate((element, value) => {
        element.focus();
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(element);
        selection?.removeAllRanges();
        selection?.addRange(range);
        try {
          document.execCommand('delete', false);
        } catch {
          element.innerHTML = '';
        }
        let ok = false;
        try {
          ok = document.execCommand('insertHTML', false, value);
        } catch {
          ok = false;
        }
        if (!ok || (element.innerText || '').trim().length < 40) {
          element.innerHTML = value;
        }
        return (element.innerText || '').trim().length;
      }, html)
      .catch(() => 0);

    if (inserted < 40) {
      throw new WebError(
        'the post body could not be inserted into the Blogger editor',
        'BLOGGER_BODY_NOT_SET'
      );
    }
    return { mode: 'compose-frame', selector: frameBody.selector, length: inserted };
  }

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
      'neither the frame body editor nor the HTML view was found',
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

/**
 * Reject any control that could publish a post.
 *
 * The draft path may only ever click a control labelled "Save". A stale or
 * over-broad selector that resolves to Publish/Update would turn a private
 * draft into a public post, so the label is checked immediately before the
 * click instead of trusting the selector alone.
 */
async function saveControlLooksSafe(locator) {
  const info = await locator
    .evaluate((element) => ({
      role: element.getAttribute('role') || '',
      aria: element.getAttribute('aria-label') || '',
      text: (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim(),
    }))
    .catch(() => null);
  if (!info) return false;
  const label = `${info.aria} ${info.text}`.toLowerCase();
  if (/publish|update/.test(label)) return false;
  return /save|저장|保存/.test(label);
}

/** Read the editor's primary action: a draft offers Publish, a live post Update. */
async function editorPrimaryAction(page) {
  const action = await resolveLocator(page, 'publishButton', { visible: true });
  if (!action) return '';
  return String(await action.locator.getAttribute('aria-label').catch(() => '') || '')
    .replace(/\s+/g, ' ')
    .trim();
}

async function applyLabels(page, labels) {
  const wanted = [...new Set(labels.map((label) => String(label).trim()).filter(Boolean))].slice(
    0,
    LABEL_LIMIT
  );
  if (wanted.length === 0) return { added: 0, skipped: true };

  const input = await openLabels(page);
  if (!input) {
    return {
      added: 0,
      skipped: true,
      warning: 'the labels field was not found; the post was saved without labels',
    };
  }

  try {
    // The labels control is a comma-separated textarea, not a chip widget.
    // Real keystrokes matter: setting .value directly, or pressing Enter (which
    // inserts a newline), leaves Blogger's model unchanged. Each label must be
    // followed by a comma, and the field must blur so the model commits.
    await input.locator.click({ timeout: 5000 });
    await page.keyboard.press('Control+A').catch(() => {});
    await page.keyboard.press('Delete').catch(() => {});
    await page.waitForTimeout(250);
    for (let index = 0; index < wanted.length; index += 1) {
      const label = wanted[index];
      await page.keyboard.type(`${label},`, { delay: 30 });
      if (index < wanted.length - 1) await page.keyboard.type(' ', { delay: 30 });
      await page.waitForTimeout(300);
    }
    const typed = String(await input.locator.inputValue().catch(() => ''));
    if (!/,\s*$/.test(typed)) await page.keyboard.type(',', { delay: 30 });
    await page.waitForTimeout(400);

    // Blur commits the label list to the post model.
    const title = await resolveLocator(page, 'titleInput', { visible: true });
    if (title) await title.locator.click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(600);

    const committed = String(await input.locator.inputValue().catch(() => ''))
      .toLowerCase()
      .trim();
    const added = wanted.filter((label) => committed.includes(label.toLowerCase())).length;
    return {
      added,
      skipped: added === 0,
      warning:
        added < wanted.length
          ? `only ${added}/${wanted.length} labels were applied`
          : '',
    };
  } catch (err) {
    return {
      added: 0,
      skipped: true,
      warning: `the labels field could not be filled: ${err.message}`,
    };
  }
}

async function waitForPublishConfirmation(page, config) {
  const deadline = Date.now() + Math.min(config.timeoutMs, 30000);
  while (Date.now() < deadline) {
    const link = await resolveLocator(page, 'publishedLink', { visible: true });
    if (link) return true;
    const text = await readBodyText(page);
    if (/your post (has been|was) published|published just now|view post/i.test(text)) return true;
    await page.waitForTimeout(500);
  }
  return false;
}

async function confirmDraftInList(page, config, title, warnings) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const found = await findPostInList(page, config, title).catch((err) => {
      if (err?.code?.startsWith('BLOGGER_SESSION')) throw err;
      warnings.push(`post-list confirmation attempt ${attempt} failed: ${err.message}`);
      return null;
    });
    if (found) return found;
    if (attempt < 3) await page.waitForTimeout(2000);
  }
  return null;
}

async function verifyDraftContent(page, config, postId, title) {
  await page.goto(editorUrlFor(config, postId), {
    waitUntil: 'domcontentloaded',
    timeout: config.timeoutMs,
  });
  const ready = await waitForEditorReady(page, config);
  await page.waitForTimeout(1500);

  const tagName = await ready.title.locator
    .evaluate((element) => element.tagName.toLowerCase())
    .catch(() => 'input');
  const actualTitle = String(
    tagName === 'input' || tagName === 'textarea'
      ? await ready.title.locator.inputValue().catch(() => '')
      : await ready.title.locator.innerText().catch(() => '')
  )
    .replace(/\s+/g, ' ')
    .trim();
  const bodyText = String(await ready.body.locator.innerText().catch(() => ''))
    .replace(/\s+/g, ' ')
    .trim();
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const action = await editorPrimaryAction(page);
  return {
    titleOk: normalize(actualTitle) === normalize(title),
    title: actualTitle,
    bodyLength: bodyText.length,
    bodyPreview: bodyText.slice(0, 80),
    // A reopened draft still offers "Publish"; a live post offers "Update".
    // Unknown (empty) is treated as not-live so the posts-list check stays the
    // authority, but an explicit "Update" is a hard failure in draft mode.
    isDraft: !/^update$/i.test(action),
    editorAction: action,
  };
}

async function saveDraft(page, config, title, warnings) {
  // Typing triggers a delayed autosave; saving before it completes persists the
  // previous revision. Wait for the editor to report a settled save first.
  const synced = await waitForEditorSync(page, Math.min(config.timeoutMs, 30000));
  if (!synced) warnings.push('the editor never reported a settled save before clicking Save');

  // A brand-new draft offers "Publish". If the editor offers "Update", Blogger
  // already considers this post live; saving would silently modify a public
  // post, so stop before touching any control.
  const initialAction = await editorPrimaryAction(page);
  if (/^update$/i.test(initialAction)) {
    throw new WebError(
      'the Blogger editor is showing "Update", which means this post is already live. ' +
        'Astrion will not save a live post through the draft path.',
      'BLOGGER_DRAFT_BECAME_LIVE'
    );
  }

  let control = null;

  // The current editor hides Save in the split-button menu next to Publish.
  const direct = await resolveLocator(page, 'saveDraftButton', { visible: true });
  if (direct && (await saveControlLooksSafe(direct.locator))) {
    control = direct;
  } else {
    const menu = await openPublishMenu(page);
    if (menu) {
      const item = await waitForLocator(page, 'saveDraftButton', { timeoutMs: 6000 });
      if (item && (await saveControlLooksSafe(item.locator))) control = item;
    }
  }

  if (!control) {
    throw new WebError(
      'the Save draft control was not found. Astrion will not click Publish while a draft was requested.',
      'BLOGGER_DRAFT_BUTTON_MISSING'
    );
  }
  await control.locator.click({ timeout: 10000 });

  await page.waitForTimeout(700);
  await clickDialogButton(page, [/^save draft$/i, /^ok$/i, /^continue$/i]);
  const confirmed = await waitForSaved(page, Math.min(config.timeoutMs, 30000));
  const postId = extractPostId(page.url(), config.blogId);
  if (!confirmed) {
    warnings.push('Blogger did not show the "Changes saved" indicator; checking the posts list');
  }

  // The posts list is the source of truth. A visible saved indicator alone is
  // not enough: it has been shown while an empty draft was stored.
  const found = await confirmDraftInList(page, config, title, warnings);
  if (!found) {
    throw new WebError(
      'Save was clicked but the draft with this title never appeared in the Blogger posts list. ' +
        'The next run reconciles by title before creating another post.',
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }

  const id = found.id || postId;
  if (!id) {
    throw new WebError(
      'the saved draft could not be mapped to a Blogger post id.',
      'BLOGGER_POST_ID_MISSING'
    );
  }

  // Regression guard: draft mode must never leave a public post behind. The
  // earlier one-off upload reported DRAFT while four posts were actually live
  // because this status was never checked.
  if (found.status !== 'draft') {
    throw new WebError(
      `Draft mode saved "${title}" as ${found.status || 'live'} (post ${id}) instead of a draft. ` +
        'Blogger did not keep it private; revert it to draft in the editor, then retry.',
      'BLOGGER_DRAFT_BECAME_LIVE'
    );
  }

  // Reopen the saved post and confirm the server stored the text, not just a
  // title in the list. Slower, but it turns silent empty drafts into a retry.
  const verified = await verifyDraftContent(page, config, id, title).catch((err) => {
    if (err?.code?.startsWith('BLOGGER_SESSION') || err?.code === 'BLOGGER_SELECTOR_MISSING') {
      throw err;
    }
    warnings.push(`draft verification failed: ${err.message}`);
    return { titleOk: false, title: '', bodyLength: 0, bodyPreview: '', isDraft: true };
  });

  if (!verified.titleOk) {
    throw new WebError(
      `Save was reported but the stored draft title is "${verified.title || '(empty)'}" ` +
        `instead of "${title}". The next run reconciles by title before creating another post.`,
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }
  if (verified.bodyLength < 40) {
    throw new WebError(
      'the saved draft has an empty body. The next run reconciles by title before creating another post.',
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }
  if (verified.isDraft === false) {
    throw new WebError(
      `Draft mode reported success but the reopened editor treats "${title}" as live ` +
        `(action: ${verified.editorAction || 'Update'}). Revert it to draft before retrying.`,
      'BLOGGER_DRAFT_BECAME_LIVE'
    );
  }

  return {
    postId: id,
    editUrl: editorUrlFor(config, id),
    url: found.url || '',
    how: 'post-list+verified',
    verified,
  };
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

  const live = await waitForPublishConfirmation(page, config);
  const postId = extractPostId(page.url(), config.blogId);
  const found = await confirmDraftInList(page, config, title, warnings);
  if (!found) {
    throw new WebError(
      'Publish was clicked but the post with this title never appeared in the Blogger posts list. ' +
        'The next run reconciles by title before creating another post.',
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }
  if (found.status !== 'live') {
    throw new WebError(
      `Publish was clicked but the post is still listed as ${found.status || 'draft'}.`,
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }

  const id = found.id || postId;
  if (!id) {
    throw new WebError(
      'the published post could not be mapped to a Blogger post id.',
      'BLOGGER_POST_ID_MISSING'
    );
  }
  const verified = await verifyDraftContent(page, config, id, title).catch((err) => {
    if (err?.code?.startsWith('BLOGGER_SESSION') || err?.code === 'BLOGGER_SELECTOR_MISSING') {
      throw err;
    }
    warnings.push(`publish verification failed: ${err.message}`);
    return { titleOk: false, title: '', bodyLength: 0, bodyPreview: '' };
  });
  if (!verified.titleOk) {
    throw new WebError(
      `Publish was reported but the stored title is "${verified.title || '(empty)'}".`,
      'BLOGGER_SAVE_UNCONFIRMED'
    );
  }
  if (verified.bodyLength < 40) {
    throw new WebError('the published post has an empty body.', 'BLOGGER_SAVE_UNCONFIRMED');
  }

  return {
    postId: id,
    editUrl: editorUrlFor(config, id),
    url: found.url || '',
    how: live ? 'publish-confirmation+verified' : 'post-list+verified',
    verified,
  };
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

    await openEditor(page, config, { create: true });
    const ready = await waitForEditorReady(page, config);

    const titleSelector = await setTitle(page, config, title);
    const body = await setBody(page, config, html, warnings, ready);
    await waitForEditorSync(page, Math.min(config.timeoutMs, 30000));

    const labelResult = await applyLabels(page, asArray(payload.labels));
    if (labelResult.warning) warnings.push(labelResult.warning);
    await waitForEditorSync(page, Math.min(config.timeoutMs, 30000));

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
      editUrl: saved.editUrl || (saved.postId ? editorUrlFor(config, saved.postId) : ''),
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
