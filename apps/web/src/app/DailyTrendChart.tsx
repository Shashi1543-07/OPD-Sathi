import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export type DailyPoint = { date: string; count: number };

const HEIGHT = 240;
const PAD = { top: 12, right: 12, bottom: 30, left: 48 };

/** Rounds a maximum up to a readable axis top with 4 even steps (e.g. 37 → 40, 7 → 8). */
function niceScale(max: number): { top: number; step: number } {
  if (max <= 0) return { top: 4, step: 1 };
  const rough = max / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * magnitude).find((s) => s >= rough) ?? rough;
  const whole = Math.max(1, Math.ceil(step));
  return { top: whole * Math.ceil(max / whole), step: whole };
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(240, Math.floor(entry.contentRect.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * Daily visit counts as bars: labelled count axis, thinned date ticks, and the exact date and
 * count on hover, tap or arrow keys. Days without visits are real zeros, not gaps.
 */
export function DailyTrendChart({
  points,
  label,
  axisLabel,
  formatDay,
  formatDayLong,
  unitLabel,
}: {
  points: DailyPoint[];
  label: string;
  axisLabel: string;
  formatDay: (date: string) => string;
  formatDayLong: (date: string) => string;
  unitLabel: (count: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(0, ...points.map((point) => point.count));
  const { top, step } = niceScale(max);
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const slot = plotWidth / Math.max(1, points.length);
  const barWidth = Math.max(2, Math.min(28, slot * 0.68));
  const y = (value: number) => PAD.top + plotHeight - (value / top) * plotHeight;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, index) => index * step);
  // Keep date labels at least ~64px apart, always including the last day.
  const every = Math.max(1, Math.ceil(64 / slot));
  const labelled = (index: number) =>
    index === points.length - 1 || (index % every === 0 && points.length - 1 - index >= every);
  const current = active === null ? undefined : points[active];

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!points.length) return;
    const last = points.length - 1;
    const next =
      event.key === "ArrowRight"
        ? Math.min(last, (active ?? -1) + 1)
        : event.key === "ArrowLeft"
          ? Math.max(0, (active ?? points.length) - 1)
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : undefined;
    if (next === undefined) return;
    event.preventDefault();
    setActive(next);
  }

  return (
    <div className="rp-chart" ref={ref}>
      <div
        className="rp-chart-frame"
        role="img"
        aria-label={label}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onFocus={() => setActive((value) => value ?? points.length - 1)}
        onBlur={() => setActive(null)}
        onMouseLeave={() => setActive(null)}
      >
        <svg width={width} height={HEIGHT} aria-hidden="true">
          <text
            className="rp-axis-title"
            transform={`translate(12 ${PAD.top + plotHeight / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            {axisLabel}
          </text>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="rp-grid"
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text className="rp-tick" x={PAD.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                {tick}
              </text>
            </g>
          ))}
          {points.map((point, index) => {
            const x = PAD.left + slot * index + slot / 2;
            const height = y(0) - y(point.count);
            return (
              <g key={point.date}>
                <rect
                  className={`rp-bar${active === index ? " is-active" : ""}`}
                  x={x - barWidth / 2}
                  y={y(point.count)}
                  width={barWidth}
                  height={Math.max(0, height)}
                  rx={Math.min(3, barWidth / 3)}
                />
                <rect
                  className="rp-hit"
                  x={PAD.left + slot * index}
                  y={PAD.top}
                  width={slot}
                  height={plotHeight}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => setActive(index)}
                >
                  <title>{`${formatDayLong(point.date)}: ${unitLabel(point.count)}`}</title>
                </rect>
                {labelled(index) && (
                  <text className="rp-tick" x={x} y={HEIGHT - 10} textAnchor="middle">
                    {formatDay(point.date)}
                  </text>
                )}
              </g>
            );
          })}
          <line className="rp-axis" x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} />
        </svg>
      </div>
      {current && active !== null && (
          <div
            className="rp-tooltip"
            role="status"
            style={{
              left: Math.min(
                width - 150,
                Math.max(0, PAD.left + slot * active + slot / 2 - 75),
              ),
              top: Math.max(0, y(current.count) - 56),
            }}
          >
            <strong>{unitLabel(current.count)}</strong>
            <span>{formatDayLong(current.date)}</span>
          </div>
        )}
    </div>
  );
}
