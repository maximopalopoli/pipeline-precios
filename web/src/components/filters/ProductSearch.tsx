import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchProducts, type Product } from '../../lib/api';
import { formatCurrency } from '../../lib/constants';

// Suggestions only; the full list is the cards of a search.
const SUGGESTIONS = 10;

interface ProductSearchProps {
  onSelect: (product: Product | null) => void;
  onSearchAll?: (query: string) => void;
  selected?: Product | null;
  genericQuery?: string | null;
}

export function ProductSearch({ onSelect, onSearchAll, selected, genericQuery }: ProductSearchProps) {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const { data: products = [] } = useQuery({
    queryKey: ['products', debouncedQuery],
    queryFn: () => searchProducts({ q: debouncedQuery, limit: SUGGESTIONS }),
    enabled: debouncedQuery.length >= 2,
  });

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && query.length >= 2 && onSearchAll) {
      e.preventDefault();
      onSearchAll(query);
      setIsOpen(false);
      setQuery('');
    }
  };

  const handleSearchAll = () => {
    if (query.length >= 2 && onSearchAll) {
      onSearchAll(query);
      setIsOpen(false);
      setQuery('');
    }
  };

  // Show generic query chip
  if (genericQuery) {
    return (
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">
          Buscar producto
        </label>
        <div className="flex items-center gap-2 rounded-md bg-indigo-50 px-3 py-2 text-sm">
          <span className="flex-1">
            "{genericQuery}" <span className="text-slate-500">(todos los resultados)</span>
          </span>
          <button
            onClick={() => onSearchAll?.('')}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>
      </div>
    );
  }

  // Show selected single product chip
  if (selected) {
    return (
      <div>
        <label className="block text-sm font-medium text-slate-700 mb-1">
          Buscar producto
        </label>
        <div className="flex items-center gap-2 rounded-md bg-blue-50 px-3 py-2 text-sm">
          <span className="flex-1">
            {selected.nombre} <span className="text-slate-500">({selected.id_producto})</span>
          </span>
          <button
            onClick={() => { onSelect(null); setQuery(''); }}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <label className="block text-sm font-medium text-slate-700 mb-1">
        Buscar producto
      </label>
      <input
        type="text"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setIsOpen(true); }}
        onFocus={() => setIsOpen(true)}
        onKeyDown={handleKeyDown}
        placeholder="Nombre, marca o código de barras"
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      />
      {onSearchAll && query.length >= 2 && (
        <button
          onClick={handleSearchAll}
          className="mt-1.5 w-full rounded-md bg-indigo-50 px-3 py-1.5 text-sm text-indigo-700 hover:bg-indigo-100 transition-colors"
        >
          Ver todos los "{query}"
        </button>
      )}
      {isOpen && products.length > 0 && (
        <ul className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {products.map((p) => (
            <li
              key={p.producto_sk}
              onClick={() => { onSelect(p); setIsOpen(false); setQuery(''); }}
              className="cursor-pointer px-3 py-2 text-sm hover:bg-slate-100"
            >
              <div className="flex justify-between gap-2">
                <span className="text-slate-900">{p.nombre}</span>
                {p.precio_desde != null && (
                  <span className="whitespace-nowrap text-slate-500">
                    desde {formatCurrency(p.precio_desde)}
                  </span>
                )}
              </div>
              <div className="text-xs text-slate-400">
                {p.nombre !== p.descripcion && `${p.descripcion} · `}{p.id_producto}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
