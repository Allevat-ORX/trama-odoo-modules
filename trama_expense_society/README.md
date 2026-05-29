# Trama - Gastos de Sociedad

Módulo Odoo 18 para gestionar gastos entre socios con distribución automática.

## Instalación

```bash
# En la VM .80 (Odoo 18)
cd /home/odoo18/custom-addons
git pull origin main  # o copiar el módulo manualmente

# Reiniciar Odoo
sudo systemctl restart odoo18

# Instalar desde Interfaz
Apps → Buscar "Gastos Sociedad" → Instalar
```

## Funcionalidades

### 1. Tipos de Sociedad (pre-configurados)
- **Sociedad Inicial 50/50**: Aleix 50%, Enrique 50%
- **Sociedad Nueva 51/40/9**: Aleix 51%, Enrique 40%, Méndez 9%

### 2. Gastos
- Vista Kanban (arrastrable por estado)
- Distribución automática según sociedad
- Estados: Borrador → Pendiente → Justificado → Pagado

### 3. Depósitos
- Registro de aportaciones de cada socio
- Estados: Pendiente → Confirmado

### 4. Sweat Equity
- Reconocimiento de aportaciones en especie
- Períodos configurables

### 5. Reportes
- **Estado de Cuenta**: PDF con resumen y detalle
- **Reporte de Gastos**: Listado simple

## Uso

1. Crear gasto en "Gastos de Sociedad → Gastos"
2. Seleccionar tipo de sociedad (distribución automática)
3. Ver saldos en "Reportes → Estado de Cuenta"
4. Exportar a PDF para revisión

## Datos migrados desde Excel

Los Excel originales se encuentran en:
- `Baixades/OnRentX_PostCierre_v2.xlsx` (societat nova)
- `Baixades/Estado de Cuenta Sociedad .xlsx` (societat inicial)
