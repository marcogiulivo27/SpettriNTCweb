from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request
from math import log
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np
import pandas as pd
import streamlit as st
from pyproj import Transformer

from spettri_core import (
    CLASS_LABELS,
    CLASS_USE,
    PVR,
    STATE_ORDER,
    MunicipalityDB,
    SeismicHazardDB,
    calculate_spectrum,
    detect_table2_island_group,
    normalize_text,
    resource_path,
)


st.set_page_config(
    page_title="Generatore Spettri Sismici | Giulivo Ingegneria",
    page_icon="📈",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.5rem; padding-bottom: 2.5rem;}
      [data-testid="stMetricValue"] {font-size: 1.25rem;}
      .small-note {color:#666; font-size:.88rem; line-height:1.35;}
      .gi-card {border:1px solid rgba(128,128,128,.25); border-radius:10px; padding:14px 16px; margin-bottom:12px;}
    </style>
    """,
    unsafe_allow_html=True,
)

WGS84_TO_ED50 = Transformer.from_crs("EPSG:4326", "EPSG:4230", always_xy=True)
ED50_TO_WGS84 = Transformer.from_crs("EPSG:4230", "EPSG:4326", always_xy=True)


@st.cache_resource(show_spinner="Caricamento database sismico…")
def get_hazard_db() -> SeismicHazardDB:
    return SeismicHazardDB(resource_path("spettri2008.csv"), resource_path("italia_ag_002.npz"))


@st.cache_resource(show_spinner="Caricamento elenco ufficiale dei Comuni…")
def get_municipality_db() -> MunicipalityDB:
    return MunicipalityDB()


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_municipality(comune: str, provincia: str, regione: str):
    queries = [
        f"{comune}, {provincia}, {regione}, Italia",
        f"{comune}, {regione}, Italia",
    ]
    for q in queries:
        params = urllib.parse.urlencode({
            "q": q,
            "format": "jsonv2",
            "limit": 5,
            "countrycodes": "it",
            "addressdetails": 1,
        })
        req = urllib.request.Request(
            f"https://nominatim.openstreetmap.org/search?{params}",
            headers={"User-Agent": "GiulivoIngegneria-GeneratoreSpettri-Web/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue

        nreg = normalize_text(regione)
        ncom = normalize_text(comune)
        best = None
        for item in data:
            addr = item.get("address") or {}
            display = normalize_text(item.get("display_name", ""))
            state = normalize_text(addr.get("state", ""))
            country = normalize_text(addr.get("country_code", ""))
            if country and country != "it":
                continue
            score = 0
            if nreg and (nreg in state or nreg in display):
                score += 3
            if ncom and ncom in display:
                score += 4
            typ = normalize_text(item.get("type", ""))
            if typ in {"administrative", "city", "town", "village", "municipality"}:
                score += 2
            if best is None or score > best[0]:
                best = (score, float(item["lat"]), float(item["lon"]))
        if best and best[0] >= 5:
            return best[1], best[2]
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_altitude(lat: float, lon: float):
    try:
        url = "https://api.open-meteo.com/v1/elevation?" + urllib.parse.urlencode(
            {"latitude": lat, "longitude": lon}
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "GiulivoIngegneria-GeneratoreSpettri-Web/1.0"},
        )
        with urllib.request.urlopen(req, timeout=7) as response:
            data = json.loads(response.read().decode("utf-8"))
        value = data.get("elevation")
        if isinstance(value, list) and value:
            return float(value[0])
        if value is not None:
            return float(value)
    except Exception:
        pass
    return None


def compute_return_periods(vn: float, class_code: str):
    if vn <= 0:
        raise ValueError("La vita nominale VN deve essere > 0.")
    cu = CLASS_USE[class_code]
    vr_raw = vn * cu
    vr = max(35.0, vr_raw)
    out = {}
    for st_lim in STATE_ORDER:
        tr_raw = -vr / log(1.0 - PVR[st_lim])
        tr = int(round(tr_raw))
        out[st_lim] = max(30, min(2475, tr))
    return cu, vr_raw, vr, out


def build_hazard_dataframe(hazard_db, lat_wgs, lon_wgs, region, municipality, vn, class_code):
    lon_ed50, lat_ed50 = WGS84_TO_ED50.transform(lon_wgs, lat_wgs)
    cu, vr_raw, vr, tr_by_state = compute_return_periods(vn, class_code)
    table2_group = detect_table2_island_group(lat_wgs, lon_wgs, region, municipality)

    rows = []
    all_nodes = set()
    source_label = ""
    for st_lim in STATE_ORDER:
        p = hazard_db.site_parameters(
            lat_ed50,
            lon_ed50,
            tr_by_state[st_lim],
            table2_group=table2_group,
        )
        all_nodes.update(p["node_ids"])
        source_label = p.get("source", "")
        rows.append({
            "SL": st_lim,
            "Tr [anni]": float(tr_by_state[st_lim]),
            "ag/g": float(p["ag_g"]),
            "F0": float(p["F0"]),
            "Tc* [s]": float(p["Tc_star"]),
        })

    meta = {
        "lat_ed50": lat_ed50,
        "lon_ed50": lon_ed50,
        "cu": cu,
        "vr_raw": vr_raw,
        "vr": vr,
        "source": source_label,
        "nodes": sorted(all_nodes),
        "table2_group": table2_group,
    }
    return pd.DataFrame(rows), meta


def make_spectra(df: pd.DataFrame, soil, topo, xi, q, t_max):
    results = {}
    for _, row in df.iterrows():
        st_lim = str(row["SL"])
        inp = {
            "Tr": float(row["Tr [anni]"]),
            "ag_g": float(row["ag/g"]),
            "F0": float(row["F0"]),
            "Tc_star": float(row["Tc* [s]"]),
        }
        results[st_lim] = {
            "input": inp,
            "spectrum": calculate_spectrum(
                inp["ag_g"],
                inp["F0"],
                inp["Tc_star"],
                soil=soil,
                topo=topo,
                xi=xi,
                q=q,
                t_max=t_max,
            ),
        }
    return results


def results_table(results):
    rows = []
    for st_lim in STATE_ORDER:
        r = results[st_lim]["spectrum"]
        rows.append({
            "SL": st_lim,
            "Ss": r["Ss"],
            "Cc": r["Cc"],
            "S": r["S"],
            "TB [s]": r["TB"],
            "TC [s]": r["TC"],
            "TD [s]": r["TD"],
            "Se(0) [g]": r["Se0_g"],
            "Plateau [g]": r["plateau_el_g"],
        })
    return pd.DataFrame(rows)


def spectra_figure(results, plot_type, highlight):
    fig, ax = plt.subplots(figsize=(10, 6))
    mapping = {
        "Elastico": ("Se_g", "Accelerazione [g]", "Spettro elastico"),
        "Progetto": ("Sa_g", "Accelerazione [g]", "Spettro di progetto"),
        "Spostamento elastico": ("SDe_mm", "Spostamento [mm]", "Spettro di spostamento elastico"),
        "Spostamento progetto": ("Sd_mm", "Spostamento [mm]", "Spettro di spostamento di progetto"),
    }
    field, ylabel, title = mapping[plot_type]
    for st_lim in STATE_ORDER:
        r = results[st_lim]["spectrum"]
        lw = 3.0 if st_lim == highlight else 1.6
        alpha = 1.0 if st_lim == highlight else 0.72
        ax.plot(r["T"], r[field], label=st_lim, linewidth=lw, alpha=alpha)
    ax.set_xlabel("Periodo T [s]")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=4)
    fig.tight_layout()
    return fig


def hazard_figure(hazard_db, meta, state, tr, radius_km, site_name):
    lon, lat, ag, source = hazard_db.hazard_points(
        meta["lat_ed50"],
        meta["lon_ed50"],
        tr,
        radius_km=radius_km,
        table2_group=meta["table2_group"],
    )
    fig, ax = plt.subplots(figsize=(8.5, 6.2))
    if len(ag) < 4:
        ax.text(0.5, 0.5, "Dati insufficienti nell'area selezionata", ha="center", va="center")
        ax.set_axis_off()
    elif np.nanmax(ag) - np.nanmin(ag) < 1e-10:
        ax.scatter(lon, lat, c=ag, s=26, cmap="turbo", vmin=ag[0] * 0.95, vmax=ag[0] * 1.05)
        ax.scatter([meta["lon_ed50"]], [meta["lat_ed50"]], marker="*", s=170, c="black", zorder=10)
        ax.text(0.02, 0.02, f"ag/g = {ag[0]:.4f}", transform=ax.transAxes)
    else:
        triang = mtri.Triangulation(lon, lat)
        levels = np.linspace(float(np.nanmin(ag)), float(np.nanmax(ag)), 16)
        contour = ax.tricontourf(triang, ag, levels=levels, cmap="turbo")
        ax.scatter([meta["lon_ed50"]], [meta["lat_ed50"]], marker="*", s=170, c="black", zorder=10)
        cbar = fig.colorbar(contour, ax=ax, pad=0.02)
        cbar.set_label("ag / g")
    ax.set_xlabel("Longitudine ED50")
    ax.set_ylabel("Latitudine ED50")
    ax.grid(True, alpha=0.2)
    ax.set_title(f"Pericolosità sismica – {site_name} | {state} | TR = {tr:.0f} anni")
    fig.tight_layout()
    return fig, source


def csv_bytes(results):
    rows = []
    for st_lim in STATE_ORDER:
        p = results[st_lim]["input"]
        r = results[st_lim]["spectrum"]
        rows.append({
            "SL": st_lim,
            "Tr [anni]": p["Tr"],
            "ag/g": p["ag_g"],
            "F0": p["F0"],
            "Tc* [s]": p["Tc_star"],
            "Ss": r["Ss"],
            "Cc": r["Cc"],
            "St": r["St"],
            "S": r["S"],
            "TB [s]": r["TB"],
            "TC [s]": r["TC"],
            "TD [s]": r["TD"],
            "Se(0) [g]": r["Se0_g"],
            "Plateau elastico [g]": r["plateau_el_g"],
        })
    return pd.DataFrame(rows).to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig")


# ---------------------------
# Stato iniziale
# ---------------------------
for key, value in {
    "lat_wgs": 40.91684430,
    "lon_wgs": 14.78984040,
    "lat_wgs_input": 40.91684430,
    "lon_wgs_input": 14.78984040,
    "site_name": "Avellino",
    "site_name_input": "Avellino",
    "region_name": "Campania",
    "municipality_name": "Avellino",
}.items():
    st.session_state.setdefault(key, value)

try:
    hazard_db = get_hazard_db()
except Exception as exc:
    st.error(f"Impossibile caricare il database sismico: {exc}")
    st.stop()

logo_path = resource_path("logo.png")
head1, head2 = st.columns([4, 1])
with head1:
    st.title("Generatore di spettri di risposta sismica")
    st.caption("SLO · SLD · SLV · SLC — versione web")
with head2:
    if Path(logo_path).exists():
        st.image(str(logo_path), width=220)

left, right = st.columns([0.95, 1.55], gap="large")

with left:
    st.subheader("1. Sito di riferimento")

    use_municipality = st.toggle("Seleziona da Comune", value=False)
    selected_region = st.session_state.get("region_name", "")
    selected_municipality = st.session_state.get("municipality_name", "")

    if use_municipality:
        try:
            municipality_db = get_municipality_db()
            regions = municipality_db.regions
            region_idx = regions.index(selected_region) if selected_region in regions else 0
            region = st.selectbox("Regione", regions, index=region_idx)
            provinces = municipality_db.provinces(region)
            province = st.selectbox("Provincia", provinces)
            municipalities = municipality_db.municipalities(region, province)
            municipality = st.selectbox("Comune", municipalities)

            if st.button("Trova coordinate del Comune", use_container_width=True):
                with st.spinner(f"Ricerca coordinate di {municipality}…"):
                    coords = geocode_municipality(municipality, province, region)
                if coords is None:
                    st.error("Coordinate non risolte in modo affidabile. Inserisci WGS84 manualmente.")
                else:
                    st.session_state["lat_wgs"], st.session_state["lon_wgs"] = coords
                    st.session_state["lat_wgs_input"], st.session_state["lon_wgs_input"] = coords
                    st.session_state["site_name"] = municipality
                    st.session_state["site_name_input"] = municipality
                    st.session_state["region_name"] = region
                    st.session_state["municipality_name"] = municipality
                    st.rerun()
        except Exception as exc:
            st.warning(f"Elenco Comuni non disponibile: {exc}. Puoi usare le coordinate manuali.")

    c1, c2 = st.columns(2)
    with c1:
        lat_wgs = st.number_input(
            "Latitudine WGS84",
            min_value=34.0,
            max_value=48.5,
            value=float(st.session_state["lat_wgs_input"]),
            format="%.8f",
            key="lat_wgs_input",
        )
    with c2:
        lon_wgs = st.number_input(
            "Longitudine WGS84",
            min_value=5.0,
            max_value=20.5,
            value=float(st.session_state["lon_wgs_input"]),
            format="%.8f",
            key="lon_wgs_input",
        )

    # Mantiene coordinate correnti senza modificare lo stato dei widget già creati.
    st.session_state["lat_wgs"] = float(lat_wgs)
    st.session_state["lon_wgs"] = float(lon_wgs)

    site_name = st.text_input("Nome sito", key="site_name_input")
    st.session_state["site_name"] = site_name

    lon_ed50, lat_ed50 = WGS84_TO_ED50.transform(lon_wgs, lat_wgs)
    altitude = fetch_altitude(lat_wgs, lon_wgs)

    m1, m2, m3 = st.columns(3)
    m1.metric("Lat. ED50", f"{lat_ed50:.6f}")
    m2.metric("Lon. ED50", f"{lon_ed50:.6f}")
    m3.metric("Quota", "—" if altitude is None else f"{altitude:.0f} m")

    class_code = st.selectbox(
        "Classe d'uso",
        list(CLASS_USE.keys()),
        index=1,
        format_func=lambda x: CLASS_LABELS[x],
    )
    vn = st.number_input("Vita nominale VN [anni]", min_value=1.0, value=50.0, step=1.0)

    st.subheader("2. Parametri generali")
    g1, g2 = st.columns(2)
    with g1:
        soil = st.selectbox("Sottosuolo", ["A", "B", "C", "D", "E"], index=2)
        xi = st.number_input("Smorzamento ξ [%]", min_value=0.1, value=5.0, step=0.5)
        t_max = st.number_input("T max [s]", min_value=0.1, value=3.0, step=0.5)
    with g2:
        topo = st.selectbox("Topografia", ["T1", "T2", "T3", "T4"], index=0)
        q = st.number_input("Fattore q", min_value=1.0, value=1.0, step=0.1)
        highlight = st.selectbox("Evidenzia", STATE_ORDER, index=2)

    plot_type = st.selectbox(
        "Grafico",
        ["Elastico", "Progetto", "Spostamento elastico", "Spostamento progetto"],
    )

    try:
        hazard_auto, meta = build_hazard_dataframe(
            hazard_db,
            lat_wgs,
            lon_wgs,
            st.session_state.get("region_name", ""),
            st.session_state.get("municipality_name", ""),
            vn,
            class_code,
        )
    except Exception as exc:
        st.error(f"Errore nei parametri di pericolosità: {exc}")
        st.stop()

    st.subheader("3. Parametri di pericolosità")
    st.caption("I valori sono calcolati automaticamente, ma puoi modificarli prima del calcolo.")

    # Cambia la chiave quando cambia realmente il sito/VR per non trascinare edit di un sito precedente.
    editor_key = (
        f"hazard_{lat_wgs:.5f}_{lon_wgs:.5f}_{class_code}_{vn:.2f}"
        .replace(".", "_")
        .replace("-", "m")
    )
    hazard_df = st.data_editor(
        hazard_auto,
        key=editor_key,
        hide_index=True,
        disabled=["SL"],
        use_container_width=True,
        column_config={
            "Tr [anni]": st.column_config.NumberColumn(format="%.0f"),
            "ag/g": st.column_config.NumberColumn(format="%.4f"),
            "F0": st.column_config.NumberColumn(format="%.3f"),
            "Tc* [s]": st.column_config.NumberColumn(format="%.3f"),
        },
    )

    vr_note = ""
    if meta["vr_raw"] < 35.0:
        vr_note = f" · VN·CU={meta['vr_raw']:.1f}, assunto VR=35 anni"
    nodes_note = "" if meta["table2_group"] else f" · nodi: {', '.join(map(str, meta['nodes']))}"
    st.markdown(
        f"<div class='small-note'><b>{site_name}</b> · Classe {class_code} · CU={meta['cu']:.2f} · "
        f"VR={meta['vr']:.0f} anni{vr_note}<br>{meta['source']}{nodes_note}</div>",
        unsafe_allow_html=True,
    )

with right:
    tabs = st.tabs(["Spettri", "Mappa sito", "Pericolosità"])

    try:
        results = make_spectra(hazard_df, soil, topo, xi, q, t_max)
    except Exception as exc:
        st.error(f"Errore nel calcolo degli spettri: {exc}")
        st.stop()

    with tabs[0]:
        fig = spectra_figure(results, plot_type, highlight)
        st.pyplot(fig, use_container_width=True)

        table = results_table(results)
        st.dataframe(
            table.style.format({
                "Ss": "{:.3f}", "Cc": "{:.3f}", "S": "{:.3f}",
                "TB [s]": "{:.3f}", "TC [s]": "{:.3f}", "TD [s]": "{:.3f}",
                "Se(0) [g]": "{:.4f}", "Plateau [g]": "{:.4f}",
            }),
            hide_index=True,
            use_container_width=True,
        )

        png = io.BytesIO()
        fig.savefig(png, format="png", dpi=300, bbox_inches="tight")
        d1, d2 = st.columns(2)
        d1.download_button(
            "Scarica grafico PNG",
            data=png.getvalue(),
            file_name="spettri_sismici.png",
            mime="image/png",
            use_container_width=True,
        )
        d2.download_button(
            "Scarica risultati CSV",
            data=csv_bytes(results),
            file_name="parametri_spettrali.csv",
            mime="text/csv",
            use_container_width=True,
        )
        plt.close(fig)

        st.info(
            "Per T = 0 lo spettro elastico vale Se(0)/g = ag · S. "
            "Per un suolo con S > 1 la curva non parte quindi da ag/g."
        )

    with tabs[1]:
        map_df = pd.DataFrame({"lat": [lat_wgs], "lon": [lon_wgs]})
        st.map(map_df, latitude="lat", longitude="lon", zoom=12, use_container_width=True)
        encoded = urllib.parse.quote(f"{lat_wgs:.8f},{lon_wgs:.8f}")
        st.markdown(
            f"[Apri in Google Maps](https://www.google.com/maps/search/?api=1&query={encoded}) · "
            f"[Apri in Google Earth](https://earth.google.com/web/search/{encoded})"
        )
        st.caption("La posizione esatta dell'opera può essere affinata modificando le coordinate WGS84.")

    with tabs[2]:
        h1, h2 = st.columns(2)
        with h1:
            hazard_state = st.selectbox("Stato limite mappa", STATE_ORDER, index=2)
        with h2:
            radius = st.number_input("Raggio [km]", min_value=20.0, max_value=250.0, value=90.0, step=10.0)
        tr_for_map = float(hazard_df.loc[hazard_df["SL"] == hazard_state, "Tr [anni]"].iloc[0])
        try:
            hfig, hsource = hazard_figure(hazard_db, meta, hazard_state, tr_for_map, radius, site_name)
            st.pyplot(hfig, use_container_width=True)
            st.caption(hsource)
            plt.close(hfig)
        except Exception as exc:
            st.warning(f"Mappa di pericolosità non disponibile: {exc}")

st.divider()
st.caption(
    "Strumento di supporto al calcolo. Prima dell'impiego professionale verificare sempre i dati di input, "
    "le ipotesi adottate e la coerenza con la normativa applicabile al caso specifico."
)
