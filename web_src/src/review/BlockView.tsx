import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import { generateHTML } from '@tiptap/html'
import { drawAllMath, extensions } from '../markup/schema'
import { markupToDoc, Unsupported, type PM } from '../markup/parse'
import { docToMarkup } from '../markup/serialize'
import * as C from '../markup/commands'
import { api } from '../api'
import { useStore } from '../state/store'
import type { BlockInfo } from '../types'
import { useTick, useVisible } from './hooks'
import { label, leaveField, shortId } from './util'

type Parsed = { doc: PM } | { raw: string }
function parse(markup: string): Parsed {
  try { return { doc: markupToDoc(markup) } } catch (e) { if (e instanceof Unsupported) return { raw: e.message }; throw e }
}

const GRIP = (
  <svg width="12" height="16" viewBox="0 0 12 16" fill="#2B50D8" aria-hidden="true">
    {[3, 8, 13].flatMap((y) => [3, 9].map((x) => <circle key={`${x}${y}`} cx={x} cy={y} r="1.4" />))}
  </svg>
)

/** One real editor for one block. Saves a moment after you stop typing, and when you leave it. */
function BlockEditor({ block, doc, grab }: { block: BlockInfo; doc: PM; grab: boolean }) {
  const save = useStore((s) => s.saveBlock)
  const lastSaved = useRef(block.markup)
  const dirty = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const flush = (editor: { getJSON: () => unknown }) => {
    clearTimeout(timer.current)
    if (!dirty.current) return
    const md = docToMarkup(editor.getJSON() as PM)
    dirty.current = false
    lastSaved.current = md
    useStore.getState().setEditorDirty(false)
    void save(block.id, md)
  }

  const editor = useEditor({
    extensions, content: doc, autofocus: grab ? 'end' : false,
    editorProps: { attributes: { 'aria-label': `Edit ${label(block.id)}`, spellcheck: 'false' } },
    onUpdate: ({ editor: ed }) => {
      dirty.current = true
      useStore.getState().setEditorDirty(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => flush(ed), 900)
    },
    onFocus: ({ editor: ed }) => {
      const st = useStore.getState()
      st.setActive(block.id)
      st.setEditor(ed)
      if (!st.selected.includes(block.id)) st.select([block.id])
    },
    onBlur: ({ editor: ed }) => flush(ed),
  }, [block.id])

  // changes that come from the server (undo, redo, accepted proposals) replace the text unless you are typing
  useEffect(() => {
    if (!editor || dirty.current || block.markup === lastSaved.current) return
    lastSaved.current = block.markup
    editor.commands.setContent(markupToDoc(block.markup), { emitUpdate: false })
  }, [block.markup, editor])

  useEffect(() => () => {
    if (editor && dirty.current) { dirty.current = false; void save(block.id, docToMarkup(editor.getJSON() as PM)) }
    clearTimeout(timer.current)
    if (useStore.getState().editor === editor) useStore.getState().setEditor(null)
  }, [editor])   // eslint-disable-line react-hooks/exhaustive-deps

  return (<>{editor && <TableBar block={block.id} editor={editor} />}<EditorContent editor={editor} /></>)
}

function TableBar({ block, editor }: { block: string; editor: NonNullable<ReturnType<typeof useEditor>> }) {
  useTick(editor)
  const active = useStore((s) => s.active)
  const [del, setDel] = useState(false)
  if (active !== block || !editor.isFocused || !editor.isActive('table')) return null     // only while you are working in the table
  const run = (fn: () => void) => (e: React.MouseEvent) => { e.preventDefault(); fn() }          // buttons keep the caret where it is
  const scroller = editor.view.dom.closest('.docscroll')
  const roomAbove = scroller ? editor.view.dom.getBoundingClientRect().top - scroller.getBoundingClientRect().top : 999
  return (
    <div className="tablebar" role="toolbar" aria-label="Table" onMouseDown={(e) => e.preventDefault()} style={roomAbove < 56 ? { top: 'auto', bottom: -42 } : undefined}>
      <button onMouseDown={run(() => editor.chain().focus().addRowBefore().run())}>Row ↑</button>
      <button onMouseDown={run(() => editor.chain().focus().addRowAfter().run())}>Row ↓</button>
      <button onMouseDown={run(() => editor.chain().focus().addColumnBefore().run())}>Col ←</button>
      <button onMouseDown={run(() => editor.chain().focus().addColumnAfter().run())}>Col →</button>
      <button disabled={!C.canMergeAcross(editor)} title="Select two or more cells in one row" onMouseDown={run(() => C.mergeAcross(editor))}>Merge across</button>
      <button className={C.headerRowActive(editor) ? 'on' : ''} onMouseDown={run(() => C.toggleHeaderRow(editor))}>Header row</button>
      <span style={{ position: 'relative' }}>
        <button onMouseDown={run(() => setDel(!del))}>Delete ▾</button>
        {del && (
          <div className="popover" style={{ right: 0, top: 32, width: 150, padding: 4 }}>
            <button className="opt" onMouseDown={run(() => { editor.chain().focus().deleteRow().run(); setDel(false) })}>Delete row</button>
            <button className="opt" onMouseDown={run(() => { editor.chain().focus().deleteColumn().run(); setDel(false) })}>Delete column</button>
            <button className="opt" onMouseDown={run(() => { editor.chain().focus().deleteTable().run(); setDel(false) })}>Delete table</button>
          </div>
        )}
      </span>
    </div>
  )
}

/** Markup the editor cannot show without changing the Word output: edit the tags directly. */
function RawEditor({ block, reason }: { block: BlockInfo; reason: string }) {
  const save = useStore((s) => s.saveBlock)
  const [text, setText] = useState(block.markup)
  useEffect(() => setText(block.markup), [block.markup])
  return (
    <>
      <div className="note">Shown as tags because it uses {reason}.</div>
      <textarea className="raw" aria-label={`Tags of ${label(block.id)}`} value={text} onChange={(e) => setText(e.target.value)}
        onBlur={() => void save(block.id, text)} />
    </>
  )
}

function Static({ doc }: { doc: PM }) {
  const html = useMemo(() => generateHTML(doc as never, extensions), [doc])
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => { if (ref.current) drawAllMath(ref.current) }, [html])
  return <div ref={ref} className="ProseMirror" dangerouslySetInnerHTML={{ __html: html }} />
}

/** Read-only view of some markup, for the comparison window. */
export function MarkupView({ markup }: { markup: string }) {
  const parsed = useMemo(() => parse(markup), [markup])
  if (!markup.trim()) return <div className="muted">(nothing)</div>
  return <div className="bk">{'raw' in parsed ? <pre className="raw">{markup}</pre> : <Static doc={parsed.doc} />}</div>
}

export const BlockView = memo(function BlockView({ block, jobId, continues, dragging, onGrip, onNudge }: {
  block: BlockInfo; jobId: string; continues: boolean; dragging: boolean
  onGrip: (e: React.PointerEvent, id: string) => void; onNudge: (id: string, dir: -1 | 1) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const visible = useVisible(ref)
  const selected = useStore((s) => s.selected.includes(block.id))
  const [woken, setWoken] = useState(false)                       // clicked before its editor existed: build it now and focus it
  const parsed = useMemo(() => parse(block.markup), [block.markup])
  const empty = !block.markup.trim() && !(block.is_picture && block.has_image)

  // Ctrl/Cmd+click adds or removes this block from the selection without touching the text. Shift+click stays with the editor
  // (text and cell ranges). Plain clicks on editable blocks select them when the caret lands inside (see BlockEditor).
  const onDown = (e: React.MouseEvent) => {
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); e.stopPropagation(); leaveField(); useStore.getState().select([block.id], 'toggle'); return }
    if (empty || block.is_picture) useStore.getState().select([block.id])
  }

  return (
    <div ref={ref} data-block={block.id} className={`bk${selected ? ' sel' : ''}${dragging ? ' dragging' : ''}`} onMouseDownCapture={onDown}>
      <span className="tag">{shortId(block.id)}</span>
      <span className="grip" role="button" tabIndex={0} aria-label={`Move ${label(block.id)}: drag, or use the arrow keys`} title="Drag to move"
        onPointerDown={(e) => onGrip(e, block.id)}
        onKeyDown={(e) => { if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); onNudge(block.id, e.key === 'ArrowUp' ? -1 : 1) } }}>{GRIP}</span>
      {block.is_picture && block.has_image ? (
        <img className="pic" alt={`Picture ${label(block.id)}`} src={api.blockImageUrl(jobId, block.id)} />
      ) : empty ? (
        <div className="ghostbox">Empty block {label(block.id)}{block.reason ? ` (${block.reason.replace(/_/g, ' ')})` : ''}. Select it and use Re-read to read it again.</div>
      ) : 'raw' in parsed ? (
        <RawEditor block={block} reason={parsed.raw} />
      ) : visible || woken ? (
        <BlockEditor block={block} doc={parsed.doc} grab={woken && !visible} />
      ) : (
        <div onMouseDown={(e) => { if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); setWoken(true) } }}><Static doc={parsed.doc} /></div>
      )}
      {block.absorbed && <span className="join" contentEditable={false}>joined into {block.absorbed_by ? label(block.absorbed_by) : 'the block above'} in the Word file</span>}
      {continues && !block.absorbed && <span className="join" contentEditable={false}>continues on the next page</span>}
    </div>
  )
})
