# Cambios Phase 02 - UX Improvements

## Resumen
Fecha: 28-29 Marzo 2026
Branch: `feature/02-ux-improvements`
Status: ✅ Completado

---

## Cambios Detallados

### 1. wa_chatbot.py - Mejoras UX Chatbot

**Problema:** Chatbot no manejaba bien intervención humana y búsqueda de candidatos era estricta.

**Solución:**
```python
# NUEVO: Estados añadidos
WA_CHAT_STATES = [
    ("idle", "Sin contactar"),
    ("contacto_inicial", "Contacto inicial enviado"),
    ("prescreening", "Pre-screening en curso"),
    ("prescreening_eval", "Evaluando pre-screening"),
    ("pasa_pendiente_jcf", "Pasa - Pendiente comprobante JCF"),
    ("listo_entrevista", "Listo para entrevista"),
    ("atendido_humano", "Atendido por humano (bot pausado)"),  # ← NUEVO
    ("rechazado", "Rechazado en pre-screening"),
]

# NUEVO: Campo atendido_humano
atendido_humano = fields.Boolean(
    string="Atendido por humano",
    default=False,
    help="Cuando está activo, el bot no responde automáticamente",
)

# NUEVO: Liberar al bot
def action_liberar_bot(self):
    """Reactivar chatbot después de atención humana."""
    self.atendido_humano = False
```

**Archivos modificados:**
- `models/wa_chatbot.py`
- `views/hr_applicant_views.xml` (botón "Liberar al Bot")

---

### 2. hr_applicant.py - Fix WhatsApp Markup

**Problema:** Mensajes con HTML/Markup causaban errores en WASender.

**Solución:**
```python
# ANTES (causaba error):
message = Markup(f"Hola {name}...")

# DESPUÉS (limpia Markup):
def _send_wa_with_config(self, phone, message, config):
    if isinstance(message, Markup):
        message = str(message)
    # Limpia HTML
    import re
    message = re.sub(r'<[^>]+>', '', message)
    message = html.unescape(message)
```

**Archivos modificados:**
- `models/hr_applicant.py`

---

### 3. interview_webhooks.py - Fuzzy Matching

**Problema:** Matching de candidatos por nombre exacto fallaba frecuentemente.

**Solución:**
```python
import difflib

def _find_applicant_by_name(self, name, email=None):
    """Find applicant with fuzzy matching."""
    # Búsqueda exacta primero
    applicant = self.env['hr.applicant'].search([
        ('email_from', '=ilike', email)
    ], limit=1) if email else None

    if applicant:
        return applicant

    # Fuzzy matching por nombre
    applicants = self.env['hr.applicant'].search([
        ('create_date', '>=', fields.Date.today() - timedelta(days=30))
    ])

    best_match = None
    best_ratio = 0

    for app in applicants:
        ratio = difflib.SequenceMatcher(
            None,
            name.lower(),
            (app.partner_name or '').lower()
        ).ratio()
        if ratio > 0.8 and ratio > best_ratio:  # 80% similaridad
            best_ratio = ratio
            best_match = app

    return best_match
```

**Archivos modificados:**
- `controllers/interview_webhooks.py`

---

### 4. Wizard de Selección de Sender

**Nuevo:** Wizard para elegir qué número WASender usar.

```python
class WAChatbotStartWizard(models.TransientModel):
    _name = 'wa.chatbot.start.wizard'

    applicant_id = fields.Many2one('hr.applicant')
    wasender_config_id = fields.Many2one(
        'onrentx.wasender.config',
        string="Número de WhatsApp",
        domain=[('active', '=', True)]
    )

    def action_start_chatbot(self):
        self.applicant_id.start_wa_chatbot(
            sender_id=self.wasender_config_id.id
        )
```

**Archivos modificados:**
- `wizard/wa_chatbot_start_wizard.py` (nuevo)
- `wizard/wa_chatbot_start_wizard_view.xml` (nuevo)

---

## Archivos Afectados

| Archivo | Cambio | Líneas +/- |
|---------|--------|------------|
| `models/wa_chatbot.py` | Fuzzy matching, atendido_humano | +45 / -12 |
| `models/hr_applicant.py` | Fix Markup, botones | +23 / -8 |
| `controllers/interview_webhooks.py` | Fuzzy matching candidatos | +38 / -15 |
| `views/hr_applicant_views.xml` | Botón "Liberar al Bot" | +12 / -2 |
| `wizard/wa_chatbot_start_wizard.py` | Nuevo wizard | +89 / 0 |
| `wizard/wa_chatbot_start_wizard_view.xml` | Vista wizard | +45 / 0 |

---

## Testing Realizado

### Tests Manuales
- [x] Chatbot responde correctamente
- [x] Atención humana pausa bot
- [x] Liberar bot reactiva respuestas
- [x] WhatsApp sin errores de Markup
- [x] Fuzzy matching encuentra candidatos similares
- [x] Wizard selección sender funciona

### Tests Pendientes (Phase 03)
- [ ] Test automatizado fuzzy matching
- [ ] Test webhook con timeouts
- [ ] Test rate limiting

---

## Deployment

### VM .80
```bash
# Actualizar código
cd /opt/odoo/custom-addons/ModulosOdoo/onrentx_recruitment_booking
git pull origin feature/02-ux-improvements

# Reiniciar Odoo
sudo systemctl restart odoo

# Actualizar módulo (modo developer en Odoo)
# Apps > Update Apps List > Upgrade onrentx_recruitment_booking
```

### Commit Git
```bash
git add -A
git commit -m "feat(02): UX Improvements - Fuzzy matching + human control

- Add fuzzy matching for candidate search (80% threshold)
- Add atendido_humano field to pause bot
- Add 'Liberar al Bot' button
- Fix Markup handling in WhatsApp messages
- Add sender selection wizard for WhatsApp
- Improve webhook phone normalization"
```

---

## Referencias

- Plan completo: `.paul/phases/02-ux-improvements/02-01-PLAN.md`
- Issue relacionado: (crear en GitHub)
- PR: (crear hacia main)
