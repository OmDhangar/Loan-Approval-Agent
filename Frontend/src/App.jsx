import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import JoinPage from './pages/joinPage'
import AdminPage from './pages/AdminPage'
import NotFound from './pages/NotFound'
import './global.css'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/join/:sessionToken" element={<JoinPage />} />
        <Route path="/admin"             element={<AdminPage />} />
        <Route path="/"                  element={<Navigate to="/admin" replace />} />
        <Route path="*"                  element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  )
}
