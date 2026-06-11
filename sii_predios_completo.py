"""
sii_predios_completo.py
=======================
Pipeline completo SII Chile:
  1. Descarga datos tabulares por grilla (getFeatureInfo)
  2. Descarga tiles WMS y vectoriza polígonos prediales
  3. Join espacial polígonos + datos tabulares
  4. Exporta GeoPackage con geometría + todos los atributos

Uso:
  python sii_predios_completo.py --comuna 5601
  python sii_predios_completo.py --comuna 5601 --zoom 17 --grilla 80
  python sii_predios_completo.py --comuna 5601 --solo-tabular   # sin WMS
  python sii_predios_completo.py --comuna 5601 --solo-wms       # sin grilla
  python sii_predios_completo.py --comuna 5601 --scheduler --dia 1 --hora 02:00
  python sii_predios_completo.py --listar-comunas

Dependencias:
  pip install requests pandas geopandas shapely rasterio pillow numpy tqdm schedule
"""
from __future__ import annotations

import io
import time
import math
import json
import logging
import argparse
import schedule
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import numpy as np
import pandas as pd
from PIL import Image
from tqdm import tqdm

try:
    import geopandas as gpd
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    from rasterio.features import shapes as rio_shapes
    from shapely.geometry import shape as shapely_shape, Point, Polygon
    from shapely.validation import make_valid
    HAS_GEO = True
except ImportError:
    HAS_GEO = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("sii_predios.log")],
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_DIR   = Path("output_sii")
OUTPUT_DIR.mkdir(exist_ok=True)

API_URL      = "https://www4.sii.cl/mapasui/services/data/mapasFacadeService"
WMS_URL      = "https://www4.sii.cl/mapasui/services/ui/wmsProxyService/call"
TILE_SIZE    = 256
DELAY_API    = 0.4
DELAY_WMS    = 0.3
MAX_RETRY    = 3
TIMEOUT      = 15
GRILLA_DEF   = 60
ZOOM_DEF     = 16

HEADERS_API = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":         "https://www4.sii.cl/mapasui/index.html",
    "Origin":          "https://www4.sii.cl",
    "Content-Type":    "application/json;charset=UTF-8",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-CL,es;q=0.9",
}
HEADERS_WMS = {**HEADERS_API, "Accept": "image/png,image/*,*/*"}

# ── Comunas ───────────────────────────────────────────────────────────────────
COMUNAS = {
    "1301": "Santiago Centro",  "1302": "Cerrillos",
    "1303": "Cerro Navia",      "1304": "Conchalí",
    "1305": "El Bosque",        "1306": "Estación Central",
    "1307": "Huechuraba",       "1308": "Independencia",
    "1309": "La Cisterna",      "1310": "La Florida",
    "1311": "La Granja",        "1312": "La Pintana",
    "1313": "La Reina",         "1314": "Las Condes",
    "1315": "Lo Barnechea",     "1316": "Lo Espejo",
    "1317": "Lo Prado",         "1318": "Macul",
    "1319": "Maipú",            "1320": "Ñuñoa",
    "1321": "Pedro Aguirre Cerda", "1322": "Peñalolén",
    "1323": "Providencia",      "1324": "Pudahuel",
    "1325": "Quilicura",        "1326": "Quinta Normal",
    "1327": "Recoleta",         "1328": "Renca",
    "1329": "San Joaquín",      "1330": "San Miguel",
    "1331": "San Ramón",        "1332": "Vitacura",
    "1333": "Puente Alto",      "1336": "Buin",
    "1339": "San Bernardo",
    "3301": "Valparaíso",       "3310": "Viña del Mar",
    "3311": "Concón",           "3312": "Quilpué",
    "5601": "San Felipe",       "5602": "Panquehue",
    "5603": "Catemu",           "5604": "Putaendo",
    "5605": "Santa María",
    "1101": "Iquique",          "2101": "Antofagasta",
    "4101": "La Serena",        "4102": "Coquimbo",
    "6101": "Rancagua",         "7101": "Talca",
    "8101": "Concepción",       "9101": "Temuco",
    "10101": "Puerto Montt",    "14101": "Valdivia",
    "15101": "Arica",
}


# ════════════════════════════════════════════════════════════════════════════
# PARTE 1 — DATOS TABULARES (getFeatureInfo)
# ════════════════════════════════════════════════════════════════════════════

class SIIClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS_API)

    def post(self, endpoint: str, data: dict) -> Optional[dict]:
        url = f"{API_URL}/{endpoint}"
        payload = {
            "data": data,
            "metaData": {
                "namespace": f"cl.sii.sdi.lob.bbrr.mapas.data.api.interfaces.MapasFacadeService/{endpoint}",
                "conversationId": "UNAUTHENTICATED-CALL",
                "transactionId": f"script-{int(time.time()*1000)}",
            }
        }
        for intento in range(1, MAX_RETRY + 1):
            try:
                time.sleep(DELAY_API)
                resp = self.session.post(url, json=payload, timeout=TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    time.sleep(60 * intento)
                else:
                    return None
            except Exception as e:
                log.debug(f"Error {endpoint} intento {intento}: {e}")
        return None


def obtener_contexto(client: SIIClient, cod: int) -> Optional[dict]:
    r = client.post("getServicioPredio", {"comuna": cod, "eac": -1})
    if not r or not r.get("data"):
        return None
    data = r["data"]
    ctx = data[0] if isinstance(data, list) and data else data
    return ctx if isinstance(ctx, dict) else None


def consultar_punto(client: SIIClient, cod: int, lat: float, lon: float,
                    sw: tuple, ne: tuple, layer: str, eac: int, eacano: int,
                    width: int = 800, height: int = 600) -> Optional[dict]:
    lat_r = ne[0] - sw[0]
    lon_r = ne[1] - sw[1]
    if lat_r == 0 or lon_r == 0:
        return None
    px = ((lon - sw[1]) / lon_r) * width
    py = height - ((lat - sw[0]) / lat_r) * height

    payload = {
        "clickInfo": {
            "x": round(px, 4), "y": round(py, 4),
            "southwestx": sw[0], "southwesty": sw[1],
            "northeastx": ne[0], "northeasty": ne[1],
            "width": width, "height": height,
            "layer": layer,
            "servicios": [{"comuna": cod, "layer": layer,
                           "style": "PREDIOS_WMS_V0", "eac": eac, "eacano": eacano}],
        }
    }
    resp = client.post("getFeatureInfo", payload)
    if not resp or "data" not in resp:
        return None
    d = resp["data"]
    if d.get("existePredio", -1) == -1 or d.get("manzana", 0) == 0:
        return None
    return d


def descargar_tabular(cod_comuna: str, n_grilla: int = GRILLA_DEF,
                      ctx: dict = None,
                      bbox: tuple = None) -> pd.DataFrame:
    """Descarga datos tabulares por grilla de puntos.
    bbox = (lat_min, lon_min, lat_max, lon_max) — si None se estima desde contexto.
    """
    cod_int = int(cod_comuna)
    nombre  = COMUNAS.get(cod_comuna, cod_comuna)
    fecha   = datetime.now().strftime("%Y%m")
    csv_out = OUTPUT_DIR / f"{cod_comuna}_{nombre.replace(' ','_')}_{fecha}.csv"
    chk     = OUTPUT_DIR / f".chk_{cod_comuna}.json"

    log.info(f"[TABULAR] {nombre} ({cod_comuna}) | Grilla {n_grilla}x{n_grilla}")

    client  = SIIClient()
    predios = {}
    puntos_ok = set()

    if chk.exists():
        with open(chk) as f:
            c = json.load(f)
            puntos_ok = set(map(tuple, c.get("puntos", [])))
            predios   = c.get("predios", {})
        log.info(f"Checkpoint: {len(puntos_ok)} puntos, {len(predios)} predios")

    if ctx is None:
        ctx = obtener_contexto(client, cod_int) or {}

    layer  = ctx.get("layer",    "sii:BR_CART_WMS")
    eac    = ctx.get("eac",      0)
    eacano = ctx.get("eacano",   0)

    if bbox:
        sw = (bbox[0], bbox[1])
        ne = (bbox[2], bbox[3])
        log.info(f"[TABULAR] Bbox manual: SW{sw} NE{ne}")
    else:
        lat_c = ctx.get("latitud",  -33.45)
        lon_c = ctx.get("longitud", -70.65)
        zoom  = ctx.get("zoom",     14)
        gpp   = 360.0 / (256 * (2 ** zoom))
        sw    = (lat_c - gpp * 300, lon_c - gpp * 400)
        ne    = (lat_c + gpp * 300, lon_c + gpp * 400)
        log.info(f"[TABULAR] Bbox estimado: SW{sw} NE{ne}")

    grilla    = [(sw[0] + (ne[0]-sw[0])*(i+.5)/n_grilla,
                  sw[1] + (ne[1]-sw[1])*(j+.5)/n_grilla)
                 for i in range(n_grilla) for j in range(n_grilla)]
    pendientes = [p for p in grilla if p not in puntos_ok]
    log.info(f"Puntos pendientes: {len(pendientes)}")

    for idx, (lat, lon) in enumerate(tqdm(pendientes, desc=f"Grilla {nombre}")):
        d = consultar_punto(client, cod_int, lat, lon, sw, ne, layer, eac, eacano)
        if d:
            key = f"{d.get('manzana',0)}_{d.get('predio',0)}"
            if key not in predios:
                predios[key] = {
                    "comuna":             d.get("comuna"),
                    "manzana":            d.get("manzana"),
                    "predio":             d.get("predio"),
                    "rol":                d.get("rol"),
                    "direccion":          d.get("direccion"),
                    "nombreComuna":       d.get("nombreComuna"),
                    "destinoDescripcion": d.get("destinoDescripcion"),
                    "ubicacion":          d.get("ubicacion"),
                    "valorTotal":         d.get("valorTotal"),
                    "valorAfecto":        d.get("valorAfecto"),
                    "valorExento":        d.get("valorExento"),
                    "supTerreno":         d.get("supTerreno"),
                    "supConsMt2":         d.get("supConsMt2"),
                    "ah":                 d.get("ah"),
                    "eacsDescripcion":    d.get("eacsDescripcion"),
                    "periodo":            d.get("periodo"),
                    "sector":             d.get("sector"),
                    "ubicacionX":         d.get("ubicacionX"),
                    "ubicacionY":         d.get("ubicacionY"),
                    "predioNac":          d.get("predioNac"),
                    "tablaOrigen":        d.get("tablaOrigen"),
                    "fecha_descarga":     datetime.now().strftime("%Y-%m-%d"),
                }
        puntos_ok.add((lat, lon))
        if (idx + 1) % 200 == 0:
            with open(chk, "w") as f:
                json.dump({"puntos": [list(p) for p in puntos_ok],
                           "predios": predios}, f)
            log.info(f"[{idx+1}/{len(pendientes)}] Predios: {len(predios)}")

    if not predios:
        log.warning("Sin predios tabulares.")
        return pd.DataFrame()

    df = pd.DataFrame(list(predios.values()))
    df.to_csv(csv_out, index=False, encoding="utf-8-sig")
    if chk.exists():
        chk.unlink()
    log.info(f"[TABULAR] ✓ {len(df)} predios -> {csv_out}")
    return df


# ════════════════════════════════════════════════════════════════════════════
# PARTE 2 — WMS + VECTORIZACIÓN
# ════════════════════════════════════════════════════════════════════════════

def _lon_x(lon): return lon * 20037508.34 / 180.0
def _lat_y(lat):
    return math.log(math.tan(math.pi/4 + math.radians(lat)/2)) * 20037508.34 / math.pi

def _latlon_tile(lat, lon, zoom):
    n = 2**zoom
    x = int((lon+180)/360*n)
    y = int((1 - math.log(math.tan(math.radians(lat)) +
             1/math.cos(math.radians(lat)))/math.pi)/2*n)
    return x, y

def _tile_bbox_3857(x, y, zoom):
    n = 2**zoom
    lo = x/n*360-180
    hi = (x+1)/n*360-180
    lt = math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
    lb = math.degrees(math.atan(math.sinh(math.pi*(1-2*(y+1)/n))))
    return _lon_x(lo), _lat_y(lb), _lon_x(hi), _lat_y(lt)


def descargar_tile_wms(session: requests.Session, layer: str,
                       x: int, y: int, zoom: int) -> Optional[np.ndarray]:
    xmin, ymin, xmax, ymax = _tile_bbox_3857(x, y, zoom)
    params = {
        "service": "WMS", "request": "GetMap",
        "layers": layer, "styles": "PREDIOS_WMS_V0",
        "format": "image/png", "transparent": "true",
        "version": "1.1.1", "height": TILE_SIZE, "width": TILE_SIZE,
        "srs": "EPSG:3857",
        "bbox": f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}",
    }
    for intento in range(1, MAX_RETRY+1):
        try:
            time.sleep(DELAY_WMS)
            resp = session.get(WMS_URL, params=params,
                               headers=HEADERS_WMS, timeout=20)
            if resp.status_code == 200 and len(resp.content) > 100:
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                return np.array(img)
            elif resp.status_code == 429:
                time.sleep(30*intento)
        except Exception as e:
            log.debug(f"Tile ({x},{y}) intento {intento}: {e}")
    return None


def vectorizar_tif(tif_path: Path) -> Optional[object]:
    """Vectoriza polígonos prediales desde GeoTIFF WMS."""
    if not HAS_GEO:
        log.error("Instala: pip install geopandas rasterio shapely")
        return None

    log.info(f"[WMS] Vectorizando: {tif_path}")
    with rasterio.open(tif_path) as src:
        r = src.read(1).astype(np.float32)
        g = src.read(2).astype(np.float32)
        b = src.read(3).astype(np.float32)
        a = src.read(4).astype(np.float32)
        transform = src.transform
        crs = src.crs

    # Interior predial = fondo azul-celeste RGB(182,221,232)
    mascara = (
        (r >= 165) & (r <= 200) &
        (g >= 205) & (g <= 240) &
        (b >= 215) & (b <= 248) &
        (a > 10)
    ).astype(np.uint8)

    log.info(f"[WMS] Píxeles prediales: {mascara.sum():,}")
    if mascara.sum() == 0:
        log.warning("[WMS] Sin píxeles prediales — ajusta el filtro de color")
        return None

    polys = []
    for geom_dict, val in rio_shapes(mascara, mask=mascara, transform=transform):
        if val == 1:
            geom = make_valid(shapely_shape(geom_dict))
            if geom.is_valid and not geom.is_empty:
                polys.append(geom)

    log.info(f"[WMS] Polígonos raw: {len(polys)}")
    if not polys:
        return None

    gdf = gpd.GeoDataFrame(geometry=polys, crs=crs)
    gdf_m = gdf.to_crs("EPSG:3857")
    gdf_m["area_m2"] = gdf_m.geometry.area
    gdf_m = gdf_m[(gdf_m.area_m2 >= 30) & (gdf_m.area_m2 <= 1_000_000)].copy()
    gdf_m = gdf_m.reset_index(drop=True)
    log.info(f"[WMS] Polígonos filtrados: {len(gdf_m)}")
    return gdf_m.to_crs("EPSG:4326")


def descargar_wms(cod_comuna: str, layer: str,
                  lat_min: float, lon_min: float,
                  lat_max: float, lon_max: float,
                  zoom: int = ZOOM_DEF) -> Optional[Path]:
    """Descarga tiles WMS, ensambla GeoTIFF y vectoriza."""
    nombre   = COMUNAS.get(cod_comuna, cod_comuna)
    tif_path = OUTPUT_DIR / f"{cod_comuna}_z{zoom}.tif"

    # Calcular tiles
    x_nw, y_nw = _latlon_tile(lat_max, lon_min, zoom)
    x_se, y_se = _latlon_tile(lat_min, lon_max, zoom)
    tiles = [(x, y) for y in range(y_nw, y_se+1) for x in range(x_nw, x_se+1)]
    log.info(f"[WMS] {nombre} | Tiles: {len(tiles)} (zoom {zoom})")

    if len(tiles) > 5000:
        log.warning(f"[WMS] {len(tiles)} tiles es mucho — considera zoom menor")

    # Descargar tiles
    session = requests.Session()
    tiles_data = {}
    for (x, y) in tqdm(tiles, desc=f"Tiles WMS {cod_comuna}"):
        tiles_data[(x, y)] = descargar_tile_wms(session, layer, x, y, zoom)

    ok = sum(1 for v in tiles_data.values() if v is not None)
    log.info(f"[WMS] Tiles descargados: {ok}/{len(tiles)}")
    if ok == 0:
        return None

    # Ensamblar GeoTIFF con bbox CORRECTO
    cols = x_se - x_nw + 1
    rows = y_se - y_nw + 1
    W, H = cols * TILE_SIZE, rows * TILE_SIZE
    canvas = np.zeros((H, W, 4), dtype=np.uint8)

    for (x, y), arr in tiles_data.items():
        if arr is None:
            continue
        px = (x - x_nw) * TILE_SIZE
        py = (y - y_nw) * TILE_SIZE
        h, w = arr.shape[:2]
        canvas[py:py+h, px:px+w] = arr

    # Bbox directo desde índices de tile (fórmula verificada)
    n = 2 ** zoom
    lon_min_r = x_nw / n * 360.0 - 180.0
    lon_max_r = (x_se + 1) / n * 360.0 - 180.0
    lat_max_r = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y_nw / n))))
    lat_min_r = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y_se + 1) / n))))

    log.info(f"[WMS] Bbox: lon({lon_min_r:.4f}->{lon_max_r:.4f}) "
             f"lat({lat_min_r:.4f}->{lat_max_r:.4f})")

    transform = from_bounds(lon_min_r, lat_min_r, lon_max_r, lat_max_r, W, H)
    with rasterio.open(
        tif_path, "w", driver="GTiff",
        height=H, width=W, count=4, dtype=np.uint8,
        crs=CRS.from_epsg(4326), transform=transform,
        compress="deflate", tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        for i in range(4):
            dst.write(canvas[:, :, i], i+1)

    log.info(f"[WMS] GeoTIFF: {tif_path} ({W}x{H} px)")
    return tif_path


# ════════════════════════════════════════════════════════════════════════════
# PARTE 3 — JOIN ESPACIAL
# ════════════════════════════════════════════════════════════════════════════

def join_poligonos_datos(gdf_polys: object, df_datos: pd.DataFrame) -> object:
    """
    Une polígonos WMS con datos tabulares.
    - Point-in-polygon con ubicacionX/Y
    - Predios sin polígono se agregan como puntos
    """
    if not HAS_GEO:
        return gdf_polys

    df_coords = df_datos.dropna(subset=["ubicacionX", "ubicacionY"]).copy()
    if len(df_coords) == 0:
        log.warning("[JOIN] Sin coordenadas en datos tabulares")
        return gdf_polys

    # ubicacionX = latitud, ubicacionY = longitud (convención SII)
    df_coords["geometry"] = df_coords.apply(
        lambda r: Point(r["ubicacionY"], r["ubicacionX"]), axis=1
    )
    gdf_pts = gpd.GeoDataFrame(df_coords, crs="EPSG:4326")

    # Point-in-polygon
    joined = gpd.sjoin(gdf_pts, gdf_polys.reset_index(),
                       how="left", predicate="within")
    log.info(f"[JOIN] PIP: {joined['index_right'].notna().sum()}/{len(gdf_pts)} predios con poligono")

    # Construir geometrías finales: polígono si matched, punto si no
    all_geoms = []
    for _, row in joined.iterrows():
        ir = row.get("index_right")
        if pd.notna(ir):
            all_geoms.append(gdf_polys.iloc[int(ir)].geometry)
        else:
            all_geoms.append(Point(row["ubicacionY"], row["ubicacionX"]))

    # Limpiar columnas duplicadas antes de construir GeoDataFrame
    cols_drop = [c for c in joined.columns
                 if c in ["geometry", "index_right", "index_left", "_dist"]]
    result_df = joined.drop(columns=cols_drop, errors="ignore")
    result_df = pd.DataFrame(result_df)

    # Agregar area_m2 desde polígono
    areas = []
    for _, row in joined.iterrows():
        ir = row.get("index_right")
        areas.append(gdf_polys.iloc[int(ir)]["area_m2"] if pd.notna(ir) else None)
    result_df["area_m2"] = areas

    result = gpd.GeoDataFrame(result_df, geometry=all_geoms, crs="EPSG:4326")
    log.info(f"[JOIN] Total final: {len(result)} registros")
    return result




def consultar_por_centroides(gdf_polys: object, cod_comuna: str,
                              layer: str, eac: int, eacano: int) -> pd.DataFrame:
    """
    Consulta getFeatureInfo usando el centroide de cada polígono WMS.
    Garantiza cobertura completa — un punto por polígono.
    """
    cod_int = int(cod_comuna)
    nombre  = COMUNAS.get(cod_comuna, cod_comuna)
    fecha   = datetime.now().strftime("%Y%m")
    csv_out = OUTPUT_DIR / f"{cod_comuna}_{nombre.replace(' ','_')}_{fecha}.csv"
    chk     = OUTPUT_DIR / f".chk_centroides_{cod_comuna}.json"

    log.info(f"[CENTROIDES] {nombre} | {len(gdf_polys)} poligonos")

    client    = SIIClient()
    predios   = {}
    procesados = set()

    if chk.exists():
        with open(chk) as f:
            c = json.load(f)
            procesados = set(c.get("procesados", []))
            predios    = c.get("predios", {})
        log.info(f"Checkpoint: {len(procesados)} procesados, {len(predios)} predios")

    # Calcular centroides en EPSG:4326
    gdf_c = gdf_polys.copy()
    gdf_c["centroid"] = gdf_polys.to_crs("EPSG:3857").centroid.to_crs("EPSG:4326")

    # Bbox del conjunto de polígonos
    bounds = gdf_polys.total_bounds  # (minx, miny, maxx, maxy) = (lon_min, lat_min, lon_max, lat_max)
    sw = (bounds[1], bounds[0])  # (lat_min, lon_min)
    ne = (bounds[3], bounds[2])  # (lat_max, lon_max)

    pendientes = [(i, row) for i, row in gdf_c.iterrows() if i not in procesados]
    log.info(f"Pendientes: {len(pendientes)}")

    for idx, (i, row) in enumerate(tqdm(pendientes, desc=f"Centroides {nombre}")):
        centroid = row["centroid"]
        lat, lon = centroid.y, centroid.x

        d = consultar_punto(client, cod_int, lat, lon, sw, ne, layer, eac, eacano)

        if d:
            key = f"{d.get('manzana',0)}_{d.get('predio',0)}"
            if key not in predios:
                predios[key] = {
                    "poly_idx":           i,
                    "comuna":             d.get("comuna"),
                    "manzana":            d.get("manzana"),
                    "predio":             d.get("predio"),
                    "rol":                d.get("rol"),
                    "direccion":          d.get("direccion"),
                    "nombreComuna":       d.get("nombreComuna"),
                    "destinoDescripcion": d.get("destinoDescripcion"),
                    "ubicacion":          d.get("ubicacion"),
                    "valorTotal":         d.get("valorTotal"),
                    "valorAfecto":        d.get("valorAfecto"),
                    "valorExento":        d.get("valorExento"),
                    "supTerreno":         d.get("supTerreno"),
                    "supConsMt2":         d.get("supConsMt2"),
                    "ah":                 d.get("ah"),
                    "eacsDescripcion":    d.get("eacsDescripcion"),
                    "periodo":            d.get("periodo"),
                    "sector":             d.get("sector"),
                    "ubicacionX":         d.get("ubicacionX"),
                    "ubicacionY":         d.get("ubicacionY"),
                    "predioNac":          d.get("predioNac"),
                    "tablaOrigen":        d.get("tablaOrigen"),
                    "fecha_descarga":     datetime.now().strftime("%Y-%m-%d"),
                }
        else:
            # Centroide no cayó en predio — intentar 4 puntos offset dentro del polígono
            geom = row.geometry
            if geom and not geom.is_empty:
                offsets = [
                    (lat + 0.00005, lon), (lat - 0.00005, lon),
                    (lat, lon + 0.00005), (lat, lon - 0.00005),
                ]
                for lat2, lon2 in offsets:
                    d2 = consultar_punto(client, cod_int, lat2, lon2,
                                        sw, ne, layer, eac, eacano)
                    if d2:
                        key = f"{d2.get('manzana',0)}_{d2.get('predio',0)}"
                        if key not in predios:
                            predios[key] = {
                                "poly_idx":           i,
                                "comuna":             d2.get("comuna"),
                                "manzana":            d2.get("manzana"),
                                "predio":             d2.get("predio"),
                                "rol":                d2.get("rol"),
                                "direccion":          d2.get("direccion"),
                                "nombreComuna":       d2.get("nombreComuna"),
                                "destinoDescripcion": d2.get("destinoDescripcion"),
                                "ubicacion":          d2.get("ubicacion"),
                                "valorTotal":         d2.get("valorTotal"),
                                "valorAfecto":        d2.get("valorAfecto"),
                                "valorExento":        d2.get("valorExento"),
                                "supTerreno":         d2.get("supTerreno"),
                                "supConsMt2":         d2.get("supConsMt2"),
                                "ah":                 d2.get("ah"),
                                "eacsDescripcion":    d2.get("eacsDescripcion"),
                                "periodo":            d2.get("periodo"),
                                "sector":             d2.get("sector"),
                                "ubicacionX":         d2.get("ubicacionX"),
                                "ubicacionY":         d2.get("ubicacionY"),
                                "predioNac":          d2.get("predioNac"),
                                "tablaOrigen":        d2.get("tablaOrigen"),
                                "fecha_descarga":     datetime.now().strftime("%Y-%m-%d"),
                            }
                        break

        procesados.add(i)

        if (idx + 1) % 200 == 0:
            with open(chk, "w") as f:
                json.dump({"procesados": list(procesados), "predios": predios}, f)
            tasa = len(predios) / max(len(procesados), 1) * 100
            log.info(f"[{idx+1}/{len(pendientes)}] Predios: {len(predios)} ({tasa:.0f}% cobertura)")

    if chk.exists():
        chk.unlink()

    if not predios:
        log.warning("[CENTROIDES] Sin predios encontrados")
        return pd.DataFrame()

    df = pd.DataFrame(list(predios.values()))
    df.to_csv(csv_out, index=False, encoding="utf-8-sig")
    log.info(f"[CENTROIDES] {len(df)} predios -> {csv_out}")
    return df

# ════════════════════════════════════════════════════════════════════════════
# PIPELINE PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════

def pipeline(cod_comuna: str, n_grilla: int = GRILLA_DEF,
             zoom: int = ZOOM_DEF,
             solo_tabular: bool = False,
             solo_wms: bool = False,
             solo_join: bool = False,
             lat_min: float = None, lon_min: float = None,
             lat_max: float = None, lon_max: float = None) -> Optional[Path]:

    if not HAS_GEO and not solo_tabular:
        log.error("Faltan dependencias GIS. Instala: pip install geopandas rasterio shapely")
        solo_tabular = True

    cod_int = int(cod_comuna)
    nombre  = COMUNAS.get(cod_comuna, cod_comuna)
    fecha   = datetime.now().strftime("%Y%m")
    gpkg_out = OUTPUT_DIR / f"{cod_comuna}_{nombre.replace(' ','_')}_{fecha}.gpkg"

    log.info(f"{'='*55}")
    log.info(f"PIPELINE: {nombre} ({cod_comuna})")
    log.info(f"{'='*55}")

    # Contexto
    client = SIIClient()
    ctx    = obtener_contexto(client, cod_int) or {}
    layer  = ctx.get("layer", "sii:BR_CART_WMS")
    eac    = ctx.get("eac",    0)
    eacano = ctx.get("eacano", 0)
    lat_c  = ctx.get("latitud",  -33.45)
    lon_c  = ctx.get("longitud", -70.65)
    zoom_c = ctx.get("zoom",     14)

    # Bbox
    if lat_min is None:
        gpp     = 360.0 / (256 * (2 ** zoom_c))
        lat_min = lat_c - gpp * 300
        lat_max = lat_c + gpp * 300
        lon_min = lon_c - gpp * 400
        lon_max = lon_c + gpp * 400
        log.info(f"Bbox estimado: ({lat_min:.4f},{lon_min:.4f}) -> ({lat_max:.4f},{lon_max:.4f})")
    else:
        log.info(f"Bbox manual: ({lat_min:.4f},{lon_min:.4f}) -> ({lat_max:.4f},{lon_max:.4f})")

    df      = pd.DataFrame()
    gdf_wms = None
    tif_path = OUTPUT_DIR / f"{cod_comuna}_z{zoom}.tif"

    # 1. WMS — saltar si TIF ya existe o si es solo_join/solo_tabular
    if not solo_tabular and not solo_join:
        if tif_path.exists():
            log.info(f"[WMS] TIF existente encontrado: {tif_path} — saltando descarga")
            gdf_wms = vectorizar_tif(tif_path)
        else:
            tif_path = descargar_wms(cod_comuna, layer,
                                     lat_min, lon_min, lat_max, lon_max, zoom)
            if tif_path:
                gdf_wms = vectorizar_tif(tif_path)
    elif solo_join:
        if tif_path.exists():
            log.info(f"[WMS] Modo solo-join: vectorizando TIF existente {tif_path}")
            gdf_wms = vectorizar_tif(tif_path)
        else:
            log.error(f"[WMS] TIF no encontrado: {tif_path}. Ejecuta primero sin --solo-join")
            return None

    # 2. Datos tabulares
    if not solo_wms:
        if gdf_wms is not None and len(gdf_wms) > 0:
            # Estrategia óptima: consultar centroide de cada polígono
            log.info("[PIPELINE] Usando estrategia de centroides")
            df = consultar_por_centroides(gdf_wms, cod_comuna, layer, eac,
                                          ctx.get("eacano", 0))
        else:
            # Fallback: grilla de puntos
            log.info("[PIPELINE] Fallback: grilla de puntos")
            df = descargar_tabular(cod_comuna, n_grilla, ctx,
                                   bbox=(lat_min, lon_min, lat_max, lon_max))

    # 3. Solo tabular -> GeoPackage de puntos
    if solo_tabular or gdf_wms is None:
        if len(df) == 0:
            log.warning("Sin datos.")
            return None
        df_coords = df.dropna(subset=["ubicacionX","ubicacionY"]).copy()
        df_coords["geometry"] = df_coords.apply(
            lambda r: Point(r["ubicacionY"], r["ubicacionX"]), axis=1
        )
        result = gpd.GeoDataFrame(df_coords, crs="EPSG:4326")
        result.to_file(gpkg_out, driver="GPKG")
        log.info(f"✓ GeoPackage puntos: {gpkg_out} ({len(result)} predios)")
        return gpkg_out

    # 4. Join polígonos + datos
    if len(df) > 0:
        result = join_poligonos_datos(gdf_wms, df)
    else:
        result = gdf_wms
        log.info("[JOIN] Sin datos tabulares — exportando solo polígonos WMS")

    result.to_file(gpkg_out, driver="GPKG")

    # Resumen
    log.info(f"\n{'='*55}")
    log.info(f"RESULTADO FINAL — {nombre}")
    log.info(f"  Polígonos:  {len(gdf_wms)}")
    log.info(f"  Con datos:  {result['rol'].notna().sum() if 'rol' in result.columns else 'N/A'}")
    log.info(f"  Sin datos:  {result['rol'].isna().sum() if 'rol' in result.columns else 'N/A'}")
    if "valorTotal" in result.columns:
        v = pd.to_numeric(result["valorTotal"], errors="coerce").dropna()
        if len(v):
            log.info(f"  Avalúo total: ${v.sum():,.0f} CLP")
    log.info(f"  Archivo: {gpkg_out}")
    log.info(f"{'='*55}")

    return gpkg_out


# ════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════════════════

def scheduler_mensual(cod_comuna: str, dia: int = 1, hora: str = "02:00",
                      n_grilla: int = GRILLA_DEF, zoom: int = ZOOM_DEF):
    def tarea():
        if datetime.now().day == dia:
            log.info(f"Tarea mensual: {cod_comuna}")
            pipeline(cod_comuna, n_grilla, zoom)

    schedule.every().day.at(hora).do(tarea)
    nombre = COMUNAS.get(cod_comuna, cod_comuna)
    log.info(f"Scheduler: {nombre} -> día {dia} de cada mes a las {hora}")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Pipeline completo SII Chile — datos + geometría predial",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Pipeline completo (tabular + WMS + join)
  python sii_predios_completo.py --comuna 5601

  # Con bbox manual y zoom específico
  python sii_predios_completo.py --comuna 5601 --zoom 17 ^
      --lat-min -32.85 --lon-min -70.85 --lat-max -32.65 --lon-max -70.65

  # Solo datos tabulares (sin WMS)
  python sii_predios_completo.py --comuna 5601 --solo-tabular

  # Solo WMS (sin grilla de datos)
  python sii_predios_completo.py --comuna 5601 --solo-wms

  # Scheduler mensual
  python sii_predios_completo.py --comuna 5601 --scheduler --dia 1 --hora 02:00
        """
    )
    p.add_argument("--comuna",        type=str)
    p.add_argument("--grilla",        type=int,   default=GRILLA_DEF)
    p.add_argument("--zoom",          type=int,   default=ZOOM_DEF)
    p.add_argument("--lat-min",       type=float, default=None)
    p.add_argument("--lon-min",       type=float, default=None)
    p.add_argument("--lat-max",       type=float, default=None)
    p.add_argument("--lon-max",       type=float, default=None)
    p.add_argument("--solo-tabular",  action="store_true")
    p.add_argument("--solo-wms",      action="store_true")
    p.add_argument("--solo-join",     action="store_true",
                   help="Usar TIF ya descargado, solo vectorizar y consultar datos")
    p.add_argument("--scheduler",     action="store_true")
    p.add_argument("--dia",           type=int,   default=1)
    p.add_argument("--hora",          type=str,   default="02:00")
    p.add_argument("--listar-comunas",action="store_true")

    args = p.parse_args()

    if args.listar_comunas:
        print(f"\n{'Código':<8} {'Comuna'}")
        print("-"*35)
        for cod, nom in sorted(COMUNAS.items()):
            print(f"{cod:<8} {nom}")
        return

    if not args.comuna:
        p.error("Especifica --comuna o --listar-comunas")

    if args.scheduler:
        scheduler_mensual(args.comuna, args.dia, args.hora, args.grilla, args.zoom)
    else:
        pipeline(
            cod_comuna   = args.comuna,
            n_grilla     = args.grilla,
            zoom         = args.zoom,
            solo_tabular = args.solo_tabular,
            solo_wms     = args.solo_wms,
            solo_join    = args.solo_join,
            lat_min      = args.lat_min,
            lon_min      = args.lon_min,
            lat_max      = args.lat_max,
            lon_max      = args.lon_max,
        )


if __name__ == "__main__":
    main()