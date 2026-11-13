#!/usr/bin/env node
/**
 * Create or refresh the saved Google browser session used for publishing.
 *
 * Usage:
 *   npm run login                # headed browser on this machine
 *   npm run login -- --timeout 600
 *
 * How it works:
 *   1. Opens a headed Chromium window at the Blogger dashboard.
 *   2. Waits for you to sign in to Google by hand.
 *   3. Waits until the Blogger dashboard (not the sign-in page) is reachable.
 *   4. Saves the cookies/storage to .state/blogger_storage_state.json.
 *   5. Prints the Base64 value for the BLOGGER_STORAGE_STATE secret.
 *
 * The script never receives your password. Google's sign-in flow, including
 * 2FA and device prompts, happens in the real browser window.
 */

import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import {
  PROJECT_ROOT,
  WEB_DIR,
  configFromPayload,
  nowIso,
  resolveConfig,
  scrub,
} from './lib.mjs';

const STATE_PATH = path.join(PROJECT_ROOT, '.state', 'blogger_storage_state.json');
const DASHBOARD_URL = 'https://www.blogger.com/';
const LOGIN_TIMEOUT_SECONDS = 600;

function fail(message) {
  process.stderr.write(`\n${message}\n`);
  process.exitCode = 1;
}

function parseTimeout() {
  const index = process.argv.indexOf('--timeout');
  if (index === -1) return LOGIN_TIMEOUT_SECONDS;
  const value = Number(process.argv[index + 1]);
  return Number.isFinite(value) && value > 0 ? value : LOGIN_TIMEOUT_SECONDS;
}

async function looksSignedIn(page) {
  const url = page.url();
  if (/accounts\.google\.com|servicelogin|signin\/v2/i.test(url)) return false;
  if (!/blogger\.com/i.test(url)) return false;
  const email = page.locator('input[type="email"]');
  if ((await email.count().catch(() => 0)) > 0) {
    for (let index = 0; index < (await email.count()); index += 1) {
      if (await email.nth(index).isVisible().catch(() => false)) return false;
    }
  }
  return true;
}

async function main() {
  let config;
  try {
    config = resolveConfig();
  } catch (err) {
    fail(`Could not resolve configuration: ${err.message}`);
    return;
  }

  fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true });

  let browser;
  try {
    browser = await chromium.launch({ headless: false });
  } catch (err) {
    fail(
      `Could not launch Chromium: ${err.message}\n` +
        'Run `npx playwright install chromium` and try again.'
    );
    return;
  }

  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    locale: 'en-US',
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);

  await page.goto(DASHBOARD_URL, { waitUntil: 'domcontentloaded' }).catch(() => {});

  const timeout = parseTimeout();
  const deadline = Date.now() + timeout * 1000;

  process.stdout.write(
    '\nA Chromium window is open.\n' +
      '  1. Sign in to the Google account that owns the Astrion blog.\n' +
      '  2. Wait until you can see the Blogger dashboard or post list.\n' +
      '  3. Leave the window open; the session saves automatically.\n' +
      `\nWaiting up to ${timeout} seconds...\n`
  );

  let signedIn = false;
  let lastUrl = '';
  while (Date.now() < deadline) {
    if (page.isClosed()) break;
    const url = page.url();
    if (url !== lastUrl) {
      lastUrl = url;
      process.stdout.write(`  current page: ${scrub(url)}\n`);
    }
    // Visit the blog's post list so the editor host issues its cookies too.
    if (await looksSignedIn(page)) {
      const postsUrl = `https://www.blogger.com/blog/posts/${config.blogId || ''}`;
      if (config.blogId && !page.url().includes('/blog/')) {
        await page.goto(postsUrl, { waitUntil: 'domcontentloaded' }).catch(() => {});
        await page.waitForTimeout(1500);
      }
      if (await looksSignedIn(page)) {
        signedIn = true;
        break;
      }
    }
    await page.waitForTimeout(2000);
  }

  if (!signedIn) {
    fail(
      'Timed out before the Blogger dashboard became reachable.\n' +
        'If you were asked for 2FA or a device prompt, complete it and run `npm run login` again.'
    );
    await browser.close().catch(() => {});
    return;
  }

  // Opening the editor once confirms that the blog-scoped session works.
  if (config.blogId) {
    await page
      .goto(`https://www.blogger.com/blog/post/edit/${config.blogId}`, {
        waitUntil: 'domcontentloaded',
      })
      .catch(() => {});
    await page.waitForTimeout(2000);
  }

  await page.waitForTimeout(1000);
  await context.storageState({ path: STATE_PATH });
  await browser.close().catch(() => {});

  const raw = fs.readFileSync(STATE_PATH, 'utf8');
  const base64 = Buffer.from(raw, 'utf8').toString('base64');

  process.stdout.write(
    `\nSaved browser session to ${STATE_PATH}\n` +
      `Cookies: ${JSON.parse(raw).cookies?.length || 0}\n` +
      `Captured: ${nowIso()}\n\n` +
      'Next steps:\n' +
      '  1. Copy the Base64 value below into the BLOGGER_STORAGE_STATE secret.\n' +
      '     GitHub: Settings -> Secrets and variables -> Actions -> New repository secret\n' +
      '  2. Run `npm run session:check` to verify the session opens the editor.\n\n' +
      'BLOGGER_STORAGE_STATE (base64):\n'
  );
  process.stdout.write(`${base64}\n`);
}

main().catch((err) => {
  fail(String(err?.message || err));
});
