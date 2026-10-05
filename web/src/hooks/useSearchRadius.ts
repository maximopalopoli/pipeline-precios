import { useQuery } from '@tanstack/react-query';
import { fetchSearchRadius, type SearchRadius } from '../lib/api';

/**
 * The radius to search around a location: the chosen one, grown by the API
 * until stores of three grocery chains are within it.
 *
 * `ready` is false while that radius is unknown, so located queries wait for
 * it instead of running twice. Without a location the chosen radius is kept.
 */
export function useSearchRadius(lat: number | null, lon: number | null, radius: number) {
  const located = lat != null && lon != null;
  const { data, isError } = useQuery({
    queryKey: ['search-radius', lat, lon, radius],
    queryFn: () => fetchSearchRadius({ lat: lat!, lon: lon!, radius }),
    enabled: located,
    staleTime: Infinity,
  });
  const area: SearchRadius | null = located && data ? data : null;
  return {
    radius: area?.radio_km ?? radius,
    area,
    ready: !located || !!data || isError,
  };
}
