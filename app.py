"""SpettriNTC WEB gratuito - calcoli NTC, senza modulo relazione PRO."""
import csv
import io
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np
import streamlit as st
from matplotlib.colors import BoundaryNorm, ListedColormap
from pyproj import Transformer
from engine import (MunicipalityDB, SeismicHazardDB, calculate_spectrum,
                    detect_table2_island_group, PVR, STATE_ORDER, CLASS_USE)

BASE = Path(__file__).resolve().parent
st.set_page_config(page_title="SpettriNTC | Giulivo Ingegneria", page_icon=str(BASE/'data'/'g_icon.png'), layout="wide")
st.markdown("""<style>
.stApp {background:#fff;color:#111} .stButton>button[kind='primary'] {background:#158552;border-color:#158552}
h1,h2,h3 {color:#111} div[data-testid='stMetricValue'] {color:#155fa0}
</style>""",unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def hazard_db():
    return SeismicHazardDB(BASE/'data'/'spettri2008.csv', BASE/'data'/'italia_ag_002.npz')


@st.cache_resource(show_spinner=False)
def transform():
    return Transformer.from_crs('EPSG:4326','EPSG:4230',always_xy=True)


@st.cache_resource(show_spinner=False, ttl=86400)
def municipality_db():
    return MunicipalityDB()


from map_view import render_map, VERSION as MAP_VERSION

@st.cache_data(show_spinner=False, max_entries=10, ttl=3600)
def seismic_map_image(map_version, state, tr_value, lat_ed, lon_ed, lat_wgs, lon_wgs, group, tight_zoom):
    return render_map(hazard_db(), BASE/'data', state, tr_value, lat_ed, lon_ed,
                      lat_wgs, lon_wgs, group, radius_km=55 if tight_zoom else 90)


logo = BASE/'data'/'logo.png'
head1, head2=st.columns([1,5])
with head1: st.image(str(logo),width=120)
with head2:
    st.title('SpettriNTC WEB')
    st.caption('Giulivo Ingegneria | Spettri di risposta NTC 2018 | Versione gratuita')
    st.caption(f'✅ {MAP_VERSION} · Mappa nazionale e zoom separati')

with st.sidebar:
    st.header('1 · Localizzazione')
    choice=st.radio('Inserimento sito', ['Coordinate WGS84','Regione / Provincia / Comune'], horizontal=False)
    location_hint=''
    if choice=='Regione / Provincia / Comune':
        try:
            citydb=municipality_db()
            reg=st.selectbox('Regione',citydb.regions,index=citydb.regions.index('Sardegna') if 'Sardegna' in citydb.regions else 0)
            prov=st.selectbox('Provincia / UTS',citydb.provinces(reg))
            comune=st.selectbox('Comune',citydb.municipalities(reg,prov))
            location_hint=f'{comune}, {prov}, {reg}'
            st.info('Le coordinate devono riferirsi al punto effettivo di progetto. La selezione del Comune non sostituisce la posizione del sito.')
        except Exception as exc:
            st.warning('Elenco ISTAT non disponibile: inserire le coordinate WGS84. ' + str(exc))
            reg=''; comune=''
    else:
        reg=''; comune=''
    lat=st.number_input('Latitudine WGS84 (°)',min_value=34.0,max_value=48.5,value=40.7259,format='%.6f',step=0.0001)
    lon=st.number_input('Longitudine WGS84 (°)',min_value=5.0,max_value=20.5,value=8.5595,format='%.6f',step=0.0001)
    st.header('2 · Vita nominale e classe')
    vn=st.number_input('Vita nominale VN (anni)',min_value=1.0,value=50.0,step=5.0)
    cl=st.selectbox('Classe d’uso',['I','II','III','IV'],index=1)
    st.header('3 · Parametri di spettro')
    soil=st.selectbox('Sottosuolo',['A','B','C','D','E'],index=2)
    topo=st.selectbox('Topografia',['T1','T2','T3','T4'])
    xi=st.number_input('Smorzamento ξ (%)',min_value=0.1,max_value=50.,value=5.,step=.5)
    q=st.number_input('Fattore di comportamento q',min_value=1.0,max_value=10.0,value=1.5,step=.1)
    tmax=st.number_input('Periodo massimo T (s)',min_value=1.,max_value=20.,value=5.,step=.5)

vr_raw=vn*CLASS_USE[cl]
vr=max(35.,vr_raw)
tr={state:max(30,min(2475,round(-vr/math.log(1-PVR[state])))) for state in STATE_ORDER}
grp=detect_table2_island_group(lat,lon,reg,comune)
try:
    x_ed,y_ed=transform().transform(lon,lat)
    params={state:hazard_db().site_parameters(y_ed,x_ed,tr[state],table2_group=grp) for state in STATE_ORDER}
    spectra={state:calculate_spectrum(params[state]['ag_g'],params[state]['F0'],params[state]['Tc_star'],soil=soil,topo=topo,xi=xi,q=q,t_max=tmax,state=state) for state in STATE_ORDER}
except Exception as exc:
    st.error(f'Impossibile calcolare gli spettri per questo punto: {exc}')
    st.stop()

if grp:
    st.info(f'Localizzazione in area Tabella 2 (gruppo {grp}): parametri sismici per isole, senza interpolazione sul reticolo continentale.')
else:
    st.info('Localizzazione sul reticolo NTC: parametri interpolati sui nodi del reticolo ufficiale.')
if vr_raw<35:
    st.warning(f'VR nominale {vr_raw:.1f} anni: adottato VR = 35 anni per la determinazione dell’azione sismica.')

st.subheader('Mappa della pericolosità sismica')
map_left, map_right = st.columns([1,1])
with map_left:
    map_state = st.selectbox('Stato limite per la mappa', STATE_ORDER, index=2,
                             help='A TR=475 anni è disponibile la griglia INGV 0,02°. Per altri TR vengono visualizzati i nodi NTC.')
with map_right:
    map_zoom = st.toggle('Zoom locale più stretto', value=False)
selected_tr = float(tr[map_state])
try:
    st.image(seismic_map_image(MAP_VERSION, map_state, selected_tr, float(y_ed), float(x_ed), round(lat,5), round(lon,5), grp, map_zoom), use_container_width=True)
    st.caption('Carta nazionale a sinistra e zoom locale a destra: due pannelli indipendenti. Sardegna: Tabella 2 NTC, gruppo G1.')
except Exception as err:
    st.warning('Mappa temporaneamente non disponibile: gli spettri continuano a funzionare. Dettaglio: '+str(err))

st.subheader('Parametri di pericolosità per stato limite')
rows=[{'Stato':stato, 'TR (anni)':tr[stato], 'ag (g)':round(params[stato]['ag_g'],5),
       'F0':round(params[stato]['F0'],4),'Tc* (s)':round(params[stato]['Tc_star'],4),
       'Ss':round(spectra[stato]['Ss'],3),'Cc':round(spectra[stato]['Cc'],3),
       'TB (s)':round(spectra[stato]['TB'],3),'TC (s)':round(spectra[stato]['TC'],3),
       'TD (s)':round(spectra[stato]['TD'],3)} for stato in STATE_ORDER]
st.dataframe(rows,hide_index=True,use_container_width=True)

st.subheader('Spettri di risposta')
kind=st.radio('Diagramma', ['Accelerazione elastica','Accelerazione di progetto','Spostamento elastico','Spostamento di progetto'],horizontal=True)
lookup={'Accelerazione elastica':('Se_g','Accelerazione (g)'), 'Accelerazione di progetto':('Sd_g','Accelerazione (g)'),
'Spostamento elastico':('SDe_mm','Spostamento (mm)'), 'Spostamento di progetto':('SDd_mm','Spostamento (mm)')}
key,ylabel=lookup[kind]
colors={'SLO':'#171717','SLD':'#1362a3','SLV':'#c82333','SLC':'#168351'}
fig,ax=plt.subplots(figsize=(11,5.4))
for state in STATE_ORDER:
    ax.plot(spectra[state]['T'],spectra[state][key],label=state,color=colors[state],lw=2)
ax.set_xlabel('Periodo T (s)',fontweight='bold'); ax.set_ylabel(ylabel,fontweight='bold')
ax.set_xlim(0,tmax);ax.set_ylim(bottom=0);ax.grid(alpha=.2);ax.legend(ncol=4)
fig.tight_layout()
st.pyplot(fig,clear_figure=True)
plt.close(fig)

output=io.StringIO();writer=csv.writer(output,delimiter=';')
writer.writerow(['T (s)']+[f'{state} {key}' for state in STATE_ORDER])
for i,t in enumerate(spectra['SLO']['T']):
    writer.writerow([f'{t:.6f}']+[f'{spectra[state][key][i]:.8f}' for state in STATE_ORDER])
st.download_button('Scarica valori spettro CSV',output.getvalue().encode('utf-8-sig'),file_name='spettri_ntc.csv',mime='text/csv')
st.caption(f'Coordinate sito: {lat:.6f} N, {lon:.6f} E (WGS84) · VN={vn:g} anni · CU={CLASS_USE[cl]:g} · VR={vr:g} anni.')
st.caption('Software di supporto al calcolo. Verificare sempre dati di ingresso, normativa applicabile e risultati nell’ambito della progettazione professionale.')
st.divider()
st.markdown('**Relazione tecnica Word e impaginazione professionale**: disponibili esclusivamente nella versione commerciale **SpettriNTC PRO**. Nessuna esportazione di relazione è inclusa in questa app.')
