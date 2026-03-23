import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchProducts, type Product } from '../../lib/api';

interface ProductSearchProps {
  onSelect: (product: Product) => void;
  selected?: Product | null;
}

export function ProductSearch({ onSelect, selected }: ProductSearchProps) {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const { data: products = [] } = useQuery({
    queryKey: ['products', debouncedQuery],
    queryFn: () => searchProducts(debouncedQuery),
    enabled: debouncedQuery.length >= 2,
  });

  return (
    <div className="relative">
      <label className="block text-sm font-medium text-slate-700 mb-1">
        Buscar producto
      </label>
      {selected && (
        <div className="mb-2 flex items-center gap-2 rounded-md bg-blue-50 px-3 py-2 text-sm">
          <span className="flex-1">
            {selected.descripcion} — {selected.marca || ''} ({selected.id_producto})
          </span>
          <button
            onClick={() => { onSelect(null as any); setQuery(''); }}
            className="text-slate-400 hover:text-slate-600"
          >
            ✕
          </button>
        </div>
      )}
      {!selected && (
        <>
          <input
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setIsOpen(true); }}
            onFocus={() => setIsOpen(true)}
            placeholder="Nombre, marca o EAN..."
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {isOpen && products.length > 0 && (
            <ul className="absolute z-50 mt-1 max-h-60 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
              {products.map((p) => (
                <li
                  key={p.producto_sk}
                  onClick={() => { onSelect(p); setIsOpen(false); setQuery(''); }}
                  className="cursor-pointer px-3 py-2 text-sm hover:bg-slate-100"
                >
                  {p.descripcion} — {p.marca || ''} <span className="text-slate-400">({p.id_producto})</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
