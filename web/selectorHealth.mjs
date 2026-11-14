#!/usr/bin/env node
/**
 * Blogger editor selector health check.
 *
 *   node web/selectorHealth.mjs check   # report only (0 = ok, 2 = broken, 3 = session)
 *   node web/selectorHealth.mjs fix     # infer replacements and patch selectors.json
 *
 * The publish workflow and the daily web-health workflow both call this. When
 * Blogger ships a new editor UI, `fix` writes the inferred primary selectors to
 * web/selectors.json and the workflow opens a pull request.
 */

import fs from 'node:fs';
import path from 'node:path';
import {
  assertStorageState,
  clickDialogButton,
  configFromPayload,
  emit,
  launchContext,
  nowIso,
  openEditorProbe,
  openPublishMenu,
  patchSelector,
  readPayload,
  resolveConfig,
  resolveLocator,
  resolveLocatorInFrame,
  saveArtifacts,
  selectorEntry,
  waitForEditorFrame,
  waitForEditorReady,
  waitForLocator,
} from './lib.mjs';
import { inspectEditor } from './inspect.mjs';

const SKIP_KEYS = new Set([
  'draftConfirmButton',
  'publishConfirmButton',
  'publishedLink',
  'postRow',
]);

const REQUIRED_KEYS = new Set([
  'createPostButton',
  'titleInput',
  'composeFrame',
  'composeEditor',
  'saveDraftButton',
]);

const EDITOR_KEYS = [
  'titleInput',
  'composeFrame',
  'composeEditor',
  'savedIndicator',
  'publishButton',
  'labelsSectionButton',
  'labelsInput',
];

function reportPath(config) {
  return path.join(config.artifactDir, 'selector-health', 'report.json');
}

function writeReport(config, report) {
  try {
    const file = reportPath(config);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(report, null, 2) + '\n', 'utf8');
    return file;
  } catch {
    return '';
  }
}

async function inferSelector(root, kind) {
  return root
    .evaluate((key) => {
      const escape = (value) =>
        window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, '\\$&');
      const attr = (element, name) => element.getAttribute(name) || '';
      const cssPath = (element) => {
        if (!element) return '';
        if (element.id) return `#${escape(element.id)}`;
        for (const name of ['aria-label', 'data-testid', 'name', 'placeholder']) {
          const value = element.getAttribute(name);
          if (value) {
            return `${element.tagName.toLowerCase()}[${name}=${JSON.stringify(value)}]`;
          }
        }
        const parts = [];
        let node = element;
        while (node && node.nodeType === 1 && parts.length < 5) {
          let part = node.tagName.toLowerCase();
          if (node.id) {
            parts.unshift(`#${escape(node.id)}`);
            break;
          }
          const parent = node.parentElement;
          if (parent) {
            const siblings = [...parent.children].filter(
              (child) => child.tagName === node.tagName
            );
            if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
          }
          parts.unshift(part);
          node = node.parentElement;
        }
        return parts.join(' > ');
      };
      const visible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.display !== 'none' &&
          style.visibility !== 'hidden'
        );
      };
      const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const textOf = (element) =>
        norm(
          `${element.innerText || element.textContent || ''} ${attr(element, 'aria-label')} ${attr(
            element,
            'title'
          )}`
        );
      const metaOf = (element) =>
        norm(
          [
            attr(element, 'aria-label'),
            attr(element, 'placeholder'),
            attr(element, 'name'),
            attr(element, 'data-testid'),
            element.id,
            element.className,
          ].join(' ')
        );

      const controls = [
        ...document.querySelectorAll(
          'button, [role="button"], [role="menuitem"], a, input, textarea, [contenteditable="true"]'
        ),
      ].filter(visible);
      const fields = controls.filter(
        (element) =>
          ['INPUT', 'TEXTAREA'].includes(element.tagName) || element.isContentEditable
      );
      const byText = (patterns) => controls.find((element) => patterns.some((re) => re.test(textOf(element))));
      const largest = (elements) =>
        elements
          .map((element) => {
            const rect = element.getBoundingClientRect();
            return { element, area: rect.width * rect.height };
          })
          .sort((left, right) => right.area - left.area)[0]?.element || null;

      switch (key) {
        case 'createPostButton':
          return cssPath(
            byText([/^create new post$/i, /^new post$/i]) || null
          );
        case 'titleInput': {
          const scored = fields
            .map((element) => {
              const meta = metaOf(element);
              let score = 0;
              if (/title/.test(meta)) score += 20;
              if (element.isContentEditable) score += 1;
              if (element.tagName === 'INPUT') score += 3;
              const type = attr(element, 'type').toLowerCase();
              if (['email', 'password', 'search', 'hidden'].includes(type)) score -= 50;
              const rect = element.getBoundingClientRect();
              if (rect.top < window.innerHeight * 0.45) score += 2;
              if (rect.width > 200) score += 1;
              return { element, score };
            })
            .sort((left, right) => right.score - left.score);
          return scored.length && scored[0].score > 2 ? cssPath(scored[0].element) : '';
        }
        case 'composeFrame': {
          const frames = [...document.querySelectorAll('iframe')]
            .filter(visible)
            .map((element) => {
              const rect = element.getBoundingClientRect();
              const meta = metaOf(element);
              let score = rect.width * rect.height;
              if (/editable/.test(meta)) score += 1_000_000;
              if (!attr(element, 'src')) score += 100_000;
              return { element, score };
            })
            .sort((left, right) => right.score - left.score);
          return cssPath(frames[0]?.element || null);
        }
        case 'composeEditor': {
          const editors = fields.filter(
            (element) => element.isContentEditable && !/title/.test(metaOf(element))
          );
          return cssPath(largest(editors));
        }
        case 'savedIndicator':
          return cssPath(byText([/^changes saved$/i, /^saved$/i]));
        case 'htmlViewButton':
          return cssPath(byText([/^html view$/i, /^html$/i, /switch to html/i]));
        case 'htmlViewTextarea': {
          const candidates = fields.filter((element) => {
            if (element.tagName === 'TEXTAREA') return true;
            return element.isContentEditable && !/title/.test(metaOf(element));
          });
          return cssPath(largest(candidates));
        }
        case 'labelsInput':
          return cssPath(fields.find((element) => /label/.test(metaOf(element))) || null);
        case 'labelsSectionButton':
          return cssPath(byText([/^labels$/i, /^post settings$/i, /^settings$/i]));
        case 'saveDraftButton':
          return cssPath(byText([/^save draft$/i, /^save$/i]));
        case 'publishButton':
          return cssPath(byText([/^publish$/i, /^publish now$/i]));
        default:
          return '';
      }
    }, kind)
    .catch(() => '');
}

async function record(page, root, key, matched, results, changed, broken) {
  const entry = selectorEntry(key);
  const required = REQUIRED_KEYS.has(key);

  if (matched) {
    if (matched === entry.primary) {
      results.push({ key, ok: true, changed: false, selector: matched });
    } else {
      results.push({
        key,
        ok: true,
        changed: true,
        old: entry.primary,
        new: matched,
        selector: matched,
      });
      changed.push({ key, from: entry.primary, to: matched });
    }
    return matched;
  }

  let replacement = await inferSelector(root, key);
  if (!replacement && root !== page) replacement = await inferSelector(page, key);
  if (replacement) {
    const didChange = replacement !== entry.primary;
    results.push({
      key,
      ok: true,
      changed: didChange,
      old: entry.primary,
      new: replacement,
      selector: replacement,
    });
    if (didChange) changed.push({ key, from: entry.primary, to: replacement });
    return replacement;
  }

  results.push({ key, ok: !required, skipped: !required, old: entry.primary, selector: '' });
  if (required) broken.push(key);
  return '';
}

async function main() {
  const mode = process.argv[2] === 'fix' ? 'fix' : 'check';
  let config = null;
  let browser = null;
  let page = null;
  const results = [];
  const changed = [];
  const broken = [];
  const applied = [];

  try {
    config = process.argv[3] ? configFromPayload(readPayload(process.argv[3])) : resolveConfig();
    assertStorageState(config);
    const launched = await launchContext(config);
    browser = launched.browser;
    page = launched.page;

    // The create button is only present on the posts list.
    await page.goto(config.postsUrl, { waitUntil: 'domcontentloaded', timeout: config.timeoutMs });
    await page.waitForTimeout(1500);
    await page
      .locator('a[href*="/post/edit/"]')
      .first()
      .waitFor({ state: 'attached', timeout: Math.min(config.timeoutMs, 20000) })
      .catch(() => {});
    let matched =
      (await waitForLocator(page, 'createPostButton', { timeoutMs: 15000 }))?.selector || '';
    await record(page, page, 'createPostButton', matched, results, changed, broken);

    // Probe an existing post so the check never creates a draft.
    const probe = await openEditorProbe(page, config);
    const ready = await waitForEditorReady(page, config);
    const inspection = await inspectEditor(page, config);

    const matchedByKey = {
      ...inspection.selectors,
      titleInput: ready.title.selector,
      composeFrame: ready.frame?.selector || '',
      composeEditor: ready.body.selector,
    };

    const frame = await waitForEditorFrame(page);
    for (const key of EDITOR_KEYS) {
      if (key === 'publishButton' && probe.status !== 'draft') {
        results.push({
          key,
          ok: true,
          skipped: true,
          reason: 'a published post shows Update instead of Publish',
        });
        continue;
      }
      const root = key === 'composeEditor' && frame ? frame.frame : page;
      matched = matchedByKey[key] || '';
      await record(page, root, key, matched, results, changed, broken);
    }

    // The Save menu item only exists while the overflow menu is open.
    if (probe.status !== 'draft') {
      // Published posts expose "Update" instead of "Save"; a missing Save item
      // here is not editor drift.
      results.push({
        key: 'saveDraftButton',
        ok: true,
        skipped: true,
        reason: 'no draft post was available to expose the Save menu item',
      });
    } else {
      matched = inspection.selectors.saveDraftButton || '';
      if (!matched) {
        const menu = await openPublishMenu(page);
        if (menu) {
          matched = (await waitForLocator(page, 'saveDraftButton', { timeoutMs: 5000 }))?.selector || '';
        }
        await record(page, page, 'saveDraftButton', matched, results, changed, broken);
        if (menu) await page.keyboard.press('Escape').catch(() => {});
      } else {
        await record(page, page, 'saveDraftButton', matched, results, changed, broken);
      }
    }

    // HTML view only exists in older editor revisions.
    matched = inspection.selectors.htmlViewButton || '';
    const htmlButton = await record(page, page, 'htmlViewButton', matched, results, changed, broken);
    if (htmlButton) {
      const button = await resolveLocator(page, 'htmlViewButton', { visible: true });
      if (button) {
        await button.locator.click({ timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(500);
        await clickDialogButton(page, [/^ok$/i, /^continue$/i, /^switch$/i, /^yes$/i]);
        matched = (await waitForLocator(page, 'htmlViewTextarea', { timeoutMs: 5000 }))?.selector || '';
      }
      await record(page, page, 'htmlViewTextarea', matched, results, changed, broken);
    } else {
      results.push({
        key: 'htmlViewTextarea',
        ok: true,
        skipped: true,
        reason: 'HTML view is not exposed by this editor revision',
      });
    }

    // Sanity check that the body frame is really reachable.
    if (frame) {
      const body = await resolveLocatorInFrame(frame.frame, 'composeEditor', { visible: true });
      if (!body) {
        results.push({ key: 'composeFrame', ok: false, error: 'the editor iframe has no body editor' });
        if (!broken.includes('composeFrame')) broken.push('composeFrame');
      }
    }

    for (const key of SKIP_KEYS) {
      results.push({
        key,
        ok: true,
        skipped: true,
        reason: 'only present inside a dialog or the posts list',
      });
    }

    if (mode === 'fix') {
      for (const change of changed) {
        if (!change.to || change.to.startsWith('role=')) continue;
        try {
          if (patchSelector(change.key, change.to)) applied.push(change.key);
        } catch {
          // A malformed inferred selector is reported, not written.
        }
      }
    }

    const report = {
      ok: broken.length === 0,
      mode,
      reason: broken.length ? 'selectors_missing' : '',
      broken,
      changed,
      applied,
      results,
      selectorsFile: 'web/selectors.json',
      checkedAt: nowIso(),
      url: page.url(),
    };
    report.reportPath = writeReport(config, report);
    report.artifacts = await saveArtifacts(page, config, 'selector-health', {
      mode,
      broken: broken.join(','),
    });
    emit(report);
    process.exitCode = broken.length === 0 ? 0 : 2;
  } catch (err) {
    const report = {
      ok: false,
      mode,
      reason:
        err?.code === 'BLOGGER_SESSION_REQUIRED' || err?.code === 'BLOGGER_SESSION_EXPIRED'
          ? 'session_expired'
          : 'error',
      error: String(err?.message || err),
      errorCode: err?.code || 'BLOGGER_SELECTOR_HEALTH_FAILED',
      broken,
      changed,
      applied,
      results,
      checkedAt: nowIso(),
    };
    if (config) {
      report.reportPath = writeReport(config, report);
      report.artifacts = page
        ? await saveArtifacts(page, config, 'selector-health-failed', {
            error: report.error,
            code: report.errorCode,
          })
        : '';
    }
    emit(report);
    process.exitCode =
      err?.code === 'BLOGGER_SESSION_REQUIRED' || err?.code === 'BLOGGER_SESSION_EXPIRED' ? 3 : 1;
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
