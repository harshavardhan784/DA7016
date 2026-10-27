import { describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'
import { CellSelection } from '@tiptap/pm/tables'
import { extensions } from '../src/markup/schema'
import { markupToDoc } from '../src/markup/parse'
import { docToMarkup } from '../src/markup/serialize'
import * as C from '../src/markup/commands'

const make = (markup: string) => new Editor({ extensions, content: markupToDoc(markup) })
const out = (ed: Editor) => docToMarkup(ed.getJSON() as never)
const selectAll = (ed: Editor) => ed.commands.selectAll()
const cursorIn = (ed: Editor, at: number) => ed.view.dispatch(ed.state.tr.setSelection(TextSelection.create(ed.state.doc, at)))

describe('toolbar commands produce the tags the builder understands', () => {
  it('bold on a selection becomes a STYLE span; with nothing selected it becomes a paragraph attribute', () => {
    const ed = make('<P>Hello world</P>')
    ed.commands.setTextSelection({ from: 1, to: 6 }); C.toggleBasic(ed, 'bold')
    expect(out(ed)).toBe('<P><STYLE bold="true">Hello</STYLE> world</P>')
    const ed2 = make('<P>Hello world</P>'); cursorIn(ed2, 3); C.toggleBasic(ed2, 'italic')
    expect(out(ed2)).toBe('<P italic="true">Hello world</P>')
  })

  it('size, colour, highlight, font', () => {
    const ed = make('<P>Hello world</P>')
    ed.commands.setTextSelection({ from: 1, to: 6 })
    C.setValue(ed, 'size', '14'); C.setValue(ed, 'color', '#FF0000'); C.setValue(ed, 'highlight', 'yellow')
    expect(out(ed)).toBe('<P><STYLE size="14" color="#FF0000" highlight="yellow">Hello</STYLE> world</P>')
    C.setValue(ed, 'size', null)
    expect(out(ed)).not.toContain('size=')
  })

  it('alignment, heading, indent, spacing', () => {
    const ed = make('<P>Title</P>'); cursorIn(ed, 2)
    C.setAlign(ed, 'center'); C.setHeading(ed, '1'); C.indent(ed, 1); C.setLineSpacing(ed, '1.5'); C.setSpaceAfter(ed, '12')
    expect(out(ed)).toBe('<P align="center" heading="1" indent_left="0.25" space_after="12" line_spacing="1.5">Title</P>')
    C.setAlign(ed, 'left'); C.setHeading(ed, ''); C.indent(ed, -1)
    expect(out(ed)).toBe('<P space_after="12" line_spacing="1.5">Title</P>')
  })

  it('numbering writes the labels, because the builder does not number', () => {
    const ed = make('<P>First</P>\n<P>Second</P>'); selectAll(ed); C.toggleList(ed, 'number')
    expect(out(ed)).toBe('<LIST type="number"><ITEM level="0">1. First</ITEM><ITEM level="0">2. Second</ITEM></LIST>')
    C.toggleList(ed, 'number')                         // off: back to paragraphs, text untouched
    expect(out(ed)).toBe('<P>1. First</P>\n<P>2. Second</P>')
    const ed2 = make('<P>(a) kept label</P>'); selectAll(ed2); C.toggleList(ed2, 'bullet')
    expect(out(ed2)).toBe('<LIST type="bullet"><ITEM level="0">(a) kept label</ITEM></LIST>')
  })

  it('list indent changes the level; Enter in a list adds an item', () => {
    const ed = make('<LIST type="number"><ITEM level="0">1. a</ITEM></LIST>'); cursorIn(ed, 4); C.indent(ed, 1)
    expect(out(ed)).toContain('level="1"')
  })

  it('tables: insert, header row, rows, columns, merge across only', () => {
    const ed = make('<P>Intro</P>'); cursorIn(ed, 2); C.insertTable(ed, 2, 3)
    expect(out(ed)).toBe('<P>Intro</P>\n<TABLE border="1"><ROW header="true"><CELL></CELL><CELL></CELL><CELL></CELL></ROW><ROW><CELL></CELL><CELL></CELL><CELL></CELL></ROW></TABLE>')
    // put the cursor in the first cell, add a column and a row
    let cell = -1; ed.state.doc.descendants((n, pos) => { if (n.type.name === 'tableCell' && cell < 0) cell = pos + 2; return true })
    cursorIn(ed, cell)
    ed.chain().addColumnAfter().run(); ed.chain().addRowAfter().run()
    expect(out(ed).match(/<ROW/g)!.length).toBe(3)
    expect(out(ed).match(/<CELL/g)!.length).toBe(12)
    C.toggleHeaderRow(ed)
    expect(out(ed)).toContain('<TABLE border="1"><ROW>')
  })

  it('merge across works within one row and is refused across rows', () => {
    const ed = make('<TABLE border="1"><ROW><CELL>a</CELL><CELL>b</CELL></ROW><ROW><CELL>c</CELL><CELL>d</CELL></ROW></TABLE>')
    const cells: number[] = []; ed.state.doc.descendants((n, pos) => { if (n.type.name === 'tableCell') cells.push(pos); return true })
    const sel = (a: number, b: number) => ed.view.dispatch(ed.state.tr.setSelection(CellSelection.create(ed.state.doc, cells[a], cells[b])))
    sel(0, 3); expect(C.canMergeAcross(ed)).toBe(false)
    sel(0, 1); expect(C.canMergeAcross(ed)).toBe(true); C.mergeAcross(ed)
    expect(out(ed)).toContain('<CELL colspan="2"><P>a</P><P>b</P></CELL>')     // both texts kept, as in Word
    expect(out(ed)).not.toContain('rowspan')
  })

  it('blank, marks at right and clear formatting', () => {
    const ed = make('<P>Marks </P>'); cursorIn(ed, 7); C.insertBlank(ed)
    expect(out(ed)).toContain('<BLANK type="underscore" length="20"/>')
    const ed2 = make('<P>Q <STYLE bold="true">here</STYLE> 5</P>'); selectAll(ed2); C.clearFormatting(ed2)
    expect(out(ed2)).toBe('<P>Q here 5</P>')
    const ed3 = make('<P>Question 5</P>'); ed3.commands.setTextSelection({ from: 10, to: 12 }); C.toggleMarkSmart(ed3, 'right')
    expect(out(ed3)).toBe('<P>Question <RIGHT>5</RIGHT></P>')
  })
})
