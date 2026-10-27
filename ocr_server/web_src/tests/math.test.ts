import { describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import { generateHTML } from '@tiptap/html'
import { drawAllMath, extensions } from '../src/markup/schema'
import { markupToDoc } from '../src/markup/parse'
import { docToMarkup } from '../src/markup/serialize'

const nodes = (markup: string) => (markupToDoc(markup).content![0].content ?? []).map((n) => (n.type === 'math' ? `math(${n.attrs!.latex}${n.attrs!.display ? ',display' : ''})` : `"${n.text}"`))

describe('formulas', () => {
  it('your block: the words stay words and the formula becomes one math object', () => {
    expect(nodes('<P align="center">మార్కులు : $5 \\times 5 = 25$</P>')).toEqual(['"మార్కులు : "', 'math(5 \\times 5 = 25)'])
  })
  it('follows the builder: money is not a formula, escaped dollars are not delimiters, display forms are display', () => {
    expect(nodes('<P>Cost is $5 and $10 today</P>')).toEqual(['"Cost is $5 and $10 today"'])
    expect(nodes('<P>a \\$ b $y$</P>')).toEqual(['"a \\$ b "', 'math(y)'])
    expect(nodes('<P>$$x^2$$ and \\(a\\neq b\\) and \\[c\\]</P>')).toEqual(['math(x^2,display)', '" and "', 'math(a\\neq b)', '" and "', 'math(c,display)'])
  })
  it('writes the formula back exactly as it was read', () => {
    for (const m of ['<P align="center">మార్కులు : $5 \\times 5 = 25$</P>', '<P>Inline $x^2$ and $$\\frac{1}{2}$$ done</P>', '<P>a &lt; b $x &lt; y$</P>']) {
      expect(docToMarkup(markupToDoc(m))).toBe(m)
    }
  })
  it('shows it as math in the editor and in the read-only view, not as LaTeX source', () => {
    const doc = markupToDoc('<P>మార్కులు : $5 \\times 5 = 25$</P>')
    const ed = new Editor({ extensions, content: doc, element: document.body.appendChild(document.createElement('div')) })
    expect(ed.view.dom.querySelector('.katex')).not.toBeNull()
    const seen = ed.view.dom.querySelector('.katex-html')!.textContent!      // the drawn formula (the rest is a hidden reading aid)
    expect(seen).toContain('×')
    expect(seen).not.toContain('\\times')
    ed.destroy()
    const box = document.createElement('div')
    box.innerHTML = generateHTML(doc as never, extensions)
    drawAllMath(box)
    expect(box.querySelector('.katex')).not.toBeNull()
    expect(box.querySelector('.katex-html')!.textContent).toContain('×')
  })
})
