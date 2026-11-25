export type ApiError = { error?: { code?: string; message?: string } };

/** True when the failure is best explained as "this browser lost its session". */
export function looksLikeLostSession(e: unknown): boolean {
  const err = e as { status?: number; code?: string } | null;
  return !!err && err.status === 404 && (err.code === 'not_found' || err.code === undefined);
}

/**
 * Session token, mirrored out of the API's `X-Session-Token` response header.
 *
 * The server also sets an HttpOnly cookie, and that is the normal path. But the
 * app is served inside a cross-origin iframe on preview hosts and in embedded
 * demos, where browsers refuse to store or send third-party cookies. When that
 * happens the tenant's lease exists on the server and the very next page says
 * "not found" - it looks like the product lost their data. Replaying the token
 * in a header keeps one browser on one identity even with cookies blocked.
 */
const SESSION_KEY = 'lg_session_token';

export function rememberSessionToken(res: Response): void {
  // Defensive on purpose: this runs on every response. A response whose headers
  // are unreadable (an opaque proxy response, a mocked fetch in a test harness)
  // must degrade to "cookie only", never throw and fail the request itself.
  const token = res.headers?.get?.('X-Session-Token');
  if (!token) return;
  try {
    if (sessionStorage.getItem(SESSION_KEY) !== token) sessionStorage.setItem(SESSION_KEY, token);
  } catch {
    /* storage disabled (private mode): fall back to the cookie alone */
  }
}

function sessionHeader(): Record<string, string> {
  try {
    const token = sessionStorage.getItem(SESSION_KEY);
    return token ? { 'X-Session-Token': token } : {};
  } catch {
    return {};
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...sessionHeader(), ...(opts.headers || {}) },
  });
  rememberSessionToken(res);
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = data?.error?.message || data?.detail?.error?.message || `Request failed (${res.status})`;
    // A 404 on a lease or invoice the user was just looking at is almost always a
    // lost session, not a missing record. Browsers drop cookies for reasons that
    // look like nothing (a Secure flag on a proxied host, an ITP purge, private
    // mode), and an unexplained "not found" makes the product look broken. Say
    // what probably happened and what to do.
    const err = new Error(msg) as Error & { status?: number; code?: string };
    err.status = res.status;
    err.code = data?.error?.code || data?.detail?.error?.code;
    throw err;
  }
  return data as T;
}

export const api = {
  health: () => request<any>('/api/health'),
  listLeases: () => request<any>('/api/leases'),
  loadSample: () => request<any>('/api/leases/sample', { method: 'POST' }),
  getLease: (id: string, asOf?: string) => request<any>(`/api/leases/${id}${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ''}`),
  runAudit: (leaseId: string, invoiceId?: string) => request<any>(`/api/leases/${leaseId}/audit${invoiceId ? `?invoice_id=${encodeURIComponent(invoiceId)}` : ''}`, { method: 'POST' }),
  getFindings: (invoiceId: string) => request<any>(`/api/invoices/${invoiceId}/findings`),
  decideFinding: (findingId: string, approved: boolean) => request<any>(`/api/findings/${findingId}`, { method: 'PATCH', body: JSON.stringify({ approved }) }),
  generateLetter: (invoiceId: string, language: 'en' | 'hi' = 'en') => request<any>(`/api/invoices/${invoiceId}/letter?language=${language}`, { method: 'POST' }),
  listAuditLog: (limit = 100) => request<any>(`/api/audit-log?limit=${limit}`),
  // A correction always carries a value; the API rejects a blank one because a
  // null would erase the extracted figure while looking like a success.
  correctTerm: (termId: string, value: number, note?: string) =>
    request<any>(`/api/terms/${termId}`, { method: 'PATCH', body: JSON.stringify({ value, note }) }),
  // For a term the document could not settle: the tenant states the value and it
  // is recorded as theirs, with no document attached.
  addUserTerm: (leaseId: string, termType: string, value: number, note?: string, effectiveDate?: string) =>
    request<any>(`/api/leases/${leaseId}/terms`, {
      method: 'POST',
      body: JSON.stringify({ term_type: termType, value, note, effective_date: effectiveDate }),
    }),
  deleteLease: (leaseId: string) => request<any>(`/api/leases/${leaseId}`, { method: 'DELETE' }),
  createLease: (body: { property_label: string; start_date?: string; end_date?: string }) =>
    request<any>('/api/leases', { method: 'POST', body: JSON.stringify(body) }),
  uploadDocument: async (leaseId: string, kind: string, file: File, effectiveDate?: string) => {
    const form = new FormData();
    form.append('kind', kind);
    if (effectiveDate) form.append('effective_date', effectiveDate);
    form.append('file', file);

    // Uploads can be slow on a weak connection (a stated Bharat constraint). Abort with a
    // clear message rather than leaving the user staring at a spinner forever.
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 90_000);
    let res: Response;
    try {
      res = await fetch(`/api/leases/${leaseId}/documents`, {
        method: 'POST',
        body: form,
        headers: sessionHeader(),
        signal: controller.signal,
      });
    } catch (e: any) {
      if (e?.name === 'AbortError') {
        throw new Error('The upload took too long and was stopped. Check your connection and try again.');
      }
      throw new Error(e?.message || 'Could not reach the server. Check your connection.');
    } finally {
      clearTimeout(timeout);
    }

    const text = await res.text();
    let data: any = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const msg =
        data?.detail?.error?.message ||
        data?.error?.message ||
        (res.status === 413 ? 'That file is too large. The limit is 10 MB.' : `Upload failed (${res.status})`);
      throw new Error(msg);
    }
    return data;
  },
};
