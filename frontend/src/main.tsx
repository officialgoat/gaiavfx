import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Tooltip from "@radix-ui/react-tooltip";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import { App } from "./app/App";
import "./shared/theme.css";
import "./app/shell.css";
import "./features/generation/generation.css";
import "./features/shell/studio.css";

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: false },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <Tooltip.Provider delayDuration={350}>
        <App />
      </Tooltip.Provider>
    </QueryClientProvider>
  </React.StrictMode>,
);
