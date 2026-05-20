import pathlib

p = pathlib.Path('frontend/src/lib/ui-language.tsx')
content = p.read_bytes()
for enc in ['utf-8-sig', 'utf-8', 'latin-1']:
    try:
        text = content.decode(enc)
        print(f'Decoded with {enc}')
        break
    except:
        pass

replacements = [
    ("appName: 'Conan Exiles Manager'", "appName: 'Maunting Server Panel'"),
    ("Manage your Conan Exiles dedicated server", "Manage your game dedicated server"),
    ("Verwalte deinen Conan Exiles-Dedicated-Server", "Verwalte deinen Dedicated-Server"),
    ("Install the Conan Exiles server first", "Install the game server first"),
    ("Installiere zuerst den Conan Exiles-Server", "Installiere zuerst den Game-Server"),
    ("Manage Conan Exiles server instances", "Manage game server instances"),
    ("Conan Exiles-Server-Instanzen verwalten", "Game-Server-Instanzen verwalten"),
    ("your new Conan Exiles server instance", "your new game server instance"),
    ("deine neue Conan Exiles-Serverinstanz", "deine neue Game-Serverinstanz"),
    ("Conan Exiles candidate volumes", "game server candidate volumes"),
    ("Conan Exiles-Kandidaten-Volumes", "Game-Server-Kandidaten-Volumes"),
    ("important Conan Exiles config files", "important game config files"),
    ("wichtigsten Conan Exiles-Konfigurationsdateien", "wichtigsten Game-Konfigurationsdateien"),
    ("Known Conan Exiles settings", "Known game settings"),
    ("Bekannte Conan Exiles-Einstellungen", "Bekannte Game-Einstellungen"),
    ("edited most often on a Conan Exiles server", "edited most often on a game server"),
    ("meist auf einem Conan Exiles-Server bearbeitet", "meist auf einem Game-Server bearbeitet"),
    ("relevant Conan Exiles server folders", "relevant game server folders"),
    ("relevanten Conan Exiles-Server-Ordnern", "relevanten Game-Server-Ordnern"),
    ("Official Conan Exiles server docs", "Official game server docs"),
]

for old, new in replacements:
    text = text.replace(old, new)

p.write_text(text, encoding='utf-8')
print('Done')
