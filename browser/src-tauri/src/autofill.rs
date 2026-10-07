use tauri::command;

/// Liefert das Content-Script zur sicheren Erkennung von Passwörtern und Formularen.
/// Dieses Script wird beim Laden in jeden Web-Tab injiziert.
pub fn autofill_beobachter_script() -> &'static str {
    r#"
    (function() {
      if (window.__MSB_AUTOFILL_INJECTED__) return;
      window.__MSB_AUTOFILL_INJECTED__ = true;

      function findePasswortFelder() {
        const pwInputs = document.querySelectorAll('input[type="password"]');
        if (pwInputs.length === 0) return null;

        // Finde zugehörigen Benutzernamen im selben Formular oder davor
        let usernameInput = null;
        const pwField = pwInputs[0];
        const form = pwField.closest('form');

        if (form) {
          const candidates = form.querySelectorAll('input[type="text"], input[type="email"], input[type="tel"], input:not([type])');
          for (const cand of candidates) {
            const name = (cand.name || cand.id || cand.autocomplete || '').toLowerCase();
            if (name.includes('user') || name.includes('mail') || name.includes('login') || cand.type === 'email') {
              usernameInput = cand;
              break;
            }
          }
          if (!usernameInput && candidates.length > 0) {
            usernameInput = candidates[0];
          }
        }

        return {
          hasPassword: true,
          pwCount: pwInputs.length,
          isRegistration: pwInputs.length >= 2,
          formFound: !!form
        };
      }

      // Reagiere auf Fokus in Passwortfeldern
      document.addEventListener('focusin', function(e) {
        if (e.target && e.target.tagName === 'INPUT' && e.target.type === 'password') {
          const info = findePasswortFelder();
          if (info) {
            window.dispatchEvent(new CustomEvent('msb:password_field_focused', {
              detail: { url: window.location.href, info: info }
            }));
          }
        }
      }, true);

      // Reagiere auf Formularabsendung, um neue Zugangsdaten zu erfassen
      document.addEventListener('submit', function(e) {
        const form = e.target;
        if (!form) return;
        const pw = form.querySelector('input[type="password"]');
        if (!pw || !pw.value) return;

        let username = '';
        const userCand = form.querySelector('input[type="text"], input[type="email"]');
        if (userCand && userCand.value) {
          username = userCand.value;
        }

        window.dispatchEvent(new CustomEvent('msb:credentials_submitted', {
          detail: {
            url: window.location.href,
            domain: window.location.hostname,
            username: username,
            password: pw.value
          }
        }));
      }, true);
    })();
    "#
}

/// Erzeugt ein sicheres Script, um Benutzername und Passwort in ein Formular einzusetzen
#[command]
pub fn generiere_einfuege_script(username: String, passwort: String) -> String {
    // Sichere Escapung für JavaScript-String-Literale
    let safe_user = serde_json::to_string(&username).unwrap_or_else(|_| "\"\"".to_string());
    let safe_pw = serde_json::to_string(&passwort).unwrap_or_else(|_| "\"\"".to_string());

    format!(
        r#"
        (function() {{
          const pwInputs = document.querySelectorAll('input[type="password"]');
          if (pwInputs.length > 0) {{
            const pw = pwInputs[0];
            pw.value = {safe_pw};
            pw.dispatchEvent(new Event('input', {{ bubbles: true }}));
            pw.dispatchEvent(new Event('change', {{ bubbles: true }}));

            const form = pw.closest('form');
            if (form) {{
              const userCand = form.querySelector('input[type="text"], input[type="email"]');
              if (userCand) {{
                userCand.value = {safe_user};
                userCand.dispatchEvent(new Event('input', {{ bubbles: true }}));
                userCand.dispatchEvent(new Event('change', {{ bubbles: true }}));
              }}
            }}
          }}
        }})();
        "#
    )
}
