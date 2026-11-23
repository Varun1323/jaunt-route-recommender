import React from 'react';

export const Header: React.FC = () => (
  <header className="bg-white border-b border-slate-200">
    <div className="max-w-[1400px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-3">
      <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white">
        <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21" />
          <line x1="9" y1="3" x2="9" y2="18" />
          <line x1="15" y1="6" x2="15" y2="21" />
        </svg>
      </div>
      <h1 className="text-lg font-bold tracking-tight text-slate-900">Jaunt</h1>
      <span className="hidden sm:inline text-sm text-slate-500">
        Walking routes for Midtown Manhattan
      </span>
    </div>
  </header>
);
