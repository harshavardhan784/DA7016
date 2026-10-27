import { describe, expect, it } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { markupToDoc, Unsupported } from '../src/markup/parse'
import { docToMarkup } from '../src/markup/serialize'

const corpus: { id: string; markup: string; refuse?: boolean }[] = JSON.parse(readFileSync('tests/corpus.json', 'utf-8'))

describe('markup <-> editor document', () => {
  const results = corpus.map((c) => {
    try {
      const out = docToMarkup(markupToDoc(c.markup))
      return { id: c.id, markup: c.markup, out, reason: null as string | null }
    } catch (e) {
      if (!(e instanceof Unsupported)) throw e
      return { id: c.id, markup: c.markup, out: null as string | null, reason: e.message }
    }
  })

  it('writes the round-trip results for the Python fidelity check', () => {
    writeFileSync('tests/roundtrip.out.json', JSON.stringify(results, null, 1))
    expect(results.length).toBe(corpus.length)
  })

  it('is stable: converting its own output again changes nothing', () => {
    for (const r of results) if (r.out !== null) expect(docToMarkup(markupToDoc(r.out)), r.id).toBe(r.out)
  })

  it('refuses exactly the shapes it cannot show', () => {
    const refused = results.filter((r) => r.out === null).map((r) => r.id)
    expect(refused).toEqual(corpus.filter((c) => c.refuse).map((c) => c.id))
  })
})
