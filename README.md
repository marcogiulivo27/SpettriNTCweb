# SpettriNTC WEB FREE — Giulivo Ingegneria

Versione gratuita separata da SpettriNTC PRO. Include calcolo NTC 2018 e mappa della pericolosità.

## Pubblicazione su Streamlit Community Cloud

1. Caricare **solo il contenuto di questa cartella** nel repository PUBBLICO. Non caricare relazionepro.py, server licenze, chiavi o dati commerciali.
2. Main file: `app.py` (se il repository mantiene la cartella, `spettrintc_web_free/app.py`).
3. Dipendenze: `requirements.txt`. Tenere la cartella `data/` insieme al codice.
4. Le app gratuite possono andare in sospensione o riavviarsi per limiti del servizio: questo aggiornamento non elimina tali limiti.

## Mappa

- A TR=475 anni: griglia INGV già inclusa nel progetto.
- Agli altri periodi di ritorno: nodi del reticolo NTC trasformati ED50→WGS84 per visualizzazione.
- Sardegna: **Tabella 2 NTC, gruppo G1**. Sagoma schematica volutamente distinta dal raster, perché non esiste un campo spaziale continuo della Tabella 2. La sagoma non deve essere usata come cartografia ufficiale.
- La mappa è orientativa; il calcolo numerico usa la funzione dedicata ai parametri del sito.

## Sicurezza commerciale

Nessun codice di esportazione della relazione Word, modulo licenze o chiave Stripe incluso.

## Esecuzione locale

`pip install -r requirements.txt`

`streamlit run app.py`

## Nota

Per la selezione dei comuni si usa il servizio ISTAT remoto: in sua assenza restano disponibili le coordinate WGS84. Verificare localizzazione e risultati con le prescrizioni vigenti prima dell'impiego professionale.

## Aggiornamento mappa FREE 3.0
Aggiornare **app.py**, **map_view.py** e **data/italy_outline.json** insieme. Versione riconoscibile in intestazione: `FREE MAP 3.0 - 09/10/2026`. Nessuna funzione Word della versione PRO è inclusa.
