import sqlite3
conn = sqlite3.connect('msm.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('Tables:', [r[0] for r in c.fetchall()])
try:
    c.execute("SELECT id, username, is_admin FROM users LIMIT 5")
    print('Users:', c.fetchall())
except Exception as e:
    print('users error:', e)
try:
    c.execute("SELECT key FROM panel_settings")
    print('Settings keys:', [r[0] for r in c.fetchall()])
except Exception as e:
    print('panel_settings error:', e)
conn.close()
