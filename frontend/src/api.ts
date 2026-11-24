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
  getLease: (id: string, asOf = '2026-04-01') => request<any>(`/api/leases/${id}?as_of=${encodeURIComponent(asOf)}`),
  runAudit: (leaseId: string, invoiceId?: string) => request<any>(`/api/leases/${leaseId}/audit${invoiceId ? `?invoice_id=${encodeURIComponent(invoiceId)}` : ''}`, { method: 'POST' }),
  getFindings: (invoiceId: string) => request<any>(`/api/invoices/${invoiceId}/findings`),
  decideFinding: (findingId: string, approved: boolean) => request<any>(`/api/findings/${findingId}`, { method: 'PATCH', body: JSON.stringify({ approved }) }),
  generateLetter: (invoiceId: string, language: 'en' | 'hi' = 'en') => request<any>(`/api/invoices/${invoiceId}/letter?language=${language}`, { method: 'POST' }),
  deleteLease: (leaseId: string) => request<any>(`/api/leases/${leaseId}`, { method: 'DELETE' }),
  createLease: (body: { property_label: string; start_date?: string; end_date?: string }) =>
    request<any>('/api/leases', { method: 'POST', body: JSON.stringify(body) }),
  uploadDocument: async (leaseId: string, kind: string, file: File, effectiveDate?: string) => {
    const form = new FormData();
    form.append('kind', kind);
    if (effectiveDate) form.append('effective_date', effectiveDate);
    form.append('file', file);
    const res = await fetch(`/api/leases/${leaseId}/documents`, { method: 'POST', body: form });
    const text = await res.text();
    let data: any = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const msg = data?.detail?.error?.message || data?.error?.message || `Upload failed (${res.status})`;
      throw new Error(msg);
    }
    return data;
  },
};
