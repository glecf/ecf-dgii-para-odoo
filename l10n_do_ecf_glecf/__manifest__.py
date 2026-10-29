{
    "name": "e-CF DGII para Odoo",
    "summary": "Send your invoices to the DGII as electronic fiscal "
    "receipts (e-CF). Requires a GLECF account.",
    "description": """
Electronic invoicing (e-CF) for the Dominican Republic.

This module sends the invoices you post in Odoo to the DGII through the GLECF
service (https://glecf.com), which signs them with your digital certificate.
Your invoice data leaves Odoo only after you accept it in Settings.
""",
    "version": "17.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "author": "GLECF",
    "website": "https://glecf.com",
    "support": "soporte@glecf.com",
    "license": "OPL-1",
    "countries": ["do"],
    "depends": ["l10n_do_accounting"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/glecf_certificate_wizard_views.xml",
        "views/glecf_sequences_views.xml",
        "views/res_config_settings_views.xml",
        "views/account_move_views.xml",
        "views/report_invoice.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
