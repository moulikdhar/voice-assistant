import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import "./index.css";
import Phone from "./components/Phone";
import Console from "./components/Console";

/** "/" = phone + console side by side (best for demos on one laptop). */
function Demo() {
  return (
    <div className="flex h-full gap-6 p-6">
      <div className="flex flex-col justify-center">
        <Phone />
      </div>
      <Console />
    </div>
  );
}

function Nav() {
  return (
    <nav className="fixed bottom-3 left-4 z-50 flex gap-3 text-[11px] text-zinc-500">
      <Link to="/" className="hover:text-zinc-200">Demo</Link>
      <Link to="/phone" target="_blank" className="hover:text-zinc-200">Phone only ↗</Link>
      <Link to="/console" target="_blank" className="hover:text-zinc-200">Console only ↗</Link>
    </nav>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Demo />} />
        <Route path="/phone" element={<div className="grid h-full place-items-center"><Phone /></div>} />
        <Route path="/console" element={<div className="h-full p-4"><Console /></div>} />
      </Routes>
      <Nav />
    </BrowserRouter>
  </StrictMode>,
);
