# Porabot

Un bot personal de Telegram para programar recordatorios y realizar seguimiento de hábitos. La característica principal: crea una tarea con una sola frase en **ruso, inglés o español** — sin asistente, sin menú; el bot interpreta cuándo.

> **TODO (propietario):** añadir GIF de creación de tarea con frase, completar un hábito e informe semanal.

También disponible en: [English](README.md) · [Русский](README.ru.md)

---

## Funcionalidades

### Creación de tareas con lenguaje natural

Escribe una frase; el bot extrae la hora y la descripción de la tarea:

| Entrada | Resultado |
|---|---|
| `вечером принять лекарство` | recordatorio a las 19:00 hoy — «принять лекарство» |
| `завтра в 10 утра позвонить маме` | mañana 10:00 — «позвонить маме» |
| `in 15 minutes check the oven` | en 15 minutos — «check the oven» |
| `mañana a las 9 tomar vitaminas` | mañana 09:00 — «tomar vitaminas» |
| `каждый день в 9 тренировка` | recurrencia diaria a las 09:00 — «тренировка» |

### Recordatorios

- **Únicos y recurrentes** — diario, semanal o cualquier patrón expresable con `RRULE`
- **Modo nagging** — recordatorio cada 5 minutos, limitado por un máximo configurable por tarea (por defecto 3)
- **Posponer** — +15 min / +1 hora / mañana
- **Adjuntos multimedia** — foto o vídeo enviados junto al texto del recordatorio
- **Horas silenciosas** — ventana configurable en la que se suprimen todas las notificaciones
- **Recuperación de tareas perdidas** — tras reiniciar el bot, un resumen de tareas perdidas con botones «completar todo» o «posponer todo»

### Hábitos

- **Modo de hora fija** — el hábito se activa a una hora específica cada día; racha contada por marca de tiempo `due_at`
- **Modo fluido** — el hábito debe completarse en cualquier momento del día local; racha contada por fecha
- **Plantillas** — plantillas de hábitos habituales seleccionables al crearlos
- **Rachas** — `habit_streak_current` / `habit_streak_best` por hábito fijo; `fluid_streak_current` / `fluid_streak_best` para hábitos fluidos
- **Deshacer** — revierte la última compleción y restaura la racha al valor anterior (flag `habit_undo_pending`)
- **«Hoy no»** — registra el ciclo como `not_today` sin romper la racha
- **Sweeper de hábitos** — tarea en segundo plano que cada minuto detecta ciclos perdidos desde el estado de la BD, independientemente de si se envió alguna notificación
- **Informes semanales y mensuales** — tasas agregadas de completado / hoy-no / perdido por hábito, enviadas en el día y hora configurados por el usuario

### General

- **Tres idiomas** — i18n completa en ruso, inglés y español (`bot/lexicon/ru.py`, `en.py`, `es.py`); el análisis de tiempo en lenguaje natural funciona en los tres
- **Zonas horarias** — todo almacenado en UTC; mostrado en la zona horaria local del usuario; transiciones DST correctamente gestionadas
- **Resúmenes matutinos y nocturnos** — resumen diario a horas configurables
- **Configuración por usuario** — zona horaria, idioma, horas silenciosas, límite de nagging, horario de informes

---

## Decisiones de ingeniería destacadas

1. **Todo en UTC, conversión solo al mostrar.** `execution_time` se almacena como UTC naive en SQLite. La conversión a hora local ocurre únicamente al formatear mensajes. `reconcile_jobs_with_db` recalcula la siguiente ocurrencia RRULE en la zona horaria del usuario para que un reinicio durante un cambio DST no desplace la hora de pared de los recordatorios diarios. Cubierto por `test_dst_recurring_reminder.py` y `test_reconcile_dst_next_occurrence.py`.

2. **APScheduler persistente con reconciliación al inicio.** Los trabajos sobreviven a reinicios mediante `SQLAlchemyJobStore`. Al arrancar, `reconcile_jobs_with_db` revisa todos los recordatorios `pending`: si falta el trabajo (perdido por tiempo de inactividad), programa uno de recuperación en «+1 minuto». `last_fired_at` evita reenviar notificaciones ya entregadas.

3. **Registro de eventos de hábitos idempotente.** `HabitEvent` tiene `UNIQUE(reminder_id, cycle_key)`. `cycle_key` es `due:<unix_ts>` para hábitos fijos y `day:<YYYY-MM-DD>` para fluidos. `HabitEventDAO.record()` comprueba primero si existe la fila; en condiciones de carrera usa `SAVEPOINT` (`begin_nested`) en lugar de `session.rollback()` — el rollback completo habría descartado silenciosamente todos los demás cambios pendientes del mismo Unit of Work.

4. **`forbidden_strikes` contra reintentos infinitos.** Cada `TelegramForbiddenError` (usuario bloqueó el bot) incrementa `forbidden_strikes`. Al alcanzar `FORBIDDEN_STRIKES_LIMIT = 3`, el recordatorio queda excluido de la reconciliación y el nagging se detiene.

5. **Sweeper desacoplado de la entrega de notificaciones.** `habit_sweeper.py` detecta ciclos perdidos desde el estado de la BD cada minuto, sin depender del momento del envío. Un ciclo perdido se registra aunque el bot estuviera caído o el usuario lo hubiera bloqueado.

6. **Capa DAO + raíz de composición.** Todo el acceso a la BD pasa por DAOs tipados (`ReminderDAO`, `UserDAO`, `HabitEventDAO`). `bot/__main__.py` es la raíz de composición: monta la infraestructura sin lógica de negocio.

7. **Parser NLP delegado al pool de hilos.** `DatesExtractor` de natasha no es reentrante; todas las llamadas se serializan con `threading.Lock`. El pipeline síncrono de análisis se ejecuta mediante `loop.run_in_executor` para no bloquear el event loop de asyncio.

---

## Cobertura de tests

```
135 tests recopilados   (python -m pytest --collect-only -q)
```

Los tests siguen el estilo de regresión: cada test se escribió para fallar con el bug concreto que protege, y luego se aplicó la corrección. Los archivos de test viven junto al código fuente en `bot/services/`.

CI (`deploy.yml`) ejecuta todos los tests en cada push a `main` y bloquea el despliegue si alguno falla — el VPS solo se actualiza si los 135 tests pasan.

---

## Artefactos del proceso

Tres rondas formales de auditoría propia forman parte del historial de este repositorio y se mantienen visibles a propósito — son evidencia del proceso de desarrollo:

- [`AUDIT.md`](AUDIT.md) — auditoría del commit `ea1ced1`; 17 hallazgos (C1–C4, W1–W8, N1–N7) con prioridad P0/P1/P2, comandos de reproducción y estado de corrección
- [`REWORK_PLAN.md`](REWORK_PLAN.md) — plan de mejoras de la fase 1 generado a partir de la auditoría
- [`REWORK_PLAN_2.md`](REWORK_PLAN_2.md) — plan de la fase 2: estadísticas de hábitos, sweeper e informes

---

## Instalación y ejecución

### Docker Compose (recomendado)

```bash
cp .env.example .env
# rellenar BOT_TOKEN y ADMIN_ID
docker compose up -d
```

Las bases de datos SQLite se persisten en `./data/`.

### Ejecución local

```bash
python3 -m venv .venv && source .venv/bin/activate

# la cadena natasha → yargy → pymorphy2 → docopt falla con setuptools ≥ 81
pip install "setuptools<81" wheel
pip install -r requirements.txt

python -m bot
```

Para una instalación totalmente reproducible, usar el archivo bloqueado:

```bash
pip install "setuptools<81" wheel
pip install -r requirements.lock
```

### Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `BOT_TOKEN` | ✅ | Token de la Telegram Bot API |
| `ADMIN_ID` | ✅ | ID de Telegram del propietario |
| `ALLOWED_USERS` | — | Array JSON de IDs de usuarios permitidos |
| `TZ` | — | Zona horaria por defecto (ej. `Europe/Moscow`) |
| `DATABASE_URL` | — | URL de SQLite; por defecto `sqlite+aiosqlite:///porabot.db` |
| `SCHEDULER_DB_URL` | — | URL del jobstore de APScheduler; por defecto `sqlite:///jobs.sqlite` |

---

## Limitaciones conocidas

- **Jobs minutales O(usuarios).** Cuatro tareas cron (resúmenes, recuperación de perdidos, sweeper de hábitos, informes) escanean a todos los usuarios cada minuto. Aceptable para un bot personal; no escala a miles de usuarios sin indexación adicional. Registrado como W4 en `AUDIT.md`.
- **Entrada de zona horaria solo en horas enteras.** El onboarding acepta desplazamientos UTC enteros (ej. `+3`). Los desplazamientos de media hora o cuarto de hora (India, Nepal, Irán, etc.) requieren configuración manual en `.env`. W8 en `AUDIT.md`.
- **La whitelist está implementada pero desactivada por defecto.** `WhitelistMiddleware` existe en `bot/middlewares/whitelist.py`. Activarla en `bot/__main__.py` antes de un despliegue público.

---

## Origen

Desarrollado con asistencia de IA — el propietario (psicólogo clínico) actúa como operador y diseñador de producto; el código se escribe en pareja con agentes de IA bajo la dirección del propietario.
