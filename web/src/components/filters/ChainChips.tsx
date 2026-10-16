import { useQuery } from '@tanstack/react-query';
import { fetchChainOptions, type ChainOption } from '../../lib/api';
import type { AddressLocation } from '../../hooks/useAddressLocation';

/**
 * The chains with stores near the location as chips, by the names shoppers
 * know; several can be chosen. A chosen chain stays listed so that it can be
 * cleared after the location moves away from it.
 */
export function ChainChips({
  location,
  chosen,
  onChange,
}: {
  location: AddressLocation;
  chosen: string[];
  onChange: (cadenas: string[]) => void;
}) {
  const { data: options = [] } = useQuery({
    queryKey: ['chain-options', location.params],
    queryFn: () => fetchChainOptions(location.params),
    enabled: location.ready,
  });
  const choices: ChainOption[] = [
    ...options,
    ...chosen
      .filter((valor) => !options.some((option) => option.valor === valor))
      .map((valor) => ({ valor, nombre: valor, sucursales: 0 })),
  ];
  if (choices.length === 0) return null;

  const toggle = (valor: string) =>
    onChange(chosen.includes(valor) ? chosen.filter((c) => c !== valor) : [...chosen, valor]);

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium text-slate-500 mr-1">Cadena</span>
      {choices.map((option) => {
        const selected = chosen.includes(option.valor);
        return (
          <button
            key={option.valor}
            onClick={() => toggle(option.valor)}
            aria-pressed={selected}
            title={`${option.sucursales} ${option.sucursales === 1 ? 'local' : 'locales'}${location.located ? ' cerca' : ''}`}
            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
              selected
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-slate-300 bg-white text-slate-700 hover:border-blue-300'
            }`}
          >
            {option.nombre}{' '}
            <span className={selected ? 'text-blue-100' : 'text-slate-400'}>{option.sucursales}</span>
          </button>
        );
      })}
    </div>
  );
}
