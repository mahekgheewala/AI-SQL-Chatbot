import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// ─── Silent access-token refresh ──────────────────────────────────────────
// The backend has always had a working POST /api/auth/refresh, and the
// refresh_token has always been stored on login — but nothing ever called
// it, so an expired access token just force-logged the user out instead of
// refreshing silently. This wires that up.

let refreshInFlight = null;

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  // A plain axios call, not apiClient — must not carry the (expired) access
  // token header or go back through this same interceptor.
  const response = await axios.post('/api/auth/refresh', { refresh_token: refreshToken });
  const { access_token, refresh_token } = response.data;
  localStorage.setItem('access_token', access_token);
  localStorage.setItem('refresh_token', refresh_token); // rotated by the backend
  return access_token;
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const isAuthEndpoint = originalRequest?.url?.includes('/auth/login') || originalRequest?.url?.includes('/auth/refresh');

    if (error.response?.status === 401 && !originalRequest._retried && !isAuthEndpoint) {
      originalRequest._retried = true;
      try {
        // Coalesce concurrent 401s into a single refresh call.
        if (!refreshInFlight) {
          refreshInFlight = refreshAccessToken().finally(() => {
            refreshInFlight = null;
          });
        }
        const newAccessToken = await refreshInFlight;
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return apiClient(originalRequest);
      } catch (refreshError) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

// ─── Phase 1 — Chat ──────────────────────────────────────────────────────────

export async function loginUser(email, password) {
  const formData = new URLSearchParams();
  formData.append('username', email);
  formData.append('password', password);
  const response = await apiClient.post('/auth/login', formData, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  });
  return response.data;
}

export async function registerUser(email, password) {
  const response = await apiClient.post('/auth/register', { email, password });
  return response.data;
}

export async function getCurrentUser() {
  const response = await apiClient.get('/auth/me');
  return response.data;
}

export async function testDbConnection(data) {
  const response = await apiClient.post('/db-setup/test-connection', data);
  return response.data;
}

export async function saveDbConnection(data) {
  const response = await apiClient.post('/db-setup/save-connection', data);
  return response.data;
}

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
