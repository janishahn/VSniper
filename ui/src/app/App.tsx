import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  CircleAlert,
  Crosshair,
  Gauge,
  HardDrive,
  LayoutGrid,
  Receipt,
  Search,
  Settings,
  Shirt,
} from 'lucide-react';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { StatusDot } from '../components/ui';

const groups = [
  {
    label: 'Review',
    links: [
      { to: '/', label: 'Dashboard', icon: Gauge, end: true },
      { to: '/candidates', label: 'Candidates', icon: LayoutGrid },
    ],
  },
  {
    label: 'Tune',
    links: [
      { to: '/my-taste', label: 'My Taste', icon: Shirt },
      { to: '/searches', label: 'Searches', icon: Search },
      { to: '/settings', label: 'Settings', icon: Settings },
    ],
  },
  {
    label: 'Monitor',
    links: [
      { to: '/costs', label: 'AI costs', icon: Receipt },
      { to: '/errors', label: 'Errors', icon: CircleAlert },
      { to: '/storage', label: 'Storage', icon: HardDrive },
    ],
  },
];

const titles: Record<string, string> = Object.fromEntries(
  groups.flatMap((group) => group.links.map((link) => [link.to, link.label])),
);

const sessionTone = { healthy: 'ok', warning: 'warn', missing: 'bad' } as const;

export function AppLayout() {
  const location = useLocation();
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  const session = settingsQuery.data?.session_health;

  useEffect(() => {
    const title = titles[location.pathname];
    document.title = title ? `${title} · VSniper` : 'VSniper';
  }, [location.pathname]);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true"><Crosshair size={14} strokeWidth={2.5} /></span>
          VSniper
        </Link>
        <nav className="nav" aria-label="Main">
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              {group.links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.end}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  <link.icon size={16} aria-hidden="true" />
                  {link.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {session ? (
          <div className="sidebar-foot">
            <StatusDot tone={sessionTone[session.status]}>
              Vinted {session.status === 'healthy' ? 'connected' : session.status}
            </StatusDot>
            <StatusDot tone={settingsQuery.data?.telegram_configured ? 'ok' : 'bad'}>
              Telegram {settingsQuery.data?.telegram_configured ? 'connected' : 'missing'}
            </StatusDot>
          </div>
        ) : null}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
