/**
 * Client-facing copy for the dash reasons.
 *
 * The generated registry (`reasons.generated.ts`, owned by the worker) phrases
 * one explanation with the override keys in parentheses — "An analyst NOI
 * schedule (noi_override_by_year / terminal_noi_override) is active…". A
 * client reads the sentence without the keys; the engineering reference keeps
 * them verbatim.
 */
import { REASONS, type ReasonCode } from '@/lib/ontology/reasons.generated';

/** The registry explanation with any parenthesised engine-key list removed. */
export function clientReasonExplanation(code: ReasonCode): string {
  return REASONS[code].explanation
    .replace(/\s*\((?:[a-z][a-z0-9]*_[a-z0-9_]*(?:\s*\/\s*)?)+\)/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
