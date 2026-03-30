# Changelog - Recruitment Booking Module

Todos los cambios notables en el módulo `onrentx_recruitment_booking`.

## [Unreleased]

### Security
- Migrar API keys hardcodeadas a `ir.config_parameter`
- Implementar rate limiting en webhooks
- Agregar signature verification para webhooks Fathom

## [2.0.0] - 2026-03-28 (Phase 02 - UX Improvements)

### Added
- **Fuzzy matching** para búsqueda de candidatos por nombre (evita falsos negativos)
- **Campo `atendido_humano`** para pausar chatbot cuando humano toma control
- **Botón "Liberar al Bot"** para reactivar chatbot después de atención humana
- **Fix Markup** en envío de WhatsApp (evita errores con HTML)
- **Wizard de selección de sender** para WhatsApp (San Luis vs Querétaro)

### Fixed
- **Webhook WhatsApp**: Normalización de teléfono (últimos 10 dígitos)
- **Búsqueda candidatos**: Ahora usa `ilike` con número normalizado
- **Estados del chatbot**: Transición más clara entre estados

### Changed
- `wa_chatbot.py`: Refactor para soportar `atendido_humano`
- `hr_applicant.py`: Fix `_send_wa_with_config` para limpiar Markup
- `interview_webhooks.py`: Mejor matching por nombre similar

## [1.1.0] - 2026-03-27 (Phase 01 - Security & Stability)

### Added
- **Deduplicación** de transcripciones Fathom por `share_url`
- **Cron survey reminder** para recordatorios automáticos
- **Parámetros de sistema** iniciales para configuración

### Fixed
- **API keys expuestas**: Identificadas en múltiples archivos (pendiente migración)
- **LiteLLM timeout**: Aumentado a 60 segundos
- **Error handling** en webhook Fathom

### Security
- Nota: API keys aún hardcodeadas - migración planificada Phase 03

## [1.0.0] - 2026-03-25 (MVP - Release Inicial)

### Added
- **Booking de entrevistas** integrado con `resource_booking`
- **WhatsApp bidireccional** (envío y recepción)
- **Chatbot pre-screening** con estados y LLM
- **Webhook Fathom** para transcripciones automáticas
- **Evaluación AI post-entrevista** con Groq Llama 3.3
- **Survey auto-trigger** - evaluación automática de cuestionarios
- **Portal candidato** tokenizado (sin login)
- **Integración Google Calendar** + Google Meet automático

### Features
- Estados del chatbot: `idle` → `contacto_inicial` → `prescreening` → `prescreening_eval` → `[pasa|rechazado]`
- Scoring 1-5 por requisito del puesto
- Comparación con otros candidatos del mismo puesto
- Banderas rojas en evaluación
- Link a grabación Fathom en chatter

### Technical
- Modelo LLM: `groq/llama-3.3-70b-versatile`
- Endpoint LiteLLM: `http://159.54.142.132:4000`
- Odoo 18.0
- Depends: `hr_recruitment`, `resource_booking`, `hr_applicant_whatsapp`, `survey`

---

## Notas de Versión

### Versionado
Usamos [Semantic Versioning](https://semver.org/):
- `MAJOR` - Cambios breaking (API, BD, flujos)
- `MINOR` - Nuevas features (compatibles hacia atrás)
- `PATCH` - Fixes y mejoras menores

### Tags Git
```bash
# Crear tag de versión
git tag -a v2.0.0 -m "Phase 02 - UX Improvements"
git push origin v2.0.0
```

### Branches
- `main` - Producción estable
- `feature/*` - Nuevas features
- `hotfix/*` - Urgentes a producción

---

*Changelog iniciado el 29/03/2026*
*Última actualización: Phase 02 completado*
