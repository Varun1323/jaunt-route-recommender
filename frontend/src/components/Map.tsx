
import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import { RouteItem } from '../types';

interface MapProps {
  routes: RouteItem[];
  originName?: string;
  destinationName?: string;
}

export const Map: React.FC<MapProps> = ({
  routes,
  originName,
  destinationName,
}) => {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const layerGroupRef = useRef<L.LayerGroup | null>(null);

  // Initialize Leaflet once.
  useEffect(() => {
    if (!mapContainerRef.current || mapInstanceRef.current) return;

    const map = L.map(mapContainerRef.current, {
      center: [40.7525, -73.980],
      zoom: 16,
      zoomControl: true,
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);

    const layers = L.layerGroup().addTo(map);

    mapInstanceRef.current = map;
    layerGroupRef.current = layers;

    return () => {
      map.remove();
      mapInstanceRef.current = null;
      layerGroupRef.current = null;
    };
  }, []);

  // Render the single V1 route and its explicit stops.
  useEffect(() => {
    const map = mapInstanceRef.current;
    const layers = layerGroupRef.current;

    if (!map || !layers) return;

    layers.clearLayers();

    const route = routes[0];
    const feature = route?.geojson?.features?.[0];

    if (!feature?.geometry?.coordinates?.length) return;

    const coordinates = feature.geometry.coordinates;

    const latLngs: L.LatLngExpression[] = coordinates.map(
      ([lon, lat]) => [lat, lon]
    );

    const routeLine = L.polyline(latLngs, {
      color: '#4338ca',
      weight: 5,
      opacity: 0.9,
      lineCap: 'round',
      lineJoin: 'round',
    }).addTo(layers);

    routeLine.bindTooltip('Jaunt walking route');

    // Start and finish positions come from the actual returned route geometry.
    const [startLon, startLat] = coordinates[0];
    const [endLon, endLat] = coordinates[coordinates.length - 1];

    const startIcon = L.divIcon({
      className: 'custom-map-pin',
      html: `
        <div style="
          background:#059669;
          color:white;
          border:2px solid white;
          border-radius:50%;
          width:28px;
          height:28px;
          display:flex;
          align-items:center;
          justify-content:center;
          font-size:12px;
          font-weight:bold;
          box-shadow:0 2px 6px rgba(0,0,0,.25);
        ">A</div>
      `,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });

    const endIcon = L.divIcon({
      className: 'custom-map-pin',
      html: `
        <div style="
          background:#e11d48;
          color:white;
          border:2px solid white;
          border-radius:50%;
          width:28px;
          height:28px;
          display:flex;
          align-items:center;
          justify-content:center;
          font-size:12px;
          font-weight:bold;
          box-shadow:0 2px 6px rgba(0,0,0,.25);
        ">B</div>
      `,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });

    L.marker([startLat, startLon], { icon: startIcon })
      .addTo(layers)
      .bindPopup(
        `<strong>Start</strong><br>${originName || 'Origin'}`
      );

    if (originName !== destinationName || startLat !== endLat || startLon !== endLon) {
      L.marker([endLat, endLon], { icon: endIcon })
        .addTo(layers)
        .bindPopup(
          `<strong>Destination</strong><br>${destinationName || 'Destination'}`
        );
    }

    // Only display explicitly selected waypoints returned in route metadata.
    const properties = feature.properties as Record<string, unknown>;
    const waypoints = properties?.via_waypoints;

    if (Array.isArray(waypoints)) {
      waypoints.forEach((value) => {
        if (!value || typeof value !== 'object') return;

        const waypoint = value as {
          lat?: unknown;
          lon?: unknown;
          landmark_name?: unknown;
        };

        if (
          typeof waypoint.lat !== 'number' ||
          typeof waypoint.lon !== 'number'
        ) {
          return;
        }

        const marker = L.circleMarker(
          [waypoint.lat, waypoint.lon],
          {
            radius: 7,
            color: '#7e22ce',
            fillColor: '#c084fc',
            fillOpacity: 0.95,
            weight: 2,
          }
        ).addTo(layers);

        marker.bindPopup(
          typeof waypoint.landmark_name === 'string'
            ? waypoint.landmark_name
            : 'Selected intermediate stop'
        );
      });
    }

    // Fit the map to the returned route geometry.
    map.fitBounds(routeLine.getBounds(), {
      padding: [40, 40],
      maxZoom: 17,
    });
  }, [routes, originName, destinationName]);

  return (
    <div className="relative w-full h-[360px] sm:h-[460px] lg:h-full lg:min-h-[540px] overflow-hidden">
      <div ref={mapContainerRef} className="w-full h-full" />

      <div className="hidden sm:block absolute bottom-3 left-3 z-[1000] bg-white/95 border border-slate-200 rounded-lg p-2.5 shadow-md text-[11px] text-slate-700 space-y-1.5">
        <p className="font-bold text-xs text-slate-900 border-b border-slate-100 pb-1">
          Map Legend
        </p>

        <div className="flex items-center gap-2">
          <span className="w-4 h-1 bg-indigo-700 rounded-full" />
          <span>Walking route</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-3.5 h-3.5 rounded-full bg-emerald-600 text-white font-bold text-[9px] flex items-center justify-center">
            A
          </span>
          <span>Start</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-3.5 h-3.5 rounded-full bg-rose-600 text-white font-bold text-[9px] flex items-center justify-center">
            B
          </span>
          <span>Destination</span>
        </div>

        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-full bg-purple-400 border-2 border-purple-700" />
          <span>Selected intermediate stop</span>
        </div>
      </div>
    </div>
  );
};
