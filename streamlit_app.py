from __future__ import annotations

import io
import json
import base64
import urllib.parse
import urllib.request
from math import log
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.tri as mtri
from matplotlib.patches import Circle
from matplotlib.colors import ListedColormap, BoundaryNorm
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from pyproj import Transformer

from spettri_core import (
    CLASS_LABELS,
    CLASS_USE,
    PVR,
    STATE_ORDER,
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

ITALIAN_REGIONS = [
    "", "Abruzzo", "Basilicata", "Calabria", "Campania", "Emilia-Romagna",
    "Friuli-Venezia Giulia", "Lazio", "Liguria", "Lombardia", "Marche",
    "Molise", "Piemonte", "Puglia", "Sardegna", "Sicilia", "Toscana",
    "Trentino-Alto Adige", "Umbria", "Valle d'Aosta", "Veneto",
]


@st.cache_resource(show_spinner="Caricamento database sismico…")
def get_hazard_db() -> SeismicHazardDB:
    return SeismicHazardDB(resource_path("spettri2008.csv"), resource_path("italia_ag_002.npz"))


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_municipality(comune: str, provincia: str = "", regione: str = ""):
    comune = (comune or "").strip()
    provincia = (provincia or "").strip()
    regione = (regione or "").strip()
    if not comune:
        return None

    ncom = normalize_text(comune)
    nprov = normalize_text(provincia)
    nreg = normalize_text(regione)

    def _read_json(url: str, headers: dict | None = None, timeout: int = 10):
        req = urllib.request.Request(
            url,
            headers=headers or {"User-Agent": "GiulivoIngegneria-GeneratoreSpettri-Web/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _score(label: str, fields: list[str], typ: str = ""):
        label_n = normalize_text(label)
        fields_n = [normalize_text(x) for x in fields if x]
        score = 0
        if ncom:
            if any(x == ncom for x in fields_n):
                score += 12
            elif any(ncom in x or x in ncom for x in fields_n):
                score += 8
            elif ncom in label_n:
                score += 5
        if nprov:
            if any(nprov == x for x in fields_n):
                score += 4
            elif any(nprov in x or x in nprov for x in fields_n) or nprov in label_n:
                score += 2
        if nreg:
            if any(nreg == x for x in fields_n):
                score += 5
            elif any(nreg in x or x in nreg for x in fields_n) or nreg in label_n:
                score += 3
        t = normalize_text(typ)
        if t in {"administrative", "city", "town", "village", "municipality", "hamlet"}:
            score += 2
        return score

    candidates = []
    queries = []
    variants = [
        ", ".join([x for x in [comune, provincia, regione, "Italia"] if x]),
        ", ".join([x for x in [comune, regione, "Italia"] if x]),
        ", ".join([x for x in [comune, provincia, "Italia"] if x]),
        f"Comune di {comune}, {regione}, Italia" if regione else f"Comune di {comune}, Italia",
        f"{comune}, Italia",
    ]
    for q in variants:
        if q and q not in queries:
            queries.append(q)

    for q in queries:
        params = urllib.parse.urlencode({
            "q": q,
            "format": "jsonv2",
            "limit": 10,
            "countrycodes": "it",
            "addressdetails": 1,
            "namedetails": 1,
            "accept-language": "it",
        })
        try:
            data = _read_json(
                f"https://nominatim.openstreetmap.org/search?{params}",
                timeout=12,
            )
        except Exception:
            data = []
        for item in data or []:
            addr = item.get("address") or {}
            fields = [
                addr.get("city"), addr.get("town"), addr.get("village"), addr.get("municipality"),
                addr.get("county"), addr.get("province"), addr.get("state"),
                item.get("display_name"), item.get("name"),
            ]
            score = _score(item.get("display_name", ""), fields, item.get("type", ""))
            if score >= 4:
                candidates.append((score, float(item["lat"]), float(item["lon"])))

    # Fallback Photon / Komoot, spesso più tollerante con i toponimi italiani
    if not candidates:
        for q in queries[:3]:
            params = urllib.parse.urlencode({"q": q, "limit": 8, "lang": "it"})
            try:
                data = _read_json(f"https://photon.komoot.io/api/?{params}", timeout=10)
            except Exception:
                data = {}
            for feat in (data.get("features") or []):
                props = feat.get("properties") or {}
                coords = feat.get("geometry", {}).get("coordinates", [None, None])
                if coords[0] is None or coords[1] is None:
                    continue
                fields = [
                    props.get("name"), props.get("city"), props.get("district"), props.get("county"),
                    props.get("state"), props.get("country"),
                ]
                label = ", ".join([x for x in [props.get("name"), props.get("city"), props.get("state"), props.get("country")] if x])
                score = _score(label, fields, props.get("osm_value", ""))
                if score >= 4:
                    candidates.append((score, float(coords[1]), float(coords[0])))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][2]

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


def spectra_figure(results, plot_type, highlight, site_name, soil, topo, xi, q):
    """Grafico con lo stesso stile della versione desktop originale."""
    fig, ax = plt.subplots(figsize=(8.8, 5.4), dpi=100)

    mapping = {
        "Elastico": ("Se_g", r"Accelerazione spettrale $S_e(T)$ / g"),
        "Progetto": ("Sa_g", r"Accelerazione di progetto $S_a(T)$ / g"),
        "Spostamento elastico": ("SDe_mm", r"Spostamento spettrale elastico [mm]"),
        "Spostamento progetto": ("Sd_mm", r"Spostamento spettrale di progetto [mm]"),
    }
    field, ylabel = mapping[plot_type]

    styles = {
        "SLO": ":",
        "SLD": "--",
        "SLV": "-",
        "SLC": "-.",
    }

    for st_lim in STATE_ORDER:
        r = results[st_lim]["spectrum"]
        is_highlight = st_lim == highlight
        ax.plot(
            r["T"],
            r[field],
            linestyle=styles[st_lim],
            linewidth=2.8 if is_highlight else 1.5,
            color="red" if is_highlight else "black",
            label=st_lim,
        )

    ax.set_xlabel("Periodo T [s]", fontweight="bold")
    ax.set_ylabel(ylabel, fontweight="bold")
    ax.set_xlim(left=0.0, right=float(results[STATE_ORDER[0]]["spectrum"]["T"][-1]))
    ax.set_ylim(bottom=0.0)
    ax.margins(x=0.0, y=0.0)
    ax.grid(True, alpha=0.30)
    ax.legend(ncol=4, frameon=False)

    name = (site_name or "Sito personalizzato").strip()
    ax.set_title(
        f"{plot_type} - {name} | Suolo {soil} | Topografia {topo} | ξ={xi:g}% | q={q:g}",
        fontweight="bold",
    )

    fig.tight_layout()
    return fig

def _masked_triangulation(lon, lat, max_edge_deg=0.95):
    triang = mtri.Triangulation(lon, lat)
    tri = triang.triangles
    x = np.asarray(lon)
    y = np.asarray(lat)
    e01 = np.hypot(x[tri[:, 0]] - x[tri[:, 1]], y[tri[:, 0]] - y[tri[:, 1]])
    e12 = np.hypot(x[tri[:, 1]] - x[tri[:, 2]], y[tri[:, 1]] - y[tri[:, 2]])
    e20 = np.hypot(x[tri[:, 2]] - x[tri[:, 0]], y[tri[:, 2]] - y[tri[:, 0]])
    triang.set_mask(np.maximum.reduce([e01, e12, e20]) > max_edge_deg)
    return triang


def _apply_tnr_style():
    return plt.rc_context({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
    })


def _apply_tnr_style():
    return plt.rc_context({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
    })


def _apply_tnr_style():
    return plt.rc_context({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "axes.titlesize": 14,
        "axes.labelsize": 11,
        "xtick.labelsize": 9.5,
        "ytick.labelsize": 9.5,
    })


def combined_hazard_figure(hazard_db, meta, state, tr, radius_km, site_name):
    n_lon, n_lat, n_ag, nsource = hazard_db.national_hazard_points(tr)
    l_lon, l_lat, l_ag, lsource = hazard_db.hazard_points(
        meta["lat_ed50"],
        meta["lon_ed50"],
        tr,
        radius_km=radius_km,
        table2_group=meta["table2_group"],
    )

    # Palette ispirata alle mappe INGV
    levels = np.array([0.025, 0.050, 0.075, 0.100, 0.125, 0.150, 0.175, 0.200, 0.225, 0.250, 0.275, 0.300])
    colors = [
        "#d7d7d7",  # grigio
        "#c7e6f2",  # azzurro chiaro
        "#8fd0e8",  # azzurro
        "#72c768",  # verde
        "#b7df78",  # verde chiaro
        "#f0dd57",  # giallo
        "#f5bf4b",  # giallo-arancio
        "#ef8a34",  # arancio
        "#ea4d2e",  # rosso-arancio
        "#d61f27",  # rosso
        "#8d61c2",  # viola
    ]
    cmap = ListedColormap(colors)
    cmap.set_under("#efefef")
    cmap.set_over("#7b49b2")
    norm = BoundaryNorm(levels, cmap.N)

    with _apply_tnr_style():
        fig = plt.figure(figsize=(11.0, 8.6), facecolor="white")
        ax = fig.add_axes([0.08, 0.12, 0.64, 0.80])
        ax.set_facecolor("#fbfbf8")

        triang = _masked_triangulation(n_lon, n_lat, max_edge_deg=0.85)
        cf = ax.tricontourf(triang, n_ag, levels=levels, cmap=cmap, norm=norm, extend="both")
        try:
            ax.tricontour(triang, n_ag, levels=levels, colors="#555555", linewidths=0.45, alpha=0.55)
        except Exception:
            pass

        site_x = float(meta["lon_ed50"])
        site_y = float(meta["lat_ed50"])
        dlat = float(radius_km) / 111.0
        dlon = float(radius_km) / max(20.0, 111.0 * np.cos(np.radians(site_y)))
        rdeg = max(dlat, dlon) * 0.70

        ax.scatter([site_x], [site_y], marker="*", s=190, c="white", edgecolors="black", linewidths=1.0, zorder=15)
        ax.add_patch(Circle((site_x, site_y), radius=rdeg, fill=False, ec="#222222", lw=1.2, ls="--", zorder=12))
        ax.text(site_x + 0.10, site_y + 0.12, (site_name or "Sito"), fontsize=10.0, fontweight="bold", color="black", zorder=16)

        ax.set_xlim(6.2, 19.2)
        ax.set_ylim(35.0, 47.7)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Longitudine ED50 [°]", fontweight="bold", labelpad=6)
        ax.set_ylabel("Latitudine ED50 [°]", fontweight="bold", labelpad=8)
        ax.grid(True, color="#7f7f7f", alpha=0.12, linewidth=0.35)
        ax.set_title(
            f"Pericolosità sismica in Italia - {state}  |  TR = {tr:.0f} anni",
            fontweight="bold",
            pad=12,
        )

        # Zoom locale: inset pulito, senza etichette assi che interferiscono
        axins = inset_axes(ax, width="34%", height="36%", loc="lower left", borderpad=1.2)
        axins.set_facecolor("white")
        if len(l_ag) >= 4 and np.nanmax(l_ag) - np.nanmin(l_ag) > 1e-10:
            local_triang = mtri.Triangulation(l_lon, l_lat)
            axins.tricontourf(local_triang, l_ag, levels=levels, cmap=cmap, norm=norm, extend="both")
            try:
                axins.tricontour(local_triang, l_ag, levels=levels, colors="#555555", linewidths=0.35, alpha=0.50)
            except Exception:
                pass
        else:
            axins.scatter(l_lon, l_lat, c=l_ag, cmap=cmap, norm=norm, s=26)
        axins.scatter([site_x], [site_y], marker="*", s=110, c="white", edgecolors="black", linewidths=0.9, zorder=10)
        axins.set_xlim(site_x - dlon, site_x + dlon)
        axins.set_ylim(site_y - dlat, site_y + dlat)
        axins.set_title("Zoom locale", fontsize=10.5, fontweight="bold", pad=4)
        axins.grid(True, color="#7f7f7f", alpha=0.12, linewidth=0.30)
        axins.set_xticks([])
        axins.set_yticks([])
        axins.tick_params(bottom=False, left=False, labelbottom=False, labelleft=False)
        for spine in axins.spines.values():
            spine.set_linewidth(1.1)
            spine.set_color("#222222")

        cax = fig.add_axes([0.77, 0.24, 0.024, 0.56])
        cb = fig.colorbar(cf, cax=cax, ticks=levels[:-1])
        cb.set_label("ag / g", fontweight="bold")
        cb.ax.tick_params(labelsize=9)

        # Nessun box testuale aggiuntivo: la sola colorbar resta pulita e leggibile
        return fig, nsource, lsource


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
head1, head2 = st.columns([3.2, 1.8], gap="large", vertical_alignment="center")
with head1:
    st.title("Generatore di spettri di risposta sismica")
    st.caption("SLO · SLD · SLV · SLC — versione web")
with head2:
    if Path(logo_path).exists():
        encoded_logo = base64.b64encode(Path(logo_path).read_bytes()).decode("ascii")
        html_logo = (
            '<div style="padding-top:14px;padding-bottom:6px;display:flex;justify-content:center;align-items:flex-start;width:100%;">'
            f'<img src="data:image/png;base64,{encoded_logo}" '
            'style="display:block;width:min(100%,340px);max-height:120px;height:auto;object-fit:contain;object-position:center top;" '
            'alt="Giulivo Ingegneria">'
            '</div>'
        )
        st.markdown(html_logo, unsafe_allow_html=True)

left, right = st.columns([0.95, 1.55], gap="large")

with left:
    st.subheader("1. Sito di riferimento")

    use_municipality = st.toggle("Seleziona da Comune", value=False)
    selected_region = st.session_state.get("region_name", "")
    selected_municipality = st.session_state.get("municipality_name", "")

    if use_municipality:
        st.caption("Ricerca diretta del Comune: non è più necessario scaricare l'intero elenco ISTAT prima di usare l'app.")
        comune_q = st.text_input("Comune", value=st.session_state.get("municipality_name", ""), placeholder="es. Napoli")
        qm1, qm2 = st.columns(2)
        with qm1:
            reg_default = st.session_state.get("region_name", "")
            reg_idx = ITALIAN_REGIONS.index(reg_default) if reg_default in ITALIAN_REGIONS else 0
            regione_q = st.selectbox("Regione (facoltativa)", ITALIAN_REGIONS, index=reg_idx)
        with qm2:
            provincia_q = st.text_input("Provincia (facoltativa)", placeholder="es. Napoli")

        if st.button("Trova Comune", use_container_width=True):
            if not comune_q.strip():
                st.warning("Inserisci il nome del Comune.")
            else:
                with st.spinner(f"Ricerca coordinate di {comune_q.strip()}…"):
                    coords = geocode_municipality(comune_q.strip(), provincia_q.strip(), regione_q.strip())
                if coords is None:
                    st.error("Comune non risolto. Prova a compilare anche Provincia e Regione; se il problema persiste, inserisci temporaneamente le coordinate WGS84 manualmente.")
                else:
                    st.session_state["lat_wgs"], st.session_state["lon_wgs"] = coords
                    st.session_state["lat_wgs_input"], st.session_state["lon_wgs_input"] = coords
                    st.session_state["site_name"] = comune_q.strip()
                    st.session_state["site_name_input"] = comune_q.strip()
                    st.session_state["region_name"] = regione_q.strip()
                    st.session_state["municipality_name"] = comune_q.strip()
                    st.rerun()

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
        fig = spectra_figure(results, plot_type, highlight, site_name, soil, topo, xi, q)
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
        # Mappa interattiva centrata esattamente sulle coordinate WGS84 del sito.
        # PyDeck rende il punto molto più leggibile del semplice st.map.
        map_df = pd.DataFrame({
            "lat": [lat_wgs],
            "lon": [lon_wgs],
            "sito": [site_name or "Sito"],
        })
        halo_layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position="[lon, lat]",
            get_radius=125,
            radius_min_pixels=13,
            radius_max_pixels=24,
            get_fill_color=[0, 190, 120, 55],
            pickable=False,
        )
        site_layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position="[lon, lat]",
            get_radius=60,
            radius_min_pixels=7,
            radius_max_pixels=13,
            get_fill_color=[0, 185, 115, 235],
            get_line_color=[255, 255, 255, 255],
            pickable=True,
            stroked=True,
            filled=True,
            line_width_min_pixels=3,
        )
        label_layer = pdk.Layer(
            "TextLayer",
            data=map_df,
            get_position="[lon, lat]",
            get_text="sito",
            get_size=16,
            get_color=[20, 20, 20, 255],
            get_alignment_baseline="bottom",
            get_pixel_offset=[0, -18],
            pickable=False,
        )
        deck = pdk.Deck(
            map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
            initial_view_state=pdk.ViewState(
                latitude=float(lat_wgs),
                longitude=float(lon_wgs),
                zoom=14.0,
                pitch=0,
            ),
            layers=[halo_layer, site_layer, label_layer],
            tooltip={"text": "{sito}\nLat: {lat}\nLon: {lon}"},
        )
        st.pydeck_chart(deck, use_container_width=True, height=520)

        m1, m2, m3 = st.columns([1, 1, 1.4])
        encoded = urllib.parse.quote(f"{lat_wgs:.8f},{lon_wgs:.8f}")
        with m1:
            st.link_button(
                "Google Maps",
                f"https://www.google.com/maps/search/?api=1&query={encoded}",
                use_container_width=True,
            )
        with m2:
            st.link_button(
                "Google Earth",
                f"https://earth.google.com/web/search/{encoded}",
                use_container_width=True,
            )
        with m3:
            st.caption(f"WGS84: {lat_wgs:.7f}, {lon_wgs:.7f}")
        st.caption(
            "Mappa del sito centrata sulle coordinate WGS84. Per affinare il punto dell'opera "
            "modifica direttamente latitudine e longitudine; la mappa si aggiorna automaticamente."
        )

    with tabs[2]:
        h1, h2 = st.columns([1, 1])
        with h1:
            hazard_state = st.selectbox("Stato limite mappa", STATE_ORDER, index=2)
        with h2:
            radius = st.number_input("Raggio di zoom [km]", min_value=20.0, max_value=250.0, value=90.0, step=10.0)
        tr_for_map = float(hazard_df.loc[hazard_df["SL"] == hazard_state, "Tr [anni]"].iloc[0])
        try:
            hfig, nsource, lsource = combined_hazard_figure(
                hazard_db, meta, hazard_state, tr_for_map, radius, site_name
            )
            st.pyplot(hfig, use_container_width=True)
            plt.close(hfig)
            st.caption(
                f"Mappa di pericolosità in stile tecnico con palette cromatica ispirata alle tavole INGV. Il cerchio sulla carta nazionale individua l'area rappresentata nello zoom locale."
            )
        except Exception as exc:
            st.warning(f"Mappa di pericolosità non disponibile: {exc}")

st.divider()
st.caption(
    "Strumento di supporto al calcolo. Prima dell'impiego professionale verificare sempre i dati di input, "
    "le ipotesi adottate e la coerenza con la normativa applicabile al caso specifico."
)
