# Sanidad de CityPass+ Emergencias (Grupo 6)

Dashboard público con el estado de los servicios del módulo Emergencias y Seguridad
de CityPass+ (TPO DA2, UADE): **https://g6d2.github.io/sanidad/**

## Qué muestra

- **Servicios:** API principal, conexión Frontend → API (BFF), frontend y API de backup. Estado, latencia y franja de 30 días.
- **Latencia:** tendencia con las mediciones de este monitor, de los últimos 7 días.
- **Salud del dominio:** emergencias activas por estado, oficiales libres, motor de incidentes y outbox de eventos.
- **Errores:** logs de nivel error en Render (solo conteos), chequeos fallidos del día, outbox fallido y pipelines en rojo.
- **Pipelines:** último CI y CD de `main` en backend y frontend.

## Cómo funciona

El workflow `Chequeo de salud` (`.github/workflows/chequeo.yml`) corre `scripts/collect.py`:

1. Hace solo lecturas contra producción.
2. Escribe `site/data.json`.
3. Agrega la medición a `data/history.json`, donde se guardan 7 días y solo mediciones reales de este workflow.
4. Publica `site/` en GitHub Pages.

La franja de 30 días usa, en este orden:

1. Mediciones de este monitor.
2. Better Stack.
3. `data/historia.json`: días anteriores reconstruidos con evidencia real (deploys de Render, keep-warm de GitHub Actions y bitácora del equipo). Cada día indica su fuente al pasar el mouse.

**Cuándo corre:** cada 5 minutos, todo el día (y a mano desde **Actions → Chequeo de salud → Run workflow**). Cada chequeo despierta backend y frontend, que están en el plan free de Render (750 h/mes compartidas): si hace falta ahorrar horas, volver a una ventana acotada en el cron.

## Secrets (opcionales)

| Secret | Para qué | Si falta |
|---|---|---|
| `RENDER_API_KEY` | Contar logs de nivel error en Render | El bloque muestra "no configurado" |
| `GH_READ_TOKEN` | Leer los runs de Actions de los repos de la app (privados) | No se muestran pipelines |

La página es pública: solo publica conteos y estados, nunca mensajes de logs ni datos de personas.
