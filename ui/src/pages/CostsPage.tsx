import { useQuery } from '@tanstack/react-query';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { ErrorText } from '../components/ErrorText';
import { Loading, PageHeader, Section, Stat } from '../components/ui';
import type { AiCategoryStats } from '../types';

function fmtUsd(usd: number): string {
  return `$${usd.toFixed(usd >= 10 ? 2 : 4)}`;
}

function calls(count: number): string {
  return `${count.toLocaleString()} call${count === 1 ? '' : 's'}`;
}

function CategoryRow({ label, detail, cat }: { label: string; detail: string; cat: AiCategoryStats }) {
  return (
    <tr>
      <td>
        <div style={{ fontWeight: 500 }}>{label}</div>
        <div className="small muted">{detail}</div>
      </td>
      <td className="r">{fmtUsd(cat.last_24h_usd)}<div className="small faint">{calls(cat.last_24h_calls)}</div></td>
      <td className="r">{fmtUsd(cat.last_7d_usd)}<div className="small faint">{calls(cat.last_7d_calls)}</div></td>
      <td className="r">{fmtUsd(cat.last_30d_usd)}<div className="small faint">{calls(cat.last_30d_calls)}</div></td>
      <td className="r">{fmtUsd(cat.total_usd)}<div className="small faint">{calls(cat.total_calls)}</div></td>
    </tr>
  );
}

export function CostsPage() {
  const query = useQuery({ queryKey: queryKeys.costs, queryFn: api.getAiCostStats });
  const header = (
    <PageHeader
      title="AI costs"
      sub="Estimated from the token counts OpenAI reports, at standard-tier prices. Cached input uses the cached rate where OpenAI publishes one."
    />
  );

  if (query.isLoading) return <div className="page">{header}<Loading label="Loading costs…" /></div>;
  if (query.isError || !query.data) {
    return <div className="page">{header}<ErrorText error={query.error ?? 'Could not load cost data.'} /></div>;
  }

  const s = query.data;

  return (
    <div className="page">
      {header}

      <div className="stats">
        <Stat label="Last 24 hours" value={fmtUsd(s.last_24h_usd)} note={calls(s.last_24h_calls)} />
        <Stat label="Last 7 days" value={fmtUsd(s.last_7d_usd)} note={calls(s.last_7d_calls)} />
        <Stat label="Last 30 days" value={fmtUsd(s.last_30d_usd)} note={calls(s.last_30d_calls)} />
        <Stat label="All time" value={fmtUsd(s.total_usd)} note={calls(s.total_calls)} />
      </div>

      <Section title="By operation">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Operation</th>
                <th className="r">24 hours</th>
                <th className="r">7 days</th>
                <th className="r">30 days</th>
                <th className="r">All time</th>
              </tr>
            </thead>
            <tbody>
              <CategoryRow label="Judging" detail="Scoring listings and writing the explanation" cat={s.judge} />
              <CategoryRow label="Learning" detail="Describing photos and building the taste profile" cat={s.learning} />
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
