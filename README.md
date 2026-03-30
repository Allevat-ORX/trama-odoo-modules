# VM80 Recruitment Booking Module

Módulo Odoo 18 para sistema de reclutamiento automatizado. **Backend Trama/VM .80** - completamente independiente de OnRentX AWS.

---

## ⚠️ IMPORTANTE: Arquitectura Híbrida

Este módulo corre en **VM 192.168.0.80** (Trama) y consume servicios de IA desde **Oracle VM 159.54.142.132** (OnRentX-AI-System).

```
┌─────────────────┐         ┌───────────────────────┐
│   VM .80        │────────▶│   Oracle VM           │
│   (Este repo)   │  HTTP   │   159.54.142.132      │
│   Trama Odoo 18 │         │   - LiteLLM :4000     │
└─────────────────┘         │   - N8N :5678         │
                            │   - Fathom cron       │
                            └───────────────────────┘
                                    │
                                    ▼
                            ┌───────────────────────┐
                            │   Groq API            │
                            │   (llama-3.3-70b)     │
                            └───────────────────────┘
```

**Otros backends que comparten la IA:**
- **OnRentX AWS** (producción onrentx.com) - módulo de certificaciones
- **Trama APU** (en desarrollo)
- **Pro Services** (nuevo proyecto)

---

## Estructura del Módulo

```
onrentx_recruitment_booking/
├── __manifest__.py                 # Metadatos del módulo
├── __init__.py
│
├── models/
│   ├── __init__.py
│   ├── hr_applicant.py            # Booking + WA welcome
│   ├── wa_chatbot.py              # Chatbot estados + LLM
│   ├── survey_auto_trigger.py     # Evaluación survey AI
│   └── resource_booking.py        # Integración resource_booking
│
├── controllers/
│   ├── __init__.py
│   └── interview_webhooks.py      # Fathom webhook + evaluación AI
│
├── wizards/
│   ├── __init__.py
│   ├── wa_chatbot_start_wizard.py
│   └── wa_chatbot_start_wizard_view.xml
│
├── views/
│   └── hr_applicant_views.xml     # UI botones + campos
│
├── data/
│   ├── ir_config_parameter_data.xml   # Parámetros sistema
│   ├── booking_type_data.xml          # Config booking type
│   └── cron_survey_reminder.xml       # Cron survey
│
└── security/
    └── ir.model.access.csv
```

---

## Componentes Principales

### 1. `hr_applicant.py` - Booking de Entrevistas

**Campos añadidos:**
- `booking_id` - Relación con `resource.booking`
- `booking_state` - Estado de la cita
- `booking_portal_url` - URL portal candidato (dinámica)
- `booking_start` - Fecha/hora entrevista

**Métodos:**
- `action_create_interview_booking()` - Crea booking + abre email
- `action_cancel_booking()` - Cancela cita
- `_send_welcome_wa()` - Envía WA automático al postularse

### 2. `wa_chatbot.py` - Chatbot Pre-screening

**Estados del chatbot:**
```
idle → contacto_inicial → prescreening → prescreening_eval
                                          ↓
                    rechazado ←───────────┼──→ pasa_pendiente_jcf → listo_entrevista
```

**Campos:**
- `wa_chat_state` - Estado actual
- `wa_chat_data` - JSON con historial

**Integración LiteLLM:**
- Endpoint: `http://159.54.142.132:4000/v1/chat/completions`
- Modelo: `groq/llama-3.3-70b-versatile`
- Temperatura: 0.7

### 3. `interview_webhooks.py` - Webhooks + Evaluación AI

**Endpoints expuestos:**
- `POST /api/recruitment/fathom-webhook` - Recibe transcripciones Fathom
- `POST /api/recruitment/interview-summary` - Entradas manuales

**Evaluación AI:**
- Trigger: Después de recibir resumen Fathom
- Prompt: Extrae requisitos del puesto, evalúa 1-5
- Output: Tabla HTML + veredicto en chatter

### 4. `survey_auto_trigger.py` - Cuestionario

**Trigger:**
- Al completar survey (`state = done`)
- Evalúa respuestas con LiteLLM
- Notifica a Aleix por WA con score

---

## Configuración

### Parámetros del Sistema (`ir.config_parameter`)

| Clave | Valor | Descripción |
|-------|-------|-------------|
| `recruitment.litellm_url` | `http://159.54.142.132:4000` | Endpoint LiteLLM |
| `recruitment.litellm_api_key` | `***` | API Key |
| `web.base.url` | `https://odoo.tramarental.com` | URL base |

### Booking Type

**Nombre:** "Entrevista de Reclutamiento OnRentX"
- Resource: Aleix (id=21)
- Duración: 45 min
- Buffer: 15 min
- Horarios: L-V 11:30-18:00, Sáb 11:30-14:00
- Videollamada: Google Meet automático

### WASender Config

**Sesiones:**
- San Luis (id=4) - Principal
- Querétaro (id=3) - Secundario
- León (id=2) - Evitar (logged out)

**Webhook:** `https://odoo.tramarental.com/whatsapp/webhook`

---

## Dependencias

```python
depends = [
    "hr_recruitment",           # Base reclutamiento
    "resource_booking",         # Agenda
    "hr_applicant_whatsapp",    # WhatsApp base
    "survey",                   # Cuestionarios
]
```

---

## Acceso VM .80

```bash
ssh linux-odoo@192.168.0.80
# Password: VenpgeFfT5ss2Hjm

# Path módulo
/opt/odoo/custom-addons/ModulosOdoo/onrentx_recruitment_booking/

# Logs Odoo
tail -f /var/log/odoo/odoo-server.log | grep -i "recruitment\|chatbot\|fathom"

# Reiniciar Odoo
sudo systemctl restart odoo
```

---

## Cambios Realizados (Historial)

| Fecha | Cambio | Archivo |
|-------|--------|---------|
| 2026-03-27 | Fuzzy matching + atendido_humano | `wa_chatbot.py` |
| 2026-03-28 | Fix webhook búsqueda teléfono | `interview_webhooks.py` |
| 2026-03-28 | Fix Markup WA | `hr_applicant.py` |

---

## Relación con Otros Repos

| Repo | Propósito | Conexión |
|------|-----------|----------|
| `OnRentX-AI-System` | IA compartida (LiteLLM) | HTTP a Oracle VM |
| `onrentx-odoo-modules` | AWS OnRentX producción | **NO relacionado** |
| `onrentx-web` | Frontend web | **NO relacionado** |

---

## TODOs Críticos

1. [ ] **MIGRAR API KEYS** de hardcoded a `ir.config_parameter`
2. [ ] **RATE LIMITING** en webhooks
3. [ ] **DEDUPLICACIÓN** mejorada Fathom
4. [ ] **TESTS** automatizados

---

*Código extraído de VM .80 el 29/03/2026*
*Versión: 18.0.1.0.0*
