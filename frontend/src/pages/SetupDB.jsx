import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { testDbConnection, saveDbConnection } from '../services/api';
import { Database, ShieldCheck, AlertCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export default function SetupDB() {
  const { user, setUser } = useAuth();
  const [formData, setFormData] = useState({
    host: 'localhost',
    port: 5432,
    username: '',
    password: '',
    database: '',
    remember: true
  });
  const [status, setStatus] = useState({ type: '', message: '' });
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleTest = async () => {
    setStatus({ type: '', message: '' });
    setLoading(true);
    try {
      await testDbConnection({
        ...formData,
        port: parseInt(formData.port, 10)
      });
      setStatus({ type: 'success', message: 'Connection successful!' });
    } catch (err) {
      setStatus({ type: 'error', message: err.response?.data?.detail || 'Failed to connect.' });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setStatus({ type: '', message: '' });
    setLoading(true);
    try {
      await saveDbConnection({
        ...formData,
        port: parseInt(formData.port, 10)
      });
      setStatus({ type: 'success', message: 'Connection saved successfully!' });
      
      // Update local user state
      setUser({ ...user, database_connected: true });
      localStorage.setItem('active_database', formData.database);
      
      setTimeout(() => navigate('/chat'), 1500);
    } catch (err) {
      setStatus({ type: 'error', message: err.response?.data?.detail || 'Failed to save connection.' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center py-12 px-4 sm:px-6 lg:px-8 font-sans">
      <div className="max-w-md w-full bg-white rounded-2xl shadow-xl border border-gray-100 p-8 space-y-6">
        
        <div className="text-center">
          <div className="mx-auto w-12 h-12 bg-indigo-100 rounded-full flex items-center justify-center mb-4">
            <Database className="w-6 h-6 text-indigo-600" />
          </div>
          <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Connect Database</h2>
          <p className="text-sm text-gray-500 mt-2">
            Provide your PostgreSQL connection credentials. These will be encrypted and stored securely.
          </p>
        </div>

        {status.message && (
          <div className={`p-4 rounded-lg flex items-start gap-3 text-sm ${status.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            {status.type === 'success' ? <ShieldCheck className="w-5 h-5 flex-shrink-0" /> : <AlertCircle className="w-5 h-5 flex-shrink-0" />}
            <span>{status.message}</span>
          </div>
        )}

        <form className="space-y-4" onSubmit={handleSave}>
          <div className="grid grid-cols-3 gap-4">
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-700">Host</label>
              <input
                type="text"
                name="host"
                required
                value={formData.host}
                onChange={handleChange}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 text-sm"
              />
            </div>
            <div className="col-span-1">
              <label className="block text-sm font-medium text-gray-700">Port</label>
              <input
                type="number"
                name="port"
                required
                value={formData.port}
                onChange={handleChange}
                className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 text-sm"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700">Database Name</label>
            <input
              type="text"
              name="database"
              required
              value={formData.database}
              onChange={handleChange}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700">Username</label>
            <input
              type="text"
              name="username"
              required
              value={formData.username}
              onChange={handleChange}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700">Password</label>
            <input
              type="password"
              name="password"
              required
              value={formData.password}
              onChange={handleChange}
              className="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 text-sm"
            />
          </div>

          <div className="flex items-center">
            <input
              id="remember"
              name="remember"
              type="checkbox"
              checked={formData.remember}
              onChange={handleChange}
              className="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded"
            />
            <label htmlFor="remember" className="ml-2 block text-sm text-gray-700">
              Remember password (encrypted)
            </label>
          </div>

          <div className="pt-4 flex gap-3">
            <button
              type="button"
              onClick={handleTest}
              disabled={loading}
              className="w-full flex justify-center py-2 px-4 border border-indigo-600 rounded-md shadow-sm text-sm font-medium text-indigo-600 bg-white hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors disabled:opacity-50"
            >
              Test Connection
            </button>
            <button
              type="submit"
              disabled={loading}
              className="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 transition-colors disabled:opacity-50"
            >
              Save & Continue
            </button>
          </div>
        </form>

        <div className="mt-4 text-center">
          <button onClick={() => navigate(user?.database_connected ? '/chat' : '/dashboard')} className="text-sm text-gray-500 hover:text-gray-700">
            Cancel
          </button>
        </div>

      </div>
    </div>
  );
}
