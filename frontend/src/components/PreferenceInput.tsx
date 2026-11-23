import React, { useState } from 'react';

interface PreferenceInputProps {
  currentPreference: string;
  onApplyPreference: (preference: string) => void;
  isLoading: boolean;
}

const PRESET_QUERIES = [
  { label: 'Shortest', query: 'shortest route' },
  { label: 'Historic architecture', query: 'I love historic architecture and interesting buildings' },
  { label: 'Under 10 min', query: 'quick walk under 10 minutes' },
  { label: 'Most landmarks', query: 'I want to see as many interesting landmarks as possible' },
  { label: 'Fast, no detours', query: 'fast walk without architecture' },
];

export const PreferenceInput: React.FC<PreferenceInputProps> = ({
  currentPreference,
  onApplyPreference,
  isLoading,
}) => {
  const [inputText, setInputText] = useState(currentPreference);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const preference = inputText.trim();
    setInputText(preference);
    onApplyPreference(preference);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label htmlFor="traveler-preference" className="block text-sm font-semibold text-slate-800 mb-1.5">
          What kind of walk do you want?
        </label>
        <textarea
          id="traveler-preference"
          name="preference"
          rows={2}
          value={inputText}
          disabled={isLoading}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="e.g. I love architecture but don't have much time"
          className="w-full resize-none bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 disabled:bg-slate-50"
        />
      </div>

      <div className="flex flex-wrap gap-1.5">
        {PRESET_QUERIES.map((preset) => (
          <button
            key={preset.label}
            type="button"
            disabled={isLoading}
            onClick={() => setInputText(preset.query)}
            className={`text-xs px-2.5 py-1 rounded-full border ${
              inputText === preset.query
                ? 'bg-indigo-600 text-white border-indigo-600'
                : 'bg-white text-slate-600 border-slate-300 hover:border-indigo-400 hover:text-indigo-700'
            }`}
          >
            {preset.label}
          </button>
        ))}
      </div>

      <button
        type="submit"
        disabled={isLoading}
        className="w-full bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white font-semibold text-sm py-2.5 rounded-lg shadow-sm disabled:opacity-60 flex items-center justify-center gap-2"
      >
        {isLoading ? (
          <>
            <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            Finding routes…
          </>
        ) : (
          'Update route'
        )}
      </button>
    </form>
  );
};
