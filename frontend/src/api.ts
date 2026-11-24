export type ApiError = { error?: { code?: string; message?: string } };

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = data?.error?.message || data?.detail?.error?.message || `Request failed (${res.status})`;
    throw new Error(msg);
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
  correctTerm: (termId: string, value: number | null, note?: string) =>
    request<any>(`/api/terms/${termId}`, { method: 'PATCH', body: JSON.stringify({ value, note }) }),
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
