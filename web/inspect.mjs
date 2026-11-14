/**
 * Small, read-only inspection of the Blogger editor.
 *
 * Session checks and selector health both use this module. It opens menus
 * (labels, publish overflow) to make hidden controls testable, but it never
 * clicks Save or Publish.
 */

import {
  openPublishMenu,
  resolveLocator,
  waitForEditorReady,
  waitForLocator,
} from './lib.mjs';

export const REQUIRED_SELECTORS = [
  'titleInput',
  'composeFrame',
  'composeEditor',
  'saveDraftButton',
];
export const OPTIONAL_SELECTORS = [
  'htmlViewButton',
  'htmlViewTextarea',
  'labelsInput',
  'labelsSectionButton',
  'publishButton',
  'savedIndicator',
];
export const LIST_SELECTORS = ['createPostButton'];
export const DIALOG_SELECTORS = [
  'draftConfirmButton',
  'publishConfirmButton',
  'publishedLink',
  'postRow',
];

export async function inspectEditor(page, config) {
  const selectors = {};
  const ready = await waitForEditorReady(page, config);

  selectors.titleInput = ready.title.selector;
  selectors.composeFrame = ready.frame?.selector || '';
  selectors.composeEditor = ready.body.selector;

  // Labels can be behind "Post settings" or a "Labels" disclosure.
  let labels = await resolveLocator(page, 'labelsInput', { visible: true });
  if (!labels) {
    for (const key of ['labelsSectionButton']) {
      const button = await resolveLocator(page, key, { visible: true });
      if (!button) continue;
      await button.locator.click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(400);
      labels = await waitForLocator(page, 'labelsInput', { timeoutMs: 2500 });
      if (labels) break;
    }
  }
  selectors.labelsInput = labels?.selector || '';
  selectors.labelsSectionButton =
    (await resolveLocator(page, 'labelsSectionButton', { visible: true }))?.selector || '';

  selectors.savedIndicator =
    (await resolveLocator(page, 'savedIndicator', { visible: true }))?.selector || '';
  selectors.publishButton =
    (await resolveLocator(page, 'publishButton', { visible: true }))?.selector || '';

  // The Save control only exists while the publish overflow menu is open.
  let save = await resolveLocator(page, 'saveDraftButton', { visible: true });
  if (!save) {
    const menu = await openPublishMenu(page);
    if (menu) save = await waitForLocator(page, 'saveDraftButton', { timeoutMs: 5000 });
    if (menu) await page.keyboard.press('Escape').catch(() => {});
  }
  selectors.saveDraftButton = save?.selector || '';

  // HTML view only exists in older editor revisions.
  selectors.htmlViewButton =
    (await resolveLocator(page, 'htmlViewButton', { visible: true }))?.selector || '';
  selectors.htmlViewTextarea = '';

  for (const key of DIALOG_SELECTORS) selectors[key] = '';

  const broken = REQUIRED_SELECTORS.filter((key) => !selectors[key]);
  const optionalMissing = OPTIONAL_SELECTORS.filter((key) => !selectors[key]);
  return { selectors, broken, optionalMissing };
}
