/**
 * adminApi.js — Phase 9.5
 * API service client for all admin dashboard endpoints.
 * All endpoints are under /api/admin and require no authentication in dev mode.
 */

import axios from 'axios';

const adminClient = axios.create({
  baseURL: '/api/admin',
  headers: { 'Content-Type': 'application/json' },
});

/**
 * Fetch paginated, filtered log entries.
 * @param {Object} params - Filter params: log_type, level, session_id, request_id,
 *                          database, intent, from_ts, to_ts, search, page, page_size
 */
export async function getLogs(params = {}) {
  const response = await adminClient.get('/logs', { params });
  return response.data;
}

/**
 * Fetch aggregated analytics for a given time window.
 * @param {string|null} from_ts - ISO datetime string
 * @param {string|null} to_ts   - ISO datetime string
 */
export async function getAnalytics(from_ts = null, to_ts = null) {
  const params = {};
  if (from_ts) params.from_ts = from_ts;
  if (to_ts) params.to_ts = to_ts;
  const response = await adminClient.get('/analytics', { params });
  return response.data;
}

/**
 * Fetch real-time system health snapshot.
 */
export async function getHealth() {
  const response = await adminClient.get('/health');
  return response.data;
}

/**
 * Build the URL for log export (used as a direct download link).
 * @param {Object} params - Same filters as getLogs, plus `format` (json|csv)
 */
export function buildExportUrl(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') {
      query.set(k, v);
    }
  });
  return `/api/admin/export?${query.toString()}`;
}

/**
 * Trigger a log export file download in the browser.
 * @param {Object} params - Filter + format params
 */
export function downloadExport(params = {}) {
  const url = buildExportUrl(params);
  const a = document.createElement('a');
  a.href = url;
  a.download = `logs_export_${params.log_type || 'app'}.${params.format || 'json'}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
