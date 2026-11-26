# Knall im Fediverse

`worker.js` ist ein minimaler ActivityPub-Dienst: Knall wird als Bot-Account
`@knall@<Adresse des Dienstes>` auffindbar, Leute können folgen, und jeder neue
Artikel erscheint als Post (Schlagzeile, Teaser, Link) in ihrer Timeline.

- Der Dienst nimmt Follows an (signaturgeprüft) und speichert Follower und Posts.
- Zugestellt werden Posts von der Pipeline (`scripts/fediverse.py`, Job `pages`
  in `daily.yml`), nicht vom Dienst — das hält ihn im Gratis-Kontingent.

## Einrichten (einmalig)

1. Schlüsselpaar erzeugen (lokal, nicht ins Repo):
   `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out knall.pem`
   `openssl pkey -in knall.pem -pubout -out knall.pub`
2. Token erzeugen: `openssl rand -hex 32`
3. Worker anlegen, Inhalt von `worker.js` einfügen, dann:
   - Key-Value-Speicher anlegen und als `KNALL` binden
   - Variable `SITE_URL` = öffentliche Adresse der Seite (mit `/` am Ende)
   - Variable `PUBLIC_KEY` = Inhalt von `knall.pub`
   - Secret `PRIVATE_KEY` = Inhalt von `knall.pem`
   - Secret `PUBLISH_TOKEN` = Token aus Schritt 2
4. GitHub-Secrets im Repo:
   - `FEDI_ENDPOINT` = Adresse des Dienstes (ohne `/` am Ende)
   - `FEDI_TOKEN` = Token aus Schritt 2
   - `FEDI_PRIVATE_KEY` = Inhalt von `knall.pem`
5. Prüfen: in Mastodon nach `@knall@<Adresse des Dienstes>` suchen.

Ohne die Secrets überspringt die Pipeline den Schritt still.
