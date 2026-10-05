import { createBrowserRouter, Navigate } from 'react-router-dom';
import { Shell } from './Shell';

export const router = createBrowserRouter([
  { path: '/', element: <Navigate to="/kiosk" replace /> },
  { path: '/kiosk', element: <Shell title="Welcome" /> },
  { path: '/staff', element: <Shell title="Staff sign in" /> },
  { path: '/s/:token', element: <Shell title="Queue status" /> },
  { path: '/dev/components', element: <Shell title="Component gallery" /> },
]);
