import type { AppConfig, Doc, JobStatus, Proposal } from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }

async function req<T>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const r = await fetch(url, {
    method, credentials: 'same-origin',
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  })
  if (r.status === 401) { onUnauthorized(); throw new ApiError(401, 'Sign in required') }
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* keep status text */ }
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

const j = (id: string) => `/api/jobs/${id}`
export const api = {
  config: () => req<AppConfig>('GET', '/api/config'),
  me: () => req<{ email: string }>('GET', '/api/me'),
  signIn: (credential: string) => req<{ email: string }>('POST', '/api/auth/google', { credential }),
  signOut: () => req<object>('POST', '/api/auth/logout', {}),
  jobs: () => req<JobStatus[]>('GET', '/api/jobs'),
  job: (id: string) => req<JobStatus>('GET', j(id)),
  upload: (file: File) => { const f = new FormData(); f.append('file', file); return req<{ job_id: string; pages: number }>('POST', '/api/jobs', undefined, f) },
  doc: (id: string) => req<Doc>('GET', `${j(id)}/doc`),
  rebuild: (id: string) => req<Doc>('POST', `${j(id)}/rebuild`, {}),
  editBlock: (id: string, block: string, markup: string) => req<object>('PUT', `${j(id)}/blocks/${block}`, { markup }),
  reorder: (id: string, order: string[]) => req<object>('PUT', `${j(id)}/order`, { order }),
  deleteBlocks: (id: string, blocks: string[]) => req<object>('POST', `${j(id)}/delete`, { blocks }),
  addBlock: (id: string, after: string, markup: string, type: string) => req<{ id: string; rev: number; cursor: number; length: number }>('POST', `${j(id)}/blocks`, { after, markup, type }),
  undo: (id: string) => req<object>('POST', `${j(id)}/undo`, {}),
  redo: (id: string) => req<object>('POST', `${j(id)}/redo`, {}),
  review: (id: string, mode: string, blocks: string[], note: string, model: string | null) =>
    req<Proposal>('POST', `${j(id)}/review`, { mode, blocks, note, model }),
  newBox: (id: string, page: number, bbox_norm: number[], note: string) =>
    req<Proposal>('POST', `${j(id)}/newbox`, { page, bbox_norm, note }),
  accept: (id: string, pid: string, blocks?: string[]) => req<object>('POST', `${j(id)}/proposals/${pid}/accept`, { blocks }),
  reject: (id: string, pid: string, blocks?: string[]) => req<object>('POST', `${j(id)}/proposals/${pid}/reject`, { blocks }),
  pageUrl: (id: string, n: number) => `${j(id)}/pages/${n}.jpg`,
  blockImageUrl: (id: string, b: string) => `${j(id)}/blocks/${b}/image`,
  downloadUrl: (id: string) => `${j(id)}/download`,
}
