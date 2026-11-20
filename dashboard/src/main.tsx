import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { MeProvider } from "./components/me-provider";
import { ThemeProvider } from "./components/theme-provider";
import { Toaster } from "./components/ui/sonner";
import { TooltipProvider } from "./components/ui/tooltip";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ThemeProvider>
        <TooltipProvider>
          <MeProvider>
            <App />
            <Toaster position="bottom-right" />
          </MeProvider>
        </TooltipProvider>
      </ThemeProvider>
    </BrowserRouter>
  </StrictMode>,
);
