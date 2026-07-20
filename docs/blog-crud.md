# Blog-Posts über die Gramps Web REST API (CRUD)

Wie man Blog-Beiträge in **Gramps Web** über die REST API anlegt, liest, ändert und löscht.

> **Verifikationsstatus:** Alle mit ✅ markierten Aussagen wurden am 2026-07-19 **live** gegen eine
> Gramps-Web-Instanz verifiziert (`gramps_webapi` **3.17.0**, Gramps Core **6.0.8**, Rolle OWNER).
> Mit 📖 markierte Aussagen stammen aus Quellcode-/Doku-Recherche und sind nicht live getestet.

---

## 1. Datenmodell: Ein Blog-Post ist keine Note

Die naheliegende Vermutung („Blog = Note vom Typ *Blog*") ist **falsch** — einen `NoteType.BLOG` gibt
es in Gramps nicht. Tatsächlich gilt:

> **Ein Blog-Post ist ein `Source`-Objekt, das den Tag `Blog` trägt.**
> Der Fließtext des Posts steckt in der **ersten Note** aus `source.note_list`.

```
Tag "Blog" ──┐
             ├──►  Source          ← DAS ist der Blog-Post
Note ────────┘      ├── title      → Überschrift des Posts
(Body-Text)         ├── author     → Autor des Posts
                    ├── change     → angezeigtes "Datum" (Unix-ts der letzten Änderung!)
                    ├── note_list[0] → Fließtext
                    └── media_list[0] → Titelbild (weitere Bilder = Galerie)
```

| Blog-Element im Frontend | Herkunft im Datenmodell |
| --- | --- |
| Überschrift | `source.title` |
| Autor | `source.author` |
| Datum | `source.change` — **kein** eigenes Veröffentlichungsdatum, sondern der Änderungs-Zeitstempel 📖 |
| Beitragstext | `source.extended.notes[0].text.string` bzw. `.formatted.html` |
| Titelbild | erstes Element aus `source.media_list` |
| Galerie | alle weiteren `media_list`-Einträge |
| Sichtbarkeit als Blog | Tag mit `name == "Blog"` in `source.tag_list` |

Die Blog-Ansicht des Frontends holt sich die Posts mit exakt dieser Query 📖:

```
GET /api/sources/?rules={"rules":[{"name":"HasTag","values":["Blog"]}]}&sort=-change&profile=all&extend=all
```

Es gibt **keinen** Blog-spezifischen Endpunkt und keinen Blog-Container. Alles läuft über die
generischen CRUD-Endpunkte für `Source`, `Note` und `Tag`.

Offizielle Nutzer-Doku zum Feature: <https://www.grampsweb.org/user-guide/blog/>

---

## 2. Endpunkte und Rechte

Alle Primärobjekte teilen sich dieselbe generische CRUD-Implementierung:

| Methode | Pfad | Zweck | Mindestrolle |
| --- | --- | --- | --- |
| `GET` | `/api/{sources,notes,tags}/` | Liste (Filter/Paging) | beliebiges gültiges JWT |
| `GET` | `/api/{sources,notes,tags}/{handle}` | Einzelobjekt | beliebiges gültiges JWT |
| `POST` | `/api/{sources,notes,tags}/` | anlegen | `CONTRIBUTOR` (2) 📖 |
| `PUT` | `/api/{sources,notes,tags}/{handle}` | ersetzen | `EDITOR` (3) 📖 |
| `DELETE` | `/api/{sources,notes,tags}/{handle}` | löschen | `EDITOR` (3) 📖 |

Rollen-Skala: `GUEST=0, MEMBER=1, CONTRIBUTOR=2, EDITOR=3, OWNER=4, ADMIN=5`.

**Trailing Slash ist bedeutungstragend** ✅:

* Collection **mit** Slash: `/api/sources/` — ohne Slash gibt es `308 Permanent Redirect`.
* Einzelobjekt **ohne** Slash: `/api/sources/{handle}` — mit Slash gibt es `404`.

`PATCH` ist zwar geroutet, aber nicht implementiert → es gibt **kein** Partial-Update 📖.

Kein Versions-Präfix im Pfad: alles liegt direkt unter `/api/...` ✅.

---

## 3. Ablauf: Blog-Post anlegen (Create)

Ein Post braucht **drei** Requests, weil `POST` immer genau ein Objekt anlegt und Referenzen über
**Handles** laufen (nicht über Namen oder Gramps-IDs):

1. `Blog`-Tag beschaffen (einmalig pro Baum) → Handle
2. `POST /api/notes/` mit dem Beitragstext → Note-Handle
3. `POST /api/sources/` mit `note_list=[note_handle]` und `tag_list=[tag_handle]`

### 3.1 Token holen

```bash
BASE="https://your-gramps-instance/api"
TOKEN=$(curl -fsS -X POST "$BASE/token/" \
  -H "Content-Type: application/json" \
  -d '{"username":"USER","password":"PASS"}' | jq -r .access_token)
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
```

### 3.2 Tag `Blog` beschaffen (get-or-create)

Es gibt **keine** Unique-Constraint auf `tag.name` — ein zweiter `POST` mit `{"name":"Blog"}` legt
klaglos einen **zweiten** Tag gleichen Namens an ✅. Also immer erst prüfen:

```bash
TAG_HANDLE=$(curl -fsS "${AUTH[@]}" "$BASE/tags/" | jq -r '.[] | select(.name=="Blog") | .handle')
if [ -z "$TAG_HANDLE" ]; then
  TAG_HANDLE=$(curl -fsS -X POST "$BASE/tags/" "${AUTH[@]}" \
    -d '{"name":"Blog","color":"#EF9A9AEF9A9A","priority":0}' | jq -r '.[0].handle')
fi
```

### 3.3 Note (= Beitragstext) anlegen

Minimaler Payload — `handle`, `gramps_id` und `change` werden **serverseitig** vergeben ✅:

```bash
NOTE_HANDLE=$(curl -fsS -X POST "$BASE/notes/" "${AUTH[@]}" -d '{
  "text": {"string": "Dies ist mein erster Blogpost."},
  "type": "General",
  "format": 0
}' | jq -r '.[0].handle')
```

* `text` **muss** ein Objekt sein. `"text": "…"` als reiner String → `400 Schema validation failed` ✅.
* `type` darf als Klartext-String übergeben werden (`"General"`), der Server konvertiert nach
  `{"_class":"NoteType","value":1,"string":""}` ✅.
* `format`: `0` = *flowed* (Umbrüche werden zu Absätzen), `1` = *formatted* (Whitespace bleibt) 📖.
* Für eine **HTML-Body-Notiz** (`GRAMPS_BLOG_BODY_FORMAT=html`) ist der zuverlässige Payload das
  **vollständige** Typ-Objekt `{"_class": "NoteType", "value": 24, "string": ""}` (`24` =
  `HTML_CODE`, verifiziert gegen Gramps Core `gramps/gen/lib/notetype.py`) 📖. Der Klartext-String
  ist dabei **case-sensitive** (`"Html code"`, nicht `"HTML code"` o. ä.) und fällt bei falscher
  Schreibweise **stillschweigend** auf `CUSTOM` zurück statt zu fehlern — deshalb für HTML-Bodies
  immer das volle Objekt senden, nicht den String.

### 3.4 Source (= der Blog-Post) anlegen

```bash
SOURCE_HANDLE=$(curl -fsS -X POST "$BASE/sources/" "${AUTH[@]}" -d "{
  \"title\": \"Mein erster Blogpost\",
  \"author\": \"Max Mustermann\",
  \"note_list\": [\"$NOTE_HANDLE\"],
  \"tag_list\": [\"$TAG_HANDLE\"]
}" | jq -r '.[0].handle')
```

### 3.5 Response-Envelope

`POST`, `PUT` und `DELETE` antworten mit einem **Transaktions-Array** ✅ — nicht mit dem nackten
Objekt:

```json
[{
  "_class": "Source",
  "handle": "103ac3853ade696ffabcf1a880a7",
  "type": "add",            // "add" | "update" | "delete"
  "old": null,              // Vorzustand (null bei add)
  "new": { "_class": "Source", "gramps_id": "S0000", "title": "…", "change": 1784457443, … }
}]
```

Der Handle liegt also unter `response[0]["handle"]` bzw. `response[0]["new"]["handle"]`.
Statuscodes ✅: `POST` → **201**, `PUT` → **200**, `DELETE` → **200**.

---

## 4. Lesen (Read)

### 4.1 Alle Blog-Posts auflisten

```bash
RULES=$(jq -rn '{rules:[{name:"HasTag",values:["Blog"]}]} | @uri')
curl -fsS "${AUTH[@]}" "$BASE/sources/?rules=$RULES&sort=-change&profile=all&extend=all"
```

`extend=all` hängt die aufgelösten Referenzen unter `extended` an ✅ — vorhandene Schlüssel:
`notes`, `media`, `tags`, `repositories`. Damit steht der Beitragstext direkt in der Listen-Antwort:

```jsonc
{
  "title": "Mein erster Blogpost",
  "author": "Max Mustermann",
  "change": 1784457443,
  "extended": {
    "tags":  [{"name": "Blog", …}],
    "notes": [{"text": {"string": "Dies ist mein erster Blogpost."}, …}]
  }
}
```

Nützliche Query-Parameter 📖: `page`, `pagesize`, `sort`, `keys`, `skipkeys`, `filter`, `rules`,
`extend`, `profile`, `locale`, `backlinks`, `dates`, `gramps_id`, `handles`, `formats`,
`format_options`, `strip`.

Für schlanke Listen lohnt `keys` ✅ — liefert nur die gewünschten Felder:

```bash
curl -fsS "${AUTH[@]}" "$BASE/sources/?rules=$RULES&keys=handle,title,author,change&sort=-change"
# → [{"handle":"…","title":"Verify2","author":"A","change":1784457527}]
```

`sort=-change` = absteigend (Minus-Präfix), also neueste Posts zuerst.

### 4.2 Einzelnen Post lesen

```bash
curl -fsS "${AUTH[@]}" "$BASE/sources/$SOURCE_HANDLE?extend=all&profile=all"
```

### 4.3 Beitragstext als HTML rendern

Der Server rendert StyledText auf Wunsch selbst — genau das nutzt auch das Frontend ✅:

```bash
FO=$(jq -rn '{link_format:"/{obj_class}/{gramps_id}"} | @uri')
curl -fsS "${AUTH[@]}" "$BASE/notes/$NOTE_HANDLE?formats=html&format_options=$FO"
```

```json
{
  "formatted": {"html": "<div>\n<p>\n<strong>Fettdruck hier</strong>. Und ein Link zu <a href=\"https://gramps-project.org\">Gramps</a>.\n</p>\n</div>"},
  "text": {"string": "Fettdruck hier. Und ein Link zu Gramps.",
           "tags": [{"name": "bold", "ranges": [[0,14]], "value": null},
                    {"name": "link", "ranges": [[32,38]], "value": "https://gramps-project.org"}]}
}
```

---

## 5. Ändern (Update)

> ⚠️ **`PUT` ist ein vollständiger Replace, kein Merge.** Felder, die im Body fehlen, werden auf
> ihren Defaultwert zurückgesetzt — live bestätigt ✅:
> `PUT {"handle":"…","title":"x2"}` → danach ist `author=""`, `note_list=[]`, `tag_list=[]`.
> Der Blog-Post verliert damit Tag und Text und verschwindet aus der Blog-Ansicht.

`handle` **muss** im Body stehen; ohne ihn kommt `400 Error while updating object` ✅.

**Sichere Vorgehensweise: Read-Modify-Write.**

```bash
curl -fsS "${AUTH[@]}" "$BASE/sources/$SOURCE_HANDLE" \
  | jq '.title = "Mein erster Blogpost (überarbeitet)"' \
  | curl -fsS -X PUT "$BASE/sources/$SOURCE_HANDLE" "${AUTH[@]}" -d @-
```

Den Beitragstext ändert man analog auf der **Note**, nicht auf der Source:

```bash
curl -fsS "${AUTH[@]}" "$BASE/notes/$NOTE_HANDLE" \
  | jq '.text.string = "Überarbeiteter Text."' \
  | curl -fsS -X PUT "$BASE/notes/$NOTE_HANDLE" "${AUTH[@]}" -d @-
```

`change` wird bei jedem Commit serverseitig neu gesetzt; ein mitgeschickter Wert wird ignoriert 📖.
Da das Frontend `change` als Post-Datum anzeigt, **springt ein Blog-Post durch jede Änderung an die
Spitze der Liste**.

### 5.1 Optimistic Locking über `If-Match` ist praktisch unbrauchbar

`GET` liefert einen `ETag`-Header, und `PUT` wertet `If-Match` aus (`412 Precondition Failed` bei
Mismatch). Beides passt in 3.17.0 aber **nicht zusammen** ✅:

* Der `ETag` aus `GET` ist der SHA-256 über die **Response-Bytes** (live nachgerechnet und
  bestätigt).
* `If-Match` vergleicht gegen einen **anderen** Hash (über die Gramps-Objekt-Serialisierung).
* Ergebnis: Der frisch geholte, unveränderte ETag liefert bei `PUT` **immer `412`** — egal ob mit
  oder ohne umschließende Anführungszeichen.
* Ein `W/`-Präfix (`If-Match: W/"…"`) wird als *weak* geparst und die Prüfung **stillschweigend
  übersprungen** → `200`. Das ist keine Absicherung, sondern eine Umgehung.

**Empfehlung:** kein `If-Match` senden (dann gilt last-write-wins). Wer eine Kollisionserkennung
braucht, vergleicht vor dem Schreiben selbst den `change`-Zeitstempel des Objekts.

---

## 6. Löschen (Delete)

```bash
curl -fsS -X DELETE "$BASE/sources/$SOURCE_HANDLE" "${AUTH[@]}"   # entfernt den Blog-Post
curl -fsS -X DELETE "$BASE/notes/$NOTE_HANDLE"     "${AUTH[@]}"   # entfernt den Beitragstext
```

> ⚠️ Das Löschen der Source lässt die Note **verwaist zurück** ✅ (`GET /api/notes/{handle}` → `200`).
> Wer keine Datenleichen will, löscht die Notes aus `note_list` explizit mit — vorher prüfen, ob die
> Note nicht noch anderweitig referenziert wird (`?backlinks=1`).

Ein Post lässt sich auch **entveröffentlichen**, ohne etwas zu löschen: einfach den `Blog`-Tag aus
`tag_list` entfernen (Read-Modify-Write). Die Source bleibt als normale Quelle erhalten.

Delete-Envelope ✅: `[{"type":"delete","handle":"…","old":{…},"new":null}]`.

---

## 7. Formatierung: StyledText

`note.text` ist ein `StyledText`-Objekt. Formatierung steckt **nicht** als Markdown/HTML im String,
sondern als Liste von `StyledTextTag`-Objekten mit Zeichen-Ranges:

```json
{
  "text": {
    "string": "Fettdruck hier. Und ein Link zu Gramps.",
    "tags": [
      {"name": {"_class": "StyledTextTagType", "value": 0, "string": ""}, "value": null,
       "ranges": [[0, 14]]},
      {"name": {"_class": "StyledTextTagType", "value": 8, "string": ""},
       "value": "https://gramps-project.org", "ranges": [[32, 38]]}
    ]
  },
  "type": "General",
  "format": 0
}
```

`StyledTextTagType`-Werte 📖:
`BOLD=0, ITALIC=1, UNDERLINE=2, FONTFACE=3, FONTSIZE=4, FONTCOLOR=5, HIGHLIGHT=6, SUPERSCRIPT=7,
LINK=8, STRIKETHROUGH=9, SUBSCRIPT=10`.

Bei `LINK` steht die Ziel-URL in `value`; interne Verweise nutzen `gramps://<Class>/handle/<handle>` 📖.

### 7.1 HTML-Body-Notizen (`NoteType.HTML_CODE`)

Getrennt von den StyledText-Tags oben: Für Blog-Post-Bodies im HTML-Modus
(`GRAMPS_BLOG_BODY_FORMAT=html`, s. §3.3) wird keine StyledText-Formatierung verwendet, sondern
die rohe Note vom Typ `HTML_CODE` (`value: 24`) — der Server rendert und saniert deren
`text.string` beim Lesen mit `formats=html` als HTML.

> ⏳ **Offen (Task 12 / Live-Smoke):** Die genaue **Bleach-Allow-List** (erlaubte Tags/Attribute),
> die der Server beim Rendern/Sanitizing eines `HTML_CODE`-Bodies anwendet, ist noch **nicht**
> bestätigt — wird beim Live-Smoke gegen eine echte Instanz verifiziert und hier nachgetragen.

**Akzeptierte Payload-Varianten für `name`** (live durchprobiert ✅):

| Variante | Ergebnis |
| --- | --- |
| `{"_class":"StyledTextTagType","value":0,"string":""}` | ✅ 201 (empfohlen) |
| `"Bold"` (Klartext-String) | ✅ 201 |
| `{"value":0}` — Objekt **ohne** `string` | 💥 **HTTP 500**, Server-Fehler |
| `0` (nackter Integer) | ❌ 400 Schema validation failed |

> 💥 Der 500er ist ein echter Serverfehler, kein Validierungsfehler: ein verschachteltes Typ-Objekt
> **ohne** `string`-Schlüssel bringt die Deserialisierung zum Absturz. Also entweder das vollständige
> Objekt (`_class` + `value` + `string`) oder den Klartext-String senden.

---

## 8. Stolpersteine (Kurzliste)

| Stolperstein | Verhalten |
| --- | --- |
| Trailing Slash | Collection `/sources/` (mit), Einzelobjekt `/sources/{handle}` (ohne) → sonst 308 bzw. 404 ✅ |
| `PUT` = Replace | fehlende Felder werden geleert — immer Read-Modify-Write ✅ |
| `PUT` ohne `handle` | `400 Error while updating object` ✅ |
| Kein `PATCH` | nicht implementiert 📖 |
| `If-Match` | mit echtem ETag immer `412`; `W/`-Präfix umgeht die Prüfung ✅ |
| Referenzen | `note_list`/`tag_list` erwarten **Handles**, keine Namen/Gramps-IDs ✅ |
| Tag-Duplikate | kein Unique-Constraint auf `tag.name` → get-or-create selbst implementieren ✅ |
| Verwaiste Notes | Source-Delete löscht die Note nicht mit ✅ |
| `text` als String | `400 Schema validation failed` ✅ |
| Unvollständiger Typ | `{"value":0}` ohne `string` → **HTTP 500** ✅ |
| Unbekannter Handle | `404 {"code":404,"status":"Not Found"}` ✅ |
| „Datum" des Posts | ist `change` — jede Änderung datiert den Post neu 📖 |
| Suchindex | wird nach Schreibzugriffen automatisch als Task aktualisiert 📖 |

---

## 9. Referenz-Implementierung (Python)

```python
import requests

class GrampsBlog:
    def __init__(self, base_url, username, password):
        self.base = base_url.rstrip("/") + "/api"
        token = requests.post(
            f"{self.base}/token/",
            json={"username": username, "password": password}, timeout=30,
        ).json()["access_token"]
        self.h = {"Authorization": f"Bearer {token}"}

    def _blog_tag_handle(self):
        """get-or-create — die API erzwingt keine eindeutigen Tag-Namen."""
        tags = requests.get(f"{self.base}/tags/", headers=self.h, timeout=30).json()
        for tag in tags:
            if tag["name"] == "Blog":
                return tag["handle"]
        resp = requests.post(f"{self.base}/tags/", headers=self.h, timeout=30,
                             json={"name": "Blog", "color": "#EF9A9AEF9A9A", "priority": 0})
        resp.raise_for_status()
        return resp.json()[0]["handle"]

    def create(self, title, body, author=""):
        note = requests.post(f"{self.base}/notes/", headers=self.h, timeout=30, json={
            "text": {"string": body}, "type": "General", "format": 0,
        })
        note.raise_for_status()
        note_handle = note.json()[0]["handle"]

        source = requests.post(f"{self.base}/sources/", headers=self.h, timeout=30, json={
            "title": title, "author": author,
            "note_list": [note_handle], "tag_list": [self._blog_tag_handle()],
        })
        source.raise_for_status()
        return source.json()[0]["handle"], note_handle

    def list(self, page=1, pagesize=20):
        rules = '{"rules":[{"name":"HasTag","values":["Blog"]}]}'
        resp = requests.get(f"{self.base}/sources/", headers=self.h, timeout=30, params={
            "rules": rules, "sort": "-change", "extend": "all",
            "page": page, "pagesize": pagesize,
        })
        resp.raise_for_status()
        return resp.json()

    def update(self, handle, **fields):
        """Read-Modify-Write — PUT ersetzt das Objekt vollständig."""
        current = requests.get(f"{self.base}/sources/{handle}", headers=self.h, timeout=30)
        current.raise_for_status()
        obj = current.json() | fields
        resp = requests.put(f"{self.base}/sources/{handle}", headers=self.h, json=obj, timeout=30)
        resp.raise_for_status()
        return resp.json()[0]["new"]

    def delete(self, handle, delete_notes=True):
        source = requests.get(f"{self.base}/sources/{handle}", headers=self.h, timeout=30).json()
        requests.delete(f"{self.base}/sources/{handle}", headers=self.h, timeout=30).raise_for_status()
        if delete_notes:  # sonst bleiben die Notes verwaist zurueck
            for note_handle in source.get("note_list", []):
                requests.delete(f"{self.base}/notes/{note_handle}", headers=self.h, timeout=30)
```

---

## 10. Offene Punkte

* Verhalten bei `pagesize` ohne `page` ist bei anderen Endpunkten dieser API bereits als
  Fallstrick aufgefallen (Default `page=0` = „alle") — für `/api/sources/` nicht gegengetestet.
* Media-Upload für Titelbild/Galerie (`/api/media/`, Datei-Upload) ist hier nicht behandelt.
* Ob `If-Match` in neueren Versionen als `gramps_webapi` 3.17.0 repariert ist, wurde nicht geprüft.

## 11. Quellen

* Gramps-Web-Nutzerdoku, Blog-Feature: <https://www.grampsweb.org/user-guide/blog/>
* Frontend: `gramps-project/gramps-web` — `src/views/GrampsjsViewBlog.js`,
  `src/views/GrampsjsViewBlogPost.js`, `src/components/GrampsjsBlogPost.js`,
  `src/components/GrampsjsSource.js`
* Backend: `gramps-project/gramps-web-api` — `gramps_webapi/api/__init__.py` (Routing),
  `gramps_webapi/api/resources/base.py` (generisches CRUD + Query-Parameter),
  `gramps_webapi/api/resources/{notes,sources,tags}.py`, `gramps_webapi/api/html.py`,
  `gramps_webapi/auth/const.py` (Rollen/Rechte)
* Core-Schemata: `gramps-project/gramps` — `gramps/gen/lib/{note,src,tag,styledtext,
  styledtexttag,styledtexttagtype,notetype}.py`, `gramps/gen/filters/rules/source/_hastag.py`
* Live-Verifikation: Instanz mit `gramps_webapi` 3.17.0 / Gramps 6.0.8, 2026-07-19
