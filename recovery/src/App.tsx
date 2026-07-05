/**
 * App placeholder for M1 foundation milestone.
 *
 * The full step-flow UI (file picker, password/salt input, decrypt button,
 * file tree preview, DIS badge, i18n) is implemented in the `recovery-basic-ui`
 * feature. This minimal shell renders a German title and a DIS-powered note so
 * the Tauri project boots end-to-end during foundation setup.
 */
function App() {
  return (
    <main className="msm-app">
      <h1>MSM Backup Recovery</h1>
      <p>Entschlüsselungs-Logik bereit. UI folgt in M1.</p>
      <p className="msm-dis-note">Powered by DIS</p>
    </main>
  );
}

export default App;
