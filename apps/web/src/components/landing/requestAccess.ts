// Sign-up is invitation-only in Clerk (sign_up_mode: restricted), so the
// public page never links to /sign-up. A request is an email; the firm is
// then invited from Clerk. /sign-up stays in place for invitation links.
export const REQUEST_ACCESS_HREF =
  'mailto:prem@fondok.ai' +
  '?subject=' + encodeURIComponent('Fondok access request for our firm') +
  '&body=' +
  encodeURIComponent('Firm:\nYour name and role:\nHow many people would use Fondok:\nWhat kind of hotel deals do you underwrite:\n');
