import React, { useState } from 'react';
import ResultsTable from './ResultsTable';
import ExecutionMessage from './ExecutionMessage';
import PlotlyChart from './PlotlyChart';

/**
 * MessageBubble — renders a single chat message (user or assistant).
 *
 * Phase 3.5 behaviour is fully preserved:
 *   - User messages: blue right-aligned bubble
 *   - NEEDS_CLARIFICATION: amber left-aligned bubble with ❓ prefix
 *   - All other assistant messages: white/gray left-aligned bubble
 *   - Intent badge + DB badge below message
 *   - SQL code block when sql is present
 *
 * Phase 4 additions (appended AFTER the SQL block):
 *   - SAFE badge         : green pill when sql present and validation passed
 *   - HIGH_RISK card     : amber warning + Confirm/Cancel buttons
 *   - CRITICAL_RISK card : red danger + "Type CONFIRM" input + Proceed/Cancel
 *   - BLOCKED card       : slate rejected state with reason
 *   - Validation failure : red failure card with reason text
 *   - Confirmed state    : replaces confirmation card after user confirms
 *
 * Phase 5 additions:
 *   - Renders ResultsTable or ExecutionMessage if message.execution exists.
 *   - Automatically hides confirmation UI once execution is present.
 */
export default function MessageBubble({
  message,
  isConfirmed = false,
  isCancelled = false,
  onConfirm,
  onCancel,
}) {
  // Local state for the CRITICAL_RISK "Type CONFIRM" text input
  const [confirmText, setConfirmText] = useState('');

  const isUser          = message.role === 'user';
  const isClarification = !isUser && message.intent === 'NEEDS_CLARIFICATION';

  // Phase 4 validation fields
  const riskLevel           = message.risk_level;
  const requiresConfirmation = message.requires_confirmation;
  const isValid             = message.valid !== false;  // default true
  const blockedReason       = message.blocked_reason;

  // Phase 5 execution state
  const hasExecution = !!message.execution;

  // Phase 9.5 – Friendly error fields
  const errorCode   = message.error_code;
  const errorDetail = message.error_detail;
  const hasError    = !isUser && !!errorCode && !!errorDetail;

  // Category → icon mapping
  const ERROR_ICONS = {
    Database:      '🔴',
    SQL:           '🟠',
    AI:            '🟡',
    Validation:    '🛑',
    Clarification: '❓',
    System:        '⚙️',
  };
  const errorIcon = errorDetail ? (ERROR_ICONS[errorDetail.category] || '⚠️') : '⚠️';

  // Derived display flags
  const isBlocked       = riskLevel === 'BLOCKED';
  const isValidFail     = !isValid && !isBlocked;
  const showSafeBadge   = !isUser && message.sql && riskLevel === 'SAFE' && isValid && !hasExecution;
  const showHighRisk    = !isUser && riskLevel === 'HIGH_RISK' && requiresConfirmation && !isConfirmed && !isCancelled && !hasExecution;
  const showCritical    = !isUser && riskLevel === 'CRITICAL_RISK' && requiresConfirmation && !isConfirmed && !isCancelled && !hasExecution;
  const showConfirmed   = isConfirmed && (riskLevel === 'HIGH_RISK' || riskLevel === 'CRITICAL_RISK') && !hasExecution;

  return (
    <div className={`flex w-full ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div
        className={`max-w-[80%] rounded-2xl px-5 py-3 ${
          isUser
            ? 'bg-blue-600 text-white rounded-br-none'
            : isClarification
            ? 'bg-amber-50 border border-amber-200 text-amber-900 rounded-bl-none shadow-sm'
            : hasError
            ? 'bg-gray-50 border border-gray-200 text-gray-800 rounded-bl-none shadow-sm'
            : 'bg-white border text-gray-800 rounded-bl-none shadow-sm'
        }`}
      >
        {/* ── Phase 9.5 — Friendly Error Card ────────────────────────────────── */}
        {hasError && (
          <div className="mb-3 bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <div className={`px-4 py-3 flex items-center gap-2 ${
              errorDetail.category === 'Database' ? 'bg-red-50 border-b border-red-100' :
              errorDetail.category === 'SQL'      ? 'bg-orange-50 border-b border-orange-100' :
              errorDetail.category === 'AI'       ? 'bg-yellow-50 border-b border-yellow-100' :
              errorDetail.category === 'Validation' ? 'bg-slate-50 border-b border-slate-100' :
                                                    'bg-gray-50 border-b border-gray-100'
            }`}>
              <span className="text-xl">{errorIcon}</span>
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{errorDetail.category} Error</p>
                <p className="text-sm font-bold text-gray-800">{errorDetail.title}</p>
              </div>
            </div>
            <div className="px-4 py-3 space-y-2">
              <p className="text-sm text-gray-700 leading-relaxed">{errorDetail.message}</p>
              {errorDetail.suggestion && (
                <div className="flex gap-2 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
                  <span className="text-blue-500 text-sm mt-0.5">💡</span>
                  <p className="text-xs text-blue-800 leading-relaxed">{errorDetail.suggestion}</p>
                </div>
              )}
              {errorDetail.retry && (
                <p className="text-xs text-gray-400 mt-1">↻ You can try rephrasing your request.</p>
              )}
            </div>
          </div>
        )}

        {/* ── Message text ────────────────────────────────────────────────────── */}
        <p className="whitespace-pre-wrap text-sm">
          {isClarification && <span className="mr-1.5">❓</span>}
          {message.text}
        </p>

        {/* ── Phase 10.4 — Plotly Chart rendering ─────────────────────────────── */}
        {!isUser && message.visualization && (
          <div className="mt-3">
            {message.visualization.status === "SUCCESS" && message.visualization.chart ? (
              <>
                <PlotlyChart 
                  data={message.visualization.chart.chart_data} 
                  layout={message.visualization.chart.layout} 
                />
                {message.visualization.summary && (
                  <p className="text-xs text-gray-600 mt-1 mb-2 leading-relaxed bg-gray-50 border border-gray-150 rounded-lg p-2.5">
                    📊 {(() => {
                      const parts = message.visualization.summary.split(/\*\*([^*]+)\*\*/g);
                      return parts.map((part, idx) => 
                        idx % 2 === 1 ? <strong key={idx} className="font-semibold text-gray-800">{part}</strong> : part
                      );
                    })()}
                  </p>
                )}
              </>
            ) : message.visualization.status === "NEEDS_CLARIFICATION" ? (
              <div className="bg-amber-50 border border-amber-200 text-amber-800 p-3.5 rounded-lg text-sm mb-2 whitespace-pre-wrap leading-relaxed">
                <span className="font-semibold text-amber-700 mb-1.5 block">📊 Clarification Needed</span>
                {message.visualization.summary}
              </div>
            ) : message.visualization.status === "ERROR" ? (
              <div className="bg-red-50 border border-red-200 text-red-800 p-3.5 rounded-lg text-sm mb-2">
                <span className="font-semibold text-red-700 mb-1.5 block">❌ Error</span>
                {message.visualization.summary || "Failed to generate visualization."}
              </div>
            ) : null}
          </div>
        )}


        {/* ── Phase 3 / 3.5 — Intent badge + DB badge + SQL block ─────────────── */}
        {!isUser && message.intent && message.visualization?.status !== 'NEEDS_CLARIFICATION' && (
          <div className="mt-2 pt-2 border-t border-gray-100">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span
                className={`px-2 py-0.5 text-xs font-semibold rounded-md ${
                  isClarification
                    ? 'bg-amber-100 text-amber-700'
                    : 'bg-gray-100 text-gray-600'
                }`}
              >
                Intent: {message.intent}
              </span>
              {message.database && (
                <span className="px-2 py-0.5 bg-blue-50 text-xs font-semibold text-blue-600 rounded-md">
                  DB: {message.database}
                </span>
              )}
            </div>

            {/* SQL code block — shown for all risk levels including BLOCKED */}
            {message.sql && (
              <div className="bg-gray-800 text-gray-100 text-sm font-mono p-3 rounded-lg overflow-x-auto">
                <pre>{message.sql}</pre>
              </div>
            )}

            {/* ── Phase 4 — Validation cards (rendered below SQL block) ──────── */}

            {/* ─ SAFE badge ───────────────────────────────────────────────────── */}
            {showSafeBadge && (
              <div className="mt-2 flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-emerald-50 border border-emerald-200 rounded-full">
                  ✅ SAFE — Ready for Phase 5 execution
                </span>
              </div>
            )}

            {/* ─ BLOCKED card ─────────────────────────────────────────────────── */}
            {isBlocked && (
              <div className="mt-3 bg-slate-100 border border-slate-300 rounded-lg p-3">
                <p className="text-xs font-semibold text-slate-700 mb-1">
                  🚫 Operation Blocked
                </p>
                <p className="text-xs text-slate-600 leading-relaxed">
                  {blockedReason || 'This operation is permanently blocked.'}
                </p>
              </div>
            )}

            {/* ─ Validation failure card (schema / creator checker) ────────────── */}
            {isValidFail && (
              <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3">
                <p className="text-xs font-semibold text-red-700 mb-1">
                  ❌ Validation Failed
                </p>
                <p className="text-xs text-red-600 leading-relaxed">
                  {message.text}
                </p>
              </div>
            )}

            {/* ─ HIGH_RISK confirmation card ──────────────────────────────────── */}
            {showHighRisk && (
              <div className="mt-3 bg-amber-50 border border-amber-300 rounded-xl p-4">
                <p className="text-xs font-bold text-amber-900 mb-1">
                  ⚠️ HIGH RISK OPERATION
                </p>
                <p className="text-xs text-amber-800 mb-4 leading-relaxed">
                  This operation modifies existing data or schema. Confirm only if you understand the impact.
                </p>
                <div className="flex gap-2">
                  <button
                    id={`confirm-high-risk-${message.id}`}
                    onClick={onConfirm}
                    className="px-4 py-1.5 bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold rounded-lg transition-colors"
                  >
                    Confirm Operation
                  </button>
                  <button
                    id={`cancel-high-risk-${message.id}`}
                    onClick={onCancel}
                    className="px-4 py-1.5 bg-white hover:bg-gray-50 text-gray-700 border border-gray-300 text-xs font-semibold rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* ─ CRITICAL_RISK confirmation card ─────────────────────────────── */}
            {showCritical && (
              <div className="mt-3 bg-red-50 border border-red-300 rounded-xl p-4">
                <p className="text-xs font-bold text-red-900 mb-1">
                  🚨 CRITICAL OPERATION — CANNOT BE UNDONE
                </p>
                <p className="text-xs text-red-800 mb-3 leading-relaxed">
                  This action is irreversible. To proceed, type{' '}
                  <code className="font-mono font-bold bg-red-100 px-1 rounded">CONFIRM</code>{' '}
                  exactly in the field below.
                </p>
                <input
                  id={`critical-confirm-input-${message.id}`}
                  type="text"
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="Type CONFIRM here..."
                  className="w-full mb-3 px-3 py-2 text-xs border border-red-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-red-400 font-mono placeholder:text-red-300"
                />
                <div className="flex gap-2">
                  <button
                    id={`confirm-critical-${message.id}`}
                    onClick={() => {
                      if (confirmText === 'CONFIRM') onConfirm();
                    }}
                    disabled={confirmText !== 'CONFIRM'}
                    className="px-4 py-1.5 bg-red-600 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-semibold rounded-lg transition-colors"
                  >
                    Proceed
                  </button>
                  <button
                    id={`cancel-critical-${message.id}`}
                    onClick={onCancel}
                    className="px-4 py-1.5 bg-white hover:bg-gray-50 text-gray-700 border border-gray-300 text-xs font-semibold rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* ─ Confirmed state (replaces confirmation card) ─────────────────── */}
            {showConfirmed && (
              <div className="mt-3 flex items-center gap-2 text-xs font-medium text-blue-700 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
                <span>⏳</span>
                <span>Confirmed — awaiting Phase 5 execution</span>
              </div>
            )}

            {/* ─ Phase 5 Execution Results ────────────────────────────────────────────── */}
            {hasExecution && message.execution.operation === 'SELECT' && (
              <ResultsTable 
                columns={message.execution.columns} 
                rows={message.execution.rows} 
                rowCount={message.execution.row_count} 
              />
            )}
            {hasExecution && message.execution.operation !== 'SELECT' && (
              <ExecutionMessage execution={message.execution} />
            )}

          </div>
        )}
      </div>
    </div>
  );
}
