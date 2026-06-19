import React, { useState, useEffect, useRef } from 'react';

// Must match the validation regex in backend/db/database_manager.py
const VALID_IDENTIFIER = /^[a-zA-Z][a-zA-Z0-9_]*$/;

/**
 * Modal for creating a new PostgreSQL database.
 *
 * Props:
 *   onClose    — () => void      — called when user cancels or clicks backdrop
 *   onSubmit   — (name) => void  — called with the validated name on submit
 *   isLoading  — boolean         — true while the create API call is in-flight
 */
export default function CreateDatabaseModal({ onClose, onSubmit, isLoading }) {
  const [dbName, setDbName] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  // Auto-focus the input when modal opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Close modal on Escape key
  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const validate = (name) => {
    const trimmed = name.trim();
    if (!trimmed) return 'Database name is required.';
    if (!VALID_IDENTIFIER.test(trimmed)) {
      return 'Name must start with a letter and contain only letters, numbers, or underscores.';
    }
    return '';
  };

  const handleSubmit = () => {
    const validationError = validate(dbName);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError('');
    onSubmit(dbName.trim());
  };

  return (
    /* Backdrop — clicking outside closes the modal */
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-sm mx-4">
        <h2 className="text-lg font-semibold text-gray-800 mb-4">Create New Database</h2>

        {/* Name input */}
        <div className="mb-5">
          <label htmlFor="new-db-name" className="block text-sm font-medium text-gray-600 mb-1">
            Database Name
          </label>
          <input
            id="new-db-name"
            ref={inputRef}
            type="text"
            value={dbName}
            onChange={(e) => { setDbName(e.target.value); setError(''); }}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); }}
            placeholder="e.g. hr_database"
            disabled={isLoading}
            className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
          />
          {error && (
            <p className="text-red-500 text-xs mt-1">{error}</p>
          )}
          <p className="text-gray-400 text-xs mt-1">
            Only letters, numbers, and underscores. Must start with a letter.
          </p>
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 justify-end">
          <button
            id="cancel-create-db"
            onClick={onClose}
            disabled={isLoading}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 text-gray-600
              hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            id="confirm-create-db"
            onClick={handleSubmit}
            disabled={isLoading || !dbName.trim()}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium
              hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full" />
                Creating...
              </span>
            ) : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}
