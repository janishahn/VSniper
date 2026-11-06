import { createBrowserRouter } from 'react-router-dom';

import { AppLayout, ProfileRedirect } from './App';
import { BucketsPage } from '../pages/BucketsPage';
import { CandidatesPage } from '../pages/CandidatesPage';
import { CostsPage } from '../pages/CostsPage';
import { DashboardPage } from '../pages/DashboardPage';
import { ErrorsPage } from '../pages/ErrorsPage';
import { MyTastePage } from '../pages/MyTastePage';
import { ProfilesPage } from '../pages/ProfilesPage';
import { SearchesPage } from '../pages/SearchesPage';
import { SettingsPage } from '../pages/SettingsPage';
import { StoragePage } from '../pages/StoragePage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ProfileRedirect /> },
      {
        // Everything under /u/:userId belongs to one profile.
        path: 'u/:userId',
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'my-taste', element: <MyTastePage /> },
          { path: 'searches', element: <SearchesPage /> },
          { path: 'buckets', element: <BucketsPage /> },
          { path: 'candidates', element: <CandidatesPage /> },
        ],
      },
      { path: 'profiles', element: <ProfilesPage /> },
      { path: 'costs', element: <CostsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'storage', element: <StoragePage /> },
      { path: 'errors', element: <ErrorsPage /> },
    ],
  },
]);
