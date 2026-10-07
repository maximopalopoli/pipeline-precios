import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { geocode } from '../lib/api';
import { useSearchRadius } from './useSearchRadius';

/**
 * A location typed as an address, with the radius around it.
 *
 * `lat` and `lon` are set once the address resolves; `radius` is the initial
 * one grown to reach three grocery chains until the shopper sets one, which
 * is then kept as set. `params` is the located filter the API routes take
 * (empty without a location).
 */
export function useAddressLocation(initialRadius = 1) {
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [chosenRadius, setChosenRadius] = useState(initialRadius);
  const [radiusSet, setRadiusSet] = useState(false);
  const [geoTrigger, setGeoTrigger] = useState(0);
  const [enabled, setEnabled] = useState(false);

  const { data: geoResult, isFetching: isGeocoding } = useQuery({
    queryKey: ['geocode', address, city, geoTrigger],
    queryFn: () => geocode(address, city),
    enabled: address.length > 3 && geoTrigger > 0,
  });

  const lat = enabled && geoResult?.lat ? geoResult.lat : null;
  const lon = enabled && geoResult?.lon ? geoResult.lon : null;
  const searchArea = useSearchRadius(lat, lon, chosenRadius, !radiusSet);
  const located = lat != null && lon != null;

  return {
    address,
    setAddress,
    city,
    setCity,
    chosenRadius,
    setRadius: (radius: number) => {
      setChosenRadius(radius);
      setRadiusSet(true);
    },
    enabled,
    isGeocoding,
    /** The address was looked up and not found. */
    notFound: enabled && !!geoResult && !geoResult.lat,
    lat,
    lon,
    located,
    radius: searchArea.radius,
    area: searchArea.area,
    ready: searchArea.ready,
    params: located ? { lat: lat!, lon: lon!, radius: searchArea.radius } : {},
    search: () => {
      setEnabled(true);
      setGeoTrigger((n) => n + 1);
    },
    clear: () => {
      setEnabled(false);
      setAddress('');
      setGeoTrigger(0);
    },
  };
}

export type AddressLocation = ReturnType<typeof useAddressLocation>;
