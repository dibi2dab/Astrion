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
    editorUrl: `https://www.blogger.com/blog/post/edit/${blogId}`,
    postsUrl: `https://www.blogger.com/blog/posts/${blogId}`,
  };
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
  'htmlViewButton',
  'saveDraftButton',
  'publishButton',
  'draftConfirmButton',
  'publishConfirmButton',
  'labelsSectionButton',
]);

const TEXT_PATTERNS = {
  htmlViewButton: [/^html view$/i, /^switch to html( view)?$/i, /^html$/i],
  saveDraftButton: [/^save draft$/i, /^save$/i],
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
    if (TEXT_KEYS.has(key) && !root) {
      const byRole = await resolveByRoleName(page, TEXT_PATTERNS[key] || []);
      if (byRole) return byRole;
    }

    for (const selector of selectorCandidates(key)) {
      const scope = root ? root.locator(selector) : page.locator(selector);
      const count = await scope.count().catch(() => 0);
      if (count === 0) continue;
      if (!visible) return { locator: scope.first(), selector };
      const found = await firstVisible(scope);
      if (found) return { locator: found, selector };
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

export async function openEditor(page, config) {
  await page.goto(config.editorUrl, {
    waitUntil: 'domcontentloaded',
    timeout: config.timeoutMs,
  });
  await page.waitForTimeout(1200);
  await assertEditorAccess(page, config);
}

export async function waitForEditorReady(page, config) {
  const deadline = Date.now() + config.timeoutMs;
  while (Date.now() < deadline) {
    await assertEditorAccess(page, config);
    const title = await resolveLocator(page, 'titleInput', { visible: true });
    const body =
      (await resolveLocator(page, 'composeEditor', { visible: true })) ||
      (await resolveLocator(page, 'htmlViewButton', { visible: true }));
    if (title && body) return { title, body };
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

function rowText(anchor) {
  const row = anchor.closest('[role="row"], tr, li, article');
  return (row?.innerText || anchor.innerText || '').replace(/\s+/g, ' ').trim();
}

export async function findPostInList(page, config, title) {
  const wanted = String(title || '').trim();
  if (!wanted) return null;

  await page.goto(config.postsUrl, { waitUntil: 'domcontentloaded', timeout: config.timeoutMs });
  await page.waitForTimeout(1200);
  await assertEditorAccess(page, config);

  // Newer Blogger UIs expose a search box. Filtering first keeps the list short.
  const search =
    (await firstVisible(page.locator('input[type="search"]'))) ||
    (await firstVisible(page.locator('input[placeholder*="Search" i]'))) ||
    (await firstVisible(page.locator('input[aria-label*="Search" i]')));
  if (search) {
    await search.fill(wanted).catch(() => {});
    await search.press('Enter').catch(() => {});
    await page.waitForTimeout(1500);
  }

  // Wait for the list to render at least one post link.
  const linkSelector = 'a[href*="/post/edit/"], a[href*="blogspot.com/"]';
  await page
    .locator(linkSelector)
    .first()
    .waitFor({ state: 'attached', timeout: Math.min(config.timeoutMs, 15000) })
    .catch(() => {});

  const anchors = await page.evaluate((selector) => {
    return [...document.querySelectorAll(selector)]
      .map((anchor) => ({
        title: (anchor.innerText || anchor.getAttribute('aria-label') || '').trim(),
        href: anchor.href || '',
        row: (() => {
          const row = anchor.closest('[role="row"], tr, li, article');
          return (row?.innerText || '').replace(/\s+/g, ' ').trim();
        })(),
      }))
      .filter((item) => item.title && item.href);
  }, linkSelector).catch(() => []);

  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const target = normalize(wanted);
  let match =
    anchors.find((item) => normalize(item.title) === target) ||
    anchors.find((item) => normalize(item.title).includes(target)) ||
    anchors.find((item) => target.includes(normalize(item.title)) && item.title.length > 12);

  if (!match) {
    // The UI may render the title in a sibling cell rather than the anchor.
    const rowMatch = anchors.find((item) => normalize(item.row).includes(target));
    if (rowMatch) match = rowMatch;
  }
  if (!match) return null;

  const id = extractPostId(match.href, config.blogId);
  const editUrl = match.href.includes('/post/edit/')
    ? match.href
    : id
      ? `${config.editorUrl}/${id}`
      : '';
  const publicUrl =
    /blogspot\.com|blogger\.com\/blog\/post\/edit/.test(match.href) &&
    !match.href.includes('/post/edit/')
      ? match.href
      : '';
  const status = /draft/i.test(match.row) ? 'draft' : 'live';
  return {
    id,
    url: publicUrl,
    editUrl,
    status,
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
