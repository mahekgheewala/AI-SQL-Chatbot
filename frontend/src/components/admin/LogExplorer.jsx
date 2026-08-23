/**
 * LogExplorer.jsx — Phase 9.5
 * 
 * Paginated log viewer with filtering controls. Displays log entries from
 * app.log, error.log, or audit.log in a sortable table with color-coded
 * level badges and an expandable detail modal.
 */

import React, { useState, useCallback } from 'react';
import { getLogs } from '../../services/adminApi';

const LEVEL_COLORS = {
  INFO:    { bg: 'bg-emerald-100', text: 'text-emerald-800', dot: 'bg-emerald-500' },
  WARNING: { bg: 'bg-amber-100',   text: 'text-amber-800',   dot: 'bg-amber-500' },
  ERROR:   { bg: 'bg-red-100',     text: 'text-red-800',     dot: 'bg-red-500' },
  DEBUG:   { bg: 'bg-blue-100',    text: 'text-blue-800',    dot: 'bg-blue-500' },
};

function LevelBadge({ level }) {
  const colors = LEVEL_COLORS[level] || { bg: 'bg-gray-100', text: 'text-gray-700', dot: 'bg-gray-400' };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold ${colors.bg} ${colors.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${colors.dot}`} />
      {level}
    </span>
  );
}

function EntryModal({ entry, onClose }) {
  if (!entry) return null;
  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-2xl max-w-3xl w-full max-h-[80vh] overflow-hidden shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <LevelBadge level={entry.level} />
            <span className="text-gray-300 text-sm font-mono">{entry.timestamp}</span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors text-xl leading-none"
          >
            ×
          </button>
        </div>
        <div className="p-6 overflow-y-auto max-h-[calc(80vh-80px)]">
          <p className="text-white text-sm mb-4 font-medium leading-relaxed">{entry.message}</p>
          <pre className="text-gray-300 text-xs bg-gray-800 rounded-xl p-4 overflow-x-auto font-mono leading-relaxed whitespace-pre-wrap">
            {JSON.stringify(entry, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}

export default function LogExplorer() {
  const [logType, setLogType] = useState('app');
  const [level, setLevel] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [requestId, setRequestId] = useState('');
  const [database, setDatabase] = useState('');
  const [intent, setIntent] = useState('');
  const [fromTs, setFromTs] = useState('');
  const [toTs, setToTs] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedEntry, setSelectedEntry] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);

  const fetchLogs = useCallback(async (pageNum = 1) => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        log_type: logType,
        page: pageNum,
        page_size: pageSize,
      };
      if (level) params.level = level;
      if (sessionId) params.session_id = sessionId;
      if (requestId) params.request_id = requestId;
      if (database) params.database = database;
      if (intent) params.intent = intent;
      if (fromTs) params.from_ts = new Date(fromTs).toISOString();
      if (toTs) params.to_ts = new Date(toTs).toISOString();
      if (search) params.search = search;

      const data = await getLogs(params);
      setResult(data);
      setPage(pageNum);
      setHasSearched(true);
    } catch (err) {
      setError('Failed to fetch logs. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [logType, level, sessionId, requestId, database, intent, fromTs, toTs, search, pageSize]);

  const handleApply = () => fetchLogs(1);

  const handleReset = () => {
    setLogType('app');
    setLevel('');
    setSessionId('');
    setRequestId('');
    setDatabase('');
    setIntent('');
    setFromTs('');
    setToTs('');
    setSearch('');
    setResult(null);
    setHasSearched(false);
    setPage(1);
  };

  const formatTs = (ts) => {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return ts;
    }
  };

  return (
    <div className="space-y-5">
      {/* Filter Bar */}
      <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
        <h3 className="text-gray-300 text-sm font-semibold mb-4 uppercase tracking-wider">Filters</h3>

        {/* Log type pills */}
        <div className="flex flex-wrap gap-2 mb-4">
          {['app', 'error', 'audit'].map(lt => (
            <button
              key={lt}
              onClick={() => setLogType(lt)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all ${
                logType === lt
                  ? 'bg-violet-600 text-white shadow-lg shadow-violet-500/30'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {lt}.log
            </button>
          ))}
          <div className="ml-auto flex gap-2">
            {['', 'INFO', 'WARNING', 'ERROR'].map(l => (
              <button
                key={l}
                onClick={() => setLevel(l)}
                className={`px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
                  level === l
                    ? l === 'ERROR'
                      ? 'bg-red-600 text-white'
                      : l === 'WARNING'
                      ? 'bg-amber-600 text-white'
                      : l === 'INFO'
                      ? 'bg-emerald-700 text-white'
                      : 'bg-gray-500 text-white'
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
              >
                {l || 'ALL'}
              </button>
            ))}
          </div>
        </div>

        {/* Filter inputs */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          {[
            ['Session ID', sessionId, setSessionId],
            ['Request ID', requestId, setRequestId],
            ['Database', database, setDatabase],
            ['Intent', intent, setIntent],
          ].map(([placeholder, val, setter]) => (
            <input
              key={placeholder}
              type="text"
              placeholder={placeholder}
              value={val}
              onChange={e => setter(e.target.value)}
              className="bg-gray-900 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500 placeholder:text-gray-600"
            />
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="text-gray-500 text-xs mb-1 block">From</label>
            <input
              type="datetime-local"
              value={fromTs}
              onChange={e => setFromTs(e.target.value)}
              className="w-full bg-gray-900 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
          </div>
          <div>
            <label className="text-gray-500 text-xs mb-1 block">To</label>
            <input
              type="datetime-local"
              value={toTs}
              onChange={e => setToTs(e.target.value)}
              className="w-full bg-gray-900 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500"
            />
          </div>
        </div>

        <div className="flex gap-3">
          <input
            type="text"
            placeholder="🔍 Free-text search in message..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleApply()}
            className="flex-1 bg-gray-900 border border-gray-600 text-gray-200 text-sm rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500 placeholder:text-gray-600"
          />
          <button
            onClick={handleApply}
            disabled={loading}
            className="px-5 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-semibold rounded-lg transition-colors"
          >
            {loading ? 'Loading…' : 'Apply'}
          </button>
          <button
            onClick={handleReset}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm font-medium rounded-lg transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="bg-red-900/30 border border-red-500/40 text-red-300 rounded-xl px-5 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Results */}
      {!hasSearched && !loading && (
        <div className="text-center py-16 text-gray-500">
          <div className="text-4xl mb-3">📄</div>
          <p className="text-sm">Set filters and click Apply to explore logs</p>
        </div>
      )}

      {hasSearched && result && (
        <>
          <div className="flex items-center justify-between">
            <p className="text-gray-400 text-sm">
              <span className="text-white font-semibold">{result.total.toLocaleString()}</span> entries found
              {result.total > 0 && (
                <span className="ml-2 text-gray-600">· Page {result.page} of {result.pages}</span>
              )}
            </p>
          </div>

          {result.entries.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm">No matching log entries.</div>
          ) : (
            <div className="border border-gray-700/50 rounded-2xl overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-800/80 text-gray-400 text-xs uppercase tracking-wider">
                    <th className="text-left px-4 py-3 font-medium">Timestamp</th>
                    <th className="text-left px-3 py-3 font-medium">Level</th>
                    <th className="text-left px-3 py-3 font-medium">Intent</th>
                    <th className="text-left px-3 py-3 font-medium">Database</th>
                    <th className="text-left px-3 py-3 font-medium">Session</th>
                    <th className="text-left px-3 py-3 font-medium w-1/3">Message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700/30">
                  {result.entries.map((entry, idx) => (
                    <tr
                      key={idx}
                      onClick={() => setSelectedEntry(entry)}
                      className="hover:bg-gray-800/60 cursor-pointer transition-colors group"
                    >
                      <td className="px-4 py-3 text-gray-400 font-mono text-xs whitespace-nowrap">
                        {formatTs(entry.timestamp)}
                      </td>
                      <td className="px-3 py-3">
                        <LevelBadge level={entry.level} />
                      </td>
                      <td className="px-3 py-3 text-gray-400 text-xs font-mono">
                        {entry.intent || '—'}
                      </td>
                      <td className="px-3 py-3 text-gray-400 text-xs">
                        {entry.database_name || '—'}
                      </td>
                      <td className="px-3 py-3 text-gray-500 text-xs font-mono">
                        {entry.session_id ? entry.session_id.slice(0, 8) + '…' : '—'}
                      </td>
                      <td className="px-3 py-3 text-gray-300 text-xs truncate max-w-xs group-hover:text-white transition-colors">
                        {entry.message}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {result.pages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <button
                onClick={() => fetchLogs(page - 1)}
                disabled={page <= 1 || loading}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-30 text-gray-300 text-sm rounded-lg transition-colors"
              >
                ← Prev
              </button>
              <span className="text-gray-400 text-sm px-4">
                Page {page} of {result.pages}
              </span>
              <button
                onClick={() => fetchLogs(page + 1)}
                disabled={page >= result.pages || loading}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-30 text-gray-300 text-sm rounded-lg transition-colors"
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}

      {/* Detail Modal */}
      {selectedEntry && (
        <EntryModal entry={selectedEntry} onClose={() => setSelectedEntry(null)} />
      )}
    </div>
  );
}
