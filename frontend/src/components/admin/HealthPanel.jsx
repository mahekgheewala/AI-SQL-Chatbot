/**
 * HealthPanel.jsx — Phase 9.5
 * 
 * Displays real-time system health from /api/admin/health.
 * Includes uptime, active sessions, cache stats, log file sizes,
 * and raw Gemini metrics with optional auto-refresh.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { getHealth } from '../../services/adminApi';

function StatusTile({ icon, label, value, status = 'ok', sub }) {
  const statusColors = {
    ok:      'border-emerald-500/30 from-emerald-600/10 to-emerald-600/5',
    warn:    'border-amber-500/30 from-amber-600/10 to-amber-600/5',
    error:   'border-red-500/30 from-red-600/10 to-red-600/5',
    neutral: 'border-gray-600/30 from-gray-700/20 to-gray-700/5',
  };
  const iconMap = { ok: '✅', warn: '⚠️', error: '❌', neutral: '' };
  return (
    <div className={`bg-gradient-to-br ${statusColors[status]} border rounded-2xl p-5`}>
      <div className="text-2xl">{icon}</div>
      <p className="text-gray-400 text-xs uppercase tracking-wider mt-2">{label}</p>
      <div className="flex items-end gap-2 mt-1">
        <p className="text-white text-2xl font-bold">{value}</p>
        {status !== 'neutral' && (
          <span className="text-lg mb-0.5">{iconMap[status]}</span>
        )}
      </div>
      {sub && <p className="text-gray-500 text-xs mt-1">{sub}</p>}
    </div>
  );
}

function formatUptime(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

export default function HealthPanel() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [showGeminiRaw, setShowGeminiRaw] = useState(false);
  const intervalRef = useRef(null);

  const fetch = useCallback(async () => {
    try {
      setError(null);
      const result = await getHealth();
      setData(result);
      setLastUpdated(new Date());
    } catch (err) {
      setError('Failed to fetch health data. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch();
  }, [fetch]);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(fetch, 30000);
    } else {
      clearInterval(intervalRef.current);
    }
    return () => clearInterval(intervalRef.current);
  }, [autoRefresh, fetch]);

  return (
    <div className="space-y-6">
      {/* Header controls */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={fetch}
            disabled={loading}
            className="px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors"
          >
            {loading ? '⏳ Loading…' : '↻ Refresh'}
          </button>
          <label className="flex items-center gap-2 cursor-pointer">
            <div
              onClick={() => setAutoRefresh(a => !a)}
              className={`relative w-10 h-5 rounded-full transition-colors ${autoRefresh ? 'bg-violet-600' : 'bg-gray-600'}`}
            >
              <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${autoRefresh ? 'translate-x-5' : 'translate-x-0.5'}`} />
            </div>
            <span className="text-gray-400 text-sm">Auto-refresh (30s)</span>
          </label>
        </div>
        {lastUpdated && (
          <p className="text-gray-600 text-xs">
            Last updated: {lastUpdated.toLocaleTimeString()}
          </p>
        )}
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-500/40 text-red-300 rounded-xl px-5 py-3 text-sm">
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="flex justify-center py-16">
          <div className="animate-spin w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full" />
        </div>
      )}

      {data && (
        <>
          {/* Status tiles */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatusTile
              icon="🖥️"
              label="Server Uptime"
              value={formatUptime(data.server_uptime_s)}
              status="neutral"
              sub={`Started: ${data.server_start_utc ? new Date(data.server_start_utc).toLocaleTimeString() : '—'}`}
            />
            <StatusTile
              icon="👥"
              label="Active Sessions"
              value={data.active_sessions}
              status="neutral"
            />
            <StatusTile
              icon="🔄"
              label="Cache Generation"
              value={data.cache_generation}
              status="neutral"
              sub="Increments on schema refresh"
            />
            <StatusTile
              icon="🗺️"
              label="Routing Summaries"
              value={data.routing_summaries_count}
              status={data.routing_summaries_count > 0 ? 'ok' : 'warn'}
              sub="Databases indexed for routing"
            />
          </div>

          {/* Gemini call summary */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <StatusTile
              icon="🤖"
              label="Total Gemini Calls"
              value={data.gemini_metrics?.calls?.total ?? 0}
              status="neutral"
            />
            <StatusTile
              icon="🚦"
              label="Rate Limit Events"
              value={data.gemini_metrics?.calls?.rate_limit_events ?? 0}
              status={data.gemini_metrics?.calls?.rate_limit_events > 0 ? 'warn' : 'ok'}
            />
            <StatusTile
              icon="🔁"
              label="Retry Attempts"
              value={data.gemini_metrics?.calls?.retry_attempts ?? 0}
              status={data.gemini_metrics?.calls?.retry_attempts > 5 ? 'warn' : 'ok'}
            />
          </div>

          {/* Log file sizes */}
          <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
            <h3 className="text-gray-300 text-sm font-semibold mb-4">Log Files</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-500 text-xs uppercase tracking-wider border-b border-gray-700">
                    <th className="text-left py-2 pr-6">File</th>
                    <th className="text-left py-2 pr-6">Size</th>
                    <th className="text-left py-2">Last Modified</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700/30">
                  {Object.entries(data.log_file_sizes || {}).map(([filename, info]) => (
                    <tr key={filename}>
                      <td className="py-3 pr-6 font-mono text-gray-300 text-xs">{filename}</td>
                      <td className="py-3 pr-6 text-gray-400 text-xs">{info.size_human}</td>
                      <td className="py-3 text-gray-500 text-xs">
                        {info.last_modified ? new Date(info.last_modified).toLocaleString() : 'N/A'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Raw Gemini metrics */}
          <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
            <div
              className="flex items-center justify-between cursor-pointer"
              onClick={() => setShowGeminiRaw(s => !s)}
            >
              <h3 className="text-gray-300 text-sm font-semibold">Raw Gemini Metrics</h3>
              <span className="text-gray-500 text-sm">{showGeminiRaw ? '▲ Collapse' : '▼ Expand'}</span>
            </div>
            {showGeminiRaw && (
              <div className="mt-4">
                <pre className="text-gray-300 text-xs bg-gray-900 rounded-xl p-4 overflow-x-auto font-mono leading-relaxed whitespace-pre-wrap max-h-96">
                  {JSON.stringify(data.gemini_metrics, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
