import React from 'react';
import { Landmark } from '../types';

interface JourneySelectorProps {
  landmarks: Landmark[];
  originId: string;
  destinationId: string;
  onOriginChange: (id: string) => void;
  onDestinationChange: (id: string) => void;
  onSwap: () => void;
  isLoading: boolean;
}

const selectClass =
  'w-full bg-white border border-slate-300 rounded-lg pl-9 pr-3 py-2.5 text-sm font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 disabled:bg-slate-50 disabled:text-slate-500';

export const JourneySelector: React.FC<JourneySelectorProps> = ({
  landmarks,
  originId,
  destinationId,
  onOriginChange,
  onDestinationChange,
  onSwap,
  isLoading,
}) => {
  const renderOptions = () =>
    landmarks.map((landmark) => (
      <option key={landmark.id} value={landmark.id}>
        {landmark.name}
      </option>
    ));

  return (
    <div className="space-y-1.5">
      <div className="relative">
        <label htmlFor="origin-select" className="sr-only">
          Start landmark
        </label>
        <span className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-emerald-500 ring-4 ring-emerald-100" />
        <select
          id="origin-select"
          value={originId}
          disabled={isLoading}
          onChange={(e) => onOriginChange(e.target.value)}
          className={selectClass}
        >
          {renderOptions()}
        </select>
      </div>

      <div className="flex items-center gap-2 pl-[18px]">
        <span className="h-4 border-l-2 border-dotted border-slate-300" />
        <button
          type="button"
          onClick={onSwap}
          disabled={isLoading}
          title="Swap start and destination"
          aria-label="Swap origin and destination"
          className="flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-indigo-600 disabled:opacity-50"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3" />
          </svg>
          Swap
        </button>
        {originId === destinationId && (
          <span className="text-xs font-medium text-violet-600">Round trip</span>
        )}
      </div>

      <div className="relative">
        <label htmlFor="destination-select" className="sr-only">
          Destination landmark
        </label>
        <span className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-rose-500 ring-4 ring-rose-100" />
        <select
          id="destination-select"
          value={destinationId}
          disabled={isLoading}
          onChange={(e) => onDestinationChange(e.target.value)}
          className={selectClass}
        >
          {renderOptions()}
        </select>
      </div>
    </div>
  );
};
