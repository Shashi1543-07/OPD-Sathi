import { createBrowserRouter, Navigate } from "react-router-dom";
import { KioskShell } from "./KioskShell";
import { KioskWelcome } from "./KioskWelcome";
import { StaffShell } from "./StaffShell";
import {
  StaffLanding,
  StaffPageGate,
} from "./StaffNavigation";

function RouteLoading() {
  return (
    <p className="route-loading" role="status">
      Loading…
    </p>
  );
}

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/kiosk" replace /> },
  {
    path: "/kiosk",
    element: <KioskShell />,
    HydrateFallback: RouteLoading,
    children: [
      { index: true, element: <KioskWelcome /> },
      {
        path: "intake",
        lazy: async () => {
          const { KioskIntake } = await import("./KioskIntake");
          return { Component: KioskIntake };
        },
      },
      {
        path: "offline-intake",
        lazy: async () => {
          const { KioskOfflineIntake } = await import("./KioskOfflineIntake");
          return { Component: KioskOfflineIntake };
        },
      },
      ...(import.meta.env.DEV
        ? [
            {
              path: "preview/:step",
              lazy: async () => {
                const { KioskPreview } = await import("./KioskPreview");
                return { Component: KioskPreview };
              },
            },
          ]
        : []),
    ],
  },
  {
    path: "/staff",
    element: <StaffShell />,
    HydrateFallback: RouteLoading,
    children: [
      { index: true, element: <StaffLanding /> },
      {
        path: "dashboard",
        element: <Navigate to="/staff/reports" replace />,
      },
      {
        path: "queue",
        lazy: async () => {
          const { StaffQueueBoard } = await import("./StaffQueueBoard");
          return {
            Component: () => (
              <StaffPageGate page="queue">
                <StaffQueueBoard />
              </StaffPageGate>
            ),
          };
        },
      },
      {
        path: "consultations",
        lazy: async () => {
          const { DoctorConsultation } = await import("./DoctorConsultation");
          return {
            Component: () => (
              <StaffPageGate page="consultations">
                <DoctorConsultation />
              </StaffPageGate>
            ),
          };
        },
      },
      {
        path: "patients",
        lazy: async () => {
          const { NurseConsole } = await import("./NurseConsole");
          return {
            Component: () => (
              <StaffPageGate page="patients">
                <NurseConsole />
              </StaffPageGate>
            ),
          };
        },
      },
      {
        path: "reports",
        lazy: async () => {
          const { AnalyticsDashboard } = await import("./AnalyticsDashboard");
          return {
            Component: () => (
              <StaffPageGate page="reports">
                <AnalyticsDashboard />
              </StaffPageGate>
            ),
          };
        },
      },
      {
        path: "settings",
        lazy: async () => {
          const { AdminConsole } = await import("./AdminConsole");
          return {
            Component: () => (
              <StaffPageGate page="settings">
                <AdminConsole />
              </StaffPageGate>
            ),
          };
        },
      },
    ],
  },
  {
    path: "/s/:token",
    HydrateFallback: RouteLoading,
    lazy: async () => {
      const { PatientStatus } = await import("./PatientStatus");
      return { Component: PatientStatus };
    },
  },
  ...(import.meta.env.DEV
    ? [
        {
          path: "/dev/components",
          lazy: async () => {
            const { ComponentGallery } = await import("./ComponentGallery");
            return { Component: ComponentGallery };
          },
        },
      ]
    : []),
]);
