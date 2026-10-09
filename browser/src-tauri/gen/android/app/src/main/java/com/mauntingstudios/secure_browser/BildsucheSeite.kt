package com.mauntingstudios.secure_browser

import org.json.JSONObject

/**
 * Die Seite, die das Foto des Widgets an die Bildsuche schickt
 * (`TabsPlugin.bildsuche`, `Tab.bildsuche`). Google nimmt es als Datei, Bing
 * als Base64-Text (`base64`).
 */
object BildsucheSeite {
  /** `ZIEL` ist JSON, in dem `JSONObject` auch `/` maskiert; ein `</script>` kann darin nicht stehen. */
  fun bauen(url: String, feld: String, base64: Boolean, daten: String): String {
    val ziel = JSONObject().put("url", url).put("feld", feld).put("base64", base64)
    return VORLAGE.replace("ZIEL", ziel.toString()).replace("DATEN", daten)
  }

  private const val VORLAGE = """<!doctype html><meta name="viewport" content="width=device-width"><script>
(() => {
  const z = ZIEL, d = "DATEN"
  const f = document.createElement('form')
  f.method = 'post'
  f.enctype = 'multipart/form-data'
  f.action = z.url
  const e = document.createElement('input')
  e.name = z.feld
  if (z.base64) {
    e.type = 'hidden'
    e.value = d
  } else {
    e.type = 'file'
    const b = atob(d), u = new Uint8Array(b.length)
    for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i)
    const t = new DataTransfer()
    t.items.add(new File([u], 'bild.jpg', { type: 'image/jpeg' }))
    e.files = t.files
  }
  f.append(e)
  document.documentElement.append(f)
  f.submit()
})()
</script>"""
}
