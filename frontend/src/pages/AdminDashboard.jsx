/**
 * AdminDashboard.jsx — Phase 9.5
 * 
 * Main admin monitoring dashboard. Accessible at /admin.
 * 
 * Tabs:
 *   📊 Analytics   — KPI cards, charts, and aggregated metrics
 *   📄 Log Explorer — Paginated, filtered log browser
 *   🏥 Health      — System health and server status
 *   ⬇️ Export       — Download logs as JSON or CSV
 * 
 * Linked from the Chat UI header with a small icon button.
 * Unprotected in dev — Phase 10 adds JWT auth.
 */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import AnalyticsPanel from '../components/admin/AnalyticsPanel';
import LogExplorer from '../components/admin/LogExplorer';
import HealthPanel from '../components/admin/HealthPanel';
import ExportModal from '../components/admin/ExportModal';

const TABS = [
  { id: 'analytics', icon: '📊', label: 'Analytics' },
  { id: 'logs',      icon: '📄', label: 'Log Explorer' },
  { id: 'health',    icon: '🏥', label: 'Health' },
];

export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState('analytics');
  const [showExport, setShowExport] = useState(false);

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-md sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              to="/"
              className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors text-sm"
            >
              ← Chat
            </Link>
            <div className="h-4 w-px bg-gray-700" />
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-violet-500 animate-pulse" />
              <h1 className="text-white font-bold text-lg tracking-tight">Admin Dashboard</h1>
            </div>
          </div>

          <button
            onClick={() => setShowExport(true)}
            className="flex items-center gap-2 px-4 py-2 bg-violet-600/20 hover:bg-violet-600/40 border border-violet-500/40 text-violet-300 text-sm font-medium rounded-xl transition-all"
          >
            ⬇️ Export Logs
          </button>
        </div>

        {/* Tab navigation */}
        <div className="max-w-7xl mx-auto px-6">
          <div className="flex gap-1">
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-3 text-sm font-medium border-b-2 transition-all ${
                  activeTab === tab.id
                    ? 'border-violet-500 text-white'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                <span>{tab.icon}</span>
                <span>{tab.label}</span>
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* ── Main Content ──────────────────────────────────────────────────────── */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {activeTab === 'analytics' && <AnalyticsPanel />}
        {activeTab === 'logs'      && <LogExplorer />}
        {activeTab === 'health'    && <HealthPanel />}
      </main>

      {/* ── Export Modal ──────────────────────────────────────────────────────── */}
      {showExport && <ExportModal onClose={() => setShowExport(false)} />}
    </div>
  );
}
