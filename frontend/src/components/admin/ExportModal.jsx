/**
 * ExportModal.jsx — Phase 9.5
 * 
 * Modal dialog for configuring and triggering log exports (JSON or CSV).
 * Uses the /api/admin/export streaming endpoint.
 */

import React, { useState } from 'react';
import { downloadExport } from '../../services/adminApi';

export default function ExportModal({ onClose }) {
  const [logType, setLogType] = useState('app');
  const [format, setFormat] = useState('json');
  const [level, setLevel] = useState('');
  const [database, setDatabase] = useState('');
  const [intent, setIntent] = useState('');
  const [search, setSearch] = useState('');
  const [fromTs, setFromTs] = useState('');
  const [toTs, setToTs] = useState('');
  const [downloading, setDownloading] = useState(false);

  const handleDownload = () => {
    setDownloading(true);
    const params = { log_type: logType, format };
    if (level) params.level = level;
    if (database) params.database = database;
    if (intent) params.intent = intent;
    if (search) params.search = search;
    if (fromTs) params.from_ts = new Date(fromTs).toISOString();
    if (toTs) params.to_ts = new Date(toTs).toISOString();

    try {
      downloadExport(params);
    } finally {
      setTimeout(() => setDownloading(false), 1500);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-2xl max-w-lg w-full shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <span className="text-xl">⬇️</span>
            <h2 className="text-white font-semibold">Export Logs</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl transition-colors">×</button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-4">
          {/* Log type */}
          <div>
            <label className="text-gray-400 text-xs uppercase tracking-wider block mb-2">Log File</label>
            <div className="flex gap-2">
              {['app', 'error', 'audit'].map(lt => (
                <button
                  key={lt}
                  onClick={() => setLogType(lt)}
                  className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
                    logType === lt
                      ? 'bg-violet-600 text-white'
                      : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                  }`}
                >
                  {lt}.log
                </button>
              ))}
            </div>
          </div>

          {/* Format */}
          <div>
            <label className="text-gray-400 text-xs uppercase tracking-wider block mb-2">Format</label>
            <div className="flex gap-2">
              {['json', 'csv'].map(f => (
                <button
                  key={f}
                  onClick={() => setFormat(f)}
                  className={`px-5 py-2 rounded-xl text-sm font-semibold transition-all uppercase ${
                    format === f
                      ? 'bg-violet-600 text-white'
                      : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          {/* Date range */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-gray-400 text-xs block mb-1">From</label>
              <input
                type="datetime-local"
                value={fromTs}
                onChange={e => setFromTs(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>
            <div>
              <label className="text-gray-400 text-xs block mb-1">To</label>
              <input
                type="datetime-local"
                value={toTs}
                onChange={e => setToTs(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>
          </div>

          {/* Optional filters */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-gray-400 text-xs block mb-1">Level (optional)</label>
              <select
                value={level}
                onChange={e => setLevel(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500"
              >
                <option value="">All levels</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
            <div>
              <label className="text-gray-400 text-xs block mb-1">Database (optional)</label>
              <input
                type="text"
                placeholder="e.g. hr_db"
                value={database}
                onChange={e => setDatabase(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500 placeholder:text-gray-600"
              />
            </div>
          </div>

          <div>
            <label className="text-gray-400 text-xs block mb-1">Search (optional)</label>
            <input
              type="text"
              placeholder="Filter message text…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full bg-gray-800 border border-gray-600 text-gray-200 text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-violet-500 placeholder:text-gray-600"
            />
          </div>

          {/* Info note */}
          <p className="text-gray-600 text-xs bg-gray-800 rounded-xl px-4 py-3">
            ℹ️ The export downloads only matching entries as a streaming response.
            No date filter = all entries in the current log rotation.
          </p>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-700 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-5 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm font-medium rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleDownload}
            disabled={downloading}
            className="px-6 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors flex items-center gap-2"
          >
            {downloading ? '⏳ Preparing…' : `⬇️ Download ${format.toUpperCase()}`}
          </button>
        </div>
      </div>
    </div>
  );
}
