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
  WebError,
  assertStorageState,
  clickDialogButton,
  configFromPayload,
  cssPathOf,
  emit,
  launchContext,
  loadSelectors,
  nowIso,
  openEditor,
  patchSelector,
  readPayload,
  resolveConfig,
  resolveLocator,
  saveArtifacts,
  selectorEntry,
  waitForEditorReady,
  waitForLocator,
} from './lib.mjs';

const SKIP_KEYS = new Set([
  'draftConfirmButton',
  'publishConfirmButton',
  'publishedLink',
  'postRow',
]);

const REQUIRED_KEYS = new Set(['titleInput', 'composeEditor', 'saveDraftButton']);

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

async function testSelector(page, selector, visible = true) {
  if (!selector) return false;
  try {
    const locator = page.locator(selector);
    const count = await locator.count();
    if (count === 0) return false;
    if (!visible) return true;
    for (let index = 0; index < count; index += 1) {
      if (await locator.nth(index).isVisible().catch(() => false)) return true;
    }
    return false;
  } catch {
    return false;
  }
}

async function inferSelector(page, kind) {
  return page
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
          'button, [role="button"], a, input, textarea, [contenteditable="true"]'
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
        case 'composeEditor': {
          const editors = fields.filter(
            (element) => element.isContentEditable && !/title/.test(metaOf(element))
          );
          return cssPath(largest(editors));
        }
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

async function evaluateKey(page, key, results, changed, broken) {
  const entry = selectorEntry(key);
  const required = REQUIRED_KEYS.has(key);

  if (await testSelector(page, entry.primary, true)) {
    results.push({ key, ok: true, changed: false, selector: entry.primary });
    return entry.primary;
  }

  const resolved = await resolveLocator(page, key, { visible: true });
  let replacement = '';
  if (resolved) {
    replacement = resolved.selector.startsWith('role=')
      ? await cssPathOf(resolved.locator)
      : resolved.selector;
    if (!replacement) replacement = await cssPathOf(resolved.locator);
  } else {
    replacement = await inferSelector(page, key);
  }

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

    await openEditor(page, config);
    await waitForEditorReady(page, config);

    // Check the controls that are visible in the initial editor state.
    for (const key of ['titleInput', 'composeEditor', 'saveDraftButton', 'publishButton']) {
      await evaluateKey(page, key, results, changed, broken);
    }

    // Labels may need a disclosure click before the input exists.
    await evaluateKey(page, 'labelsSectionButton', results, changed, broken);
    const labelsButton = await resolveLocator(page, 'labelsSectionButton', { visible: true });
    if (labelsButton) {
      await labelsButton.locator.click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(400);
    }
    await evaluateKey(page, 'labelsInput', results, changed, broken);

    // HTML view: open it and verify the raw editor control.
    const htmlButton = await evaluateKey(page, 'htmlViewButton', results, changed, broken);
    if (htmlButton) {
      const button = await resolveLocator(page, 'htmlViewButton', { visible: true });
      if (button) {
        await button.locator.click({ timeout: 8000 }).catch(() => {});
        await page.waitForTimeout(500);
        await clickDialogButton(page, [/^ok$/i, /^continue$/i, /^switch$/i, /^yes$/i]);
        await waitForLocator(page, 'htmlViewTextarea', { timeoutMs: 5000 });
      }
      await evaluateKey(page, 'htmlViewTextarea', results, changed, broken);
    } else {
      results.push({
        key: 'htmlViewTextarea',
        ok: true,
        skipped: true,
        reason: 'HTML view is not exposed by this editor revision',
      });
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
