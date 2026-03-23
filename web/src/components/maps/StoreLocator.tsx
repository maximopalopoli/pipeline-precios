import { MapContainer, TileLayer, Marker, Popup, Tooltip, Circle } from 'react-leaflet';
import L from 'leaflet';
import type { NearbyStore } from '../../lib/api';

// Fix default marker icons in bundled builds
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

const defaultIcon = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

const homeIcon = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [30, 48],
  iconAnchor: [15, 48],
  popupAnchor: [1, -38],
  shadowSize: [48, 48],
});

interface StoreLocatorProps {
  userLat: number;
  userLon: number;
  radiusKm: number;
  stores: NearbyStore[];
}

export function StoreLocatorMap({ userLat, userLon, radiusKm, stores }: StoreLocatorProps) {
  return (
    <MapContainer
      center={[userLat, userLon]}
      zoom={13}
      style={{ height: 500, width: '100%' }}
      className="rounded-lg"
    >
      <TileLayer
        attribution='&copy; <a href="https://carto.com/">CartoDB</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />

      {/* User location */}
      <Marker position={[userLat, userLon]} icon={homeIcon}>
        <Tooltip>Tu ubicacion</Tooltip>
      </Marker>

      {/* Radius circle */}
      <Circle
        center={[userLat, userLon]}
        radius={radiusKm * 1000}
        pathOptions={{ color: '#e74c3c', fillOpacity: 0.05, weight: 1 }}
      />

      {/* Store markers */}
      {stores.map((store) => {
        const direccion = `${store.calle || ''} ${store.numero || ''}`.trim();
        return (
          <Marker key={store.sucursal_sk} position={[store.lat, store.lon]} icon={defaultIcon}>
            <Tooltip>{store.cadena} — {store.distancia_km} km</Tooltip>
            <Popup>
              <strong>{store.cadena}</strong><br />
              {store.nombre}<br />
              {direccion}, {store.localidad}<br />
              <strong>{store.distancia_km} km</strong>
            </Popup>
          </Marker>
        );
      })}
    </MapContainer>
  );
}
