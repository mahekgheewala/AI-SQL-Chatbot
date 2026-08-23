/**
 * AnalyticsPanel.jsx — Phase 9.5
 * 
 * Displays aggregated metrics from the /api/admin/analytics endpoint.
 * Charts powered by recharts. Time range is selectable via pill buttons.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from 'recharts';
import { getAnalytics } from '../../services/adminApi';

const TIME_RANGES = [
  { label: 'Last 1h',  hours: 1 },
  { label: 'Last 6h',  hours: 6 },
  { label: 'Last 24h', hours: 24 },
  { label: 'Last 7d',  hours: 168 },
  { label: 'Last 30d', hours: 720 },
];

const CHART_COLORS = [
  '#8b5cf6', '#6366f1', '#3b82f6', '#0ea5e9', '#14b8a6',
  '#22c55e', '#f59e0b', '#ef4444', '#ec4899', '#a855f7',
];

function KpiCard({ icon, label, value, sub, color = 'violet' }) {
  const gradients = {
    violet: 'from-violet-600/20 to-violet-600/5 border-violet-500/30',
    blue:   'from-blue-600/20 to-blue-600/5 border-blue-500/30',
    emerald:'from-emerald-600/20 to-emerald-600/5 border-emerald-500/30',
    amber:  'from-amber-600/20 to-amber-600/5 border-amber-500/30',
    red:    'from-red-600/20 to-red-600/5 border-red-500/30',
    cyan:   'from-cyan-600/20 to-cyan-600/5 border-cyan-500/30',
  };
  return (
    <div className={`bg-gradient-to-br ${gradients[color]} border rounded-2xl p-5 flex flex-col gap-1`}>
      <div className="text-2xl">{icon}</div>
      <p className="text-gray-400 text-xs uppercase tracking-wider mt-1">{label}</p>
      <p className="text-white text-2xl font-bold">{value}</p>
      {sub && <p className="text-gray-500 text-xs">{sub}</p>}
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-gray-800 border border-gray-600 rounded-xl px-4 py-3 shadow-xl text-sm">
      <p className="text-gray-300 font-medium mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.fill || p.color }} className="font-semibold">
          {p.value?.toLocaleString()}
        </p>
      ))}
    </div>
  );
};

export default function AnalyticsPanel() {
  const [rangeHours, setRangeHours] = useState(24);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const toTs = new Date().toISOString();
      const fromTs = new Date(Date.now() - rangeHours * 3600 * 1000).toISOString();
      const result = await getAnalytics(fromTs, toTs);
      setData(result);
    } catch (err) {
      setError('Failed to load analytics.');
    } finally {
      setLoading(false);
    }
  }, [rangeHours]);

  useEffect(() => { fetch(); }, [fetch]);

  const fmtCost = (v) => v < 0.001 ? '< $0.001' : `$${v.toFixed(4)}`;
  const fmtMs = (v) => v === 0 ? '—' : `${v.toFixed(0)} ms`;
  const fmtTokens = (v) => v > 1000 ? `${(v / 1000).toFixed(1)}K` : String(v);

  const intentChartData = (data?.top_intents || []).map(([name, count]) => ({ name, count }));
  const dbChartData = (data?.top_databases || []).map(([name, count]) => ({ name, count }));
  const geminiPieData = data ? [
    { name: 'Router',    value: data.gemini_calls?.router || 0 },
    { name: 'Planner',   value: data.gemini_calls?.planner || 0 },
    { name: 'SQL Gen',   value: data.gemini_calls?.sql_generator || 0 },
    { name: 'Summarizer',value: data.gemini_calls?.summarizer || 0 },
  ].filter(d => d.value > 0) : [];

  return (
    <div className="space-y-6">
      {/* Time Range selector */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-gray-400 text-sm mr-2">Time range:</span>
        {TIME_RANGES.map(r => (
          <button
            key={r.hours}
            onClick={() => setRangeHours(r.hours)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all ${
              rangeHours === r.hours
                ? 'bg-violet-600 text-white shadow-lg shadow-violet-500/30'
                : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
            }`}
          >
            {r.label}
          </button>
        ))}
        <button
          onClick={fetch}
          disabled={loading}
          className="ml-auto px-4 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-300 text-sm rounded-full transition-colors"
        >
          {loading ? '⏳ Loading…' : '↻ Refresh'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-500/40 text-red-300 rounded-xl px-5 py-3 text-sm">
          {error}
        </div>
      )}

      {data && (
        <>
          {/* KPI Row 1 */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <KpiCard icon="📨" label="Total Requests" value={data.total_requests.toLocaleString()} color="violet" />
            <KpiCard icon="🗄️" label="SQL Queries" value={data.sql_queries.toLocaleString()} color="blue" />
            <KpiCard icon="🤖" label="Gemini Calls" value={data.gemini_calls?.total.toLocaleString()} color="cyan" />
            <KpiCard
              icon="❌"
              label="Errors"
              value={data.error_count.toLocaleString()}
              sub={`${data.error_rate_pct}% error rate`}
              color={data.error_count > 0 ? 'red' : 'emerald'}
            />
            <KpiCard icon="⚡" label="Avg Response" value={fmtMs(data.avg_response_ms)} color="amber" />
            <KpiCard
              icon="💰"
              label="Est. API Cost"
              value={fmtCost(data.estimated_cost_usd)}
              color="emerald"
            />
          </div>

          {/* KPI Row 2 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <KpiCard icon="🔀" label="Router Calls" value={data.gemini_calls?.router || 0} color="violet" />
            <KpiCard icon="🧠" label="Planner Calls" value={data.gemini_calls?.planner || 0} color="blue" />
            <KpiCard icon="⚙️" label="SQL Generator" value={data.gemini_calls?.sql_generator || 0} color="cyan" />
            <KpiCard
              icon="🔤"
              label="Tokens Used"
              value={fmtTokens(data.token_usage?.total || 0)}
              sub={`${fmtTokens(data.token_usage?.prompt || 0)} prompt / ${fmtTokens(data.token_usage?.response || 0)} response`}
              color="amber"
            />
          </div>

          {/* Charts Row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            {/* Intent distribution */}
            {intentChartData.length > 0 && (
              <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
                <h3 className="text-gray-300 text-sm font-semibold mb-4">Intent Distribution</h3>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={intentChartData} layout="vertical" margin={{ left: 20, right: 20 }}>
                    <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 11 }} />
                    <YAxis dataKey="name" type="category" tick={{ fill: '#9ca3af', fontSize: 11 }} width={100} />
                    <Tooltip content={<CustomTooltip />} />
                    <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                      {intentChartData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Database usage */}
            {dbChartData.length > 0 && (
              <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
                <h3 className="text-gray-300 text-sm font-semibold mb-4">Database Usage</h3>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={dbChartData} layout="vertical" margin={{ left: 20, right: 20 }}>
                    <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 11 }} />
                    <YAxis dataKey="name" type="category" tick={{ fill: '#9ca3af', fontSize: 11 }} width={100} />
                    <Tooltip content={<CustomTooltip />} />
                    <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                      {dbChartData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[(i + 4) % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Gemini call breakdown */}
            {geminiPieData.length > 0 && (
              <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
                <h3 className="text-gray-300 text-sm font-semibold mb-4">Gemini Call Breakdown</h3>
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie
                      data={geminiPieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={60}
                      outerRadius={100}
                      paddingAngle={3}
                      dataKey="value"
                    >
                      {geminiPieData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip content={<CustomTooltip />} />
                    <Legend
                      formatter={(value) => <span style={{ color: '#9ca3af', fontSize: '12px' }}>{value}</span>}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Latency summary */}
            <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-5">
              <h3 className="text-gray-300 text-sm font-semibold mb-4">Average Latency Breakdown</h3>
              <div className="space-y-4 mt-6">
                {[
                  { label: 'Total Response', ms: data.avg_response_ms, color: '#8b5cf6' },
                  { label: 'SQL Execution',  ms: data.avg_sql_ms,      color: '#3b82f6' },
                  { label: 'AI Processing',  ms: data.avg_ai_ms,       color: '#14b8a6' },
                ].map(({ label, ms, color }) => {
                  const pct = data.avg_response_ms > 0 ? Math.min(100, (ms / data.avg_response_ms) * 100) : 0;
                  return (
                    <div key={label}>
                      <div className="flex justify-between mb-1">
                        <span className="text-gray-400 text-xs">{label}</span>
                        <span className="text-white text-xs font-mono font-semibold">{fmtMs(ms)}</span>
                      </div>
                      <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{ width: `${pct}%`, backgroundColor: color }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Empty state for charts */}
          {intentChartData.length === 0 && dbChartData.length === 0 && geminiPieData.length === 0 && (
            <div className="text-center py-12 text-gray-500 text-sm">
              No chart data available for the selected time range.
            </div>
          )}
        </>
      )}

      {loading && !data && (
        <div className="flex justify-center py-16">
          <div className="animate-spin w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full" />
        </div>
      )}
    </div>
  );
}
