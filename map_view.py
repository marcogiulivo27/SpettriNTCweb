"""Solo cartografia tecnica della versione FREE; nessuna funzione relazione o licenza."""
from pathlib import Path
from io import BytesIO
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.cm import ScalarMappable
from matplotlib.path import Path as MplPath
import matplotlib.tri as mtri
from pyproj import Transformer

VERSION = "FREE MAP 3.0 - 09/10/2026"


def _outlines(data_dir):
    raw=json.loads((Path(data_dir)/"italy_outline.json").read_text(encoding="utf8"))
    transformer=Transformer.from_crs("EPSG:4326","EPSG:4230",always_xy=True)
    return {name:[np.column_stack(transformer.transform(np.asarray(a)[:,0],np.asarray(a)[:,1]))
                 for a in pieces] for name,pieces in raw.items()}


def _sardinia_polygon(outlines):
    candidates=[]
    for a in outlines['polygons']:
        c=np.asarray(a)
        cx=(c[:,0].min()+c[:,0].max())/2
        cy=(c[:,1].min()+c[:,1].max())/2
        if 7.6<cx<10.5 and 38.5<cy<42:
            candidates.append(c)
    if not candidates:
        raise RuntimeError("Contorno Sardegna non presente nell'asset cartografico")
    return max(candidates,key=lambda v: np.ptp(v[:,0])*np.ptp(v[:,1]))


def _style():
    levels=np.array([.025,.05,.075,.10,.125,.15,.175,.20,.225,.25,.275,.30])
    cmap=ListedColormap(["#d9d9d9","#c7e8f2","#94d3e6","#7dcf9f","#a4d66a","#e1df67",
                         "#f8cb67","#f6aa42","#ee7635","#df402f","#ad3e8c"])
    cmap.set_under("#f2f2f2");cmap.set_over("#7a51af")
    return levels,cmap,BoundaryNorm(levels,cmap.N)


def _field(ax,lon,lat,ag,levels,cmap,norm,edge_km=65):
    lon=np.asarray(lon);lat=np.asarray(lat);ag=np.asarray(ag)
    mask=np.isfinite(lon)&np.isfinite(lat)&np.isfinite(ag)
    lon=lon[mask];lat=lat[mask];ag=ag[mask]
    if len(ag)<4 or np.ptp(ag)<1e-10:
        return ax.scatter(lon,lat,c=ag,s=9,norm=norm,cmap=cmap,zorder=2)
    try:
        tri=mtri.Triangulation(lon,lat)
        pts=tri.triangles
        xx=lon[pts];yy=lat[pts]
        kmx=111.2*np.cos(np.radians(yy.mean(axis=1)))
        dx=np.column_stack((xx[:,0]-xx[:,1],xx[:,1]-xx[:,2],xx[:,2]-xx[:,0]))
        dy=np.column_stack((yy[:,0]-yy[:,1],yy[:,1]-yy[:,2],yy[:,2]-yy[:,0]))
        edges=np.hypot(dx*kmx[:,None],dy*111.2)
        med=float(np.median(np.min(edges,axis=1)))
        tri.set_mask(np.max(edges,axis=1)>min(edge_km,max(10,4.5*med)))
        return ax.tricontourf(tri,ag,levels=levels,cmap=cmap,norm=norm,extend='both',zorder=1)
    except (RuntimeError, ValueError, IndexError, TypeError):
        return ax.scatter(lon,lat,c=ag,s=6,norm=norm,cmap=cmap,zorder=2)


def _draw_sardinia(ax, polygon, ag, cmap, norm):
    from matplotlib.patches import Polygon
    patch=Polygon(polygon,closed=True,facecolor=cmap(norm(ag)),edgecolor='#222222',lw=1.05,zorder=9)
    ax.add_patch(patch)


def render_map(db,data_dir,state,tr,lat_ed,lon_ed,lat_wgs,lon_wgs,group,radius_km=90):
    """Due pannelli NON sovrapposti. Dati in ED50, valori NTC Tabella 2 per Sardegna."""
    levels,cmap,norm=_style()
    outlines=_outlines(data_dir)
    sardinia=_sardinia_polygon(outlines)
    sard_ag=float(db._table2_values(float(tr),'G1')[0])/10.0
    if abs(tr-475)<.5 and db.dense_lon is not None:
        nlon,nlat,nag=db.dense_lon,db.dense_lat,db.dense_ag
        source='Griglia INGV 0,02° · TR 475 anni'
    else:
        nlon,nlat=db.lon,db.lat
        nag=db._node_values(np.arange(len(nlon)),float(tr),'ag')/10
        source=f'Reticolo NTC: valori nodali · TR {tr:.0f} anni'
    dlat=max(.12,radius_km/111)
    dlon=max(.12,radius_km/max(35,111*np.cos(np.radians(lat_ed))))
    lim=(lon_ed-dlon,lon_ed+dlon,lat_ed-dlat,lat_ed+dlat)
    local=(nlon>=lim[0]-.18)&(nlon<=lim[1]+.18)&(nlat>=lim[2]-.18)&(nlat<=lim[3]+.18)
    fig=Figure(figsize=(12,6.4),dpi=125,facecolor='white')
    # Assi fisicamente disgiunti: nazionale fino a x=.575; locale da x=.64.
    ax_n=fig.add_axes([.058,.15,.515,.74])
    ax_l=fig.add_axes([.645,.23,.235,.55])
    cbax=fig.add_axes([.925,.24,.021,.52])
    for ax in (ax_n,ax_l):
        ax.set_facecolor('#f8fafc')
        ax.grid(alpha=.12,lw=.5)
        ax.tick_params(labelsize=8)
    _field(ax_n,nlon,nlat,nag,levels,cmap,norm)
    _field(ax_l,nlon[local],nlat[local],nag[local],levels,cmap,norm,edge_km=42)
    # Il poligono sardo è sempre rappresentato nel nazionale.
    # Nello zoom locale è ritagliato automaticamente dalle coordinate dell'asse.
    for ax in (ax_n,ax_l):
        _draw_sardinia(ax,sardinia,sard_ag,cmap,norm)
        ax.scatter([lon_ed],[lat_ed],marker='*',s=175,facecolors='white',edgecolors='black',lw=1,zorder=25)
    # Contorni reali del file PRO, come mero asset geografico, senza motore PRO.
    for poly in outlines['polygons']:
        for ax in (ax_n,ax_l):
            ax.plot(poly[:,0],poly[:,1],color='#30353a',lw=.75,zorder=10)
    ax_n.set_xlim(6.1,19.2);ax_n.set_ylim(35.2,47.6)
    ax_l.set_xlim(lim[0],lim[1]);ax_l.set_ylim(lim[2],lim[3])
    ax_n.set_aspect(1/max(.3,np.cos(np.radians(41.5))))
    ax_l.set_aspect(1/max(.3,np.cos(np.radians(lat_ed))))
    ax_n.set_title(f'Italia · {state} | TR = {tr:.0f} anni',fontsize=12,fontweight='bold')
    ax_l.set_title('Zoom locale del sito',fontsize=10,fontweight='bold')
    ax_n.set_xlabel('Longitudine ED50 [°]',fontsize=9,fontweight='bold')
    ax_n.set_ylabel('Latitudine ED50 [°]',fontsize=9,fontweight='bold')
    ax_l.set_xlabel('Longitudine ED50 [°]',fontsize=9,fontweight='bold')
    ax_l.set_ylabel('Latitudine ED50 [°]',fontsize=9,fontweight='bold')
    cb=fig.colorbar(ScalarMappable(norm=norm,cmap=cmap),cax=cbax,extend='both',ticks=levels[::2])
    cb.set_label('ag / g',fontsize=10)
    fig.text(.058,.077,f'{source}  |  Sardegna: gruppo G1 (Tabella 2 NTC), ag = {sard_ag:.4f} g',fontsize=8,color='#444')
    fig.text(.058,.044,f'Sito WGS84: {lat_wgs:.5f}° N, {lon_wgs:.5f}° E  ·  Mappa indicativa: non sostituisce il calcolo di sito',fontsize=8,color='#555')
    out=BytesIO()
    fig.savefig(out,format='png',dpi=125,facecolor='white')
    return out.getvalue()
