"""Turn a posted Odoo invoice into the e-CF data the DGII expects.

The keys are the element names of the DGII e-CF format (Encabezado, IdDoc,
Emisor...). GLECF builds the XML, signs it and sends it, so this file only has
to get the data right. Every rule that would make the DGII reject a document is
checked here first, so the person posting the invoice learns it in Odoo.
"""

from datetime import datetime

import pytz

from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, float_round

DR_TZ = pytz.timezone("America/Santo_Domingo")

# e-CF types this version sends. The others (41, 43, 46, 47) need data Odoo
# does not keep on a sales invoice.
SUPPORTED_TYPES = ("31", "32", "33", "34", "44", "45")
# Types that carry the expiry date of the sequence.
TYPES_WITH_SEQUENCE_EXPIRY = ("31", "33", "44", "45")
# Types whose buyer must have an RNC or cédula.
TYPES_WITH_BUYER_ID = ("31", "33", "34", "44", "45")

# ITBIS rate -> DGII IndicadorFacturacion.
ITBIS_INDICATORS = {18.0: "1", 16.0: "2"}
EXEMPT = "4"

MAX_ITEM_NAME = 80
MAX_ITEM_DESCRIPTION = 1000
MAX_REASON = 90


def ecf_type(move):
    """'31' for an E31 document, '' if the invoice is not an e-CF."""
    prefix = move.l10n_latam_document_type_id.doc_code_prefix or ""
    return prefix[1:] if prefix.startswith("E") else ""


def build_ecf_data(move, now=None):
    """The `ecf_data` GLECF receives for `move`."""
    move.ensure_one()
    tipo = ecf_type(move)
    _check_invoice(move, tipo)

    items, totals = _items_and_totals(move, tipo)
    header = {
        "Version": "1.0",
        "IdDoc": _id_doc(move, tipo),
        "Emisor": _issuer(move),
        "Comprador": _buyer(move, tipo),
        "Totales": totals,
    }
    data = {
        "Encabezado": header,
        "DetallesItems": items,
        "FechaHoraFirma": _datetime(now or datetime.now(pytz.utc)),
    }
    if tipo in ("33", "34"):
        data["InformacionReferencia"] = _reference(move, tipo)
    return data


# -- Checks ------------------------------------------------------------------


def _check_invoice(move, tipo):
    if tipo not in SUPPORTED_TYPES:
        raise UserError(
            _("%(doc)s: e-CF type %(type)s is not supported yet by this module.")
            % {"doc": move.name, "type": tipo or "-"}
        )
    if move.currency_id != move.company_id.currency_id or move.currency_id.name != "DOP":
        raise UserError(
            _("%s: this version only sends invoices in Dominican pesos (DOP).") % move.name
        )
    if not move.l10n_do_fiscal_number:
        raise UserError(_("%s has no e-NCF number.") % move.name)
    if not move.invoice_date:
        raise UserError(_("%s has no invoice date.") % move.name)


# -- Header ------------------------------------------------------------------


def _id_doc(move, tipo):
    id_doc = {
        "TipoeCF": tipo,
        "eNCF": move.l10n_do_fiscal_number,
    }
    if tipo in TYPES_WITH_SEQUENCE_EXPIRY:
        if not move.l10n_do_ncf_expiration_date:
            raise UserError(
                _("%s: set the expiry date of the e-NCF sequence («Valid until») before sending it.")
                % move.name
            )
        id_doc["FechaVencimientoSecuencia"] = _date(move.l10n_do_ncf_expiration_date)
    if tipo == "34":
        id_doc["IndicadorNotaCredito"] = _credit_note_indicator(move)
    # Amounts are sent without ITBIS.
    id_doc["IndicadorMontoGravado"] = "0"
    id_doc["TipoIngresos"] = move.l10n_do_income_type or "01"

    if tipo == "34" or move.payment_state in ("paid", "in_payment"):
        id_doc["TipoPago"] = "1"
    else:
        # On credit: the DGII requires the payment deadline, never before the
        # issue date.
        id_doc["TipoPago"] = "2"
        due = max(move.invoice_date_due or move.invoice_date, move.invoice_date)
        id_doc["FechaLimitePago"] = _date(due)
    return id_doc


def _issuer(move):
    company = move.company_id
    rnc = _digits(company.vat)
    if not rnc:
        raise UserError(_("Set the RNC of %s (Tax ID) before sending e-CF.") % company.name)
    address = _one_line(" ".join(filter(None, [company.street, company.street2, company.city])))
    if not address:
        raise UserError(_("Set the address of %s before sending e-CF.") % company.name)
    issuer = {
        "RNCEmisor": rnc,
        "RazonSocialEmisor": _one_line(company.name)[:150],
        "DireccionEmisor": address[:100],
        "FechaEmision": _date(move.invoice_date),
    }
    if company.email:
        issuer["CorreoEmisor"] = company.email.strip()[:80]
    return issuer


def _buyer(move, tipo):
    partner = move.commercial_partner_id
    buyer_id = _digits(partner.vat)
    if tipo in TYPES_WITH_BUYER_ID and len(buyer_id) not in (9, 11):
        raise UserError(
            _("%(doc)s: the customer %(name)s needs a valid RNC (9 digits) or cédula (11 digits).")
            % {"doc": move.name, "name": partner.name}
        )
    buyer = {}
    if len(buyer_id) in (9, 11):
        buyer["RNCComprador"] = buyer_id
    if partner.name:
        buyer["RazonSocialComprador"] = _one_line(partner.name)[:150]
    if partner.email:
        buyer["CorreoComprador"] = partner.email.strip()[:80]
    address = _one_line(" ".join(filter(None, [partner.street, partner.street2, partner.city])))
    if address:
        buyer["DireccionComprador"] = address[:100]
    return buyer


# -- Lines and totals --------------------------------------------------------


def _items_and_totals(move, tipo):
    company = move.company_id
    currency = move.currency_id
    itbis_group = move.env.ref("account.%s_tax_group_itbis" % company.id, raise_if_not_found=False)

    lines = move.invoice_line_ids.filtered(lambda line: line.display_type == "product")
    if not lines:
        raise UserError(_("%s has no invoice lines.") % move.name)

    items = []
    bases = {"1": 0.0, "2": 0.0, EXEMPT: 0.0}
    for number, line in enumerate(lines, start=1):
        indicator = _line_indicator(move, line, tipo, itbis_group)
        if float_compare(line.quantity, 0, precision_digits=4) <= 0:
            raise UserError(_("%(doc)s: line «%(line)s» needs a positive quantity.") % {"doc": move.name, "line": line.name})

        net = currency.round(abs(line.price_subtotal))
        if line.discount and float_compare(line.discount, 100, precision_digits=2) >= 0:
            raise UserError(_("%(doc)s: line «%(line)s» has a 100%% discount.") % {"doc": move.name, "line": line.name})
        gross = currency.round(net / (1 - (line.discount or 0) / 100))
        discount = currency.round(gross - net)
        unit_price = _unit_price(gross, line.quantity)

        item = {
            "NumeroLinea": str(number),
            "IndicadorFacturacion": indicator,
            **_item_names(line.name or line.product_id.display_name),
            "IndicadorBienoServicio": "2" if line.product_id.type == "service" or not line.product_id else "1",
            "CantidadItem": _number(line.quantity, 2),
            "PrecioUnitarioItem": unit_price,
        }
        if not float_is_zero(discount, precision_digits=2):
            item["DescuentoMonto"] = _amount(discount)
            item["TablaSubDescuento"] = {
                "SubDescuento": [{
                    "TipoSubDescuento": "%",
                    "SubDescuentoPorcentaje": _number(line.discount, 2),
                    "MontoSubDescuento": _amount(discount),
                }]
            }
        item["MontoItem"] = _amount(net)
        items.append(item)
        bases[indicator] += net

    taxes = _itbis_amounts(move, itbis_group)
    totals = {}
    taxed = bases["1"] + bases["2"]
    if not float_is_zero(taxed, precision_digits=2):
        totals["MontoGravadoTotal"] = _amount(taxed)
    for indicator, rate in (("1", "18"), ("2", "16")):
        if not float_is_zero(bases[indicator], precision_digits=2):
            totals["MontoGravadoI%s" % indicator] = _amount(bases[indicator])
            totals["ITBIS%s" % indicator] = rate
            totals["TotalITBIS%s" % indicator] = _amount(taxes[indicator])
    if not float_is_zero(bases[EXEMPT], precision_digits=2):
        totals["MontoExento"] = _amount(bases[EXEMPT])
    total_itbis = taxes["1"] + taxes["2"]
    if not float_is_zero(total_itbis, precision_digits=2):
        totals["TotalITBIS"] = _amount(total_itbis)

    total = currency.round(taxed + bases[EXEMPT] + total_itbis)
    if float_compare(total, abs(move.amount_total), precision_digits=2) != 0:
        raise UserError(
            _("%(doc)s: the e-CF total (%(ecf)s) does not match the invoice total (%(odoo)s). "
              "This version only supports ITBIS 18%%, ITBIS 16%% and exempt lines.")
            % {"doc": move.name, "ecf": _amount(total), "odoo": _amount(abs(move.amount_total))}
        )
    totals["MontoTotal"] = _amount(total)
    return items, totals


def _line_indicator(move, line, tipo, itbis_group):
    itbis = line.tax_ids.filtered(lambda tax: itbis_group and tax.tax_group_id == itbis_group)
    others = line.tax_ids - itbis
    if others.filtered(lambda tax: tax.amount):
        raise UserError(
            _("%(doc)s: line «%(line)s» has taxes other than ITBIS (%(taxes)s). "
              "This version does not send withholdings or selective taxes yet.")
            % {"doc": move.name, "line": line.name, "taxes": ", ".join(others.mapped("name"))}
        )
    rated = itbis.filtered(lambda tax: tax.amount)
    if not rated:
        return EXEMPT
    if len(rated) > 1 or rated.amount_type != "percent" or rated.amount not in ITBIS_INDICATORS:
        raise UserError(
            _("%(doc)s: line «%(line)s» must have a single ITBIS of 18%% or 16%%, or none.")
            % {"doc": move.name, "line": line.name}
        )
    if tipo == "44":
        raise UserError(
            _("%(doc)s: a special regime invoice (E44) cannot charge ITBIS (line «%(line)s»).")
            % {"doc": move.name, "line": line.name}
        )
    return ITBIS_INDICATORS[rated.amount]


def _itbis_amounts(move, itbis_group):
    """ITBIS actually charged on the invoice, by DGII indicator."""
    amounts = {"1": 0.0, "2": 0.0}
    for line in move.line_ids.filtered(lambda line: line.tax_line_id):
        tax = line.tax_line_id
        if itbis_group and tax.tax_group_id == itbis_group and tax.amount in ITBIS_INDICATORS:
            amounts[ITBIS_INDICATORS[tax.amount]] += abs(line.amount_currency)
    return {key: move.currency_id.round(value) for key, value in amounts.items()}


def _unit_price(gross, quantity):
    """Unit price with 2 decimals when exact, else 4 (the DGII allows 4)."""
    for digits in (2, 4):
        price = float_round(gross / quantity, precision_digits=digits)
        if float_compare(price * quantity, gross, precision_digits=2) == 0:
            return _number(price, digits)
    return _number(gross / quantity, 4)


def _item_names(text):
    text = _one_line(text)
    if len(text) <= MAX_ITEM_NAME:
        return {"NombreItem": text or "-"}
    return {
        "NombreItem": text[:MAX_ITEM_NAME],
        "DescripcionItem": text[MAX_ITEM_NAME:MAX_ITEM_NAME + MAX_ITEM_DESCRIPTION],
    }


# -- Debit and credit notes --------------------------------------------------


def _origin(move):
    origin = move.reversed_entry_id or getattr(move, "debit_origin_id", move.browse())
    if not origin and move.l10n_do_origin_ncf:
        origin = move.search([
            ("company_id", "=", move.company_id.id),
            ("l10n_do_fiscal_number", "=", move.l10n_do_origin_ncf),
            ("state", "=", "posted"),
        ], limit=1)
    return origin


def _reference(move, tipo):
    origin = _origin(move)
    origin_ncf = move.l10n_do_origin_ncf or origin.l10n_do_fiscal_number
    if not origin_ncf or not origin.invoice_date:
        raise UserError(
            _("%s: the note must point to a posted invoice of this company (field «Modifies»).") % move.name
        )
    code = move.l10n_do_ecf_modification_code
    if not code:
        same_total = float_compare(abs(move.amount_total), abs(origin.amount_total), precision_digits=2) == 0
        code = "1" if tipo == "34" and same_total else "3"
    reference = {
        "NCFModificado": origin_ncf,
        "FechaNCFModificado": _date(origin.invoice_date),
        "CodigoModificacion": code,
    }
    reason = _one_line(move.ref or "")[:MAX_REASON]
    if reason:
        reference["RazonModificacion"] = reason
    return reference


def _credit_note_indicator(move):
    """0 if the credit note is within 30 days of the invoice, 1 if later."""
    origin = _origin(move)
    if origin.invoice_date and (move.invoice_date - origin.invoice_date).days > 30:
        return "1"
    return "0"


# -- Formats -----------------------------------------------------------------


def _date(value):
    return value.strftime("%d-%m-%Y")


def _datetime(value):
    if value.tzinfo is None:
        value = pytz.utc.localize(value)
    return value.astimezone(DR_TZ).strftime("%d-%m-%Y %H:%M:%S")


def _amount(value):
    return "%.2f" % float_round(value, precision_digits=2)


def _number(value, digits):
    text = ("%." + str(digits) + "f") % float_round(value, precision_digits=digits)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _digits(text):
    return "".join(char for char in (text or "") if char.isdigit())


def _one_line(text):
    """Line breaks and repeated spaces become one space: some e-CF readers
    reject signed documents whose texts contain line breaks."""
    return " ".join((text or "").split())
