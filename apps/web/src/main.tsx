import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/noto-sans-devanagari/wght.css";
import "./i18n";
import { router } from "./app/router";
import "./styles.css";
import { registerSW } from "virtual:pwa-register";

registerSW({ immediate: true });

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
