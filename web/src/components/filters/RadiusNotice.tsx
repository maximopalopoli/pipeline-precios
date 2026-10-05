import type { SearchRadius } from '../../lib/api';

/** Says when the radius grew to reach enough chains, or still falls short. */
export function RadiusNotice({ area }: { area: SearchRadius | null }) {
  if (!area || (!area.ampliado && area.suficiente)) return null;
  const chains = `${area.cadenas} ${area.cadenas === 1 ? 'cadena' : 'cadenas'}`;
  return (
    <p className="text-xs text-amber-700">
      {area.suficiente
        ? `Ampliamos el radio a ${area.radio_km} km para incluir ${chains} de supermercados.`
        : `Solo hay ${chains} de supermercados a menos de ${area.radio_km} km.`}
    </p>
  );
}
