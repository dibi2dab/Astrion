/**
 * Shared helpers for the Astrion Blogger web publishing engine.
 *
 * The engine drives the Blogger post editor with a saved Google browser
 * session. It exists because the Blogger API OAuth app stays in Google's
 * "Testing" mode and its refresh tokens expire after seven days.
 *
 * Design rules:
 *   - Every DOM lookup goes through web/selectors.json.
 *   - A failed lookup produces a typed error code, never a silent success.
 *   - Saving a draft never clicks "Publish". If the draft control disappears we
 *     stop instead of accidentally making a post public.
 *   - Diagnostics are scrubbed of e-mail addresses before they are written.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

export const SENTINEL = '__ASTRION_RESULT__';
export const WEB_DIR = path.dirname(fileURLToPath(import.meta.url));
export const PROJECT_ROOT = path.resolve(WEB_DIR, '..');
export const SELECTORS_FILE = path.join(WEB_DIR, 'selectors.json');

const DEFAULT_BLOG_URL = 'https://sciencew1thoutlimits.blogspot.com/';

export class WebError extends Error {
  constructor(message, code = 'BLOGGER_PUBLISH_FAILED') {
    super(message);
    this.name = 'WebError';
    this.code = code;
  }
}

export function emit(payload) {
  process.stdout.write(`\n${SENTINEL}${JSON.stringify(payload)}\n`);
}

export function readPayload(file = process.argv[2]) {
  if (!file) {
    throw new WebError('a payload JSON path is required', 'BLOGGER_BAD_PAYLOAD');
  }
  try {
    const raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      throw new Error('payload must be a JSON object');
    }
    return payload;
  } catch (err) {
    throw new WebError(`could not read payload: ${err.message}`, 'BLOGGER_BAD_PAYLOAD');
  }
}

function env(name, fallback = '') {
  const value = process.env[name];
  return value === undefined || value === null || value === '' ? fallback : value;
}

function asBool(value, fallback) {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'boolean') return value;
  return !['false', '0', 'no', 'off'].includes(String(value).toLowerCase());
}

function asNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function resolvePath(value, fallbackRelative) {
  const target = value || fallbackRelative;
  return path.isAbsolute(target) ? target : path.resolve(PROJECT_ROOT, target);
}

export function resolveConfig(overrides = {}) {
  const blogId = String(overrides.blogId ?? env('BLOGGER_BLOG_ID', '')).trim();
  const blogUrl = String(overrides.blogUrl ?? env('BLOGGER_BLOG_URL', DEFAULT_BLOG_URL)).trim();
  const storageState = resolvePath(
    overrides.storageStatePath ?? env('BLOGGER_STORAGE_STATE_PATH', ''),
    '.state/blogger_storage_state.json'
  );
  const artifactDir = resolvePath(
    overrides.artifactDir ?? env('ASTRION_WEB_ARTIFACT_DIR', ''),
    'dist/web-artifacts'
  );
  return {
    blogId,
    blogUrl: blogUrl.replace(/\/+$/, ''),
    storageState,
    artifactDir,
    headless: asBool(overrides.headless ?? env('ASTRION_WEB_HEADLESS', 'true'), true),
    slowMo: asNumber(overrides.slowMo ?? env('ASTRION_WEB_SLOW_MO', '0'), 0),
    timeoutMs: asNumber(overrides.timeoutMs ?? env('ASTRION_WEB_TIMEOUT_MS', '45000'), 45000),
    screenshots: asBool(
      overrides.screenshots ?? env('ASTRION_WEB_SCREENSHOTS', 'true'),
      true
    ),
    projectRoot: PROJECT_ROOT,
    hl: 'en',
    editorUrl: `https://www.blogger.com/blog/post/edit/${blogId}`,
    postsUrl: `https://www.blogger.com/blog/posts/${blogId}?hl=en`,
  };
}

/** Absolute editor URL for an existing post, forcing the English UI labels. */
export function editorUrlFor(config, postId) {
  return `${config.editorUrl}/${postId}?hl=${config.hl || 'en'}`;
}

export function configFromPayload(payload) {
  const config = resolveConfig({
    blogId: payload.blogId,
    blogUrl: payload.blogUrl,
    storageStatePath: payload.storageStatePath,
    artifactDir: payload.artifactDir,
    headless: payload.headless,
    slowMo: payload.slowMo,
    timeoutMs: payload.timeoutMs,
    screenshots: payload.screenshots,
  });
  if (!config.blogId) {
    throw new WebError(
      'BLOGGER_BLOG_ID is not set and the payload has no blogId',
      'BLOGGER_BAD_CONFIG'
    );
  }
  return config;
}

// ------------------------------------------------------------------ selectors
export function loadSelectors(file = SELECTORS_FILE) {
  const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!payload || typeof payload.selectors !== 'object') {
    throw new Error(`selector file has no selectors object: ${file}`);
  }
  return payload;
}

export function selectorEntry(key, file = SELECTORS_FILE) {
  const entry = loadSelectors(file).selectors[key];
  if (!entry) throw new Error(`unknown selector key: ${key}`);
  return entry;
}

export function selectorCandidates(key, file = SELECTORS_FILE) {
  const entry = selectorEntry(key, file);
  return [...new Set([entry.primary, ...(entry.candidates || [])].filter(Boolean))];
}

export function patchSelector(key, value, file = SELECTORS_FILE) {
  const document = loadSelectors(file);
  const entry = document.selectors[key];
  if (!entry) throw new Error(`unknown selector key: ${key}`);
  if (entry.primary === value) return false;
  const candidates = [entry.primary, ...(entry.candidates || [])].filter(
    (item) => item && item !== value
  );
  entry.primary = value;
  entry.candidates = [...new Set(candidates)];
  document.updated = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(file, JSON.stringify(document, null, 2) + '\n', 'utf8');
  return true;
}

const TEXT_KEYS = new Set([
  'createPostButton',
  'htmlViewButton',
  'saveDraftButton',
  'publishButton',
  'draftConfirmButton',
  'publishConfirmButton',
  'labelsSectionButton',
]);

const TEXT_PATTERNS = {
  createPostButton: [/^create new post$/i, /^new post$/i],
  htmlViewButton: [/^html view$/i, /^switch to html( view)?$/i, /^html$/i],
  saveDraftButton: [/^save draft$/i, /^save$/i, /^저장$/i, /^保存$/i],
  publishButton: [/^publish$/i, /^publish now$/i],
  draftConfirmButton: [/^save draft$/i, /^save$/i, /^ok$/i, /^continue$/i, /^yes$/i],
  publishConfirmButton: [/^publish$/i, /^publish now$/i, /^confirm$/i, /^ok$/i],
  labelsSectionButton: [/^labels$/i, /^post settings$/i, /^settings$/i],
};

async function firstVisible(locator) {
  const count = await locator.count().catch(() => 0);
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (await item.isVisible().catch(() => false)) return item;
  }
  return null;
}

async function resolveByRoleName(page, patterns) {
  for (const role of ['button', 'link', 'menuitem', 'tab', 'option']) {
    for (const pattern of patterns) {
      const locator = page.getByRole(role, { name: pattern });
      const found = await firstVisible(locator);
      if (found) {
        return { locator: found, selector: `role=${role}[name=${pattern}]` };
      }
    }
  }
  return null;
}

export async function resolveLocator(
  page,
  key,
  { visible = true, timeoutMs = 0, root = null } = {}
) {
  const deadline = Date.now() + timeoutMs;
  do {
    for (const selector of selectorCandidates(key)) {
      const scope = root ? root.locator(selector) : page.locator(selector);
      const count = await scope.count().catch(() => 0);
      if (count === 0) continue;
      if (!visible) return { locator: scope.first(), selector };
      const found = await firstVisible(scope);
      if (found) return { locator: found, selector };
    }

    if (TEXT_KEYS.has(key) && !root) {
      const byRole = await resolveByRoleName(page, TEXT_PATTERNS[key] || []);
      if (byRole) return byRole;
    }

    if (timeoutMs > 0 && Date.now() < deadline) {
      await page.waitForTimeout(400);
    }
  } while (Date.now() < deadline);
  return null;
}

export async function waitForLocator(page, key, options = {}) {
  return resolveLocator(page, key, { visible: true, ...options });
}

/**
 * Locate the iframe that hosts the rich-text body editor.
 *
 * Recent Blogger versions render the compose area in a same-origin iframe with
 * no `src`, so page-level contenteditable queries never match it. All body
 * lookups go through this frame.
 */
export async function resolveEditorFrame(page) {
  for (const selector of selectorCandidates('composeFrame')) {
    const locator = page.locator(selector);
    const count = await locator.count().catch(() => 0);
    if (count === 0) continue;
    return { frame: page.frameLocator(selector).first(), selector };
  }
  return null;
}

export async function waitForEditorFrame(page, timeoutMs = 0) {
  const deadline = Date.now() + timeoutMs;
  do {
    const found = await resolveEditorFrame(page);
    if (found) return found;
    if (timeoutMs > 0 && Date.now() < deadline) await page.waitForTimeout(400);
  } while (Date.now() < deadline);
  return null;
}

export async function resolveLocatorInFrame(frame, key, { visible = true } = {}) {
  for (const selector of selectorCandidates(key)) {
    const scope = frame.locator(selector);
    const count = await scope.count().catch(() => 0);
    if (count === 0) continue;
    if (!visible) return { locator: scope.first(), selector };
    for (let index = 0; index < count; index += 1) {
      const item = scope.nth(index);
      if (await item.isVisible().catch(() => false)) return { locator: item, selector };
    }
  }
  return null;
}

export async function firstVisibleLocator(locator) {
  return firstVisible(locator);
}

/**
 * Open the split-button menu that holds Preview and Save.
 *
 * The editor has several "More options" buttons (sidebar sections, post cards).
 * Only the arrow inside the Publish container opens the Save menu, so the
 * lookup is scoped to the Publish button's parent element.
 */
export async function openPublishMenu(page) {
  const publish = await resolveLocator(page, 'publishButton', { visible: true });
  if (publish) {
    const container = publish.locator.locator('xpath=..');
    for (const selector of selectorCandidates('publishMenuButton')) {
      const found = await firstVisible(container.locator(selector));
      if (!found) continue;
      await found.click({ timeout: 8000 });
      await page.waitForTimeout(300);
      return { locator: found, selector, scope: 'publish-container' };
    }
  }
  return null;
}

/**
 * Wait until Blogger reports that the in-flight save finished.
 *
 * The editor shows "Changes saved" only when nothing is pending. Returning
 * false is not fatal: the post list is still checked for the title.
 */
export async function waitForSaved(page, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const saving = await resolveLocator(page, 'savingIndicator', { visible: true });
    if (!saving) {
      const saved = await resolveLocator(page, 'savedIndicator', { visible: true });
      if (saved) return true;
    }
    await page.waitForTimeout(400);
  }
  return false;
}

/**
 * Wait until the editor finished uploading the current edits.
 *
 * Typing triggers an autosave a few seconds later. Saving a draft before that
 * round-trip completes stores the previous (often empty) revision, so the
 * publisher waits here after the last edit. The check requires the "saved"
 * indicator to stay visible with no pending indicator for a short settle
 * window; the indicator flickers during upload.
 */
export async function waitForEditorSync(page, timeoutMs = 30000, settleMs = 1200) {
  const deadline = Date.now() + timeoutMs;
  let quietSince = 0;
  while (Date.now() < deadline) {
    const saving = await resolveLocator(page, 'savingIndicator', { visible: true });
    const pending = saving || (await resolveLocator(page, 'unsavedIndicator', { visible: true }));
    const saved = await resolveLocator(page, 'savedIndicator', { visible: true });
    if (!pending && saved) {
      if (!quietSince) quietSince = Date.now();
      if (Date.now() - quietSince >= settleMs) return true;
    } else {
      quietSince = 0;
    }
    await page.waitForTimeout(300);
  }
  return false;
}

export async function clickLocator(page, key, options = {}) {
  const found = await resolveLocator(page, key, options);
  if (!found) {
    throw new WebError(
      `could not find the Blogger editor control '${key}'. The editor UI may have changed; run \`npm run selectors:fix\`.`,
      'BLOGGER_SELECTOR_MISSING'
    );
  }
  await found.locator.click({ timeout: options.clickTimeoutMs || 10000 });
  return found;
}

export async function clickDialogButton(page, patterns) {
  const dialog = page
    .locator('[role="dialog"], [role="alertdialog"], .modal, .dialog')
    .filter({ has: page.locator('button, [role="button"]') });
  const count = await dialog.count().catch(() => 0);
  for (let index = count - 1; index >= 0; index -= 1) {
    const root = dialog.nth(index);
    if (!(await root.isVisible().catch(() => false))) continue;
    for (const pattern of patterns) {
      const button = await firstVisible(root.getByRole('button', { name: pattern }));
      if (button) {
        await button.click({ timeout: 8000 });
        return true;
      }
    }
  }
  return false;
}

/** Turn a locator into a durable CSS selector that can be stored in selectors.json. */
export async function cssPathOf(locator) {
  return locator
    .evaluate((element) => {
      const escape = (value) =>
        window.CSS && CSS.escape
          ? CSS.escape(value)
          : String(value).replace(/["\\]/g, '\\$&');
      if (element.id) return `#${escape(element.id)}`;
      for (const name of ['aria-label', 'data-testid', 'name', 'placeholder']) {
        const value = element.getAttribute(name);
        if (value) return `${element.tagName.toLowerCase()}[${name}=${JSON.stringify(value)}]`;
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
          const siblings = [...parent.children].filter((child) => child.tagName === node.tagName);
          if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
        parts.unshift(part);
        node = node.parentElement;
      }
      return parts.join(' > ');
    })
    .catch(() => '');
}

// -------------------------------------------------------------------- session
/**
 * Write the Base64 BLOGGER_STORAGE_STATE secret to disk when present.
 *
 * Python materializes the same file before invoking Node, but the standalone
 * selector-health workflow calls npm directly, so the check lives here too.
 */
export function materializeSession(config) {
  const encoded = String(process.env.BLOGGER_STORAGE_STATE || '').trim();
  if (!encoded) return config.storageState;
  let state;
  try {
    state = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
  } catch {
    throw new WebError(
      'BLOGGER_STORAGE_STATE is not valid Base64 JSON. Re-run `npm run login`.',
      'BLOGGER_SESSION_REQUIRED'
    );
  }
  if (!state || !Array.isArray(state.cookies) || state.cookies.length === 0) {
    throw new WebError(
      'BLOGGER_STORAGE_STATE has no cookies. Re-run `npm run login`.',
      'BLOGGER_SESSION_REQUIRED'
    );
  }
  fs.mkdirSync(path.dirname(config.storageState), { recursive: true });
  fs.writeFileSync(config.storageState, JSON.stringify(state), {
    encoding: 'utf8',
    mode: 0o600,
  });
  return config.storageState;
}

export function isLoginUrl(url) {
  return /accounts\.google\.com|servicelogin|signin\/v2|\/login|auth\/login|login\.blogger/i.test(
    String(url || '')
  );
}

export function assertStorageState(config) {
  materializeSession(config);
  if (!fs.existsSync(config.storageState)) {
    throw new WebError(
      `Blogger browser session not found at ${config.storageState}. ` +
        'Run `npm run login` locally and set the BLOGGER_STORAGE_STATE secret.',
      'BLOGGER_SESSION_REQUIRED'
    );
  }
  try {
    const state = JSON.parse(fs.readFileSync(config.storageState, 'utf8'));
    if (!Array.isArray(state.cookies) || state.cookies.length === 0) {
      throw new Error('storage state has no cookies');
    }
  } catch (err) {
    throw new WebError(
      `Blogger browser session is unreadable (${err.message}). Re-run \`npm run login\`.`,
      'BLOGGER_SESSION_REQUIRED'
    );
  }
}

export async function assertEditorAccess(page, config) {
  if (isLoginUrl(page.url())) {
    throw new WebError(
      `Google redirected the editor to a sign-in page (${page.url()}). ` +
        'The saved browser session has expired; run `npm run login` and update BLOGGER_STORAGE_STATE.',
      'BLOGGER_SESSION_EXPIRED'
    );
  }
  const email = page.locator('input[type="email"]');
  if (await firstVisible(email)) {
    throw new WebError(
      'Google is asking for an account sign-in. The saved browser session has expired.',
      'BLOGGER_SESSION_EXPIRED'
    );
  }
  if (!/blogger\.com/.test(page.url())) {
    throw new WebError(
      `the editor redirected somewhere unexpected: ${page.url()}`,
      'BLOGGER_NAVIGATION_TIMEOUT'
    );
  }
}

/**
 * Open a Blogger editor page.
 *
 * Options:
 *   postId  - open that specific post/draft.
 *   create  - open the posts list and click "Create New Post" (Blogger assigns
 *             the new draft id server-side, so there is no URL to hardcode).
 *   neither - open a draft (preferred), else the newest post, in the posts list
 *             (read-only probe used by the session check; never creates a draft).
 */
/**
 * Open an existing post for a read-only session/selector probe.
 *
 * Prefers a draft because only a draft exposes the "Save" item in the publish
 * menu. Published posts show "Update" instead, so a probe on a published post
 * cannot verify the draft-save control.
 */
export async function openEditorProbe(page, config) {
  await page.goto(config.postsUrl, {
    waitUntil: 'domcontentloaded',
    timeout: config.timeoutMs,
  });
  await page.waitForTimeout(1500);
  await assertEditorAccess(page, config);
  await page
    .locator('a[href*="/post/edit/"]')
    .first()
    .waitFor({ state: 'attached', timeout: Math.min(config.timeoutMs, 20000) })
    .catch(() => {});

  const posts = await readPosts(page);
  if (posts.length === 0) {
    throw new WebError(
      'the Blogger posts list has no editable post to probe. Publish one post, ' +
        'or run `python -m astrion login` to refresh the session.',
      'BLOGGER_POST_ID_MISSING'
    );
  }

  const target = posts.find((post) => /draft/i.test(post.row)) || posts[0];
  const probeId = target.id || extractPostId(target.href, config.blogId);
  await page.goto(probeId ? editorUrlFor(config, probeId) : target.href, {
    waitUntil: 'domcontentloaded',
    timeout: config.timeoutMs,
  });
  await page.waitForTimeout(1200);
  await assertEditorAccess(page, config);
  return {
    url: page.url(),
    postId: probeId,
    title: target.title,
    status: /draft/i.test(target.row) ? 'draft' : 'published',
  };
}

export async function openEditor(page, config, { postId = '', create = false } = {}) {
  if (create) {
    await page.goto(config.postsUrl, {
      waitUntil: 'domcontentloaded',
      timeout: config.timeoutMs,
    });
    await page.waitForTimeout(1200);
    await assertEditorAccess(page, config);
    const button = await waitForLocator(page, 'createPostButton', {
      timeoutMs: Math.min(config.timeoutMs, 30000),
    });
    if (!button) {
      throw new WebError(
        'the "Create New Post" control was not found on the Blogger posts list. ' +
          'The editor UI may have changed; run `npm run selectors:check`.',
        'BLOGGER_SELECTOR_MISSING'
      );
    }
    await button.locator.click({ timeout: 15000 });
    const deadline = Date.now() + config.timeoutMs;
    let newId = '';
    while (Date.now() < deadline) {
      newId = extractPostId(page.url(), config.blogId);
      if (newId) break;
      await page.waitForTimeout(400);
    }
    if (!newId) {
      throw new WebError(
        'clicking "Create New Post" did not open an editor page. The editor UI may have changed.',
        'BLOGGER_NAVIGATION_TIMEOUT'
      );
    }
    // Re-open the freshly created draft by URL. Typing during the create
    // navigation races the editor's own initialization and the server can
    // overwrite the title/body, so always wait for a clean editor load.
    await page.goto(editorUrlFor(config, newId), {
      waitUntil: 'domcontentloaded',
      timeout: config.timeoutMs,
    });
    await page.waitForTimeout(1500);
    await assertEditorAccess(page, config);
    return page.url();
  }

  if (postId) {
    await page.goto(editorUrlFor(config, postId), {
      waitUntil: 'domcontentloaded',
      timeout: config.timeoutMs,
    });
    await page.waitForTimeout(1200);
    await assertEditorAccess(page, config);
    return page.url();
  }

  const probe = await openEditorProbe(page, config);
  return probe.url;
}

export async function waitForEditorReady(page, config) {
  const deadline = Date.now() + config.timeoutMs;
  while (Date.now() < deadline) {
    await assertEditorAccess(page, config);
    const title = await resolveLocator(page, 'titleInput', { visible: true });
    const frame = await resolveEditorFrame(page);
    const body = frame
      ? await resolveLocatorInFrame(frame.frame, 'composeEditor', { visible: true })
      : await resolveLocator(page, 'composeEditor', { visible: true });
    if (title && body) return { title, body, frame };
    await page.waitForTimeout(500);
  }
  throw new WebError(
    'the Blogger editor loaded but neither the title field nor the body editor could be found. ' +
      'The editor UI may have changed; run `npm run selectors:check`.',
    'BLOGGER_SELECTOR_MISSING'
  );
}

export async function launchContext(config) {
  const browser = await chromium.launch({
    headless: config.headless,
    slowMo: config.slowMo || 0,
  });
  const context = await browser.newContext({
    storageState: config.storageState,
    viewport: { width: 1440, height: 1000 },
    locale: 'en-US',
  });
  const page = await context.newPage();
  page.setDefaultTimeout(config.timeoutMs);
  page.on('dialog', (dialog) => dialog.accept().catch(() => {}));
  return { browser, context, page };
}

// ------------------------------------------------------------------ artifacts
export function scrub(text) {
  return String(text ?? '')
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, '***@***')
    .replace(/(?:password|passwd|pwd)\s*[=:]\s*\S+/gi, '$1=***');
}

export function stamp() {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
}

export async function saveArtifacts(page, config, label, details = {}) {
  const base = path.join(
    config.artifactDir,
    `${String(label).replace(/[^a-z0-9_-]+/gi, '-')}-${stamp()}`
  );
  try {
    fs.mkdirSync(config.artifactDir, { recursive: true });
    if (page && config.screenshots) {
      await page.screenshot({ path: `${base}.png`, fullPage: true }).catch(() => {});
    }
    const url = page ? page.url() : '';
    if (page) {
      const html = scrub(await page.content().catch(() => ''));
      fs.writeFileSync(`${base}.html`, html, 'utf8');
    }
    fs.writeFileSync(
      `${base}.txt`,
      `url=${scrub(url)}\n${Object.entries(details)
        .map(([key, value]) => `${key}=${scrub(value)}`)
        .join('\n')}\n`,
      'utf8'
    );
    return base;
  } catch {
    // Diagnostics must never mask the real failure.
    return '';
  }
}

// -------------------------------------------------------------- post list/find
export function extractPostId(url, blogId) {
  const text = String(url || '');
  const patterns = [
    new RegExp(`/post/edit/${blogId}/(\\d{6,})`),
    /\/post\/edit\/\d+\/(\d{6,})/,
    /\/post\/(\d{6,})/,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1];
  }
  return '';
}

function normalizeTitle(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

/**
 * Read the rendered post cards from the Blogger posts list.
 *
 * The current list renders every post as `[role="listitem"]` with the title in
 * an element whose id is `post-title-<postId>`. The edit anchor has no text of
 * its own, so the title is read from that element instead of the anchor.
 */
export async function readPosts(page) {
  return page
    .evaluate(() => {
      const seen = new Set();
      const items = [];
      for (const anchor of document.querySelectorAll('a[href*="/post/edit/"]')) {
        const href = anchor.href || '';
        const match = href.match(/\/post\/edit\/\d+\/(\d{6,})/);
        const id = match ? match[1] : '';
        if (!id || seen.has(id)) continue;

        let card = anchor.parentElement;
        for (let depth = 0; depth < 6 && card && !card.querySelector('[id^="post-title-"]'); depth += 1) {
          card = card.parentElement;
        }
        if (card === document.documentElement) card = anchor.parentElement;
        const titleNode = card?.querySelector('[id^="post-title-"]') || null;
        const title = (titleNode?.innerText || '').replace(/\s+/g, ' ').trim();
        const row = (card?.innerText || '').replace(/\s+/g, ' ').trim();
        seen.add(id);
        items.push({ id, title, row, href });
      }
      return items;
    })
    .catch(() => []);
}

export async function listPosts(page, config) {
  await page.goto(config.postsUrl, { waitUntil: 'domcontentloaded', timeout: config.timeoutMs });
  await page.waitForTimeout(1500);
  await assertEditorAccess(page, config);

  await page
    .locator('a[href*="/post/edit/"]')
    .first()
    .waitFor({ state: 'attached', timeout: Math.min(config.timeoutMs, 15000) })
    .catch(() => {});

  return readPosts(page);
}

export async function findPostInList(page, config, title) {
  const wanted = normalizeTitle(title);
  if (!wanted) return null;

  const posts = await listPosts(page, config);
  const match =
    posts.find((item) => normalizeTitle(item.title) === wanted) ||
    posts.find((item) => normalizeTitle(item.title).includes(wanted)) ||
    posts.find((item) => {
      const haystack = normalizeTitle(`${item.title} ${item.row}`);
      return wanted.length > 12 && haystack.includes(wanted);
    });
  if (!match) return null;

  const id = match.id || extractPostId(match.href, config.blogId);
  return {
    id,
    url: '',
    editUrl: id ? editorUrlFor(config, id) : match.href,
    status: /draft/i.test(match.row) ? 'draft' : 'live',
    title: match.title,
    row: match.row,
  };
}

// ------------------------------------------------------------------ utilities
export async function readBodyText(page) {
  return page.evaluate(() => document.body?.innerText || '').catch(() => '');
}

export async function pickLargestVisible(page, selector) {
  const locator = page.locator(selector);
  const count = await locator.count().catch(() => 0);
  let best = null;
  let bestArea = 0;
  for (let index = 0; index < count; index += 1) {
    const item = locator.nth(index);
    if (!(await item.isVisible().catch(() => false))) continue;
    const box = await item.boundingBox().catch(() => null);
    const area = box ? box.width * box.height : 0;
    if (area >= bestArea) {
      best = item;
      bestArea = area;
    }
  }
  return best;
}

export function nowIso() {
  return new Date().toISOString();
}
