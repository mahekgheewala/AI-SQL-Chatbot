import React, { useState, useEffect, useCallback } from 'react';
import ChatWindow from './components/ChatWindow';
import InputBar from './components/InputBar';
import CreateDatabaseModal from './components/CreateDatabaseModal';
import CreateTableModal from './components/CreateTableModal';
import Toast from './components/Toast';
import {
  sendMessage,
  getDatabases,
  selectDatabase,
  createDatabase,
  getTables,
  createTable,
  executeConfirmed,
} from './services/api';

function App() {
  // ─── Chat State ─────────────────────────────────────────────────────────────
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // ─── Database & Table State ──────────────────────────────────────────────────
  const [databases, setDatabases] = useState([]);
  const [selectedDb, setSelectedDb] = useState('');
  const [tables, setTables] = useState([]);
  const [selectedTable, setSelectedTable] = useState('');

  // ─── Modal Visibility & Loading ──────────────────────────────────────────────
  const [showCreateDb, setShowCreateDb] = useState(false);
  const [showCreateTable, setShowCreateTable] = useState(false);
  const [isCreatingDb, setIsCreatingDb] = useState(false);
  const [isCreatingTable, setIsCreatingTable] = useState(false);

  // ─── Toast Notifications ─────────────────────────────────────────────────────
  const [toasts, setToasts] = useState([]);

  // ─── Phase 6: Transient Session ID (RAM only — lost on page refresh) ─────────
  const [sessionId] = useState(() => crypto.randomUUID());

  const addToast = useCallback((message, type = 'success') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ─── Fetch Databases ───────────────────────────────────────────────────────
  const fetchDbs = useCallback(async () => {
    try {
      const data = await getDatabases();
      setDatabases(data.databases);
    } catch (err) {
      console.error('Failed to fetch databases', err);
    }
  }, []);

  // ─── Fetch Tables for a Given DB ─────────────────────────────────────────────
  const fetchTables = useCallback(async (dbName) => {
    if (!dbName) {
      setTables([]);
      setSelectedTable('');
      return;
    }
    try {
      const data = await getTables(dbName);
      setTables(data.tables);
    } catch (err) {
      console.error('Failed to fetch tables', err);
      setTables([]);
    }
  }, []);

  // ─── Phase 4 — Confirmation State ────────────────────────────────────────────
  // Purely visual frontend state — no API calls in Phase 4.
  // Phase 5 will read these to trigger actual SQL execution.
  const [confirmedMessages, setConfirmedMessages] = useState(new Set());
  const [cancelledMessages, setCancelledMessages] = useState(new Set());

  const handleConfirm = useCallback(async (messageId) => {
    const msgToExecute = messages.find((m) => m.id === messageId);
    if (!msgToExecute) return;

    // Transition to ⏳ Confirmed state visually immediately
    setConfirmedMessages((prev) => new Set([...prev, messageId]));
    setLoading(true);
    setError(null);

    try {
      const data = await executeConfirmed(
        msgToExecute.sql,
        msgToExecute.intent,
        msgToExecute.database,
        sessionId,             // Phase 6: carry session ID
      );

      // Phase 5 UX: Update the message IN PLACE with the execution result
      setMessages((prevMessages) =>
        prevMessages.map((m) => {
          if (m.id === messageId) {
            return {
              ...m,
              execution: data.execution,
              valid: data.valid,
              risk_level: data.risk_level,
            };
          }
          return m;
        })
      );

      // Handle auto-refresh triggers from execution
      if (data.refresh_databases) {
        await fetchDbs();
      }
      if (data.refresh_tables && selectedDb) {
        await fetchTables(selectedDb);
      }
      if (data.refresh_schema && selectedDb) {
        await selectDatabase(selectedDb);
        await fetchTables(selectedDb);
      }
    } catch (err) {
      console.error(err);
      setError(err.response?.data?.detail || 'Failed to execute confirmed operation.');
    } finally {
      setLoading(false);
    }
  }, [messages, selectedDb, fetchDbs, fetchTables]);

  const handleCancel = useCallback((messageId) => {
    setCancelledMessages((prev) => new Set([...prev, messageId]));
  }, []);

  // ─── Fetch Databases on Mount ─────────────────────────────────────────────
  useEffect(() => {
    fetchDbs();
  }, [fetchDbs]);



  // ─── Database Selection ───────────────────────────────────────────────────────
  const handleDbChange = async (e) => {
    const value = e.target.value;

    // Special sentinel value triggers the create-database modal
    if (value === '__create__') {
      setShowCreateDb(true);
      return;
    }

    setSelectedDb(value);
    setSelectedTable('');
    setTables([]);

    if (value) {
      try {
        await selectDatabase(value);
        await fetchTables(value);
      } catch (err) {
        console.error('Failed to select database', err);
        setError(`Failed to select database "${value}"`);
      }
    }
  };

  // ─── Table Selection ──────────────────────────────────────────────────────────
  const handleTableChange = (e) => {
    const value = e.target.value;

    // Special sentinel value triggers the create-table modal
    if (value === '__create__') {
      setShowCreateTable(true);
      return;
    }

    setSelectedTable(value);
  };

  // ─── Create Database ──────────────────────────────────────────────────────────
  const handleCreateDb = async (dbName) => {
    setIsCreatingDb(true);
    try {
      const data = await createDatabase(dbName);
      // Backend returns fresh list — no need to re-fetch
      setDatabases(data.databases);
      // Auto-select the newly created database
      setSelectedDb(dbName);
      setTables([]);      // New DB has no tables yet
      setSelectedTable('');
      setShowCreateDb(false);
      addToast(`Database "${dbName}" created successfully.`, 'success');
    } catch (err) {
      const msg = err.response?.data?.detail || `Failed to create database "${dbName}".`;
      addToast(msg, 'error');
    } finally {
      setIsCreatingDb(false);
    }
  };

  // ─── Create Table ─────────────────────────────────────────────────────────────
  const handleCreateTable = async (tableData) => {
    setIsCreatingTable(true);
    try {
      const data = await createTable(
        tableData.database,
        tableData.table_name,
        tableData.columns
      );
      // Backend returns the refreshed table list
      setTables(data.tables);
      // Auto-select the newly created table
      setSelectedTable(tableData.table_name);
      setShowCreateTable(false);
      addToast(`Table "${tableData.table_name}" created successfully.`, 'success');
    } catch (err) {
      const msg = err.response?.data?.detail || `Failed to create table "${tableData.table_name}".`;
      addToast(msg, 'error');
    } finally {
      setIsCreatingTable(false);
    }
  };

  // ─── Send Chat Message ────────────────────────────────────────────────────────
  const handleSend = async (text) => {
    const userMsg = { id: Date.now(), role: 'user', text };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setLoading(true);
    setError(null);

    // Format conversation history for Gemini (send last 10 messages)
    const historyPayload = updatedMessages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .slice(-10)
      .map((m) => ({
        role: m.role,
        text: m.text,
      }));

    try {
      const data = await sendMessage(text, selectedTable, historyPayload, sessionId);  // Phase 6
      const assistantMsg = { 
        id: Date.now() + 1, 
        role: 'assistant', 
        text: data.reply,
        intent: data.intent,
        database: data.database,
        sql: data.sql,
        question: data.question,
        // Phase 4 & 5 fields
        valid: data.valid,
        risk_level: data.risk_level,
        requires_confirmation: data.requires_confirmation,
        blocked_reason: data.blocked_reason,
        execution: data.execution,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // Handle auto-refresh triggers
      if (data.refresh_databases) {
        await fetchDbs();
      }
      if (data.refresh_tables && selectedDb) {
        await fetchTables(selectedDb);
      }
      if (data.refresh_schema && selectedDb) {
        // Refresh metadata state in the backend & reload table lists
        await selectDatabase(selectedDb);
        await fetchTables(selectedDb);
      }
    } catch (err) {
      console.error(err);
      setError('Failed to reach the backend. Is the server running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-white">

      {/* ── Header ────────────────────────────────────────────────────────────── */}
      <header className="bg-white border-b px-6 py-4 flex items-center justify-between shadow-sm z-10 flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-800">AI SQL Assistant</h1>

        <div className="flex items-center gap-4 flex-wrap">

          {/* Database Selector */}
          <div className="flex items-center gap-2">
            <label htmlFor="db-select" className="text-sm font-medium text-gray-600">
              Database:
            </label>
            <select
              id="db-select"
              value={selectedDb}
              onChange={handleDbChange}
              className="border rounded px-3 py-1 text-sm bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">-- Select a DB --</option>
              {databases.map((db) => (
                <option key={db} value={db}>{db}</option>
              ))}
              <option value="__create__">＋ Create New Database</option>
            </select>
          </div>

          {/* Table Selector — only shown once a DB is selected */}
          {selectedDb && (
            <div className="flex items-center gap-2">
              <label htmlFor="table-select" className="text-sm font-medium text-gray-600">
                Table:
              </label>
              <select
                id="table-select"
                value={selectedTable}
                onChange={handleTableChange}
                className="border rounded px-3 py-1 text-sm bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Select a Table --</option>
                {tables.map((tbl) => (
                  <option key={tbl} value={tbl}>{tbl}</option>
                ))}
                <option value="__create__">＋ Create New Table</option>
              </select>
            </div>
          )}

        </div>
      </header>

      {/* ── Error Banner ──────────────────────────────────────────────────────── */}
      {error && (
        <div className="bg-red-50 text-red-600 px-6 py-2 text-sm font-medium border-b border-red-100 flex justify-center">
          ⚠️ {error}
        </div>
      )}

      {/* ── Main Chat Area ────────────────────────────────────────────────────── */}
      <ChatWindow
        messages={messages}
        confirmedMessages={confirmedMessages}
        cancelledMessages={cancelledMessages}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />

      {/* ── Loading Indicator ─────────────────────────────────────────────────── */}
      {loading && (
        <div className="flex justify-center p-2 bg-gray-50 text-sm text-gray-500">
          <span className="animate-pulse">Thinking...</span>
        </div>
      )}

      {/* ── Input Area ────────────────────────────────────────────────────────── */}
      <InputBar onSubmit={handleSend} isLoading={loading} />

      {/* ── Create Database Modal ─────────────────────────────────────────────── */}
      {showCreateDb && (
        <CreateDatabaseModal
          onClose={() => setShowCreateDb(false)}
          onSubmit={handleCreateDb}
          isLoading={isCreatingDb}
        />
      )}

      {/* ── Create Table Modal (only when a DB is selected) ───────────────────── */}
      {showCreateTable && selectedDb && (
        <CreateTableModal
          database={selectedDb}
          onClose={() => setShowCreateTable(false)}
          onSubmit={handleCreateTable}
          isLoading={isCreatingTable}
        />
      )}

      {/* ── Toast Notifications ───────────────────────────────────────────────── */}
      <Toast toasts={toasts} removeToast={removeToast} />
    </div>
  );
}

export default App;
