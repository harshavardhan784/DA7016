import { useEffect, useRef, useState } from 'react'
import type { Editor } from '@tiptap/core'
import * as C from '../markup/commands'
import { useStore } from '../state/store'
import { useTick } from './hooks'

const SIZES = ['8', '9', '10', '11', '12', '14', '16', '18', '20', '24', '28']
const FONTS = ['Nirmala UI', 'Times New Roman', 'Arial', 'Calibri', 'Cambria', 'Courier New']
const HL = ['yellow', 'green', 'cyan', 'pink', 'red', 'blue', 'gray']
const stop = (e: React.MouseEvent) => e.preventDefault()          // keep the text selection while a button is pressed

const Svg = ({ d }: { d: string }) => (
  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
)
const ICONS = {
  undo: 'M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3', redo: 'M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3',
  bullets: 'M10 6h10M10 12h10M10 18h10M4.5 6h.01M4.5 12h.01M4.5 18h.01',
  left: 'M4 6h16M4 10h10M4 14h16M4 18h10', center: 'M4 6h16M7 10h10M4 14h16M7 18h10', right: 'M4 6h16M10 10h10M4 14h16M10 18h10', justify: 'M4 6h16M4 10h16M4 14h16M4 18h16',
  outdent: 'M11 6h9M11 12h9M11 18h9M7 9l-3 3 3 3', indent: 'M11 6h9M11 12h9M11 18h9M4 9l3 3-3 3', more: 'M5 12h.01M12 12h.01M19 12h.01', down: 'M6 9l6 6 6-6',
}
type Align = 'left' | 'center' | 'right' | 'justify'

/** Where a dropdown panel goes: just under its button. Fixed, because the toolbar scrolls sideways and would clip anything hanging below it. */
const below = (r: React.RefObject<HTMLElement | null>) => {
  const b = r.current?.getBoundingClientRect()
  return b ? { position: 'fixed' as const, left: b.left, top: b.bottom + 4 } : {}
}

/** Closes a popover when you click anywhere else. */
function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) close() }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open, close])
  return ref
}

export function Toolbar() {
  const ed = useStore((s) => s.editor)
  const open = useStore((s) => s.toolbarOpen)
  const tool = useStore((s) => s.tool)
  const busy = useStore((s) => s.busy)
  const { setToolbarOpen, undo, redo, setTool, addBlockAfter } = useStore.getState()
  const [grid, setGrid] = useState<{ r: number; c: number } | null>(null)
  const [tbl, setTbl] = useState(false)
  const [alignMenu, setAlignMenu] = useState(false)
  const tblRef = useOutside(tbl, () => setTbl(false))
  const alignRef = useOutside(alignMenu, () => setAlignMenu(false))
  useTick(ed)
  const at = ed ? C.blockAttrs(ed) : {}
  const sel = ed ? !ed.state.selection.empty : false
  const markVal = (n: string) => (ed && sel ? (ed.getAttributes(n).value as string | undefined) : undefined)
  const heading = String(at.heading ?? '').toLowerCase().replace(/^h/, '')
  const size = markVal('size') ?? String(at.size ?? '')
  const font = markVal('font') ?? String(at.font ?? '')
  const align = (String(at.align ?? 'left') as Align)
  const on = (b: boolean) => (b ? ' on' : '')

  const ic = (name: keyof typeof ICONS, title: string, fn: (e: Editor) => void, active = false) => (
    <button className={`gb${on(active)}`} aria-label={title} title={title} aria-pressed={active} disabled={!ed} onMouseDown={stop} onClick={() => ed && fn(ed)}><Svg d={ICONS[name]} /></button>
  )
  const mark = (txt: React.ReactNode, title: string, fn: (e: Editor) => void, active: boolean) => (
    <button className={`gb${on(active)}`} aria-label={title} title={title} aria-pressed={active} disabled={!ed} onMouseDown={stop} onClick={() => ed && fn(ed)}>{txt}</button>
  )

  return (
    <div style={{ flex: 'none' }}>
      <div className="toolbar" role="toolbar" aria-label="Formatting">
        <button className="gb" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!!busy} onMouseDown={stop} onClick={() => void undo()}><Svg d={ICONS.undo} /></button>
        <button className="gb" aria-label="Redo" title="Redo" disabled={!!busy} onMouseDown={stop} onClick={() => void redo()}><Svg d={ICONS.redo} /></button>
        <span className="sep" />
        <select aria-label="Paragraph style" style={{ width: 82 }} disabled={!ed} value={['1', '2', '3'].includes(heading) ? heading : ''} onChange={(e) => ed && C.setHeading(ed, e.target.value as '' | '1' | '2' | '3')}>
          <option value="">Normal</option><option value="1">Heading 1</option><option value="2">Heading 2</option><option value="3">Heading 3</option>
        </select>
        <select aria-label="Font size" disabled={!ed} value={SIZES.includes(size) ? size : ''} onChange={(e) => ed && C.setValue(ed, 'size', e.target.value || null)} style={{ width: 54 }}>
          <option value="">Size</option>{SIZES.map((s) => <option key={s}>{s}</option>)}
        </select>
        <span className="sep" />
        {mark(<b>B</b>, 'Bold', (e) => C.toggleBasic(e, 'bold'), !!ed && C.basicActive(ed, 'bold'))}
        {mark(<i>I</i>, 'Italic', (e) => C.toggleBasic(e, 'italic'), !!ed && C.basicActive(ed, 'italic'))}
        {mark(<u>U</u>, 'Underline', (e) => C.toggleBasic(e, 'underline'), !!ed && C.basicActive(ed, 'underline'))}
        <span className="sep" />
        {ic('bullets', 'Bullets', (e) => C.toggleList(e, 'bullet'), !!ed && ed.isActive('list', { type: 'bullet' }))}
        {mark('1.', 'Numbering', (e) => C.toggleList(e, 'number'), !!ed && ed.isActive('list', { type: 'number' }))}
        <span className="sep" />
        <span ref={alignRef} style={{ position: 'relative' }}>
          <button className="gb" aria-label="Alignment" title="Alignment" aria-expanded={alignMenu} disabled={!ed} onMouseDown={stop} onClick={() => setAlignMenu(!alignMenu)}>
            <Svg d={ICONS[align]} /><Svg d={ICONS.down} />
          </button>
          {alignMenu && ed && (
            <div className="popover" style={{ ...below(alignRef), width: 150 }} onMouseDown={stop}>
              {(['left', 'center', 'right', 'justify'] as Align[]).map((a) => (
                <button key={a} className="opt" onClick={() => { C.setAlign(ed, a); setAlignMenu(false) }}>{a === 'center' ? 'Centre' : a[0].toUpperCase() + a.slice(1)}</button>
              ))}
            </div>
          )}
        </span>
        <span className="sep" />
        <span ref={tblRef} style={{ position: 'relative' }}>
          <button className="gb" aria-expanded={tbl} title="Insert a table at the caret, or as a new block below the selected one" onMouseDown={stop} onClick={() => setTbl(!tbl)}>Table<Svg d={ICONS.down} /></button>
          {tbl && (
            <div className="popover" style={below(tblRef)} onMouseDown={stop} onMouseLeave={() => setGrid(null)}>
              <div className="grid">
                {Array.from({ length: 36 }, (_, i) => { const r = Math.floor(i / 6) + 1, c = (i % 6) + 1
                  return <i key={i} className={grid && r <= grid.r && c <= grid.c ? 'on' : ''} onMouseEnter={() => setGrid({ r, c })}
                    onClick={() => { if (ed && ed.isFocused) C.insertTable(ed, r, c); else void addBlockAfter(C.emptyTableMarkup(r, c), 'Table'); setTbl(false) }} role="button" aria-label={`${r} by ${c} table`} /> })}
              </div>
              <div className="muted" style={{ marginTop: 6 }}>{grid ? `${grid.r} rows × ${grid.c} columns` : ed && ed.isFocused ? 'Inserted at the caret' : 'Added as a new block below the selected one'}</div>
            </div>
          )}
        </span>
        <button className={`gb${on(tool === 'newbox')}`} title="Text the OCR missed: draw a box around it on the page and it will be read" aria-pressed={tool === 'newbox'} onMouseDown={stop}
          onClick={() => setTool(tool === 'newbox' ? 'tap' : 'newbox')}>New box</button>
        <span className="sp" />
        <button className={`gb pinned${on(open)}`} aria-label="More formatting" title="More formatting" aria-expanded={open} onMouseDown={stop} onClick={() => setToolbarOpen(!open)}><Svg d={ICONS.more} /></button>
      </div>
      {open && (
        <div className="toolbar more" role="toolbar" aria-label="More formatting">
          <select aria-label="Font" disabled={!ed} value={FONTS.includes(font) ? font : ''} onChange={(e) => ed && C.setValue(ed, 'font', e.target.value || null)} style={{ maxWidth: 128 }}>
            <option value="">Font</option>{FONTS.map((f) => <option key={f}>{f}</option>)}
          </select>
          <span className="sep" />
          {mark(<s>S</s>, 'Strikethrough', (e) => C.toggleMarkSmart(e, 'strike'), !!ed && ed.isActive('strike'))}
          {mark('x²', 'Superscript', (e) => C.toggleMarkSmart(e, 'superscript'), !!ed && ed.isActive('superscript'))}
          {mark('x₂', 'Subscript', (e) => C.toggleMarkSmart(e, 'subscript'), !!ed && ed.isActive('subscript'))}
          <input type="color" aria-label="Text colour" title="Text colour" disabled={!ed} defaultValue="#000000" onChange={(e) => ed && C.setValue(ed, 'color', e.target.value.toUpperCase())} />
          <select aria-label="Highlight" disabled={!ed} value="" onChange={(e) => ed && C.setValue(ed, 'highlight', e.target.value === 'none' ? null : e.target.value)} style={{ maxWidth: 96 }}>
            <option value="">Highlight</option>{HL.map((h) => <option key={h}>{h}</option>)}<option value="none">none</option>
          </select>
          <span className="sep" />
          {ic('outdent', 'Decrease indent', (e) => C.indent(e, -1))}
          {ic('indent', 'Increase indent', (e) => C.indent(e, 1))}
          <select aria-label="Line spacing" disabled={!ed} value={String(at.line_spacing ?? '1.0')} onChange={(e) => ed && C.setLineSpacing(ed, e.target.value)} style={{ maxWidth: 66 }}>
            {['1.0', '1.15', '1.5', '2'].map((v) => <option key={v}>{v}</option>)}
          </select>
          <select aria-label="Space after" title="Space after the paragraph" disabled={!ed} value={String(at.space_after ?? '')} onChange={(e) => ed && C.setSpaceAfter(ed, e.target.value)} style={{ maxWidth: 96 }}>
            <option value="">Spacing</option><option value="6">6 pt</option><option value="12">12 pt</option><option value="18">18 pt</option>
          </select>
          <span className="sep" />
          <button className="gb" disabled={!ed} onMouseDown={stop} onClick={() => ed && C.insertBlank(ed)}>Blank line</button>
          <button className="gb" disabled={!ed} title="Select the text first. It is written at the right edge, like marks." onMouseDown={stop} onClick={() => ed && C.toggleMarkSmart(ed, 'right')}>Marks at right</button>
          <button className="gb" disabled={!ed} onMouseDown={stop} onClick={() => ed && C.clearFormatting(ed)}>Clear</button>
        </div>
      )}
    </div>
  )
}
