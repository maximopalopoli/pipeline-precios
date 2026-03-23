import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchEvolucion, type Product } from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { PriceLineChart } from '../components/charts/PriceLineChart';
import { KpiCard } from '../components/layout/KpiCard';
import { formatCurrency } from '../lib/constants';

export function Evolucion() {
  const { provincia } = useFilters();
  const [product, setProduct] = useState<Product | null>(null);

  const { data: prices = [], isLoading } = useQuery({
    queryKey: ['evolucion', product?.producto_sk, provincia],
    queryFn: () =>
      fetchEvolucion({
        producto_sk: product!.producto_sk,
        provincia: provincia || undefined,
      }),
    enabled: !!product,
  });

  const current = prices.length > 0 ? prices[prices.length - 1] : null;
  const weekAgoPrice = (() => {
    if (prices.length < 2) return null;
    const lastDate = new Date(prices[prices.length - 1].fecha);
    const target = lastDate.getTime() - 7 * 86400000;
    let closest = prices[0];
    for (const p of prices) {
      if (new Date(p.fecha).getTime() <= target) closest = p;
    }
    return closest !== prices[prices.length - 1] ? closest : null;
  })();

  const scopeLabel = provincia || 'Nacional';

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Evolucion de Precios</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        <div className="space-y-4">
          <FilterPanel />
          <ProductSearch onSelect={setProduct} selected={product} />
        </div>

        <div className="space-y-4">
          {!product && (
            <p className="text-sm text-slate-400">Busca un producto para comenzar.</p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {current && (
            <>
              <div className="grid grid-cols-3 gap-4">
                <KpiCard
                  label="Precio actual (lista)"
                  value={formatCurrency(current.precio_lista ?? 0)}
                />
                {weekAgoPrice && weekAgoPrice.precio_lista && current.precio_lista && (
                  <KpiCard
                    label="Hace 7 dias"
                    value={formatCurrency(weekAgoPrice.precio_lista)}
                    delta={`${(((current.precio_lista - weekAgoPrice.precio_lista) / weekAgoPrice.precio_lista) * 100).toFixed(1)}%`}
                    deltaColor={current.precio_lista > weekAgoPrice.precio_lista ? 'red' : 'green'}
                  />
                )}
                {current.precio_promo && current.precio_lista && current.precio_promo < current.precio_lista && (
                  <KpiCard
                    label="Precio promo actual"
                    value={formatCurrency(current.precio_promo)}
                  />
                )}
              </div>

              <div>
                <h2 className="text-lg font-semibold text-slate-800">
                  Precio de lista vs mejor promo
                </h2>
                <p className="text-sm text-slate-500 mb-2">Promedio — {scopeLabel}</p>
                <PriceLineChart data={prices} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
