# Benchmark-Vergleich: Maunting Secure Browser (MSB) vs. Brave

* **Testdatum:** 11. / 12. Oktober 2026
* **Hardware-Umgebung:** Windows Desktop-PC, 144 Hz Display
* **Getestete Browser:**
  * **Maunting Secure Browser (MSB)** v5.3.5 (Tauri v2 + WebView2 / Rust Core)
  * **Brave Browser** (Chromium / Blink + V8)

---

## 1. Speedometer 3.0 (Alltags- & Web-App-Performance)
*Misst die reale Reaktionsfähigkeit moderner Single-Page-Applications (React, Vue, Lit, Preact, WebComponents, To-Do-MVC).*

| Metrik | Maunting Secure Browser | Brave Browser | Differenz |
| :--- | :---: | :---: | :---: |
| **Gesamt-Score (Runs/Min)** | **28.8** | **21.1** | **+36.5 % schneller** |
| *Charts-chartjs Tooltip* | **0.2 ms** | 0.6 ms | **+61.9 % schneller** |
| *Lit Complex-DOM Deleting* | **1.1 ms** | 2.4 ms | **+55.2 % schneller** |
| *Lit Complex-DOM Adding* | **1.2 ms** | 2.5 ms | **+52.6 % schneller** |
| *WebComponents Adding* | **5.0 ms** | 9.7 ms | **+48.2 % schneller** |
| *Preact Complex-DOM* | **0.8 ms** | 1.5 ms | **+46.7 % schneller** |
| *Durchschnitt über alle 206 Workloads* | – | – | **+26.2 % im Schnitt** |

> **Erkenntnis:** MSB führt Web-UI-Interaktionen im Schnitt über ein Viertel schneller aus und erreicht einen um 36,5 % höheren Gesamt-Score, da keine bremsenden Background-Scripts (Krypto-Wallets, Belohnungs-Dienste, Extension-Injections) im DOM-Event-Loop liegen.

---

## 2. JetStream 2 (JavaScript & WebAssembly Rohleistung)
*Misst mathematische Algorithmen, Kryptographie, Raytracing, Parsing und WASM-Berechnungen.*

| Metrik | Maunting Secure Browser | Brave Browser | Differenz |
| :--- | :---: | :---: | :---: |
| **Gesamt-Score** | **346.481** | **349.160** | **~0.7 % (Gleichstand)** |
| *quicksort-wasm* | **833.333** | 680.414 | **+22.5 % schneller** |
| *stanford-crypto-aes* | **562.024** | 436.888 | **+28.6 % schneller** |
| *stanford-crypto-pbkdf2* | **1113.474** | 866.171 | **+28.5 % schneller** |
| *splay* | **533.452** | 428.452 | **+24.5 % schneller** |
| *3d-raytrace-SP* | 713.965 | **932.241** | Brave vorne |
| *crypto* | 1564.595 | **1735.838** | Brave vorne |

> **Erkenntnis:** Bei reiner mathematischer Ausführung und JIT-Kompilierung über die V8-Engine bewegen sich beide Browser auf exakt demselben Top-Niveau (< 1 % Abweichung).

---

## 3. MotionMark 1.3.2 (Grafik- & Rendering-Pipeline)
*Misst 2D-Canvas, SVG, CSS-Animationen und Partikelsysteme bis an die Bildwiederholrate.*

| Test | Maunting Secure Browser (@ 144 fps) | Brave Browser (@ 144 fps) | Differenz |
| :--- | :---: | :---: | :---: |
| **Gesamt-Score** | **854.11** | 588.23 | **+45.2 % schneller** |
| *Multiply* | **822.34** | 429.09 | **+91.6 % schneller** |
| *Suits* | **425.05** | 246.91 | **+72.1 % schneller** |
| *Paths* | **2.976.78** | 1.978.46 | **+50.5 % schneller** |
| *Leaves* | **719.09** | 482.40 | **+49.1 % schneller** |
| *Images* | **100.66** | 67.77 | **+48.5 % schneller** |
| *Canvas Lines* | **12.796.26** | 9.551.66 | **+34.0 % schneller** |
| *Canvas Arcs* | **2.392.70** | 1.996.28 | **+19.9 % schneller** |
| *Design* | **122.81** | 109.70 | **+11.9 % schneller** |

> **Erkenntnis:** MSB liefert dank des leichtgewichtigen nativen Rust/Tauri-Compositors eine um **45,2 % höhere Grafikleistung** ab. Während Brave bei hoher Last Frames verliert (z. B. Multiply 429 vs 822), hält MSB die Rendering-Pipeline auf 144 Hz extrem stabil.

---

## 4. RAM- & Ressourcen-Profil (Praxisvergleich)
*Typischer Speicherverbrauch bei alltäglicher Nutzung (Task-Manager).*

| Zustand | Maunting Secure Browser | Brave Browser |
| :--- | :---: | :---: |
| **1 Tab aktiv** | schlanker Kaltstart | oft > 1.000 MB |
| **3 Tabs + YouTube 1080p60 HD** | **ca. 800 MB – 1.200 MB** | deutlich höherer Grundbedarf |

---

## Fazit
1. **JavaScript/WASM-Mathematik:** Gleichstand auf höchstem Niveau (V8-Engine).
2. **Reale Web-Anwendungsgeschwindigkeit (Speedometer 3):** MSB schlägt Brave um **+36,5 %**, da der native Rust-Host frei von Extension-Ballast ist.
3. **Hardwarebeschleunigung:** Saubere 144 fps-Synchronisation mit starker 2D-Canvas-Leistung (Score 854.11).
