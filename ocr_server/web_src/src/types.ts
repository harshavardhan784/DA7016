export interface Page { n: number; aspect: number }
export interface BlockInfo {
  id: string; page: number; type: string | null; is_picture: boolean; has_image: boolean
  bbox_norm: number[] | null; markup: string; rendered: boolean; reason: string | null
  absorbed: boolean; absorbed_by: string | null
}
export interface Doc {
  route: string; route_info: Record<string, unknown>; rev: number; cursor: number; length: number
  pages: Page[]; order: string[]; blocks: Record<string, BlockInfo>; log: string[]; dirty: boolean
}
export interface Choice { mode: 'format' | 'reread' | 'delete'; label: string }
export interface ProposalBlock {
  id: string; before: string; after: string; fields: Record<string, unknown>
  plain_text_fallback: boolean; resolved?: 'accepted' | 'rejected'
  // assistant cards only
  kind?: 'edit' | 'merge' | 'split' | 'insert' | 'delete'; title?: string; ref?: string; page?: number; bbox?: number[] | null
  label?: string | null; warn?: string | null
}
export interface Proposal {
  id: string; mode: 'format' | 'reread' | 'newbox' | 'delete' | 'ask' | 'assist'; note: string; model: string | null
  blocks: ProposalBlock[]; status: 'ready' | 'closed'
  skipped?: { id: string; reason: string }[]
  message?: string; choices?: Choice[]; understood?: string; alternatives?: Choice[]; block_ids?: string[]
  usage: { calls: number; tok_in: number; tok_out: number; cost: number; seconds?: number }
  new_block?: { block_id: string; page: number; bbox_norm: number[] }; index?: number
}
export interface JobStatus {
  job_id: string; state: 'queued' | 'running' | 'finalizing' | 'done' | 'error'
  filename: string; pages: number; created: number; started?: number; finished?: number; error?: string
  log_tail?: string; queue_size?: number
}
export interface AppConfig { auth: boolean; google_client_id: string; max_mb: number; max_pages: number }
