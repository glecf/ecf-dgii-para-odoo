{
    "name": "e-CF DGII para Odoo",
    "summary": "Facturación electrónica e-CF para República Dominicana: envía tus "
    "facturas de Odoo a la DGII según la Ley 32-23. Requiere una cuenta de GLECF.",
    "description": """
Facturación electrónica (e-CF) para República Dominicana.

Este módulo envía a la DGII, como comprobantes fiscales electrónicos (e-CF), las
facturas que publicas en Odoo, según la Ley núm. 32-23 de Facturación
Electrónica y el Decreto 587-24. Los firma y los envía GLECF
(https://glecf.com) con tu certificado digital. Los datos de tus facturas solo
salen de Odoo después de que lo aceptes en Ajustes.
""",
    "version": "17.0.1.1.1",
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
        "views/received_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "l10n_do_ecf_glecf/static/src/js/glecf_refresh.js",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
