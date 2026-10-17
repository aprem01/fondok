/**
 * "Duplicate deal" — copy text shared by the project-header action and its
 * test. The worker route is POST /deals/{id}/duplicate
 * (apps/worker/app/api/deal_duplicate.py); keep this list in step with what
 * that route copies and skips.
 */

/** The confirm prompt shown before duplicating. */
export function duplicateDealConfirmText(dealName: string): string {
  return (
    `Duplicate "${dealName}"?\n\n` +
    'The copy gets: the deal details, your assumptions and their notes, ' +
    'scenarios, cell comments, and every document with its latest ' +
    'extraction (including the fields you reviewed). The files themselves ' +
    'are shared, not uploaded again.\n\n' +
    'Not copied: model runs and the activity history. The new deal shows ' +
    'no results until you run the model on it.\n\n' +
    'The original deal is not changed.'
  );
}

/** Toast after a successful duplicate. */
export function duplicateDealDoneText(newName: string): string {
  return `Created "${newName}". Run the model on the copy to see results.`;
}
