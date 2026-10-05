import type { AddressLocation } from '../../hooks/useAddressLocation';

/** Address, city and radius around them; `title` names what the location filters. */
export function LocationFilter({
  location,
  title = 'Filtrar por ubicacion',
}: {
  location: AddressLocation;
  title?: string;
}) {
  const { address, setAddress, city, setCity, chosenRadius, setRadius } = location;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
        {location.enabled && (
          <button onClick={location.clear} className="text-xs text-slate-400 hover:text-slate-600">
            Desactivar
          </button>
        )}
      </div>
      <input
        type="text"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && address.length > 3) location.search(); }}
        placeholder="Av. Corrientes 1234"
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      <input
        type="text"
        value={city}
        onChange={(e) => setCity(e.target.value)}
        placeholder="Ciudad"
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      <button
        onClick={location.search}
        disabled={address.length < 3}
        className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {location.isGeocoding ? 'Buscando...' : 'Buscar ubicacion'}
      </button>
      {location.located && (
        <>
          <p className="text-xs text-slate-500">
            Ubicacion: {location.lat!.toFixed(4)}, {location.lon!.toFixed(4)}
          </p>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Radio: {chosenRadius} km</label>
            <input
              type="range"
              min={0.5}
              max={10}
              step={0.5}
              value={chosenRadius}
              onChange={(e) => setRadius(Number(e.target.value))}
              className="w-full"
            />
          </div>
        </>
      )}
      {location.notFound && <p className="text-xs text-red-500">No se encontro la direccion.</p>}
    </div>
  );
}
