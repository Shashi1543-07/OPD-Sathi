import { render, screen } from '@testing-library/react';
import { RouterProvider } from 'react-router-dom';
import { expect, it } from 'vitest';
import { router } from './router';

it('renders the initial kiosk shell', async () => {
  window.history.pushState({}, '', '/kiosk');
  render(<RouterProvider router={router} />);
  expect(await screen.findByRole('heading', { name: 'Welcome' })).toBeInTheDocument();
  expect(screen.getByText(/does not diagnose/i)).toBeInTheDocument();
});
