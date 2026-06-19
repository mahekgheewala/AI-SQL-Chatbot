import React from 'react';

/**
 * ResultsTable — displays the output of a SELECT query in a responsive grid.
 * Phase 5 Execution Component.
 */
export default function ResultsTable({ columns, rows, rowCount }) {
  if (!columns || columns.length === 0) {
    return (
      <div className="mt-3 p-3 bg-gray-50 border rounded-lg text-sm text-gray-500">
        No data returned.
      </div>
    );
  }

  return (
    <div className="mt-3 w-full border rounded-lg overflow-hidden border-gray-200">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-gray-600">
          <thead className="bg-gray-100 text-gray-700 uppercase text-xs">
            <tr>
              {columns.map((col, idx) => (
                <th key={idx} className="px-4 py-2 font-semibold border-b">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-4 text-center text-gray-400 italic">
                  0 rows found.
                </td>
              </tr>
            ) : (
              rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b last:border-0 hover:bg-gray-50">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="px-4 py-2 whitespace-nowrap">
                      {cell === null ? (
                        <span className="text-gray-300 italic">null</span>
                      ) : (
                        String(cell)
                      )}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="bg-gray-50 px-4 py-2 border-t text-xs text-gray-500 font-medium flex justify-between">
        <span>{rowCount} row{rowCount !== 1 ? 's' : ''} returned</span>
      </div>
    </div>
  );
}
