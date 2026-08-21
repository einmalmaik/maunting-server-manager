import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import Overlay from "./Overlay";
import "./styles.css";

// Ein Bundle, zwei Fenster: das Hauptfenster (Chat/Einrichtung) und das
// frameless Overlay (Sprachblase). Welches Fenster wir sind, sagt die URL —
// so gibt es keinen zweiten Build und keinen zweiten Einstiegspunkt.
const istOverlay = new URLSearchParams(window.location.search).get("fenster") === "overlay";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{istOverlay ? <Overlay /> : <App />}</React.StrictMode>,
);
