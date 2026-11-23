import React from 'react';

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
  onResetToCore?: () => void;
}

export const ErrorBanner: React.FC<ErrorBannerProps> = ({
  message,
  onRetry,
  onResetToCore,
}) => {
  const isNoRoutes = message.toLowerCase().includes('no routes');

  return (
    <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-sm text-rose-900 shadow-sm flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
      <div className="flex items-start gap-3">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className="w-5 h-5 text-rose-600 shrink-0 mt-0.5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
        <div>
          <p className="font-semibold text-rose-950">
            {isNoRoutes ? 'Journey Not Available Yet' : 'Unable to Process Request'}
          </p>
          <p className="text-xs text-rose-700 mt-0.5 leading-relaxed">{message}</p>
          {isNoRoutes && (
            <p className="text-xs text-rose-600 mt-1 italic">
              Genuine walking routes currently exist for <strong>Grand Central Terminal &rarr; Bryant Park</strong>. Routes for other landmark pairs have not yet been pre-generated.
            </p>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0 self-end sm:self-center">
        {onResetToCore && isNoRoutes && (
          <button
            type="button"
            onClick={onResetToCore}
            className="text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white px-3 py-1.5 rounded-lg transition-all shadow-xs"
          >
            Switch to Core Trip
          </button>
        )}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="text-xs font-semibold border border-rose-300 hover:bg-rose-100 text-rose-800 px-3 py-1.5 rounded-lg transition-all"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
};
