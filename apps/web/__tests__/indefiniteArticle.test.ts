/**
 * Sam's QA (2026-11-26): the memo thesis and a risk read "a 8.3% going-in cap".
 * A number-led phrase takes the article it is read with: eight, eleven and
 * eighteen open with a vowel sound; the rest do not.
 */
import { describe, it, expect } from 'vitest';
import { indefiniteArticle, withArticle } from '@/lib/format';

describe('indefiniteArticle — numbers are read aloud', () => {
  it.each([
    ['8%', 'an'],
    ['8.3%', 'an'],
    ['11%', 'an'],
    ['11.4%', 'an'],
    ['18%', 'an'],
    ['18-year hold', 'an'],
    ['80%', 'an'],
    ['850K', 'an'],
    ['1.5%', 'a'],
    ['7%', 'a'],
    ['7.9%', 'a'],
    ['10%', 'a'],
    ['12%', 'a'],
    ['19%', 'a'],
    ['100%', 'a'],
    ['2.50x', 'a'],
    ['5-year hold', 'a'],
    ['$36.4M', 'a'],
    ['$8M', 'an'],
    ['$11.2M', 'an'],
    ['$257K', 'a'],
  ])('%s → %s', (phrase, article) => {
    expect(indefiniteArticle(phrase)).toBe(article);
  });

  it('falls back to the vowel-letter rule for a word-led phrase', () => {
    expect(indefiniteArticle('institutional buyer')).toBe('an');
    expect(indefiniteArticle('going-in cap')).toBe('a');
  });

  it('withArticle joins the two — the exact strings Sam read', () => {
    expect(withArticle('8.3%')).toBe('an 8.3%');
    expect(withArticle('11%')).toBe('an 11%');
    expect(withArticle('18%')).toBe('an 18%');
    expect(withArticle('1.5%')).toBe('a 1.5%');
    expect(withArticle('8%')).toBe('an 8%');
  });
});
