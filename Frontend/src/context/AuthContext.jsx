import React, { createContext, useContext, useState } from 'react';

// Create the Auth Context
const AuthContext = createContext(null);

// Valid credentials for hackathon demo
const VALID_CREDENTIALS = {
  'evaluator@poonawalla.com': 'hackathon2026',
  'admin@poonawalla.com': 'admin123',
  'ops@poonawalla.com': 'ops123'
};

/**
 * AuthProvider Component
 * Wraps the app to provide authentication context
 */
export function AuthProvider({ children }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState(null);
  const [authToken, setAuthToken] = useState(null);

  const login = async (email, password) => {
    try {
      // Simulate API call delay
      await new Promise(resolve => setTimeout(resolve, 500));

      // Validate credentials (hackathon demo - mock validation)
      if (VALID_CREDENTIALS[email] === password) {
        const userData = {
          id: `user_${Date.now()}`,
          email,
          name: email.split('@')[0],
          role: email.includes('admin') ? 'admin' : 'evaluator',
          loginTime: new Date().toISOString()
        };
        
        const token = `token_${btoa(email)}_${Date.now()}`;
        
        setIsAuthenticated(true);
        setUser(userData);
        setAuthToken(token);
        
        // Store in localStorage for persistence
        localStorage.setItem('authToken', token);
        localStorage.setItem('user', JSON.stringify(userData));
        
        return {
          success: true,
          user: userData,
          token
        };
      } else {
        return {
          success: false,
          error: 'Invalid email or password'
        };
      }
    } catch (error) {
      return {
        success: false,
        error: error.message || 'Login failed'
      };
    }
  };

  const logout = () => {
    setIsAuthenticated(false);
    setUser(null);
    setAuthToken(null);
    localStorage.removeItem('authToken');
    localStorage.removeItem('user');
  };

  const value = {
    isAuthenticated,
    user,
    authToken,
    login,
    logout
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

/**
 * useAuth Hook
 * Use this hook in any component to access authentication methods and state
 * 
 * Example:
 * const { login, logout, isAuthenticated, user } = useAuth();
 */
export function useAuth() {
  const context = useContext(AuthContext);
  
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  
  return context;
}

export default AuthContext;
