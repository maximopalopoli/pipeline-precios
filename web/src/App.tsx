import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/Sidebar';
import { Buscador } from './pages/Buscador';
import { Ofertas } from './pages/Ofertas';
import { Locales } from './pages/Locales';
import { Lista } from './pages/Lista';
import { Estado } from './pages/Estado';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<Buscador />} />
            {/* The search used to live here; old links still reach it. */}
            <Route path="buscador" element={<Navigate to="/" replace />} />
            <Route path="ofertas" element={<Ofertas />} />
            <Route path="locales" element={<Locales />} />
            <Route path="lista" element={<Lista />} />
            <Route path="estado" element={<Estado />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
