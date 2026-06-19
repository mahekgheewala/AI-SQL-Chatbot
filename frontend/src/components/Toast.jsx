import React, { useEffect } from 'react';

/**
 * Individual toast item.
 * Auto-removes itself after DISMISS_MS milliseconds via onRemove callback.
 */
const DISMISS_MS = 3500;

function ToastItem({ toast, onRemove }) {
  useEffect(() => {
    const timer = setTimeout(() => onRemove(toast.id), DISMISS_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onRemove]);

  const isSuccess = toast.type === 'success';

  return (
    <div
      role="alert"
      className={`flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg text-sm font-medium
        transition-all duration-300 animate-fade-in
        ${isSuccess ? 'bg-green-600 text-white' : 'bg-red-600 text-white'}`}
    >
      <span className="text-base flex-shrink-0">
        {isSuccess ? '✓' : '✕'}
      </span>
      <span className="flex-1">{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        aria-label="Dismiss notification"
        className="opacity-70 hover:opacity-100 text-xl leading-none ml-1 flex-shrink-0"
      >
        ×
      </button>
    </div>
  );
}

/**
 * Toast container — renders in the bottom-right corner, stacks upward.
 *
 * Props:
 *   toasts      — array of { id, message, type: 'success' | 'error' }
 *   removeToast — (id) => void  — callback to remove a toast from App state
 */
export default function Toast({ toasts, removeToast }) {
  if (!toasts || toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 w-80 max-w-full px-2"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onRemove={removeToast} />
      ))}
    </div>
  );
}
