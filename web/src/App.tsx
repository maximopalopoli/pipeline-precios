import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppLayout } from './components/layout/Sidebar';
import { Home } from './pages/Home';
import { Buscador } from './pages/Buscador';
import { Evolucion } from './pages/Evolucion';
import { Variaciones } from './pages/Variaciones';
import { Ofertas } from './pages/Ofertas';
import { Locales } from './pages/Locales';

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
            <Route index element={<Home />} />
            <Route path="buscador" element={<Buscador />} />
            <Route path="evolucion" element={<Evolucion />} />
            <Route path="variaciones" element={<Variaciones />} />
            <Route path="ofertas" element={<Ofertas />} />
            <Route path="locales" element={<Locales />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
