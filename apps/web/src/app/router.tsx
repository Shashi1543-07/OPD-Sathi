import { createBrowserRouter, Navigate } from "react-router-dom";
import { ComponentGallery } from "./ComponentGallery";
import { KioskShell } from "./KioskShell";
import { KioskWelcome } from "./KioskWelcome";
import { KioskPreview } from "./KioskPreview";
import { StaffShell } from "./StaffShell";
import { PatientStatus } from "./PatientStatus";
import { KioskIntake } from "./KioskIntake";
import { StaffQueueBoard } from "./StaffQueueBoard";
import {
  StaffLanding,
  StaffPageGate,
  StaffPlaceholderPage,
} from "./StaffNavigation";

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/kiosk" replace /> },
  {
    path: "/kiosk",
    element: <KioskShell />,
    children: [
      { index: true, element: <KioskWelcome /> },
      { path: "intake", element: <KioskIntake /> },
      ...(import.meta.env.DEV
        ? [{ path: "preview/:step", element: <KioskPreview /> }]
        : []),
    ],
  },
  {
    path: "/staff",
    element: <StaffShell />,
    children: [
      { index: true, element: <StaffLanding /> },
      {
        path: "dashboard",
        element: (
          <StaffPageGate page="dashboard">
            <StaffPlaceholderPage page="dashboard" />
          </StaffPageGate>
        ),
      },
      {
        path: "queue",
        element: (
          <StaffPageGate page="queue">
            <StaffQueueBoard />
          </StaffPageGate>
        ),
      },
      {
        path: "consultations",
        element: (
          <StaffPageGate page="consultations">
            <StaffPlaceholderPage page="consultations" />
          </StaffPageGate>
        ),
      },
      {
        path: "patients",
        element: (
          <StaffPageGate page="patients">
            <StaffPlaceholderPage page="patients" />
          </StaffPageGate>
        ),
      },
      {
        path: "reports",
        element: (
          <StaffPageGate page="reports">
            <StaffPlaceholderPage page="reports" />
          </StaffPageGate>
        ),
      },
      {
        path: "settings",
        element: (
          <StaffPageGate page="settings">
            <StaffPlaceholderPage page="settings" />
          </StaffPageGate>
        ),
      },
    ],
  },
  {
    path: "/s/:token",
    element: <PatientStatus />,
  },
  ...(import.meta.env.DEV
    ? [{ path: "/dev/components", element: <ComponentGallery /> }]
    : []),
]);
