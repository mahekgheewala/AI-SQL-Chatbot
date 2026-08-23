import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Database, MessageSquare, Settings, LogOut, ShieldAlert } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-800 font-sans">
      <nav className="bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center shadow-sm">
        <div className="flex items-center gap-3">
          <Database className="text-indigo-600 w-6 h-6" />
          <h1 className="text-xl font-bold text-gray-800">AI SQL Assistant</h1>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-sm text-gray-600 font-medium">{user?.email}</span>
          <button 
            onClick={handleLogout}
            className="flex items-center gap-2 text-sm text-gray-500 hover:text-red-600 transition-colors"
          >
            <LogOut className="w-4 h-4" />
            Logout
          </button>
        </div>
      </nav>

      <main className="max-w-6xl mx-auto px-6 py-12">
        <div className="mb-10 text-center">
          <h2 className="text-3xl font-extrabold text-gray-900 tracking-tight mb-2">
            Welcome back, {user?.email.split('@')[0]}
          </h2>
          <p className="text-gray-500 text-lg">
            What would you like to do today?
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          
          <div 
            onClick={() => navigate('/chat')}
            className="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md border border-gray-100 cursor-pointer transition-all transform hover:-translate-y-1 group"
          >
            <div className="w-12 h-12 bg-indigo-100 rounded-xl flex items-center justify-center mb-6 group-hover:bg-indigo-600 transition-colors">
              <MessageSquare className="text-indigo-600 w-6 h-6 group-hover:text-white" />
            </div>
            <h3 className="text-xl font-bold text-gray-900 mb-2">Query Database</h3>
            <p className="text-gray-500 text-sm">
              Use natural language to chat with your connected database and generate SQL queries.
            </p>
          </div>

          <div 
            onClick={() => navigate('/setup-db')}
            className="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md border border-gray-100 cursor-pointer transition-all transform hover:-translate-y-1 group"
          >
            <div className="w-12 h-12 bg-emerald-100 rounded-xl flex items-center justify-center mb-6 group-hover:bg-emerald-600 transition-colors">
              <Database className="text-emerald-600 w-6 h-6 group-hover:text-white" />
            </div>
            <h3 className="text-xl font-bold text-gray-900 mb-2">Database Connection</h3>
            <p className="text-gray-500 text-sm">
              Manage your PostgreSQL connection credentials and switch active environments.
            </p>
          </div>

          {(user?.role === 'ADMIN' || user?.role === 'SUPER_ADMIN') && (
            <div 
              onClick={() => navigate('/admin')}
              className="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md border border-gray-100 cursor-pointer transition-all transform hover:-translate-y-1 group"
            >
              <div className="w-12 h-12 bg-orange-100 rounded-xl flex items-center justify-center mb-6 group-hover:bg-orange-600 transition-colors">
                <ShieldAlert className="text-orange-600 w-6 h-6 group-hover:text-white" />
              </div>
              <h3 className="text-xl font-bold text-gray-900 mb-2">Admin Dashboard</h3>
              <p className="text-gray-500 text-sm">
                View platform metrics, manage Gemini API keys, and monitor user activity.
              </p>
            </div>
          )}

        </div>
      </main>
    </div>
  );
}
