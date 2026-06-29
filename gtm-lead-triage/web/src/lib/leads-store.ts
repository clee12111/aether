/**
 * Shared leads store — stale-while-revalidate pattern.
 *
 * Leads are fetched once, cached in-memory, and revalidated in the background.
 * Tab switches render the cached list instantly; updates arrive async.
 * Call invalidate() after submitting a lead to force a fresh fetch.
 */

import { apiGet } from "@/lib/api";

export interface Lead {
  email: string;
  name?: string;
  company?: string;
  tier?: string;
  score?: number;
  route?: string;
  run_id?: string;
  industry?: string;
  seniority?: string;
  source?: string;
}

let _cache: Lead[] = [];
let _lastFetch = 0;
let _fetching: Promise<Lead[]> | null = null;
const _listeners = new Set<(leads: Lead[]) => void>();

const STALE_MS = 8_000; // serve cached for 8s, revalidate in background

function notify(leads: Lead[]) {
  _cache = leads;
  _lastFetch = Date.now();
  _listeners.forEach((fn) => fn(leads));
}

async function _doFetch(): Promise<Lead[]> {
  try {
    const leads = await apiGet<Lead[]>("/leads");
    notify(leads);
    return leads;
  } catch {
    return _cache; // on error, keep stale
  } finally {
    _fetching = null;
  }
}

/** Get leads — returns cached instantly if fresh, revalidates in background. */
export function getLeads(): Lead[] {
  const age = Date.now() - _lastFetch;
  if (age > STALE_MS && !_fetching) {
    _fetching = _doFetch(); // background revalidate
  }
  return _cache;
}

/** Force a fresh fetch (call after submitting a lead). */
export function invalidateLeads(): Promise<Lead[]> {
  _lastFetch = 0;
  if (!_fetching) _fetching = _doFetch();
  return _fetching;
}

/** Subscribe to lead updates. Returns unsubscribe function. */
export function subscribeLeads(fn: (leads: Lead[]) => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Initial fetch if cache is empty. */
export function ensureLeads(): Promise<Lead[]> {
  if (_cache.length > 0 && Date.now() - _lastFetch < STALE_MS) {
    return Promise.resolve(_cache);
  }
  if (!_fetching) _fetching = _doFetch();
  return _fetching;
}
