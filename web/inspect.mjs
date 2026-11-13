/**
 * Small, read-only inspection of the Blogger editor.
 *
 * Session checks and selector health both use this module. It opens panels
 * (HTML view, labels) to make hidden controls testable, but it never clicks
 * Save or Publish.
 */

import {
  clickDialogButton,
  resolveLocator,
  waitForLocator,
} from './lib.mjs';

export const REQUIRED_SELECTORS = ['titleInput', 'composeEditor', 'saveDraftButton'];
export const OPTIONAL_SELECTORS = [
  'htmlViewButton',
  'htmlViewTextarea',
  'labelsInput',
  'labelsSectionButton',
  'publishButton',
];
export const DIALOG_SELECTORS = [
  'draftConfirmButton',
  'publishConfirmButton',
  'publishedLink',
  'postRow',
];

export async function inspectEditor(page, config) {
  const selectors = {};

  // Check controls that are visible before any panel is opened.
  for (const key of ['titleInput', 'composeEditor', 'saveDraftButton', 'publishButton']) {
    const found = await resolveLocator(page, key, { visible: true });
    selectors[key] = found?.selector || '';
  }

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

  // HTML view: opening it does not create a saved post, and it is the only way
  // to verify the raw HTML textarea while the compose editor is hidden.
  const htmlButton = await resolveLocator(page, 'htmlViewButton', { visible: true });
  selectors.htmlViewButton = htmlButton?.selector || '';
  if (htmlButton) {
    await htmlButton.locator.click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(500);
    await clickDialogButton(page, [/^ok$/i, /^continue$/i, /^switch$/i, /^yes$/i]);
    const textarea = await waitForLocator(page, 'htmlViewTextarea', { timeoutMs: 6000 });
    selectors.htmlViewTextarea = textarea?.selector || '';
  } else {
    selectors.htmlViewTextarea = '';
  }

  for (const key of DIALOG_SELECTORS) selectors[key] = '';

  const broken = REQUIRED_SELECTORS.filter((key) => !selectors[key]);
  const optionalMissing = OPTIONAL_SELECTORS.filter((key) => !selectors[key]);
  return { selectors, broken, optionalMissing };
}
