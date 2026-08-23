import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import Chat from './pages/Chat';
import Login from './pages/Auth/Login';
import Register from './pages/Auth/Register';
import Dashboard from './pages/Dashboard';
import SetupDB from './pages/SetupDB';
import AdminDashboard from './pages/AdminDashboard';
import { useAuth } from './context/AuthContext';

const ProtectedRoute = ({ children }) => {
  const { user, loading } = useAuth();
  
  if (loading) {
    return <div className="flex h-screen items-center justify-center bg-gray-50 text-gray-500">Loading...</div>;
  }
  
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  
  return children;
};

function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="flex h-screen items-center justify-center bg-gray-50 text-gray-500">Loading...</div>;
  }

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      
      <Route path="/dashboard" element={
        <ProtectedRoute>
          <Dashboard />
        </ProtectedRoute>
      } />
      
      <Route path="/setup-db" element={
        <ProtectedRoute>
          <SetupDB />
        </ProtectedRoute>
      } />
      
      <Route path="/chat" element={
        <ProtectedRoute>
          <Chat />
        </ProtectedRoute>
      } />

      <Route path="/admin/*" element={
        <ProtectedRoute>
          {user?.role === 'ADMIN' || user?.role === 'SUPER_ADMIN' ? <AdminDashboard /> : <Navigate to="/dashboard" replace />}
        </ProtectedRoute>
      } />

      <Route path="/" element={
        <Navigate to={user ? (user.database_connected ? "/chat" : "/setup-db") : "/login"} replace />
      } />
    </Routes>
  );
}

export default App;
