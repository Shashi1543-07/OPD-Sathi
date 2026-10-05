import { createBrowserRouter, Navigate } from "react-router-dom";
import { ComponentGallery } from "./ComponentGallery";
import { KioskShell } from "./KioskShell";
import { KioskWelcome } from "./KioskWelcome";
import { KioskPreview } from "./KioskPreview";
import { StaffShell } from "./StaffShell";
import { PatientStatus } from "./PatientStatus";
import { KioskIntake } from "./KioskIntake";

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
  { path: "/staff", element: <StaffShell /> },
  {
    path: "/s/:token",
    element: <PatientStatus />,
  },
  ...(import.meta.env.DEV
    ? [{ path: "/dev/components", element: <ComponentGallery /> }]
    : []),
]);
