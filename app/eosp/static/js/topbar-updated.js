/**
 * Topbar "Updated … ago" line: merges server summary timestamps with local
 * research-console case mutations (add / edit / delete).
 */
/** @type {number} */
let _consoleCaseTouchMs = 0;
/** @type {any | null} */
let _lastSummary = null;

function _relativeTime(date) {
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

function _render() {
  const el = document.getElementById("topbar-status-updated");
  if (!el) return;
  const s = _lastSummary;
  const serverMs = s?.last_updated ? new Date(s.last_updated).getTime() : 0;
  const localMs = _consoleCaseTouchMs;
  if (serverMs === 0 && localMs === 0) return;
  const t = new Date(Math.max(serverMs, localMs));
  el.textContent = `Updated ${_relativeTime(t)}`;
}

/** Call when /cases/summary (or bootstrap summary) is applied. */
export function applyTopbarUpdatedFromSummary(summary) {
  if (summary != null) _lastSummary = summary;
  _render();
}

/** Call after research console adds, edits, or deletes a case row. */
export function markResearchConsoleCaseDataUpdated() {
  _consoleCaseTouchMs = Date.now();
  _render();
}
