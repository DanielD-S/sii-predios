# Contribuir a sii-predios

Gracias por tu interés en mejorar el proyecto. Aquí hay formas concretas de contribuir.

---

## Reportar problemas

Si el script falla con una comuna específica, abre un Issue con:
- Código de la comuna
- Comando exacto que ejecutaste
- Mensaje de error completo
- Sistema operativo y versión de Python

---

## Tareas disponibles

### 🟢 Fácil — buen punto de entrada

- **Agregar códigos de comunas** al diccionario `COMUNAS` en `sii_predios_completo.py`
  - Instrucciones en la sección "Encontrar código de comuna" del README
  - Solo requiere editar un diccionario Python

- **Probar en una comuna nueva** y reportar resultado
  - Ejecutar el script, capturar pantalla en QGIS
  - Abrir Issue con etiqueta `test-report` con: comuna, predios obtenidos, cobertura visual

- **Mejorar mensajes de error** — cuando falla, que diga exactamente qué hacer

### 🟡 Medio

- **Mejorar estimación automática del bbox**
  - Actualmente usa zoom y centro de la API, pero el bbox resultante es a veces demasiado grande
  - Idea: usar los límites administrativos de la comuna desde otra fuente (BCN, INE)

- **Soporte múltiples comunas en secuencia**
  - `python sii_predios_completo.py --comunas 5601,5603,5604`
  - Con pausa entre comunas para no sobrecargar el servidor

- **Script de validación de cobertura**
  - Comparar predios extraídos vs total esperado
  - Generar mapa de calor de zonas sin datos

- **Fix encoding Windows**
  - Algunos caracteres especiales en logs fallan en PowerShell con cp1252
  - Solución: `sys.stdout.reconfigure(encoding='utf-8')` al inicio

### 🔴 Difícil

- **Identificar endpoint de datos constructivos**
  - El visor SII tiene más info (pisos, año construcción, materiales)
  - Hay que inspeccionar Network en DevTools al hacer clic en "ver detalle"
  - Documentar el endpoint y agregar los campos al script

- **Descarga incremental**
  - Solo descargar predios nuevos o con avalúo modificado desde la última extracción
  - Requiere comparar con la extracción anterior

- **Soporte comunas grandes**
  - Santiago, Maipú, Puente Alto tienen >100k predios
  - Problema: memoria RAM y tiempo de ejecución
  - Idea: dividir en subzonas y unir al final

- **Mejorar vectorización**
  - Actualmente usa un rango de color fijo RGB(165-200, 205-240, 215-248)
  - Algunas comunas tienen colores WMS ligeramente distintos
  - Idea: detección automática del color dominante

---

## Cómo hacer un Pull Request

1. Fork del repositorio
2. Crear rama: `git checkout -b mi-mejora`
3. Hacer cambios y probar
4. Commit: `git commit -m "descripcion clara del cambio"`
5. Push: `git push origin mi-mejora`
6. Abrir Pull Request describiendo qué cambia y por qué

---

## Encontrar el código SII de una comuna

```python
import requests, time

# Buscar por coordenadas conocidas de la comuna
# lat/lon aproximados de la capital comunal
lat_objetivo, lon_objetivo = -33.45, -70.65  # ejemplo: Santiago

for cod in range(1300, 1400):  # ajustar rango según región
    try:
        r = requests.post(
            'https://www4.sii.cl/mapasui/services/data/mapasFacadeService/getServicioPredio',
            json={
                'data': {'comuna': cod, 'eac': -1},
                'metaData': {
                    'namespace': 'cl.sii.sdi.lob.bbrr.mapas.data.api.interfaces.MapasFacadeService/getServicioPredio',
                    'conversationId': 'test', 'transactionId': 'test'
                }
            },
            headers={
                'Content-Type': 'application/json',
                'Referer': 'https://www4.sii.cl/mapasui/index.html',
                'Origin': 'https://www4.sii.cl'
            },
            timeout=5
        )
        data = r.json().get('data', [])
        if isinstance(data, list) and data:
            d = data[0]
            lat = d.get('latitud', 0)
            lon = d.get('longitud', 0)
            if abs(lat - lat_objetivo) < 0.5 and abs(lon - lon_objetivo) < 0.5:
                print(f"cod={cod} lat={lat:.4f} lon={lon:.4f} layer={d.get('layer')}")
        time.sleep(0.2)
    except:
        pass
```

---

## Código de conducta

- Respetar el servidor del SII — no hacer requests masivas en paralelo
- Los datos son públicos pero referenciales — no presentarlos como datos oficiales
- Mantener el código legible y documentado
