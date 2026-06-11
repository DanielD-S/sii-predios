# sii-predios 🇨🇱

Pipeline para descargar predios catastrales del SII (Servicio de Impuestos Internos de Chile) por comuna, con geometría vectorial y datos tabulares completos.

> **Nota:** Los datos son referenciales y provienen del visor público del SII. Para efectos legales o tributarios consultar directamente en [www.sii.cl](https://www.sii.cl).

---

## ¿Qué hace?

1. **Descarga tiles WMS** del visor público del SII y los ensambla en un GeoTIFF georreferenciado
2. **Vectoriza los polígonos prediales** detectando el color característico del catastro
3. **Consulta datos tabulares** por cada polígono usando la API pública
4. **Exporta un GeoPackage** con geometría + rol, avalúo, dirección, destino, superficie y más

## Resultado

| Campo | Descripción |
|-------|-------------|
| `rol` | Rol predial (ej: 332-106) |
| `direccion` | Dirección del predio |
| `destinoDescripcion` | HABITACIONAL, AGRICOLA, COMERCIO, etc. |
| `ubicacion` | URBANO / RURAL |
| `valorTotal` | Avalúo total en CLP |
| `valorAfecto` | Avalúo afecto a contribuciones |
| `valorExento` | Avalúo exento |
| `supTerreno` | Superficie de terreno (m²) |
| `supConsMt2` | Superficie construida (m²) |
| `manzana` | Número de manzana |
| `predio` | Número de predio |
| `periodo` | Período del avalúo vigente |
| `geometry` | Polígono predial en EPSG:4326 |

## Ejemplo de resultado en QGIS

**San Felipe (5601) — 7.526 predios**

![San Felipe](docs/san_felipe_preview.png)

---

## Instalación

```bash
git clone https://github.com/DanielD-S/sii-predios.git
cd sii-predios
pip install -r requirements.txt
```

### Dependencias

```
requests
pandas
geopandas
shapely
rasterio
pillow
numpy
tqdm
schedule
fiona==1.9.6
```

> **Python 3.8 (Windows):** instalar fiona con versión fija:
> ```
> pip install fiona==1.9.6 geopandas==0.13.2 --no-deps
> pip install pyproj shapely pandas numpy
> ```

---

## Uso

```bash
# Pipeline completo — datos + geometría
python sii_predios_completo.py --comuna 5601

# Con bbox manual (más preciso) y zoom específico
python sii_predios_completo.py --comuna 5601 --zoom 16 \
    --lat-min -32.85 --lon-min -70.85 \
    --lat-max -32.65 --lon-max -70.65

# Si el TIF ya está descargado, solo consultar datos y hacer join
python sii_predios_completo.py --comuna 5601 --zoom 16 --solo-join

# Solo datos tabulares sin geometría (más rápido)
python sii_predios_completo.py --comuna 5601 --solo-tabular

# Ver comunas disponibles con sus códigos SII
python sii_predios_completo.py --listar-comunas

# Ejecución mensual automática
python sii_predios_completo.py --comuna 5601 --scheduler --dia 1 --hora 02:00
```

### Parámetros

| Parámetro | Descripción | Default |
|-----------|-------------|---------|
| `--comuna` | Código SII de la comuna (4 dígitos) | requerido |
| `--zoom` | Zoom WMS: 14=rápido, 16=balance, 19=máx detalle | 16 |
| `--grilla` | Resolución grilla fallback (NxN puntos) | 60 |
| `--lat-min/max` | Bbox manual en grados decimales | estimado |
| `--lon-min/max` | Bbox manual en grados decimales | estimado |
| `--solo-join` | Usar TIF existente, solo datos + join | False |
| `--solo-tabular` | Solo datos sin geometría WMS | False |
| `--solo-wms` | Solo vectorización sin datos | False |
| `--scheduler` | Activar ejecución mensual automática | False |
| `--dia` | Día del mes para ejecución automática | 1 |
| `--hora` | Hora de ejecución HH:MM | 02:00 |

---

## Códigos de comunas SII

El SII usa códigos propios de 4 dígitos, distintos al código INE.

```bash
python sii_predios_completo.py --listar-comunas
```

Algunos verificados:

| Código | Comuna | Código | Comuna |
|--------|--------|--------|--------|
| 1301 | Santiago Centro | 5601 | San Felipe |
| 1310 | La Florida | 5603 | Catemu |
| 1314 | Las Condes | 5604 | Putaendo |
| 1319 | Maipú | 6101 | Rancagua |
| 1323 | Providencia | 7101 | Talca |
| 1333 | Puente Alto | 8101 | Concepción |
| 3301 | Valparaíso | 9101 | Temuco |
| 3310 | Viña del Mar | 4101 | La Serena |

---

## Obtener el bbox correcto desde el browser

Para mayor precisión se recomienda obtener el bbox directamente desde el visor del SII:

1. Abrir `https://www4.sii.cl/mapasui/index.html` en Chrome
2. Presionar `F12` → Network → Fetch/XHR
3. Hacer clic en cualquier predio del mapa
4. Buscar la request `getFeatureInfo` → pestaña Payload
5. Los valores `southwestx/y` y `northeastx/y` son el bbox

> **Nota:** El SII usa una convención no estándar — `southwestx` es latitud (no longitud).

---

## Archivos generados

```
output_sii/
├── {cod}_z{zoom}.tif               # GeoTIFF con tiles WMS ensamblados
├── {cod}_{nombre}_{YYYYMM}.csv     # Datos tabulares sin geometría
└── {cod}_{nombre}_{YYYYMM}.gpkg    # GeoPackage final (geometría + datos)
```

---

## Cobertura esperada

| Tipo de zona | Cobertura estimada |
|---|---|
| Urbana densa (zoom 16+) | 90-98% |
| Urbana dispersa | 85-95% |
| Rural predios grandes | 95-99% |
| Rural predios pequeños | 70-85% |

Los predios sin polígono se incluyen como puntos con sus datos tabulares.

---

## Comunas probadas

| Comuna | Código | Predios | Cobertura | Zoom |
|--------|--------|---------|-----------|------|
| San Felipe | 5601 | 7.526 | ~95% | 16 |
| Catemu | 5603 | 240 | ~90% | 14 |

> ¿Probaste en otra comuna? Abre un PR actualizando esta tabla.

---

## Cómo contribuir

Lee [CONTRIBUTING.md](CONTRIBUTING.md) para ver las tareas disponibles.

En resumen:
- **Reportar** una comuna que no funciona → abre un Issue
- **Agregar** un código de comuna al diccionario → PR directo
- **Probar** el script en una comuna nueva y reportar resultado → Issue con etiqueta `test-report`
- **Mejorar** la estimación automática del bbox → Issue `enhancement`

---

## Relación con catastral.cl

[catastral.cl](https://catastral.cl) es un proyecto que descargó los 9.5M predios de Chile en una operación masiva. Este script es complementario:

| | catastral.cl | sii-predios |
|---|---|---|
| Cobertura | Nacional completa | Por comuna |
| Actualización | Foto fija (2024) | Mensual automatizable |
| Infraestructura | 30 túneles VPN | 1 computador |
| Variables | 112 | ~20 |
| Uso | Dataset de referencia | Monitoreo y análisis local |

---

## Fuente de datos

- **API:** `www4.sii.cl/mapasui` (pública, sin autenticación)
- **Institución:** Servicio de Impuestos Internos de Chile
- **Sistema de referencia:** WGS84 — EPSG:4326
- **Actualización:** según fecha de extracción en nombre de archivo

---

## Licencia

MIT — ver [LICENSE](LICENSE)

Los datos extraídos son públicos según la Ley 20.285 de Transparencia y Acceso a la Información Pública. El script accede únicamente a información disponible en el visor público del SII sin autenticación.
