import React from 'react';

/**
 * ExecutionMessage — displays success or failure status for non-SELECT operations.
 * Phase 5 Execution Component.
 */
export default function ExecutionMessage({ execution }) {
  const { success, operation, error, message, row_count } = execution;

  if (success) {
    return (
      <div className="mt-3 flex items-start gap-2 p-3 bg-emerald-50 border border-emerald-200 rounded-lg">
        <span className="text-emerald-500 text-lg leading-none">✅</span>
        <div>
          <p className="text-sm font-semibold text-emerald-800">
            {operation} Successful
          </p>
          <p className="text-xs text-emerald-700 mt-0.5">
            {message || `Operation completed successfully.`}
            {row_count > 0 && ` (${row_count} row${row_count > 1 ? 's' : ''} affected)`}
          </p>
        </div>
      </div>
    );
  } else {
    return (
      <div className="mt-3 flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
        <span className="text-red-500 text-lg leading-none">❌</span>
        <div>
          <p className="text-sm font-semibold text-red-800">
            Execution Failed
          </p>
          <p className="text-xs text-red-700 mt-0.5 font-mono bg-red-100 p-1.5 rounded mt-1 overflow-x-auto whitespace-pre-wrap">
            {error || message || "Unknown error occurred."}
          </p>
        </div>
      </div>
    );
  }
}
