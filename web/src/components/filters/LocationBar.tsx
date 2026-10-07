import { useState } from 'react';
import type { AddressLocation } from '../../hooks/useAddressLocation';
import { RadiusNotice } from './RadiusNotice';

// The radius stops; the API may grow the first one up to 50 km.
const RADII_KM = [0.5, 1, 2, 3, 5, 10, 20, 50];

/**
 * The shopper's location as a full-width bar: an address form until it is
 * found, then one line with the address, the radius and a way to change it.
 *
 * The address is typed into a draft and looked up on submit only, so typing
 * a new one does not drop the current location keystroke by keystroke.
 */
export function LocationBar({ location }: { location: AddressLocation }) {
  const [editing, setEditing] = useState(false);
  const [draftAddress, setDraftAddress] = useState(location.address);
  const [draftCity, setDraftCity] = useState(location.city);

  if (location.located && !editing) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className="text-slate-800">
            📍 <strong>{location.address}</strong>
            {location.city && `, ${location.city}`}
          </span>
          <RadiusSlider radius={location.radius} onChange={location.setRadius} />
          <button
            onClick={() => {
              setDraftAddress(location.address);
              setDraftCity(location.city);
              setEditing(true);
            }}
            className="text-blue-700 hover:text-blue-900"
          >
            Cambiar
          </button>
          <button onClick={location.clear} className="text-slate-400 hover:text-slate-600">
            Quitar
          </button>
        </div>
        <RadiusNotice area={location.area} />
      </div>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (draftAddress.trim().length < 3) return;
        location.setAddress(draftAddress.trim());
        location.setCity(draftCity.trim());
        location.search();
        setEditing(false);
      }}
      className="rounded-lg border border-slate-200 bg-white p-4"
    >
      <label htmlFor="location-address" className="mb-2 block text-sm font-medium text-slate-700">
        ¿Dónde estás?
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="location-address"
          type="text"
          value={draftAddress}
          onChange={(e) => setDraftAddress(e.target.value)}
          placeholder="Av. Corrientes 1234"
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        <input
          type="text"
          value={draftCity}
          onChange={(e) => setDraftCity(e.target.value)}
          placeholder="Ciudad"
          aria-label="Ciudad"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm sm:w-48"
        />
        <button
          type="submit"
          disabled={draftAddress.trim().length < 3 || location.isGeocoding}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {location.isGeocoding ? 'Buscando...' : 'Buscar'}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
          >
            Cancelar
          </button>
        )}
      </div>
      {location.notFound && <p className="mt-2 text-xs text-red-500">No se encontró la dirección.</p>}
    </form>
  );
}

/**
 * The radius as a bar from 0.5 to 50 km, stopping at the usual distances.
 * It shows the radius searched, grown or not, and is applied on release.
 */
function RadiusSlider({ radius, onChange }: { radius: number; onChange: (km: number) => void }) {
  const [dragged, setDragged] = useState<number | null>(null);
  const index = dragged ?? nearestIndex(radius);
  const commit = () => {
    if (dragged === null) return;
    setDragged(null);
    if (RADII_KM[dragged] !== radius) onChange(RADII_KM[dragged]);
  };
  return (
    <label className="flex min-w-56 flex-1 items-center gap-2 text-slate-600 sm:max-w-xs">
      Radio
      <input
        type="range"
        min={0}
        max={RADII_KM.length - 1}
        value={index}
        onChange={(e) => setDragged(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        aria-valuetext={`${RADII_KM[index]} km`}
        className="flex-1 accent-blue-600"
      />
      <span className="w-12 whitespace-nowrap text-right text-slate-800">
        {RADII_KM[index].toLocaleString('es-AR')} km
      </span>
    </label>
  );
}

function nearestIndex(km: number): number {
  let best = 0;
  RADII_KM.forEach((r, i) => {
    if (Math.abs(r - km) < Math.abs(RADII_KM[best] - km)) best = i;
  });
  return best;
}
