# Marquesina Bursátil local

Cinta horizontal con las 10 especies más negociadas de la rueda de renta variable de la
Bolsa de Valores de Colombia. Muestra nemotécnico, último precio y variación porcentual,
con la fecha de los datos y la hora de actualización.

Fuente: `https://www.bvc.com.co/mercado-local-en-linea?tab=renta-variable_mercado-local`

## Cómo funciona

- `scraper/scrape_bvc.py` abre la página con Playwright (la tabla se pinta con React, por eso
  no sirve `requests`), lee las primeras 10 filas de la tabla —que viene ordenada por volumen
  descendente— y escribe `docs/data/bvc.json`.
- La fecha se toma del selector de la propia página (`input[name="date"]`). Sábados, domingos
  y festivos ese selector ya trae el último día hábil, así que la marquesina muestra ese cierre
  y lo dice explícitamente. Si el selector no se puede leer, el script calcula el último día
  hábil con el calendario de festivos colombianos (incluye la Ley Emiliani).
- `.github/workflows/scrape.yml` ejecuta el scraper cada 10 minutos durante la rueda
  (9:30–15:55 COT, lunes a viernes), un cierre a las 16:15 COT y un refresco diario el fin de
  semana. Hace commit del JSON solo si cambió.
- `docs/index.html` lee el JSON y anima la cinta. Se recarga sola cada 5 minutos.

## Montar el repositorio

```bash
git init
git add .
git commit -m "Marquesina bursátil local"
git branch -M main
git remote add origin https://github.com/Tribudata/marquesina-bursatil-local.git
git push -u origin main
```

Después, en GitHub:

1. **Settings → Pages**: Source `Deploy from a branch`, rama `main`, carpeta `/docs`.
2. **Settings → Actions → General → Workflow permissions**: `Read and write permissions`
   (el workflow necesita hacer commit del JSON).
3. **Actions → Actualizar marquesina bursátil → Run workflow** para la primera carga.

La marquesina queda en `https://tribudata.github.io/marquesina-bursatil-local/`.

## Probar en local

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
python -m playwright install chromium
python scraper/scrape_bvc.py
python -m http.server 8000 --directory docs        # abre http://localhost:8000
```

## Incrustar en otro sitio

```html
<iframe src="https://tribudata.github.io/marquesina-bursatil-local/"
        style="width:100%;height:64px;border:0" title="Bursátil local" loading="lazy"></iframe>
```

Para que el iframe muestre solo la cinta, borra el párrafo `<p class="estado">` de `index.html`
o dale `display:none`.

## Ajustes frecuentes

| Qué | Dónde |
| --- | --- |
| Número de especies | `TOP_N` en `scraper/scrape_bvc.py` |
| Velocidad de la cinta | `animation:correr 42s` en `docs/index.html` |
| Colores de alza/baja | variables `--alza` / `--baja` en `docs/index.html` |
| Frecuencia de scraping | bloque `schedule` en `.github/workflows/scrape.yml` (cron en UTC) |

Si la BVC cambia la estructura de la tabla, el punto único a tocar es la constante
`EXTRACT_JS` del scraper: allí están los índices de columna (0 nemotécnico, 2 variación,
3 volumen, 10 emisor).
