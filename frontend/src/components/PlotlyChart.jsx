import React, { useEffect, useRef, useState } from 'react';
import Plotly from 'plotly.js-dist-min';

/**
 * PlotlyChart — A wrapper component that safely renders Plotly charts in React.
 * Uses a container ref and invokes Plotly.newPlot inside useEffect.
 * Captures rendering exceptions and handles window resizing dynamically.
 */
export default function PlotlyChart({ data = [], layout = {}, config = {} }) {
  const containerRef = useRef(null);
  const [renderError, setRenderError] = useState(null);

  // Set default config options for a cleaner modern look
  const defaultConfig = {
    responsive: true,
    displayModeBar: 'hover',
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    ...config
  };

  useEffect(() => {
    if (!containerRef.current) return;
    setRenderError(null);

    // Basic structural checks
    if (!data || data.length === 0 || !data.some(trace => trace && Object.keys(trace).length > 0)) {
      setRenderError("No printable data traces available in dataset.");
      return;
    }

    try {
      // Modern styling adjustments for seamless integration
      const mergedLayout = {
        ...layout,
        autosize: true,
        // Enforce dark background styling if not specified
        paper_bgcolor: layout.paper_bgcolor || '#1e293b',
        plot_bgcolor: layout.plot_bgcolor || '#0f172a',
        font: {
          color: '#e2e8f0',
          family: 'Inter, Roboto, sans-serif',
          size: 12,
          ...layout.font
        }
      };

      Plotly.newPlot(containerRef.current, data, mergedLayout, defaultConfig)
        .catch(err => {
          console.error("Plotly inner rendering error:", err);
          setRenderError(err?.message || "Internal Plotly rendering exception.");
        });

      // Handle responsive resizing
      const handleResize = () => {
        if (containerRef.current) {
          Plotly.Plots.resize(containerRef.current).catch(() => {});
        }
      };

      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
        if (containerRef.current) {
          Plotly.purge(containerRef.current);
        }
      };
    } catch (err) {
      console.error("Error setting up Plotly chart:", err);
      setRenderError(err?.message || "Failed to initialize Plotly graphics.");
    }
  }, [data, layout]);

  if (renderError) {
    return (
      <div className="my-3 p-4 bg-slate-800 border border-slate-700 rounded-xl text-slate-300">
        <p className="text-sm font-semibold text-rose-400 mb-1">
          ⚠️ Unable to render chart
        </p>
        <p className="text-xs text-slate-400 leading-relaxed mb-1">
          Reason: {renderError}
        </p>
        <p className="text-xs text-slate-500 italic">
          Showing data table below instead.
        </p>
      </div>
    );
  }

  return (
    <div className="w-full my-3 overflow-hidden rounded-xl border border-slate-700 shadow-lg bg-[#1e293b]">
      <div 
        ref={containerRef} 
        style={{ width: '100%', height: '400px' }}
        className="w-full"
      />
    </div>
  );
}
