import React, { useState, useEffect } from 'react';

// Must match ALLOWED_COLUMN_TYPES in backend/db/table_manager.py
const ALLOWED_TYPES = ['TEXT', 'INTEGER', 'BOOLEAN', 'DATE', 'FLOAT', 'TIMESTAMP'];

const VALID_IDENTIFIER = /^[a-zA-Z][a-zA-Z0-9_]*$/;

/**
 * Modal for creating a new PostgreSQL table with dynamic column definitions.
 *
 * The modal auto-prepends `id SERIAL PRIMARY KEY` (shown as read-only).
 * Users can add/remove additional columns, each with a name + type.
 *
 * Props:
 *   database   — string          — name of the currently selected database
 *   onClose    — () => void
 *   onSubmit   — (tableData) => void  — tableData: { database, table_name, columns }
 *   isLoading  — boolean
 */
export default function CreateTableModal({ database, onClose, onSubmit, isLoading }) {
  const [tableName, setTableName] = useState('');
  const [columns, setColumns] = useState([{ name: '', type: 'TEXT' }]);
  const [error, setError] = useState('');

  // Close on Escape
  useEffect(() => {
    const handleKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const addColumn = () => {
    setColumns((prev) => [...prev, { name: '', type: 'TEXT' }]);
  };

  const removeColumn = (index) => {
    if (columns.length <= 1) return; // Keep at least one column
    setColumns((prev) => prev.filter((_, i) => i !== index));
  };

  const updateColumn = (index, field, value) => {
    setColumns((prev) =>
      prev.map((col, i) => (i === index ? { ...col, [field]: value } : col))
    );
  };

  const handleSubmit = () => {
    const trimmedName = tableName.trim();

    if (!trimmedName) {
      setError('Table name is required.');
      return;
    }
    if (!VALID_IDENTIFIER.test(trimmedName)) {
      setError('Table name must start with a letter and contain only letters, numbers, or underscores.');
      return;
    }
    for (const col of columns) {
      const colName = col.name.trim();
      if (!colName) {
        setError('All column names are required.');
        return;
      }
      if (!VALID_IDENTIFIER.test(colName)) {
        setError(`Column name "${colName}" is invalid. Use only letters, numbers, or underscores.`);
        return;
      }
    }

    setError('');
    onSubmit({
      database,
      table_name: trimmedName,
      columns: columns.map((c) => ({ name: c.name.trim(), type: c.type })),
    });
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-lg mx-4 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="mb-4 flex-shrink-0">
          <h2 className="text-lg font-semibold text-gray-800">Create New Table</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            in database{' '}
            <span className="font-medium text-blue-600">{database}</span>
          </p>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1 pr-1">
          {/* Table name */}
          <div className="mb-4">
            <label htmlFor="new-table-name" className="block text-sm font-medium text-gray-600 mb-1">
              Table Name
            </label>
            <input
              id="new-table-name"
              type="text"
              value={tableName}
              onChange={(e) => { setTableName(e.target.value); setError(''); }}
              placeholder="e.g. employees"
              disabled={isLoading}
              className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100"
            />
          </div>

          {/* Columns */}
          <div className="mb-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-sm font-medium text-gray-600">Columns</label>
              <button
                id="add-column-btn"
                onClick={addColumn}
                disabled={isLoading}
                className="text-xs px-3 py-1 rounded-md border border-blue-500 text-blue-600
                  hover:bg-blue-50 disabled:opacity-50 transition-colors"
              >
                + Add Column
              </button>
            </div>

            {/* Column list */}
            <div className="bg-gray-50 rounded-lg p-3 space-y-1">
              {/* Column header */}
              <div className="flex gap-2 px-1 mb-1">
                <span className="flex-1 text-xs text-gray-400 font-medium uppercase tracking-wide">Name</span>
                <span className="w-36 text-xs text-gray-400 font-medium uppercase tracking-wide">Type</span>
                <span className="w-7" />
              </div>

              {/* Auto id row — read-only */}
              <div className="flex gap-2 items-center px-1 py-1.5 bg-gray-100 rounded text-sm text-gray-400 italic">
                <span className="flex-1 text-xs">id</span>
                <span className="w-36 text-xs">SERIAL PRIMARY KEY</span>
                <span className="w-7" />
              </div>

              {/* User-defined columns */}
              {columns.map((col, index) => (
                <div key={index} className="flex gap-2 items-center px-1">
                  <input
                    type="text"
                    value={col.name}
                    onChange={(e) => { updateColumn(index, 'name', e.target.value); setError(''); }}
                    placeholder={`column_${index + 1}`}
                    disabled={isLoading}
                    className="flex-1 border rounded px-2 py-1.5 text-sm focus:outline-none
                      focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                  />
                  <select
                    value={col.type}
                    onChange={(e) => updateColumn(index, 'type', e.target.value)}
                    disabled={isLoading}
                    className="w-36 border rounded px-2 py-1.5 text-sm bg-white focus:outline-none
                      focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
                  >
                    {ALLOWED_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => removeColumn(index)}
                    disabled={isLoading || columns.length <= 1}
                    title="Remove column"
                    className="w-7 text-center text-red-400 hover:text-red-600
                      disabled:opacity-30 disabled:cursor-not-allowed text-xl leading-none"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Inline error */}
          {error && (
            <p className="text-red-500 text-sm mb-2">{error}</p>
          )}
        </div>

        {/* Footer buttons */}
        <div className="flex gap-2 justify-end pt-4 border-t flex-shrink-0">
          <button
            id="cancel-create-table"
            onClick={onClose}
            disabled={isLoading}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 text-gray-600
              hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            id="confirm-create-table"
            onClick={handleSubmit}
            disabled={isLoading}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white font-medium
              hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full" />
                Creating...
              </span>
            ) : 'Create Table'}
          </button>
        </div>
      </div>
    </div>
  );
}
