import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useUserId } from '../lib/useUserId';
import type { ScoreDistributionBin, ScoreDistributionWindow } from '../types';
import { ErrorText } from './ErrorText';
import { Empty, Loading, Section } from './ui';

const WINDOW_OPTIONS: { value: ScoreDistributionWindow; label: string }[] = [
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '12h', label: '12h' },
  { value: '1d', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: 'all', label: 'All' },
];

const CHART_WIDTH = 720;
const CHART_HEIGHT = 220;
const MARGIN = { top: 18, right: 8, bottom: 26, left: 34 };
const PLOT_WIDTH = CHART_WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = CHART_HEIGHT - MARGIN.top - MARGIN.bottom;
const GRID_STEPS = [0, 0.5, 1];

// Nicely-rounded y-axis ceiling so gridline labels land on clean percentages.
function niceMax(value: number): number {
  if (value <= 0) return 10;
  const step = value <= 20 ? 5 : 10;
  return Math.ceil(value / step) * step;
}

// Where the threshold falls on the x axis, interpolated inside the bin that contains it.
function thresholdX(bins: ScoreDistributionBin[], threshold: number, bandWidth: number): number | null {
  const index = bins.findIndex((bin) => threshold >= bin.min_score && threshold <= bin.max_score);
  if (index < 0) return null;
  const bin = bins[index];
  const span = bin.max_score - bin.min_score + 1;
  return (index + (threshold - bin.min_score) / span) * bandWidth;
}

export function ScoreDistributionChart({ threshold }: { threshold: number }) {
  const userId = useUserId();
  const [selectedWindow, setSelectedWindow] = useState<ScoreDistributionWindow>('7d');
  const [hovered, setHovered] = useState<number | null>(null);

  const query = useQuery({
    queryKey: queryKeys.scoreDistribution(userId, selectedWindow),
    queryFn: () => api.getScoreDistribution(userId, selectedWindow),
    placeholderData: (previous) => previous,
  });

  const data = query.data;
  const bins: ScoreDistributionBin[] = data?.bins ?? [];
  const yMax = niceMax(Math.max(0, ...bins.map((bin) => bin.percentage)));
  const bandWidth = bins.length ? PLOT_WIDTH / bins.length : 0;
  const barWidth = Math.max(4, bandWidth - 4);
  const lineX = thresholdX(bins, threshold, bandWidth);

  return (
    <Section
      title="Judge scores"
      sub={data && data.total_count > 0
        ? `${data.total_count} listing${data.total_count === 1 ? '' : 's'} judged. Highlighted bars reach the alert threshold of ${threshold}.`
        : 'How the judge scored listings, with the alert threshold marked.'}
      actions={(
        <div className="seg" role="group" aria-label="Time window">
          {WINDOW_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              aria-pressed={selectedWindow === opt.value}
              onClick={() => setSelectedWindow(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    >
      <div className="panel">
        {query.isLoading ? (
          <Loading label="Loading scores…" />
        ) : query.isError ? (
          <ErrorText error={query.error} prefix="Could not load score distribution" />
        ) : !data || data.total_count === 0 ? (
          <Empty title="No judged listings in this window">Pick a longer window or run a search.</Empty>
        ) : (
          <div className="chart" style={{ opacity: query.isFetching ? 0.6 : 1 }}>
            <svg
              viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
              role="img"
              aria-label={`Distribution of judge scores; alert threshold ${threshold}`}
            >
              <g transform={`translate(${MARGIN.left}, ${MARGIN.top})`}>
                {GRID_STEPS.map((step) => {
                  const y = PLOT_HEIGHT - step * PLOT_HEIGHT;
                  return (
                    <g key={step}>
                      <line x1={0} x2={PLOT_WIDTH} y1={y} y2={y} stroke="var(--line)" strokeWidth={1} />
                      <text x={-8} y={y} textAnchor="end" dominantBaseline="middle" className="chart-axis">
                        {Math.round(step * yMax)}%
                      </text>
                    </g>
                  );
                })}

                {bins.map((bin, index) => {
                  const barHeight = yMax > 0 ? (bin.percentage / yMax) * PLOT_HEIGHT : 0;
                  const bandX = index * bandWidth;
                  const isAlert = bin.max_score >= threshold;
                  const cls = `chart-bar${isAlert ? ' is-alert' : ''}${hovered === index ? ' is-hover' : ''}`;
                  return (
                    <g
                      key={`${bin.min_score}-${bin.max_score}`}
                      tabIndex={0}
                      role="img"
                      aria-label={`Score ${bin.min_score} to ${bin.max_score}: ${bin.count} listings, ${bin.percentage}%`}
                      onMouseEnter={() => setHovered(index)}
                      onMouseLeave={() => setHovered(null)}
                      onFocus={() => setHovered(index)}
                      onBlur={() => setHovered(null)}
                    >
                      <rect x={bandX} y={0} width={bandWidth} height={PLOT_HEIGHT} fill="transparent" />
                      {barHeight > 0 ? (
                        <rect
                          className={cls}
                          x={bandX + (bandWidth - barWidth) / 2}
                          y={PLOT_HEIGHT - barHeight}
                          width={barWidth}
                          height={barHeight}
                          rx={2}
                        />
                      ) : null}
                      {index % 2 === 0 || bins.length <= 10 ? (
                        <text x={bandX + bandWidth / 2} y={PLOT_HEIGHT + 17} textAnchor="middle" className="chart-axis">
                          {bin.min_score}
                        </text>
                      ) : null}
                    </g>
                  );
                })}

                {lineX !== null ? (
                  <g aria-hidden="true">
                    <line className="chart-threshold" x1={lineX} x2={lineX} y1={-6} y2={PLOT_HEIGHT} />
                    <text className="chart-threshold-label" x={lineX - 4} y={-8} textAnchor="end">
                      Alert {threshold}
                    </text>
                  </g>
                ) : null}
              </g>
            </svg>

            <p className="chart-readout" aria-live="polite">
              {hovered !== null && bins[hovered] ? (
                <>
                  Score {bins[hovered].min_score}–{bins[hovered].max_score}: {bins[hovered].count} listing
                  {bins[hovered].count === 1 ? '' : 's'} ({bins[hovered].percentage}%)
                </>
              ) : (
                'Hover a bar for its count.'
              )}
            </p>
          </div>
        )}
      </div>
    </Section>
  );
}
