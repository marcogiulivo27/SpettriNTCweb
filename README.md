# Generatore Spettri Sismici – versione web

Conversione Streamlit dell'applicazione desktop Tkinter.

## Avvio locale

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Pubblicazione rapida

1. Crea un repository GitHub (anche privato).
2. Carica `streamlit_app.py`, `spettri_core.py` e `requirements.txt` nella root.
3. Accedi a Streamlit Community Cloud e crea una nuova app dal repository.
4. Imposta `streamlit_app.py` come entrypoint.
5. Una volta online, copia l'URL pubblico della web app.

## Inserimento in WordPress

In una pagina WordPress aggiungi un blocco **HTML personalizzato** e inserisci:

```html
<div style="width:100%;height:1100px;overflow:hidden;border:0;">
  <iframe
    src="https://TUO-URL.streamlit.app/?embed=true"
    style="width:100%;height:100%;border:0;"
    loading="lazy"
    allow="clipboard-read; clipboard-write"
  ></iframe>
</div>
```

In alternativa crea un pulsante WordPress che apre l'URL della web app in una nuova scheda.

## Note

- Il database NTC e le risorse grafiche già incorporate nel sorgente originale restano incorporate in `spettri_core.py`.
- L'elenco dei Comuni viene recuperato dal file ufficiale ISTAT quando necessario.
- La geocodifica utilizza Nominatim/OpenStreetMap.
- L'altitudine viene recuperata tramite Open-Meteo.
- Gli export PNG e CSV avvengono direttamente dal browser.

## Aggiornamento V2

- Grafico degli spettri riallineato allo stile della versione desktop originale: SLO/SLD/SLV/SLC con tratteggi distinti, stato limite evidenziato in rosso, assi e titolo completi.
- Elenco Comuni più robusto su Streamlit Cloud: se il download ISTAT va in timeout viene usato automaticamente un mirror CSV di fallback.


## Aggiornamento V5

- Ricerca Comune immediata: non viene più scaricato l'intero elenco ISTAT prima di usare l'app; si cerca direttamente Comune/Regione/Provincia e si aggiornano le coordinate WGS84.
- Logo Giulivo Ingegneria reso responsive e spostato leggermente più in basso, senza tagli.
- Scheda Pericolosità: vista nazionale dell'Italia + zoom locale del sito affiancati.
- Vista nazionale e zoom locale usano la stessa scala cromatica ag/g per un confronto diretto.
- La vista nazionale usa il reticolo NTC completo; lo zoom locale mantiene la griglia ad alta risoluzione quando disponibile.
