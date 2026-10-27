import type { BlockInfo } from '../types'
/** "p3_b4" -> "p3 · B4" */
export const label = (id: string) => id.replace(/^p(\d+)_(.*)$/, (_, n: string, r: string) => `p${n} · ${r.toUpperCase()}`)
/** "p3_B4" -> "B4" */
export const shortId = (id: string) => id.replace(/^p\d+_/, '').toUpperCase()
export const blocksOnPage = (order: string[], blocks: Record<string, BlockInfo>, page: number) => order.filter((i) => blocks[i].page === page)

/** Selecting blocks takes the caret out of any text field, so Delete then means "delete the selected blocks". */
export function leaveField() {
  const a = document.activeElement as HTMLElement | null
  if (a && a !== document.body && a.closest('.ProseMirror, textarea, input, select')) a.blur()
}
