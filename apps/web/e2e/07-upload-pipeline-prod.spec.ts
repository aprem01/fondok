import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Production upload pipeline regression — OPT-IN.
 *
 * Runs only when PLAYWRIGHT_BASE_URL points at the live deploy AND a
 * real T-12 PDF fixture is present. Catches the P0 we shipped a fix
 * for: "uploaded T-12 silently lands with status=FAILED,
 * error_kind=db_insert_failed".
 *
 * NEVER runs in CI. It writes a real deal into production and uploads a real
 * document to the real worker; a push-triggered job doing that on every merge
 * would fill the pipeline with junk deals. It is opt-in on an explicit
 * environment flag, not on the shape of the base URL — a URL check is easy to
 * satisfy by accident (a staging alias, a copied command) and this is a test
 * that must only run when someone means it. Run it by hand after a deploy:
 *
 *   FONDOK_E2E_PROD=1 PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app \
 *     pnpm test:e2e 07-upload-pipeline-prod
 */
test.describe('@prod upload pipeline', () => {
  test.skip(() => {
    if (process.env.FONDOK_E2E_PROD !== '1') return true;
    // Belt and braces: the flag says "I mean it", the URL says "against prod".
    return !(process.env.PLAYWRIGHT_BASE_URL ?? '').includes('fondok-app.vercel.app');
  }, 'Opt-in only: set FONDOK_E2E_PROD=1 and PLAYWRIGHT_BASE_URL to the prod deploy.');

  test('uploaded document lands with status != FAILED', async ({ page }) => {
    const fixturePath = resolve(__dirname, 'fixtures', 'sample-t12.pdf');
    if (!existsSync(fixturePath)) {
      test.skip(true, 'Real T-12 PDF fixture not present.');
      return;
    }

    // Capture the worker's upload response so we can poll status.
    const uploadPromise = page.waitForResponse(
      (resp) => resp.url().includes('/documents/upload') && resp.status() === 200,
      { timeout: 30_000 },
    );

    await page.goto('/projects/new');
    await page.evaluate(() => {
      localStorage.setItem('fondok:coachmarks:disabled', 'true');
    });
    await page.reload();

    await page.getByPlaceholder('Chicago Downtown Acquisition').fill(`E2E Prod ${Date.now()}`);
    await page.getByPlaceholder('Chicago, IL').fill('E2E Land');
    await page.getByRole('button', { name: /^next/i }).click();
    await page.getByRole('button', { name: /^next/i }).click();

    // Only the ACTIVE category renders a panel; Step 3 opens on the OM.
    await page.getByRole('button', { name: /^Financial Statements/ }).first().click();
    await page.locator('#wizard-financials-drop').setInputFiles(fixturePath);
    await page.getByRole('button', { name: /^next$/i }).last().click(); // Step 3 → 4
    await page.getByRole('button', { name: /^next$/i }).last().click(); // 4 → 5
    await page.getByRole('button', { name: /^next$/i }).last().click(); // 5 → 6
    await page.getByRole('button', { name: /create deal/i }).click();

    const uploadResp = await uploadPromise;
    const uploaded = (await uploadResp.json()) as Array<{ id: string }>;
    const docId = uploaded[0]?.id;
    expect(docId).toBeTruthy();

    // Poll the doc detail endpoint until status leaves PARSING.
    const baseURL = page.url().replace(/\/projects\/.*$/, '');
    const start = Date.now();
    let finalStatus = 'PARSING';
    let errorKind: string | null = null;
    while (Date.now() - start < 60_000) {
      const detail = await page.request.get(
        `${baseURL.replace(/\/$/, '')}/api/documents/${docId}`,
      );
      if (detail.ok()) {
        const body = (await detail.json()) as { status?: string; error_kind?: string };
        finalStatus = body.status ?? finalStatus;
        errorKind = body.error_kind ?? null;
        if (finalStatus !== 'PARSING' && finalStatus !== 'CLASSIFYING') break;
      }
      await page.waitForTimeout(2_000);
    }

    expect(finalStatus, `Final doc status: ${finalStatus}`).not.toBe('FAILED');
    expect(errorKind).not.toBe('db_insert_failed');
  });
});
