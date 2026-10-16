import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { geocode } from '../lib/api';
import { useSearchRadius } from './useSearchRadius';

interface Place {
  /** What the bar shows: the address typed, or none for the device's location. */
  address: string;
  city: string;
  lat: number | null;
  lon: number | null;
  /** The radius the shopper set, or null to grow each view's initial one. */
  radius: number | null;
}

/** One location for every view, kept across visits. */
const usePlace = create<Place>()(
  persist((): Place => ({ address: '', city: 'Buenos Aires', lat: null, lon: null, radius: null }), {
    name: 'location',
  }),
);

/**
 * The shopper's location, shared by every view, with the radius around it.
 *
 * `radius` is the view's initial one grown to reach three grocery chains
 * until the shopper sets one, which is then kept as set. `params` is the
 * located filter the API routes take (empty without a location).
 */
export function useAddressLocation(initialRadius = 1) {
  const place = usePlace();
  const queryClient = useQueryClient();
  const [isGeocoding, setGeocoding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const located = place.lat != null && place.lon != null;
  const searchArea = useSearchRadius(place.lat, place.lon, place.radius ?? initialRadius, place.radius == null);

  return {
    address: place.address,
    city: place.city,
    lat: place.lat,
    lon: place.lon,
    located,
    isGeocoding,
    /** Why the last lookup found no location. */
    error,
    radius: searchArea.radius,
    area: searchArea.area,
    ready: searchArea.ready,
    params: located ? { lat: place.lat!, lon: place.lon!, radius: searchArea.radius } : {},
    setRadius: (radius: number) => usePlace.setState({ radius }),
    /** Look an address up and, when found, make it the location. */
    search: async (address: string, city: string) => {
      setGeocoding(true);
      setError(null);
      try {
        const found = await queryClient.fetchQuery({
          queryKey: ['geocode', address, city],
          queryFn: () => geocode(address, city),
          staleTime: Infinity,
        });
        if (found.lat != null && found.lon != null) {
          usePlace.setState({ address, city, lat: found.lat, lon: found.lon });
        } else {
          setError('No se encontró la dirección.');
        }
      } catch {
        setError('No se pudo buscar la dirección. Probá de nuevo.');
      } finally {
        setGeocoding(false);
      }
    },
    /** Use the device's location, when the browser shares it. */
    locate: () => {
      if (!navigator.geolocation) {
        setError('Tu navegador no comparte la ubicación. Escribí una dirección.');
        return;
      }
      setGeocoding(true);
      setError(null);
      navigator.geolocation.getCurrentPosition(
        (position) => {
          usePlace.setState({ address: '', lat: position.coords.latitude, lon: position.coords.longitude });
          setGeocoding(false);
        },
        (failure) => {
          setError(
            failure.code === failure.PERMISSION_DENIED
              ? 'No diste permiso para usar tu ubicación. Escribí una dirección.'
              : 'No se pudo obtener tu ubicación. Escribí una dirección.',
          );
          setGeocoding(false);
        },
        { enableHighAccuracy: false, timeout: 10000 },
      );
    },
    clear: () => usePlace.setState({ address: '', lat: null, lon: null }),
  };
}

export type AddressLocation = ReturnType<typeof useAddressLocation>;
