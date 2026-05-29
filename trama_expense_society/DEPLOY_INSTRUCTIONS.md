# Deploy Instructions - trama_expense_society v1.6.0

## Bug Fixed
- **O2BF-1155**: PDF reports (Estado de Cuenta) were generating empty - only header visible

## Changes
1. **reports/trama_society_report.xml**: Fixed QWeb templates to use `data` parameter from wizard report_action
2. **models/trama_society_deposit.py**: Added missing `income_id` Many2one field (required by `trama.personal.income.deposit_ids` One2many inverse)

## Commit
- Commit: `a769d8a` on `main` branch
- Files changed: 2

## Deployment Steps (VM .80 - 192.168.0.80:8069)

### Option A: Via Odoo Web Interface (Recommended)
1. Login to http://192.168.0.80:8069 as `admin.odoo@onrentx.com`
2. Activate developer mode: Settings → Activate Developer
3. Apps → Update Apps List
4. Search "trama_expense_society"
5. Click "Upgrade" button
6. Wait for upgrade to complete

### Option B: Via VirtualBox Guest Control (Windows Server Host)
```powershell
# Run on Windows Server (192.168.0.50)
$VMName = "Odoo18_OnRentX_Test"
$VBoxPath = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"

# Copy module files
& $VBoxPath guestcontrol $VMName run --username root --password odooserver -- rm -rf /opt/odoo/addons/trama_expense_society
& $VBoxPath guestcontrol $VMName copyto --username linux-odoo --password odooserver --target-path /tmp/ C:\Users\Aleix\Desktop\trama_expense_society.zip
& $VBoxPath guestcontrol $VMName run --username root --password odooserver -- /bin/bash -c "unzip -o /tmp/trama_expense_society.zip -d /opt/odoo/addons/trama_expense_society/"
& $VBoxPath guestcontrol $VMName run --username root --password odooserver -- chown -R odoo:odoo /opt/odoo/addons/trama_expense_society
& $VBoxPath guestcontrol $VMName run --username root --password odooserver -- systemctl restart odoo
```

## Test Steps
1. Go to Societies → Reports → Estado de Cuenta
2. Select a society type and date range
3. Click "Generar Report Individual"
4. Verify PDF contains:
   - Budget vs Actual table (categories with budgeted, spent, remaining, % used)
   - Invoice Status summary (total, with invoice, without invoice, % justified)
   - Partner Summary table (gastos que le corresponden, depositado, sweat equity, saldo)
   - Expense Detail table (date, concept, total, per-partner amounts, state)

## Expected Result
PDF should now show full content, not just header.

## Rollback
If issues occur, restore from backup:
```bash
# On VM
tar -xzf /tmp/trama_backup_*.tar.gz -C /opt/odoo/addons/
systemctl restart odoo
```
