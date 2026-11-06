import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  CircleAlert,
  Crosshair,
  Gauge,
  HardDrive,
  Layers,
  LayoutGrid,
  Receipt,
  Search,
  Settings,
  Shirt,
  Users,
} from 'lucide-react';

import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { ErrorText } from '../components/ErrorText';
import { Loading, StatusDot } from '../components/ui';

// Links under a profile are relative to /u/:userId.
const profileGroups = [
  {
    label: 'Review',
    links: [
      { to: '', label: 'Dashboard', icon: Gauge, end: true },
      { to: '/candidates', label: 'Candidates', icon: LayoutGrid },
    ],
  },
  {
    label: 'Tune',
    links: [
      { to: '/my-taste', label: 'My Taste', icon: Shirt },
      { to: '/searches', label: 'Searches', icon: Search },
      { to: '/buckets', label: 'Buckets', icon: Layers },
    ],
  },
];

const installationGroups = [
  {
    label: 'Installation',
    links: [
      { to: '/profiles', label: 'Profiles', icon: Users },
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
  [...profileGroups, ...installationGroups].flatMap((group) => group.links.map((link) => [link.to || '/', link.label])),
);

const PROFILE_PATH = /^\/u\/\d+/;
const LAST_PROFILE_KEY = 'vsniper.lastProfile';

function readLastProfile(): number | null {
  try {
    const value = Number(localStorage.getItem(LAST_PROFILE_KEY));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function rememberProfile(userId: number) {
  try {
    localStorage.setItem(LAST_PROFILE_KEY, String(userId));
  } catch {
    // Remembering the profile is a convenience only.
  }
}

// Opens the profile used last, or the first one.
export function ProfileRedirect() {
  const usersQuery = useQuery({ queryKey: queryKeys.users, queryFn: api.getUsers });
  if (usersQuery.isLoading) return <div className="page"><Loading label="Loading profiles…" /></div>;
  if (usersQuery.isError || !usersQuery.data) {
    return <div className="page"><ErrorText error={usersQuery.error ?? 'Could not load profiles.'} /></div>;
  }
  const users = usersQuery.data;
  const last = readLastProfile();
  const target = users.find((user) => user.id === last) ?? users[0];
  return target ? <Navigate to={`/u/${target.id}`} replace /> : <Navigate to="/profiles" replace />;
}

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = useParams();
  const settingsQuery = useQuery({ queryKey: queryKeys.settings, queryFn: api.getSettings });
  const usersQuery = useQuery({ queryKey: queryKeys.users, queryFn: api.getUsers });
  const routeUserId = params.userId ? Number(params.userId) : null;
  const [lastUserId, setLastUserId] = useState<number | null>(readLastProfile);

  useEffect(() => {
    if (routeUserId === null) return;
    setLastUserId(routeUserId);
    rememberProfile(routeUserId);
  }, [routeUserId]);

  const users = usersQuery.data ?? [];
  const currentUserId = routeUserId ?? (users.some((user) => user.id === lastUserId) ? lastUserId : users[0]?.id ?? null);

  useEffect(() => {
    const title = titles[location.pathname.replace(PROFILE_PATH, '') || '/'];
    document.title = title ? `${title} · VSniper` : 'VSniper';
  }, [location.pathname]);

  const switchProfile = (userId: number) => {
    const rest = PROFILE_PATH.test(location.pathname) ? location.pathname.replace(PROFILE_PATH, '') : '';
    navigate(`/u/${userId}${rest}`);
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true"><Crosshair size={14} strokeWidth={2.5} /></span>
          VSniper
        </Link>
        <nav className="nav" aria-label="Main">
          {users.length > 1 && currentUserId !== null ? (
            <label className="nav-group">
              <span className="nav-group-label">Profile</span>
              <select value={currentUserId} onChange={(event) => switchProfile(Number(event.target.value))}>
                {users.map((user) => (
                  <option key={user.id} value={user.id}>{user.name}</option>
                ))}
              </select>
            </label>
          ) : null}
          {currentUserId !== null
            ? profileGroups.map((group) => (
                <div className="nav-group" key={group.label}>
                  <span className="nav-group-label">{group.label}</span>
                  {group.links.map((link) => (
                    <NavLink
                      key={link.to}
                      to={`/u/${currentUserId}${link.to}`}
                      end={link.end}
                      className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                    >
                      <link.icon size={16} aria-hidden="true" />
                      {link.label}
                    </NavLink>
                  ))}
                </div>
              ))
            : null}
          {installationGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              {group.links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  <link.icon size={16} aria-hidden="true" />
                  {link.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {settingsQuery.data ? (
          <div className="sidebar-foot">
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
