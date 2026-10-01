import { MapContainer, TileLayer, CircleMarker, Popup, Tooltip } from 'react-leaflet';
import type { StorePrice } from '../../lib/api';
import { formatCurrency } from '../../lib/constants';
import { PriceDate } from '../pricing/PriceDate';

interface PriceMapProps {
  stores: StorePrice[];
}

function priceColor(price: number, min: number, max: number): string {
  if (max === min) return '#f1c40f';
  const t = (price - min) / (max - min);
  if (t < 0.5) {
    // green to yellow
    const r = Math.round(39 + (241 - 39) * (t * 2));
    const g = Math.round(174 + (196 - 174) * (t * 2));
    const b = Math.round(96 + (15 - 96) * (t * 2));
    return `rgb(${r},${g},${b})`;
  } else {
    // yellow to red
    const r2 = (t - 0.5) * 2;
    const r = Math.round(241 + (231 - 241) * r2);
    const g = Math.round(196 + (76 - 196) * r2);
    const b = Math.round(15 + (60 - 15) * r2);
    return `rgb(${r},${g},${b})`;
  }
}

export function PriceMap({ stores }: PriceMapProps) {
  const withCoords = stores.filter((s) => s.lat && s.lon && s.lat !== 0 && s.lon !== 0);
  if (withCoords.length === 0) return null;

  const prices = withCoords.map((s) => s.mejor_precio);
  const min = Math.min(...prices);
  const max = Math.max(...prices);

  const center = withCoords.length > 0
    ? [withCoords[0].lat!, withCoords[0].lon!] as [number, number]
    : [-38, -64] as [number, number];

  return (
    <MapContainer center={center} zoom={withCoords.length > 5 ? 6 : 12} style={{ height: 500, width: '100%' }} className="rounded-lg">
      <TileLayer
        attribution='&copy; <a href="https://carto.com/">CartoDB</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />
      {withCoords.map((store, i) => (
        <CircleMarker
          key={i}
          center={[store.lat!, store.lon!]}
          radius={8}
          pathOptions={{
            color: priceColor(store.mejor_precio, min, max),
            fillColor: priceColor(store.mejor_precio, min, max),
            fillOpacity: 0.8,
          }}
        >
          <Tooltip>
            {store.cadena} — {formatCurrency(store.mejor_precio)}<br />
            <PriceDate value={store.fecha_precio} />
          </Tooltip>
          <Popup>
            <strong>{store.cadena}</strong> — {store.sucursal}<br />
            {store.localidad}<br />
            Precio: <strong>{formatCurrency(store.mejor_precio)}</strong><br />
            <PriceDate value={store.fecha_precio} /><br />
            Tipo: {store.tipo}
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
