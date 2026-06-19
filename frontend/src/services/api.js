import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

// ─── Phase 1 — Chat ──────────────────────────────────────────────────────────

export async function sendMessage(text, table = null, history = null, sessionId = null) {
  const response = await apiClient.post('/chat', { message: text, table, history, session_id: sessionId });
  return response.data;
}

// ─── Phase 2 Base — Schema ───────────────────────────────────────────────────

export async function getDatabases() {
  const response = await apiClient.get('/databases');
  return response.data;
}

export async function selectDatabase(database) {
  const response = await apiClient.post('/select-database', { database });
  return response.data;
}

// ─── Phase 2 Additional — Database Management ────────────────────────────────

/**
 * Create a new PostgreSQL database.
 * @param {string} database_name
 * @returns {{ message: string, databases: string[] }}
 */
export async function createDatabase(database_name) {
  const response = await apiClient.post('/create-database', { database_name });
  return response.data;
}

/**
 * Fetch all table names in a given database.
 * @param {string} database_name
 * @returns {{ tables: string[] }}
 */
export async function getTables(database_name) {
  const response = await apiClient.get(`/tables/${database_name}`);
  return response.data;
}

/**
 * Dynamically create a table with the given columns.
 * @param {string} database
 * @param {string} table_name
 * @param {{ name: string, type: string }[]} columns
 * @returns {{ message: string, tables: string[] }}
 */
export async function createTable(database, table_name, columns) {
  const response = await apiClient.post('/create-table', { database, table_name, columns });
  return response.data;
}

// ─── Phase 5: Execute Confirmed Operations ───────────────────────────────────

/**
 * Execute a highly sensitive operation after explicit frontend confirmation.
 * @param {string} sql 
 * @param {string} intent 
 * @param {string} database 
 */
export async function executeConfirmed(sql, intent, database, sessionId = null) {
  const response = await apiClient.post('/execute-confirmed', { sql, intent, database, session_id: sessionId });
  return response.data;
}
