/**
 * adminApi.js — Phase 9.5
 * API service client for all admin dashboard endpoints.
 * All endpoints are under /api/admin and require an authenticated admin JWT
 * (see backend/routers/admin.py's get_current_admin dependency).
 */

import axios from 'axios';

const adminClient = axios.create({
  baseURL: '/api/admin',
  headers: { 'Content-Type': 'application/json' },
});

adminClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Silent access-token refresh on 401, mirroring services/api.js.
let refreshInFlight = null;

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  const response = await axios.post('/api/auth/refresh', { refresh_token: refreshToken });
  const { access_token, refresh_token } = response.data;
  localStorage.setItem('access_token', access_token);
  localStorage.setItem('refresh_token', refresh_token);
  return access_token;
}

adminClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retried) {
      originalRequest._retried = true;
      try {
        if (!refreshInFlight) {
          refreshInFlight = refreshAccessToken().finally(() => {
            refreshInFlight = null;
          });
        }
        const newAccessToken = await refreshInFlight;
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return adminClient(originalRequest);
      } catch (refreshError) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        return Promise.reject(refreshError);
      }
    }
    return Promise.reject(error);
  }
);

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
