import { afterEach, describe, expect, it } from 'vitest';
import {
  clearSupportSession,
  getSupportSession,
  setSupportSession,
  supportHeaderFor,
} from '@/lib/supportAccess';

const future = () => new Date(Date.now() + 60 * 60 * 1000).toISOString();

afterEach(() => clearSupportSession());

describe('support access session', () => {
  it('adds no header when support mode is off', () => {
    expect(supportHeaderFor('/deals')).toEqual({});
  });

  it('adds the firm header to worker requests while a grant is live', () => {
    setSupportSession({ grantId: 'g1', tenantId: 't-firm', firm: 'Harbor Point', expiresAt: future() });
    expect(supportHeaderFor('/deals')).toEqual({ 'X-Support-Tenant': 't-firm' });
    expect(supportHeaderFor('/deals/abc/documents')).toEqual({ 'X-Support-Tenant': 't-firm' });
  });

  it('never sends the header to the grant-management routes', () => {
    setSupportSession({ grantId: 'g1', tenantId: 't-firm', firm: 'Harbor Point', expiresAt: future() });
    expect(supportHeaderFor('/admin/support-access')).toEqual({});
    expect(supportHeaderFor('/admin/support-access/g1')).toEqual({});
  });

  it('drops an expired session on read', () => {
    setSupportSession({ grantId: 'g1', tenantId: 't-firm', firm: 'Harbor Point', expiresAt: new Date(Date.now() - 1000).toISOString() });
    expect(getSupportSession()).toBeNull();
    expect(supportHeaderFor('/deals')).toEqual({});
  });

  it('ignores a corrupt stored value', () => {
    window.sessionStorage.setItem('fondok:support-session', '{not json');
    expect(getSupportSession()).toBeNull();
  });
});
