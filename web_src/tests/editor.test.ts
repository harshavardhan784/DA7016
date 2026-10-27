import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { Editor } from '@tiptap/core'
import { generateHTML } from '@tiptap/html'
import { extensions } from '../src/markup/schema'
import { markupToDoc, Unsupported } from '../src/markup/parse'
import { docToMarkup } from '../src/markup/serialize'

// Computed here from the corpus (the same conversion roundtrip.test.ts runs), so this file does not depend on
// roundtrip.out.json existing yet: tests in different files run in parallel and that file may not be written first.
const corpus: { id: string; markup: string }[] = JSON.parse(readFileSync('tests/corpus.json', 'utf-8'))
const results = corpus.map((c) => {
  try { return { id: c.id, markup: c.markup, out: docToMarkup(markupToDoc(c.markup)) as string | null } }
  catch (e) { if (!(e instanceof Unsupported)) throw e; return { id: c.id, markup: c.markup, out: null as string | null } }
})

describe('the editor holds the converted documents without changing them', () => {
  for (const r of results.filter((r) => r.out !== null)) {
    it(r.id, () => {
      const doc = markupToDoc(r.markup)
      const ed = new Editor({ extensions, content: doc })
      expect(docToMarkup(ed.getJSON() as never)).toBe(r.out)
      expect(generateHTML(doc as never, extensions).length).toBeGreaterThan(0)
      ed.destroy()
    })
  }
})
