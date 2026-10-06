import { useState } from 'react';
import type { SizeFacetValue, SizeUnit } from '../../lib/api';

export interface SizeSelection {
  unidad?: SizeUnit;
  contenido_min?: number;
  contenido_max?: number;
}

const UNIT_LABELS: Record<SizeUnit, string> = { g: 'Peso', ml: 'Volumen', u: 'Unidades' };

/**
 * A size range between two handles that stop only at the sizes the search
 * lists, so no part of the bar is empty.  Sizes of different units cannot
 * share a bar: with several, the shopper first chooses weight, volume or
 * units.  The range is applied when a handle is released, not while it moves.
 */
export function SizeRange({
  sizes,
  selection,
  onChange,
}: {
  sizes: SizeFacetValue[];
  selection: SizeSelection;
  onChange: (selection: SizeSelection) => void;
}) {
  const units = unitsByProducts(sizes);
  const unit = selection.unidad && units.includes(selection.unidad) ? selection.unidad : units[0];
  const values = sizes.filter((s) => s.unidad === unit).sort((a, b) => a.contenido - b.contenido);
  const last = values.length - 1;

  const chosen: [number, number] = [
    selection.contenido_min === undefined ? 0 : Math.max(0, values.findIndex((v) => v.contenido >= selection.contenido_min!)),
    selection.contenido_max === undefined ? last : lastIndexAtMost(values, selection.contenido_max),
  ];
  // The handles move freely; the range they hold is applied on release.
  const [dragged, setDragged] = useState<[number, number] | null>(null);
  const [low, high] = dragged ?? chosen;

  if (values.length === 0) return null;

  const commit = () => {
    if (!dragged) return;
    setDragged(null);
    const [lo, hi] = dragged;
    const whole = lo === 0 && hi === last;
    onChange({
      // A unit the shopper chose keeps filtering; the first one only frames the bar.
      unidad: whole ? selection.unidad : unit,
      contenido_min: whole ? undefined : values[lo].contenido,
      contenido_max: whole ? undefined : values[hi].contenido,
    });
  };

  const products = values.slice(low, high + 1).reduce((sum, v) => sum + v.productos, 0);
  const narrowed = selection.contenido_min !== undefined || selection.contenido_max !== undefined;
  const pct = (index: number) => (last === 0 ? 0 : (index / last) * 100);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <span className="text-xs font-medium text-slate-500">Tamaño</span>
      {units.length > 1 && (
        <div className="flex gap-1">
          {units.map((u) => (
            <button
              key={u}
              onClick={() => onChange(selection.unidad === u ? {} : { unidad: u })}
              aria-pressed={selection.unidad === u}
              className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                selection.unidad === u
                  ? 'border-blue-600 bg-blue-600 text-white'
                  : 'border-slate-300 bg-white text-slate-700 hover:border-blue-300'
              }`}
            >
              {UNIT_LABELS[u]}
            </button>
          ))}
        </div>
      )}
      {last > 0 && (
        <div className="flex min-w-60 flex-1 items-center gap-3 sm:max-w-md">
          <div className="relative h-5 flex-1">
            <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded bg-slate-200" />
            <div
              className="absolute top-1/2 h-1 -translate-y-1/2 rounded bg-blue-600"
              style={{ left: `${pct(low)}%`, right: `${100 - pct(high)}%` }}
            />
            <input
              type="range"
              min={0}
              max={last}
              value={low}
              onChange={(e) => setDragged([Math.min(Number(e.target.value), high), high])}
              onPointerUp={commit}
              onKeyUp={commit}
              aria-label="Tamaño mínimo"
              aria-valuetext={values[low].valor}
              className="size-range"
              // Above the other handle once it nears the end, or both could stick there.
              style={{ zIndex: low > last / 2 ? 3 : 1 }}
            />
            <input
              type="range"
              min={0}
              max={last}
              value={high}
              onChange={(e) => setDragged([low, Math.max(Number(e.target.value), low)])}
              onPointerUp={commit}
              onKeyUp={commit}
              aria-label="Tamaño máximo"
              aria-valuetext={values[high].valor}
              className="size-range"
              style={{ zIndex: 2 }}
            />
          </div>
          <span className="whitespace-nowrap text-xs text-slate-700">
            {low === high ? values[low].valor : `${values[low].valor} – ${values[high].valor}`}
            <span className="ml-1 text-slate-400">{products}</span>
          </span>
        </div>
      )}
      {narrowed && (
        <button
          onClick={() => onChange({ unidad: selection.unidad })}
          className="text-xs text-slate-400 hover:text-slate-600"
        >
          Todos los tamaños
        </button>
      )}
    </div>
  );
}

/** Units with the most products first. */
function unitsByProducts(sizes: SizeFacetValue[]): SizeUnit[] {
  const totals = new Map<SizeUnit, number>();
  for (const s of sizes) totals.set(s.unidad, (totals.get(s.unidad) ?? 0) + s.productos);
  return [...totals.keys()].sort((a, b) => totals.get(b)! - totals.get(a)!);
}

function lastIndexAtMost(values: SizeFacetValue[], max: number): number {
  let index = 0;
  values.forEach((v, i) => {
    if (v.contenido <= max) index = i;
  });
  return index;
}
